from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import Document, session_scope, utcnow
from app.documents import delete_expired_documents, file_path
from app.services import Services
from tests.conftest import make_pdf, upload


def create_drawer(client: TestClient, name: str = "Sensors", temporary: bool = False) -> dict:
    response = client.post("/drawers", json={"name": name, "is_temporary": temporary})
    assert response.status_code == 201, response.text
    return response.json()


def test_drawer_crud(client: TestClient) -> None:
    drawer = create_drawer(client)
    assert drawer["document_count"] == 0

    assert client.post("/drawers", json={"name": "sensors"}).status_code == 409

    renamed = client.patch(f"/drawers/{drawer['id']}", json={"name": "MCUs"})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "MCUs"

    names = [d["name"] for d in client.get("/drawers").json()]
    assert names == ["MCUs"]

    assert client.delete(f"/drawers/{drawer['id']}").status_code == 204
    assert client.get("/drawers").json() == []
    assert client.patch("/drawers/999", json={"name": "x"}).status_code == 404


def test_blank_drawer_name_is_rejected(client: TestClient) -> None:
    assert client.post("/drawers", json={"name": "   "}).status_code == 422


def test_upload_indexes_chunks_with_correct_payload(
    client: TestClient, services: Services, sample_pdf: Path
) -> None:
    drawer = create_drawer(client)
    document = upload(client, drawer["id"], sample_pdf)
    assert document["page_count"] == 3

    listed = client.get(f"/drawers/{drawer['id']}/documents").json()
    assert listed[0]["status"] == "ready", listed[0]["error"]

    chunks = services.store.document_chunks(document["id"])
    assert {c.page for c in chunks} == {1, 2, 3}
    for chunk in chunks:
        assert chunk.doc_id == document["id"]
        assert chunk.drawer_id == drawer["id"]
        assert chunk.filename == "sensor.pdf"
        assert chunk.kind == "text"
        assert chunk.section
    page_two = " ".join(c.text for c in chunks if c.page == 2)
    assert "maximum operating temperature is 85 C" in page_two
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_get_document_file_returns_pdf(client: TestClient, sample_pdf: Path) -> None:
    drawer = create_drawer(client)
    document = upload(client, drawer["id"], sample_pdf)
    response = client.get(f"/documents/{document['id']}/file")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content == sample_pdf.read_bytes()


def test_delete_document_removes_row_vectors_and_file(
    client: TestClient, services: Services, sample_pdf: Path
) -> None:
    drawer = create_drawer(client)
    keep = upload(client, drawer["id"], sample_pdf)
    gone = upload(client, drawer["id"], sample_pdf)
    assert services.store.count(gone["id"]) > 0

    assert client.delete(f"/documents/{gone['id']}").status_code == 204

    assert services.store.count(gone["id"]) == 0
    assert not file_path(services, gone["id"]).exists()
    assert client.get(f"/documents/{gone['id']}/file").status_code == 404
    assert [d["id"] for d in client.get(f"/drawers/{drawer['id']}/documents").json()] == [
        keep["id"]
    ]
    assert services.store.count(keep["id"]) > 0
    assert client.delete(f"/documents/{gone['id']}").status_code == 404


def test_delete_drawer_removes_all_documents(
    client: TestClient, services: Services, sample_pdf: Path
) -> None:
    drawer = create_drawer(client)
    other = create_drawer(client, "Other")
    doc = upload(client, drawer["id"], sample_pdf)
    survivor = upload(client, other["id"], sample_pdf)

    assert client.delete(f"/drawers/{drawer['id']}").status_code == 204

    assert services.store.count(doc["id"]) == 0
    assert not file_path(services, doc["id"]).exists()
    assert services.store.count(survivor["id"]) > 0
    assert client.get(f"/drawers/{drawer['id']}/documents").status_code == 404


def test_non_pdf_upload_is_rejected(client: TestClient, tmp_path: Path) -> None:
    drawer = create_drawer(client)
    fake = tmp_path / "notes.pdf"
    fake.write_text("just text")
    response = client.post(
        f"/drawers/{drawer['id']}/documents", files={"file": (fake.name, fake.read_bytes())}
    )
    assert response.status_code == 415


def test_corrupt_pdf_is_rejected(client: TestClient) -> None:
    drawer = create_drawer(client)
    response = client.post(
        f"/drawers/{drawer['id']}/documents",
        files={"file": ("broken.pdf", b"%PDF-1.7\nthis is not really a pdf")},
    )
    assert response.status_code == 400
    assert "could not be opened" in response.json()["detail"]


def test_oversized_upload_is_rejected(client: TestClient) -> None:
    drawer = create_drawer(client)
    big = b"%PDF-1.7\n" + b"0" * (1024 * 1024 + 10)
    response = client.post(f"/drawers/{drawer['id']}/documents", files={"file": ("big.pdf", big)})
    assert response.status_code == 413


def test_pdf_without_text_is_marked_failed(client: TestClient, tmp_path: Path) -> None:
    drawer = create_drawer(client)
    blank = make_pdf(tmp_path / "scan.pdf", [[]])
    document = upload(client, drawer["id"], blank)
    listed = client.get(f"/drawers/{drawer['id']}/documents").json()
    assert listed[0]["id"] == document["id"]
    assert listed[0]["status"] == "failed"
    assert "OCR" in listed[0]["error"]


def test_temporary_documents_expire_and_are_cleaned_up(
    client: TestClient, services: Services, sample_pdf: Path
) -> None:
    temporary = create_drawer(client, "Scratch", temporary=True)
    permanent = create_drawer(client, "Archive")
    temp_doc = upload(client, temporary["id"], sample_pdf)
    perm_doc = upload(client, permanent["id"], sample_pdf)
    assert temp_doc["expires_at"] is not None
    assert perm_doc["expires_at"] is None

    assert delete_expired_documents(services) == 0
    with session_scope(services.sessions) as session:
        session.get(Document, temp_doc["id"]).expires_at = utcnow() - timedelta(minutes=1)

    assert delete_expired_documents(services) == 1
    assert services.store.count(temp_doc["id"]) == 0
    assert not file_path(services, temp_doc["id"]).exists()
    assert services.store.count(perm_doc["id"]) > 0


def test_health_reports_components(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body == {
        "api": "ok",
        "qdrant": True,
        "ollama": True,
        "llm_model": "qwen3:4b",
        "llm_model_available": True,
    }


def test_document_ids_are_never_reused(client: TestClient, sample_pdf: Path) -> None:
    drawer = create_drawer(client)
    first = upload(client, drawer["id"], sample_pdf)
    assert client.delete(f"/documents/{first['id']}").status_code == 204
    second = upload(client, drawer["id"], sample_pdf)
    assert second["id"] > first["id"]


def test_ingestion_reports_parsed_pages(
    client: TestClient, sample_pdf: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import documents

    recorded: list[int | None] = []
    original = documents._record_pages

    def record(services: Services, doc_id: int, done: int | None) -> None:
        recorded.append(done)
        original(services, doc_id, done)

    monkeypatch.setattr(documents, "_record_pages", record)
    drawer = create_drawer(client)
    document = upload(client, drawer["id"], sample_pdf)

    assert recorded == [1, 2, 3, None]
    listed = client.get(f"/drawers/{drawer['id']}/documents").json()
    assert listed[0]["ingest_pages_done"] == document["page_count"] == 3
