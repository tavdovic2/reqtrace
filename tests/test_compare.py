"""Revision comparison: matching, classification, value diffs, carry-over and exports."""

import csv
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.compare import Side, assign, compare_rows, masked, values_in, word_diff
from app.db import Document, ExtractionStatus, Requirement, session_scope
from app.export import COMPARE_COLUMNS
from app.services import Services
from tests.compare_fixture import EXPECTED, NEW, OLD
from tests.conftest import make_pdf, upload

MIN, RELATED = 75.0, 60.0


def classified() -> dict:
    rows = compare_rows(OLD, NEW, MIN, RELATED)
    return {(r.old.code if r.old else None, r.new.code if r.new else None): r for r in rows}


def test_fixture_pair_is_classified_as_expected() -> None:
    assert {key: row.cls for key, row in classified().items()} == EXPECTED


def test_value_unit_condition_and_modality_changes_are_recorded() -> None:
    rows = classified()
    value = rows[("REQ-002", "REQ-002")]
    assert value.changes == ["value"]
    assert [(v.old, v.new) for v in value.values] == [("150 °C", "155 °C")]
    unit = rows[("REQ-004", "REQ-004")]
    assert unit.changes == ["unit"]
    assert [(v.old, v.new) for v in unit.values] == [("100 nF", "100 pF")]
    assert rows[("REQ-003", "REQ-003")].changes == ["condition"]
    assert rows[("REQ-005", "REQ-005")].changes == ["modality"]


def test_a_split_names_the_old_row_and_nothing_is_lost() -> None:
    rows = classified()
    assert rows[(None, "REQ-011")].related_code == "REQ-008"
    assert rows[(None, "REQ-010")].related_code == "REQ-003"
    assert rows[("REQ-007", None)].related_code is None
    old_codes = {key[0] for key in rows if key[0]}
    new_codes = {key[1] for key in rows if key[1]}
    assert old_codes == {s.code for s in OLD}
    assert new_codes == {s.code for s in NEW}


def test_codes_do_not_drive_the_match_and_pages_may_move() -> None:
    row = classified()[("REQ-006", "REQ-009")]
    assert row.cls == "unchanged"
    assert (row.old.page, row.new.page) == (20, 21)


def test_assignment_is_one_to_one() -> None:
    assert len(assign([OLD[0], OLD[0]], [NEW[0]], MIN)) == 1


def test_values_carry_the_unit_of_their_run_and_masking_hides_them() -> None:
    assert [str(v) for v in values_in("Supply voltage 1.8 3.3 5.5 V")] == [
        "1.8 V",
        "3.3 V",
        "5.5 V",
    ]
    assert [str(v) for v in values_in("TJ –55 to 155 °C")] == ["-55 °C", "155 °C"]
    assert masked("TJ -55 150 °C") == masked("TJ -55 155 °C")


def test_word_diff_marks_only_the_changed_words() -> None:
    ops = word_diff("The pad must be soldered.", "The pad should be soldered.")
    assert [(o.op, o.old, o.new) for o in ops if o.op != "equal"] == [("replace", "must", "should")]


# ---------- through the API ----------


def make_document(
    client: TestClient, services: Services, tmp_path: Path, name: str, rows: list[Side]
) -> int:
    """A document with an extraction result (rows inserted as an extraction would)."""
    # The test PDF font has no Greek letters; the rows keep their own text.
    lines = [r.quote.encode("latin-1", "replace").decode("latin-1") for r in rows]
    pdf = make_pdf(tmp_path / f"{name}.pdf", [["Requirements", *lines]])
    drawer = client.post("/drawers", json={"name": name}).json()
    doc_id = upload(client, drawer["id"], pdf)["id"]
    with session_scope(services.sessions) as session:
        session.get(Document, doc_id).extraction_status = ExtractionStatus.DONE
        for row in rows:
            session.add(
                Requirement(
                    document_id=doc_id,
                    code=row.code,
                    kind=row.kind,
                    type=row.type,
                    page=row.page,
                    quote=row.quote,
                    paraphrase="",
                    verified=True,
                )
            )
    return doc_id


@pytest.fixture
def pair(client: TestClient, services: Services, tmp_path: Path) -> tuple[int, int]:
    return (
        make_document(client, services, tmp_path, "rev_b", OLD),
        make_document(client, services, tmp_path, "rev_c", NEW),
    )


def set_review(services: Services, doc_id: int, code: str, **fields: object) -> None:
    with session_scope(services.sessions) as session:
        row = session.scalar(
            select(Requirement).where(Requirement.document_id == doc_id, Requirement.code == code)
        )
        for name, value in fields.items():
            setattr(row, name, value)


def row_of(services: Services, doc_id: int, code: str) -> Requirement:
    with session_scope(services.sessions) as session:
        row = session.scalar(
            select(Requirement).where(Requirement.document_id == doc_id, Requirement.code == code)
        )
        session.expunge(row)
        return row


def test_compare_api_counts_classes_and_carries_decisions_to_unchanged_rows_only(
    client: TestClient, services: Services, pair: tuple[int, int]
) -> None:
    old, new = pair
    set_review(services, old, "REQ-006", status="accepted", reviewer_note="checked")
    set_review(services, old, "REQ-002", status="accepted", reviewer_note="ok for rev B")
    set_review(services, old, "REQ-001", status="accepted")
    set_review(services, new, "REQ-001", status="rejected")  # the newer row decided already
    response = client.post("/compare", json={"old_document_id": old, "new_document_id": new})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["counts"] == {"unchanged": 2, "changed": 5, "added": 3, "removed": 1}
    assert body["carried_over"] == 1

    carried = row_of(services, new, "REQ-009")
    assert (carried.status, carried.reviewer_note) == ("accepted", "checked")
    assert carried.carried_from == "REQ-006 in rev_b.pdf"
    changed = row_of(services, new, "REQ-002")
    assert changed.status == "proposed"
    assert "was accepted" in changed.recheck_reason
    assert "ok for rev B" in changed.recheck_reason
    assert row_of(services, new, "REQ-001").status == "rejected"
    assert row_of(services, old, "REQ-002").recheck_reason is None

    rows = {(r["old"] or {}).get("code"): r for r in body["rows"]}
    assert rows["REQ-002"]["class"] == "changed"
    assert rows["REQ-002"]["carried"] == "recheck"
    assert rows["REQ-002"]["values"] == [{"old": "150 °C", "new": "155 °C"}]
    # Running it again copies nothing twice.
    again = client.post("/compare", json={"old_document_id": old, "new_document_id": new})
    assert again.json()["carried_over"] == 0


def test_compare_export_has_the_documented_columns(
    client: TestClient, pair: tuple[int, int]
) -> None:
    old, new = pair
    text = client.get(f"/compare/{old}/{new}?format=csv").text
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    assert rows[0] == COMPARE_COLUMNS
    by_new = {row[2]: dict(zip(COMPARE_COLUMNS, row, strict=True)) for row in rows[1:]}
    assert by_new["REQ-002"]["Class"] == "changed"
    assert by_new["REQ-002"]["Changed values"] == "150 °C -> 155 °C"
    assert by_new["REQ-011"]["Class"] == "added"
    xlsx = client.get(f"/compare/{old}/{new}?format=xlsx")
    assert xlsx.content[:2] == b"PK"
    assert "rev_b_vs_rev_c_compare.xlsx" in xlsx.headers["content-disposition"]


def test_compare_without_extraction_or_with_itself_is_refused(
    client: TestClient, pair: tuple[int, int], tmp_path: Path
) -> None:
    old, _new = pair
    pdf = make_pdf(tmp_path / "plain.pdf", [["Overview", "Text without extraction."]])
    drawer = client.post("/drawers", json={"name": "Plain"}).json()
    plain = upload(client, drawer["id"], pdf)["id"]
    response = client.post("/compare", json={"old_document_id": old, "new_document_id": plain})
    assert response.status_code == 409
    assert "extract its requirements first" in response.json()["detail"]
    same = client.post("/compare", json={"old_document_id": old, "new_document_id": old})
    assert same.status_code == 422
    missing = client.post("/compare", json={"old_document_id": old, "new_document_id": 999})
    assert missing.status_code == 404
