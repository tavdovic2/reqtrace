"""Human review of requirements: status, edits, notes, bulk review, exports, merging."""

import sqlite3
from pathlib import Path

from app.db import Requirement, create_session_factory


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
