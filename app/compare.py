"""Compare the extracted requirements of two revisions of a document.

Rows are matched by content, never by `REQ-###` code (codes are per document):

- the quote, normalised, with every number and unit masked, so a row whose value
  changed still matches its old self; for a ratings-table row this is the parameter
  name and its conditions;
- the last heading of the row's section path (found from the document's chunks);
- the kind (obligation or limit) and the type.

The page only breaks ties: pages shift between revisions. Pairs are assigned one to one,
best score first, above a minimum score (COMPARE_MIN_SCORE).

Matched rows are "unchanged" when their normalised quotes are equal, otherwise "changed"
with the differing values, a modality change and a word-level diff. Unmatched rows are
"added" or "removed". A requirement split into two (or two merged into one) shows as the
best match plus an added (or removed) row that names its closest counterpart in
`related_code`, so nothing is silently lost.
"""

import difflib
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from itertools import zip_longest
from typing import Literal

from rapidfuzz import fuzz
from sqlalchemy.orm import Session

from app.db import Document, ExtractionStatus, Requirement, ReviewStatus, session_scope
from app.errors import ConflictError, NotFoundError, UnprocessableError
from app.extraction_scope import heading_text
from app.review import code_number, is_reviewed
from app.schemas import CompareResponse, CompareRow, CompareSide, ValueChangeOut, WordOpOut
from app.services import Services
from app.verification import display_quote, normalize

CompareClass = Literal["unchanged", "changed", "added", "removed"]

_UNIT = (
    r"(?:[pnuµμmkMG]?(?:v|a|w|f|hz|ω|ohms?|s|g)|k?ohms?|°\s?[cf]|%|ppm|bits?|lsb|"
    r"hrs?|hours?|days?|years?|samples?)"
)
_VALUE = re.compile(
    r"(?<![\w.])([-+±]?\d+(?:[.,]\d+)?)(?:\s*(" + _UNIT + r"))?(?![\w])", re.IGNORECASE
)
_RUN_GAP = re.compile(r"^\s*(?:to|-|/|,|and)?\s*$")
_STRONG = re.compile(r"\b(?:shall|must|required|requires|mandatory)\b", re.IGNORECASE)
_WEAK = re.compile(r"\b(?:should|recommended|recommends|may|can)\b", re.IGNORECASE)
_WORD = re.compile(r"\S+")


@dataclass(frozen=True)
class Value:
    number: str
    unit: str | None

    def __str__(self) -> str:
        return f"{self.number} {self.unit}" if self.unit else self.number


def _plain(text: str) -> str:
    """The text with typographic forms unified (NFKC, one kind of minus), case kept."""
    text = unicodedata.normalize("NFKC", text)
    return re.sub("[\u2010-\u2015\u2212]", "-", text)


def values_in(text: str) -> list[Value]:
    """The numbers of a quote with their units, in order. A unit after a run of numbers
    ("1.8 3.3 5.5 V", "-55 to 155 °C") belongs to every number of the run."""
    norm = _plain(text)
    matches = list(_VALUE.finditer(norm))
    found: list[Value] = []
    for index, match in enumerate(matches):
        unit = match.group(2)
        if unit is None:
            for later in matches[index + 1 :]:
                gap = norm[matches[matches.index(later) - 1].end() : later.start()]
                if not _RUN_GAP.match(gap):
                    break
                if later.group(2):
                    unit = later.group(2)
                    break
        number = match.group(1).replace(",", ".").lstrip("+")
        found.append(Value(number, unit.replace(" ", "") if unit else None))
    return found


def masked(text: str) -> str:
    """The normalised quote with every number and unit replaced by "#"."""
    return re.sub(r"\s+", " ", _VALUE.sub(" # ", normalize(text))).strip()


def _without_modals(text: str) -> str:
    return re.sub(r"\s+", " ", _STRONG.sub(" ", _WEAK.sub(" ", text))).strip()


def _modality(text: str) -> str | None:
    if _STRONG.search(text):
        return "must"
    if _WEAK.search(text):
        return "should"
    return None


@dataclass
class Side:
    id: int
    code: str
    kind: str
    type: str
    page: int
    quote: str
    section: str
    status: str = ReviewStatus.PROPOSED
    edited_text: str | None = None
    reviewer_note: str | None = None
    reviewed_at: datetime | None = None
    reviewed: bool = False


@dataclass
class ValueChange:
    old: str | None
    new: str | None


@dataclass
class WordOp:
    op: Literal["equal", "delete", "insert", "replace"]
    old: str
    new: str


@dataclass
class Comparison:
    cls: CompareClass
    old: Side | None
    new: Side | None
    score: float | None = None
    changes: list[str] = field(default_factory=list)
    values: list[ValueChange] = field(default_factory=list)
    words: list[WordOp] = field(default_factory=list)
    related_code: str | None = None
    carried: Literal["carried", "recheck", ""] = ""


def value_similarity(old: str, new: str) -> float | None:
    """Share (0..100) of values two quotes have in common; None when neither has any."""
    a = Counter((v.number, (v.unit or "").lower()) for v in values_in(old))
    b = Counter((v.number, (v.unit or "").lower()) for v in values_in(new))
    if not a and not b:
        return None
    return 100.0 * sum((a & b).values()) / max(sum(a.values()), sum(b.values()))


def score(old: Side, new: Side) -> float:
    """Similarity 0..100 of two rows.

    Mostly the masked quote (parameter name and conditions for a table row); the section,
    the type and the shared values add a little, so that of two similar rows the one with
    the same values wins. The page is a tiny tie-breaker only.
    """
    quote = fuzz.token_sort_ratio(masked(old.quote), masked(new.quote))
    same_type = 100.0 if old.type == new.type else 0.0
    values = value_similarity(old.quote, new.quote)
    parts = [(0.70, quote), (0.05, same_type)]
    if old.section and new.section:
        parts.append((0.15, fuzz.token_set_ratio(old.section, new.section)))
    if values is not None:
        parts.append((0.10, values))
    # Weights of the parts that apply, scaled to sum to one.
    total = sum(w * v for w, v in parts) / sum(w for w, _ in parts)
    if old.kind != new.kind:
        total -= 25
    return total - 0.01 * min(abs(old.page - new.page), 50)


def assign(old: list[Side], new: list[Side], min_score: float) -> list[tuple[int, int, float]]:
    """One-to-one pairs (old index, new index, score), best score first, above min_score."""
    pairs = sorted(
        ((score(o, n), i, j) for i, o in enumerate(old) for j, n in enumerate(new)),
        key=lambda p: -p[0],
    )
    used_old: set[int] = set()
    used_new: set[int] = set()
    chosen: list[tuple[int, int, float]] = []
    for value, i, j in pairs:
        if value < min_score:
            break
        if i in used_old or j in used_new:
            continue
        used_old.add(i)
        used_new.add(j)
        chosen.append((i, j, value))
    return chosen


def word_diff(old: str, new: str) -> list[WordOp]:
    """A word-level diff of two quotes."""
    a = _WORD.findall(old)
    b = _WORD.findall(new)
    ops = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        ops.append(WordOp(tag, " ".join(a[i1:i2]), " ".join(b[j1:j2])))  # type: ignore[arg-type]
    return ops


def describe(old: Side, new: Side) -> tuple[list[str], list[ValueChange]]:
    """What changed between two matched rows: kinds of change and the value pairs."""
    changes: list[str] = []
    value_changes: list[ValueChange] = []
    old_values, new_values = values_in(old.quote), values_in(new.quote)
    for a, b in zip_longest(old_values, new_values):
        if a == b:
            continue
        value_changes.append(ValueChange(str(a) if a else None, str(b) if b else None))
        if a and b and a.number == b.number:
            if "unit" not in changes:
                changes.append("unit")
        elif "value" not in changes:
            changes.append("value")
    if _without_modals(masked(old.quote)) != _without_modals(masked(new.quote)):
        changes.append("condition" if old.kind == "limit" else "wording")
    if _modality(old.quote) != _modality(new.quote):
        changes.append("modality")
    return changes or ["wording"], value_changes


def compare_rows(
    old: list[Side], new: list[Side], min_score: float, related_score: float
) -> list[Comparison]:
    """Classify every row of both documents."""
    pairs = assign(old, new, min_score)
    matched_old = {i for i, _, _ in pairs}
    matched_new = {j for _, j, _ in pairs}
    result: list[Comparison] = []
    for i, j, value in pairs:
        o, n = old[i], new[j]
        if normalize(o.quote) == normalize(n.quote):
            result.append(Comparison("unchanged", o, n, round(value, 1)))
            continue
        changes, values = describe(o, n)
        result.append(
            Comparison(
                "changed", o, n, round(value, 1), changes, values, word_diff(o.quote, n.quote)
            )
        )
    for j, n in enumerate(new):
        if j not in matched_new:
            result.append(
                Comparison("added", None, n, related_code=_related(n, old, related_score))
            )
    for i, o in enumerate(old):
        if i not in matched_old:
            result.append(
                Comparison("removed", o, None, related_code=_related(o, new, related_score))
            )
    result.sort(key=_order)
    return result


def _related(row: Side, others: list[Side], related_score: float) -> str | None:
    """The closest row of the other document, if close enough to be a split or merge."""
    best = max(others, key=lambda other: score(row, other), default=None)
    if best is not None and score(row, best) >= related_score:
        return best.code
    return None


def _order(item: Comparison) -> tuple[int, int, int]:
    side = item.new or item.old
    assert side is not None
    other = item.old if item.new else None
    return side.page, code_number(side.code), code_number(other.code) if other else 0


def counts(rows: list[Comparison]) -> dict[str, int]:
    totals = dict.fromkeys(("unchanged", "changed", "added", "removed"), 0)
    for row in rows:
        totals[row.cls] += 1
    return totals


# ---------- documents ----------


def _section_of(quote: str, page: int, chunks_by_page: dict[int, list[tuple[str, str]]]) -> str:
    """The last heading of the chunk on `page` that holds the quote best."""
    candidates = chunks_by_page.get(page, [])
    if not candidates:
        return ""
    needle = normalize(quote)[:120]
    section, _text = max(candidates, key=lambda c: fuzz.partial_ratio(needle, c[1]))
    last = section.split(" > ")[-1] if section else ""
    return heading_text(last)


def _sides(services: Services, session: Session, doc_id: int) -> tuple[Document, list[Side]]:
    document = session.get(Document, doc_id)
    if document is None:
        raise NotFoundError(f"Document {doc_id} does not exist.")
    if document.extraction_status != ExtractionStatus.DONE:
        state = (
            "is still running"
            if document.extraction_status == ExtractionStatus.RUNNING
            else "has not been run"
        )
        raise ConflictError(
            f"Requirement extraction for '{document.filename}' {state}; extract its "
            "requirements first, then compare."
        )
    chunks_by_page: dict[int, list[tuple[str, str]]] = {}
    for chunk in services.store.document_chunks(doc_id):
        chunks_by_page.setdefault(chunk.page, []).append((chunk.section, normalize(chunk.text)))
    sides = []
    for row in sorted(document.requirements, key=lambda r: (r.page, code_number(r.code))):
        if row.not_found:
            continue
        sides.append(
            Side(
                id=row.id,
                code=row.code,
                kind=row.kind,
                type=row.type,
                page=row.page,
                quote=display_quote(row.quote),
                section=_section_of(row.quote, row.page, chunks_by_page),
                status=row.status,
                edited_text=row.edited_text,
                reviewer_note=row.reviewer_note,
                reviewed_at=row.reviewed_at,
                reviewed=is_reviewed(row),
            )
        )
    return document, sides


@dataclass
class DocumentComparison:
    old: Document
    new: Document
    rows: list[Comparison]
    carried_over: int


def compare_documents(
    services: Services, old_id: int, new_id: int, carry_over: bool
) -> DocumentComparison:
    """Compare two documents; with `carry_over`, copy review decisions to the newer one.

    Unchanged rows get the older row's status, edited text and note when the newer row
    has no decision of its own. Changed rows never inherit an acceptance; the newer row is
    marked for review again with the older decision named. The older document's review
    data is never changed.
    """
    if old_id == new_id:
        raise UnprocessableError("Choose two different documents to compare.")
    settings = services.settings
    with session_scope(services.sessions) as session:
        old_doc, old_rows = _sides(services, session, old_id)
        new_doc, new_rows = _sides(services, session, new_id)
        rows = compare_rows(
            old_rows, new_rows, settings.compare_min_score, settings.compare_related_score
        )
        carried = 0
        if carry_over:
            for row in rows:
                carried += _carry(session, row, old_doc.filename)
        session.flush()
        session.expunge_all()
        return DocumentComparison(old_doc, new_doc, rows, carried)


def _carry(session: Session, row: Comparison, old_filename: str) -> int:
    """Apply the carry-over rules to one compared pair; returns 1 if a decision was copied."""
    if row.old is None or row.new is None or not row.old.reviewed:
        return 0
    target = session.get(Requirement, row.new.id)
    if target is None:
        return 0
    source = f"{row.old.code} in {old_filename}"
    if row.cls == "unchanged":
        if is_reviewed(target):
            row.carried = "carried" if target.carried_from else ""
            return 0
        target.status = row.old.status
        target.edited_text = row.old.edited_text
        target.reviewer_note = row.old.reviewer_note
        target.reviewed_at = row.old.reviewed_at
        target.carried_from = source
        target.recheck_reason = None
        row.carried = "carried"
        row.new.status = target.status
        return 1
    note = f" (note: {row.old.reviewer_note})" if row.old.reviewer_note else ""
    target.recheck_reason = (
        f"Changed since {source}, which was {row.old.status}{note}; review this row again."
    )
    row.carried = "recheck"
    return 0


def to_response(result: DocumentComparison) -> CompareResponse:
    """The API view of a comparison."""

    def side(item: Side | None) -> CompareSide | None:
        if item is None:
            return None
        return CompareSide(
            id=item.id,
            code=item.code,
            kind=item.kind,
            type=item.type,
            page=item.page,
            quote=item.quote,
            section=item.section,
            status=item.status,
            reviewer_note=item.reviewer_note,
            edited_text=item.edited_text,
        )

    return CompareResponse(
        old_document_id=result.old.id,
        old_filename=result.old.filename,
        new_document_id=result.new.id,
        new_filename=result.new.filename,
        counts=counts(result.rows),
        carried_over=result.carried_over,
        rows=[
            CompareRow(
                cls=row.cls,
                old=side(row.old),
                new=side(row.new),
                score=row.score,
                changes=row.changes,
                values=[ValueChangeOut(old=v.old, new=v.new) for v in row.values],
                words=[WordOpOut(op=w.op, old=w.old, new=w.new) for w in row.words],
                related_code=row.related_code,
                carried=row.carried,
            )
            for row in result.rows
        ],
    )
