"""Human review of extracted requirements, and how a new extraction keeps it.

Two things are kept apart everywhere:

- **verified** is automatic: extraction found the quote on the cited page;
- **status** is a person's decision: proposed, accepted or rejected.

A reviewer can also rewrite the requirement text and add a note. The verbatim quote and
the page are never edited; the edit is stored next to them.

When extraction runs again, its rows are matched to the existing ones by page and
normalised quote (`app.verification.normalize`, as in quote verification). A matched row
keeps its code and everything a person did to it. New rows get the next free codes;
codes are never reused. A reviewed row that the new run does not find is kept and
flagged `not_found`; an unreviewed one is removed.
"""

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import Document, Requirement, ReviewStatus, session_scope, utcnow
from app.errors import NotFoundError, UnprocessableError
from app.schemas import RequirementOut, RequirementUpdate, ReviewCounts
from app.services import Services
from app.verification import display_quote, normalize

_CODE_NUMBER = re.compile(r"(\d+)$")


class ExtractedRow(Protocol):
    """What a new extraction delivers per row (app.requirements.Candidate)."""

    kind: str
    paraphrase: str
    type: str
    page: int
    quote: str
    start: int


@dataclass(frozen=True)
class MergeResult:
    kept: int
    added: int
    not_found: int


def code_number(code: str) -> int:
    """The number of a REQ-### code (0 when it has none)."""
    match = _CODE_NUMBER.search(code)
    return int(match.group(1)) if match else 0


def format_code(number: int) -> str:
    """REQ-001, ..., REQ-999, REQ-1000."""
    return f"REQ-{number:03d}"


def is_reviewed(requirement: Requirement) -> bool:
    """True when a person decided, edited or commented on the row."""
    return (
        requirement.status != ReviewStatus.PROPOSED
        or requirement.edited_text is not None
        or requirement.reviewer_note is not None
    )


def _key(page: int, quote: str) -> tuple[int, str]:
    return page, normalize(quote)


def merge_extraction(
    session: Session, document: Document, rows: Iterable[ExtractedRow]
) -> MergeResult:
    """Replace the document's extraction results with `rows`, keeping human review.

    Matched rows (same page, same normalised quote) keep id, code, status, edited text,
    note and review time; their extraction fields (kind, type, paraphrase, exact quote)
    are refreshed. New rows are proposed and get the next free codes in page order.
    Rows not found again are removed, unless reviewed: those stay, flagged `not_found`.
    """
    existing = list(document.requirements)
    by_key: dict[tuple[int, str], list[Requirement]] = defaultdict(list)
    for requirement in sorted(existing, key=lambda r: code_number(r.code)):
        by_key[_key(requirement.page, requirement.quote)].append(requirement)
    next_number = max(
        [document.last_code_number, *(code_number(r.code) for r in existing)], default=0
    )
    matched: set[int] = set()
    kept = added = 0
    for row in sorted(rows, key=lambda r: (r.page, r.start)):
        candidates = by_key.get(_key(row.page, row.quote))
        if candidates:
            requirement = candidates.pop(0)
            requirement.kind = row.kind
            requirement.type = row.type
            requirement.paraphrase = row.paraphrase
            requirement.quote = row.quote
            requirement.verified = True
            requirement.not_found = False
            matched.add(requirement.id)
            kept += 1
            continue
        next_number += 1
        session.add(
            Requirement(
                document_id=document.id,
                code=format_code(next_number),
                kind=row.kind,
                paraphrase=row.paraphrase,
                type=row.type,
                page=row.page,
                quote=row.quote,
                verified=True,
                status=ReviewStatus.PROPOSED,
            )
        )
        added += 1
    not_found = 0
    for requirement in existing:
        if requirement.id in matched:
            continue
        if is_reviewed(requirement):
            requirement.not_found = True
            not_found += 1
        else:
            session.delete(requirement)
    document.last_code_number = next_number
    return MergeResult(kept, added, not_found)


def requirement_out(requirement: Requirement) -> RequirementOut:
    """The API view of a row: quote on one line, the text to use filled in."""
    out = RequirementOut.model_validate(requirement)
    quote = display_quote(out.quote)
    return out.model_copy(
        update={
            "quote": quote,
            "requirement_text": out.edited_text or out.paraphrase or quote,
        }
    )


def count_by_status(requirements: Iterable[Requirement | RequirementOut]) -> ReviewCounts:
    counts = ReviewCounts()
    for requirement in requirements:
        counts.total += 1
        setattr(counts, requirement.status, getattr(counts, requirement.status) + 1)
        counts.not_found += bool(requirement.not_found)
    return counts


def matches_filter(requirement: Requirement | RequirementOut, status: str | None) -> bool:
    """Whether a row belongs to a table filter (None: all rows)."""
    if status is None:
        return True
    if status == "not_found":
        return bool(requirement.not_found)
    return requirement.status == status


def _set_status(requirement: Requirement, status: str) -> None:
    requirement.status = status
    requirement.reviewed_at = None if status == ReviewStatus.PROPOSED else utcnow()


def update_requirement(
    services: Services, requirement_id: int, change: RequirementUpdate
) -> RequirementOut:
    """Apply a review change to one row. Setting a status records the time; setting it
    back to proposed clears the time. Only the fields sent are changed."""
    with session_scope(services.sessions) as session:
        requirement = session.get(Requirement, requirement_id)
        if requirement is None:
            raise NotFoundError(f"Requirement {requirement_id} does not exist.")
        sent = change.model_fields_set
        if "status" in sent and change.status is not None:
            _set_status(requirement, change.status)
        if "edited_text" in sent:
            requirement.edited_text = change.edited_text
        if "reviewer_note" in sent:
            requirement.reviewer_note = change.reviewer_note
        session.flush()
        return requirement_out(requirement)


def bulk_review(services: Services, doc_id: int, ids: list[int], status: str) -> int:
    """Set one status on several rows of a document, all or nothing. Returns the count."""
    unique = list(dict.fromkeys(ids))
    with session_scope(services.sessions) as session:
        if session.get(Document, doc_id) is None:
            raise NotFoundError(f"Document {doc_id} does not exist.")
        rows = session.scalars(select(Requirement).where(Requirement.id.in_(unique))).all()
        found = {r.id: r for r in rows if r.document_id == doc_id}
        foreign = [i for i in unique if i not in found]
        if foreign:
            listed = ", ".join(str(i) for i in foreign[:20]) + (" …" if len(foreign) > 20 else "")
            subject = (
                f"Requirement {listed} does" if len(foreign) == 1 else f"Requirements {listed} do"
            )
            raise UnprocessableError(
                f"{subject} not belong to document {doc_id}; nothing was changed."
            )
        for requirement in found.values():
            _set_status(requirement, status)
    return len(unique)
