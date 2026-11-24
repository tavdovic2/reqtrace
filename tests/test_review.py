"""Human review of requirements: status, edits, notes, bulk review, exports, merging."""

import csv
import io
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import Requirement, create_session_factory
from app.export import COLUMNS, REVIEW_COLUMNS
from app.main import create_app
from app.requirements import ExtractedItemWithParaphrase, ExtractionResultWithParaphrase
from app.services import Services
from tests.conftest import FakeLLM, make_pdf, upload

FIRST = "Place the sensor close to the heat source."
SECOND = "Do not route high-speed traces under the device."
THIRD = "Connect the thermal pad to the ground plane."
FOURTH = "Avoid pullup resistors smaller than 2 kOhm."


@pytest.fixture
def layout_pdf(tmp_path: Path) -> Path:
    return make_pdf(tmp_path / "layout.pdf", [["9 Layout", FIRST, SECOND, THIRD, FOURTH]])


def run_extraction(client: TestClient, fake_llm: FakeLLM, doc_id: int, *quotes: str) -> dict:
    items = [ExtractedItemWithParaphrase(quote=q, paraphrase="", type="other") for q in quotes]
    fake_llm.structured_replies.extend(
        [lambda _p: ExtractionResultWithParaphrase(requirements=items)] * 5
    )
    assert client.post(f"/documents/{doc_id}/requirements").status_code == 202
    table = client.get(f"/documents/{doc_id}/requirements").json()
    assert table["status"] == "done", table["error"]
    fake_llm.structured_replies.clear()
    return table


@pytest.fixture
def extracted(client: TestClient, fake_llm: FakeLLM, layout_pdf: Path) -> dict:
    drawer = client.post("/drawers", json={"name": "Layout"}).json()
    document = upload(client, drawer["id"], layout_pdf)
    return run_extraction(client, fake_llm, document["id"], FIRST, SECOND, THIRD)


def rows_by_quote(table: dict) -> dict[str, dict]:
    return {r["quote"]: r for r in table["requirements"]}


def patch(client: TestClient, requirement_id: int, body: dict) -> dict:
    response = client.patch(f"/requirements/{requirement_id}", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_new_rows_are_proposed_and_verified_is_separate(extracted: dict) -> None:
    assert [r["code"] for r in extracted["requirements"]] == ["REQ-001", "REQ-002", "REQ-003"]
    assert all(r["status"] == "proposed" and r["verified"] for r in extracted["requirements"])
    assert extracted["counts"] == {
        "total": 3,
        "proposed": 3,
        "accepted": 0,
        "rejected": 0,
        "not_found": 0,
    }
    assert (extracted["kept"], extracted["added"], extracted["no_longer_found"]) == (0, 3, 0)


def test_status_change_sets_and_clears_the_review_time(client: TestClient, extracted: dict) -> None:
    row = extracted["requirements"][0]
    accepted = patch(client, row["id"], {"status": "accepted"})
    assert accepted["status"] == "accepted" and accepted["reviewed_at"] is not None
    assert accepted["verified"] is True
    rejected = patch(client, row["id"], {"status": "rejected"})
    assert rejected["status"] == "rejected" and rejected["reviewed_at"] is not None
    reset = patch(client, row["id"], {"status": "proposed"})
    assert reset["status"] == "proposed" and reset["reviewed_at"] is None


def test_edit_changes_only_the_requirement_text(client: TestClient, extracted: dict) -> None:
    row = extracted["requirements"][0]
    edited = patch(client, row["id"], {"edited_text": "  Keep the sensor near the source.  "})
    assert edited["edited_text"] == "Keep the sensor near the source."
    assert edited["requirement_text"] == "Keep the sensor near the source."
    assert (edited["quote"], edited["page"], edited["code"]) == (FIRST, 1, "REQ-001")
    assert edited["status"] == "proposed" and edited["reviewed_at"] is None
    restored = patch(client, row["id"], {"edited_text": None})
    assert restored["edited_text"] is None and restored["requirement_text"] == FIRST


def test_quote_and_page_cannot_be_edited(client: TestClient, extracted: dict) -> None:
    row = extracted["requirements"][0]
    changed = patch(
        client, row["id"], {"quote": "Something else.", "page": 9, "status": "accepted"}
    )
    assert (changed["quote"], changed["page"]) == (FIRST, 1)


def test_note_is_set_and_removed(client: TestClient, extracted: dict) -> None:
    row = extracted["requirements"][1]
    assert (
        patch(client, row["id"], {"reviewer_note": "Check with layout team"})["reviewer_note"]
        == "Check with layout team"
    )
    assert patch(client, row["id"], {"reviewer_note": "   "})["reviewer_note"] is None


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"status": "approved"}, "proposed"),
        ({"status": None}, "status must be one of"),
        ({"edited_text": "   "}, "edited text must not be empty"),
        ({}, "nothing to change"),
    ],
)
def test_invalid_review_changes_are_rejected_with_a_clear_message(
    client: TestClient, extracted: dict, body: dict, message: str
) -> None:
    response = client.patch(f"/requirements/{extracted['requirements'][0]['id']}", json=body)
    assert response.status_code == 422
    assert message in response.text


def test_review_of_an_unknown_requirement_is_404(client: TestClient) -> None:
    assert client.patch("/requirements/999", json={"status": "accepted"}).status_code == 404


def test_bulk_review_changes_all_rows_in_one_go(client: TestClient, extracted: dict) -> None:
    doc = extracted["document_id"]
    ids = [r["id"] for r in extracted["requirements"][:2]]
    response = client.post(
        f"/documents/{doc}/requirements/review", json={"ids": ids, "status": "accepted"}
    )
    assert response.status_code == 200
    table = response.json()
    assert table["counts"]["accepted"] == 2 and table["counts"]["proposed"] == 1
    assert all(r["reviewed_at"] for r in table["requirements"] if r["id"] in ids)


def test_bulk_review_with_a_foreign_id_changes_nothing(
    client: TestClient, fake_llm: FakeLLM, extracted: dict, layout_pdf: Path
) -> None:
    drawer = client.post("/drawers", json={"name": "Other"}).json()
    other = upload(client, drawer["id"], layout_pdf)
    foreign = run_extraction(client, fake_llm, other["id"], FOURTH)["requirements"][0]["id"]
    doc = extracted["document_id"]
    own = extracted["requirements"][0]["id"]
    response = client.post(
        f"/documents/{doc}/requirements/review",
        json={"ids": [own, foreign], "status": "rejected"},
    )
    assert response.status_code == 422
    assert f"{foreign}" in response.json()["detail"]
    assert response.json()["detail"] == (
        f"Requirement {foreign} does not belong to document {doc}; nothing was changed."
    )
    table = client.get(f"/documents/{doc}/requirements").json()
    assert table["counts"]["rejected"] == 0


@pytest.mark.parametrize(
    "body", [{"ids": [], "status": "accepted"}, {"ids": [1], "status": "maybe"}]
)
def test_bulk_review_validates_its_body(client: TestClient, extracted: dict, body: dict) -> None:
    doc = extracted["document_id"]
    assert client.post(f"/documents/{doc}/requirements/review", json=body).status_code == 422


def test_bulk_review_of_an_unknown_document_is_404(client: TestClient) -> None:
    response = client.post(
        "/documents/999/requirements/review", json={"ids": [1], "status": "accepted"}
    )
    assert response.status_code == 404


def test_table_filters_by_status_and_counts_all_rows(client: TestClient, extracted: dict) -> None:
    doc = extracted["document_id"]
    first, second, _third = extracted["requirements"]
    patch(client, first["id"], {"status": "accepted"})
    patch(client, second["id"], {"status": "rejected"})
    accepted = client.get(f"/documents/{doc}/requirements?status=accepted").json()
    assert [r["code"] for r in accepted["requirements"]] == ["REQ-001"]
    assert accepted["counts"] == {
        "total": 3,
        "proposed": 1,
        "accepted": 1,
        "rejected": 1,
        "not_found": 0,
    }
    proposed = client.get(f"/documents/{doc}/requirements?status=proposed").json()
    assert [r["code"] for r in proposed["requirements"]] == ["REQ-003"]
    assert client.get(f"/documents/{doc}/requirements?status=done").status_code == 422


def csv_rows(client: TestClient, doc: int, query: str = "") -> list[list[str]]:
    text = client.get(f"/documents/{doc}/requirements?format=csv{query}").text
    return list(csv.reader(io.StringIO(text.lstrip("﻿"))))


def test_default_export_has_only_accepted_rows_with_edits_and_the_quote(
    client: TestClient, extracted: dict
) -> None:
    doc = extracted["document_id"]
    first, second, _third = extracted["requirements"]
    patch(client, first["id"], {"status": "accepted", "edited_text": "Keep it near the source."})
    patch(client, second["id"], {"status": "rejected"})
    rows = csv_rows(client, doc)
    assert rows[0] == COLUMNS
    assert len(rows) == 2
    exported = dict(zip(COLUMNS, rows[1], strict=True))
    assert exported["ID"] == "REQ-001"
    assert exported["Requirement"] == "Keep it near the source."
    assert exported["Edited"] == "yes"
    assert exported["Quote (verbatim)"] == FIRST
    assert exported["Page"] == "1"
    assert exported["Verified (quote on page)"] == "yes"


def test_all_rows_export_adds_status_note_and_review_time(
    client: TestClient, extracted: dict
) -> None:
    doc = extracted["document_id"]
    first, second, _third = extracted["requirements"]
    patch(client, first["id"], {"status": "accepted"})
    patch(client, second["id"], {"status": "rejected", "reviewer_note": "Duplicate of REQ-001"})
    rows = csv_rows(client, doc, "&scope=all")
    assert rows[0] == COLUMNS + REVIEW_COLUMNS
    by_id = {row[0]: dict(zip(rows[0], row, strict=True)) for row in rows[1:]}
    assert [by_id[c]["Review status"] for c in ("REQ-001", "REQ-002", "REQ-003")] == [
        "accepted",
        "rejected",
        "proposed",
    ]
    assert by_id["REQ-002"]["Reviewer note"] == "Duplicate of REQ-001"
    assert by_id["REQ-002"]["Reviewed at (UTC)"] != ""
    assert by_id["REQ-003"]["Reviewed at (UTC)"] == ""
    assert by_id["REQ-002"]["Verified (quote on page)"] == "yes"
    xlsx = client.get(f"/documents/{doc}/requirements?format=xlsx&scope=all")
    assert xlsx.content[:2] == b"PK"
    assert "layout_requirements_all.xlsx" in xlsx.headers["content-disposition"]


def test_reextraction_keeps_decisions_and_codes_and_reports_the_merge(
    client: TestClient, fake_llm: FakeLLM, extracted: dict
) -> None:
    doc = extracted["document_id"]
    rows = rows_by_quote(extracted)
    patch(client, rows[FIRST]["id"], {"status": "accepted", "edited_text": "Near the source."})
    patch(client, rows[SECOND]["id"], {"status": "rejected", "reviewer_note": "Not ours"})
    # THIRD stays proposed and untouched.

    # The new run finds FIRST again, no longer finds SECOND and THIRD, and finds FOURTH.
    rerun = run_extraction(client, fake_llm, doc, FIRST, FOURTH)
    assert (rerun["kept"], rerun["added"], rerun["no_longer_found"]) == (1, 1, 1)
    after = rows_by_quote(rerun)
    kept = after[FIRST]
    assert (kept["id"], kept["code"], kept["status"]) == (rows[FIRST]["id"], "REQ-001", "accepted")
    assert kept["edited_text"] == "Near the source." and kept["reviewed_at"] is not None
    assert kept["not_found"] is False
    # The rejected row is reviewed work: kept, flagged, never deleted silently.
    gone = after[SECOND]
    assert (gone["code"], gone["status"], gone["not_found"]) == ("REQ-002", "rejected", True)
    assert gone["reviewer_note"] == "Not ours"
    # The untouched proposed row is removed; its code is not given to the new row.
    assert THIRD not in after
    assert (after[FOURTH]["code"], after[FOURTH]["status"]) == ("REQ-004", "proposed")
    assert rerun["counts"]["not_found"] == 1
    flagged = client.get(f"/documents/{doc}/requirements?status=not_found").json()
    assert [r["code"] for r in flagged["requirements"]] == ["REQ-002"]

    # Found again later: the flag goes, code and decision stay.
    third_run = run_extraction(client, fake_llm, doc, FIRST, SECOND, FOURTH)
    assert (third_run["kept"], third_run["added"], third_run["no_longer_found"]) == (3, 0, 0)
    back = rows_by_quote(third_run)[SECOND]
    assert (back["code"], back["status"], back["not_found"]) == ("REQ-002", "rejected", False)


def test_codes_are_never_reused_after_rows_are_removed(
    client: TestClient, fake_llm: FakeLLM, extracted: dict
) -> None:
    doc = extracted["document_id"]
    # Nothing reviewed: REQ-002 and REQ-003 disappear, then come back as new rows.
    run_extraction(client, fake_llm, doc, FIRST)
    again = run_extraction(client, fake_llm, doc, FIRST, SECOND, THIRD)
    assert [r["code"] for r in again["requirements"]] == ["REQ-001", "REQ-004", "REQ-005"]


def test_decisions_survive_a_restart(
    client: TestClient, services: Services, extracted: dict
) -> None:
    row = extracted["requirements"][0]
    patch(client, row["id"], {"status": "accepted", "reviewer_note": "ok"})
    restarted = Services(
        settings=services.settings,
        sessions=create_session_factory(services.settings.resolved_database_url),
        store=services.store,
        embedder=services.embedder,
        llm=services.llm,
        parser=services.parser,
    )
    with TestClient(create_app(restarted, run_background_jobs=False)) as fresh:
        table = fresh.get(f"/documents/{extracted['document_id']}/requirements").json()
    first = table["requirements"][0]
    assert (first["status"], first["reviewer_note"]) == ("accepted", "ok")


def test_deleting_a_document_deletes_its_review_data(
    client: TestClient, services: Services, extracted: dict
) -> None:
    patch(client, extracted["requirements"][0]["id"], {"status": "accepted"})
    assert client.delete(f"/documents/{extracted['document_id']}").status_code == 204
    with services.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Requirement)) == 0


def test_deleting_a_drawer_deletes_its_review_data(
    client: TestClient, services: Services, extracted: dict
) -> None:
    patch(client, extracted["requirements"][0]["id"], {"reviewer_note": "keep"})
    drawers = client.get("/drawers").json()
    assert client.delete(f"/drawers/{drawers[0]['id']}").status_code == 204
    with services.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Requirement)) == 0


def test_existing_rows_become_proposed_after_the_migration(tmp_path: Path) -> None:
    path = tmp_path / "before_review.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE documents (id INTEGER PRIMARY KEY, drawer_id INTEGER, filename VARCHAR, "
            "page_count INTEGER, status VARCHAR, error TEXT, created_at DATETIME, "
            "expires_at DATETIME, extraction_status VARCHAR, extraction_error TEXT, "
            "extraction_done_chunks INTEGER, extraction_total_chunks INTEGER, "
            "requirements_rejected INTEGER)"
        )
        connection.execute(
            "CREATE TABLE requirements (id INTEGER PRIMARY KEY, document_id INTEGER, code VARCHAR, "
            "text TEXT, kind VARCHAR, type VARCHAR, page INTEGER, quote TEXT, verified BOOLEAN, "
            "created_at DATETIME)"
        )
        connection.execute(
            "INSERT INTO requirements (id, document_id, code, text, kind, type, page, quote, "
            "verified, created_at) VALUES (1, 1, 'REQ-007', '', 'obligation', 'other', 2, "
            "'Place the sensor.', 1, '2026-01-01 00:00:00')"
        )
    factory = create_session_factory(f"sqlite:///{path.as_posix()}")
    with factory() as session:
        row = session.get(Requirement, 1)
        assert row is not None
        assert (row.status, row.edited_text, row.reviewer_note, row.reviewed_at) == (
            "proposed",
            None,
            None,
            None,
        )
        assert row.not_found is False
        assert row.verified is True
