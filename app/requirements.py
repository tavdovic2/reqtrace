"""Requirement extraction: plan, ask the LLM, verify against the PDF, store.

1. **Plan.** Chunks in sections that cannot hold obligations or limits (features,
   description, register maps, legal notices, ...) and tables of known non-requirement
   shapes are skipped before any model call. The rest is grouped into calls of
   consecutive chunks from the same top-level section.
2. **Ask.** The model proposes quotes (and optionally a paraphrase) as JSON.
3. **Verify.** Each quote is located on one of the group's pages; the stored quote is
   the exact page span. Its kind is decided by deterministic rules (see
   `app.extraction_scope`): an obligation, a limit, or a description that is dropped.
   A paraphrase that is not trustworthy is removed, the row is kept.
4. **Store.** Parts of one sentence are joined, duplicates merged, codes assigned.
"""

import logging
import re
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import BaseModel, Field
from rapidfuzz import fuzz
from sqlalchemy import delete, select

from app.db import Document, DocumentStatus, ExtractionStatus, Requirement, session_scope, utcnow
from app.documents import file_path
from app.errors import ConflictError, NotFoundError, ServiceUnavailableError
from app.extraction_scope import (
    classify,
    invents_obligation,
    is_limit_section,
    is_process_instruction,
    limit_rows,
    non_requirement_table,
    skipped_section,
    type_from_unit,
)
from app.llm import LLMOutputError, LLMUnavailableError
from app.pdf import extract_page_text
from app.schemas import RequirementOut, RequirementsResponse, RequirementType
from app.services import Services
from app.vector_store import ChunkPayload, VectorStoreUnavailableError
from app.verification import (
    display_quote,
    drop_foreign_colons,
    is_fragment,
    locate_quote,
    modality_preserved,
    normalize,
    numbers_in,
    requirement_support,
)

logger = logging.getLogger(__name__)

# The output format is shown with placeholders only. A worked example with real
# content was copied verbatim into extraction results on unrelated documents.
PLACEHOLDERS = (
    "<exact text from the excerpt>",
    "<the same item in other words>",
    "<one type from the list>",
)

_PROMPT_HEAD = """You find two kinds of items in excerpts of a datasheet, a specification or \
an assignment, and nothing else.

1. Obligations: the reader is told to do or not to do something. Sentences with shall, must, \
should, is recommended, is required, do not, avoid or never, and instructions that start with \
a verb, such as "Use ...", "Place ..." or "Add ...".
2. Limits: a specified boundary the product must be operated within, such as one row of an \
absolute maximum ratings table or a recommended operating conditions table, with its values \
and units.

Most excerpts contain neither. An empty list is the normal answer.

Never return: feature lists, overview or marketing text, descriptions of how the product \
behaves or what it does, typical values, register or bit-field descriptions, footnotes, table \
headers, revision history, ordering information, lists of documents, trademarks, legal text, \
troubleshooting tables, test steps or inputs, learning objectives, tips, summaries.

For each item return:
- "quote": the sentence, list item or table row that states it, copied character for \
character. One sentence gives one item. For a table row, copy the whole row with its \
values and units, not only its label."""

_PROMPT_PARAPHRASE = """
- "paraphrase": the same item in one short sentence, using the words of the quote. If the \
quote is already clear, repeat it. Never add must, shall or required when the quote has none."""

_PROMPT_TYPE = """
- "type": one of
  electrical: voltage, current, power, resistance, capacitors, pull-up resistors;
  environmental: temperature, humidity, vibration, materials, storage;
  performance: speed, timing, accuracy, capacity;
  interface: pins, buses, protocols, APIs, data formats;
  safety: protecting people or equipment, damage, hazards;
  functional: a behaviour, feature or screen to build;
  process: how the work is done, such as version control, testing, submission;
  other: none of the above."""


def system_prompt(paraphrase: bool) -> str:
    """The extraction instructions, with or without the paraphrase field."""
    fields = f'"quote": "{PLACEHOLDERS[0]}", '
    if paraphrase:
        fields += f'"paraphrase": "{PLACEHOLDERS[1]}", '
    fields += f'"type": "{PLACEHOLDERS[2]}"'
    return (
        _PROMPT_HEAD
        + (_PROMPT_PARAPHRASE if paraphrase else "")
        + _PROMPT_TYPE
        + f'\n\nOutput format (placeholders, not content):\n{{"requirements": [{{{fields}}}]}}'
    )


_BROKEN_NUMBER = re.compile(r"\d\s*:\s*\d")
# What may separate two parts of one sentence: spaces, commas, colons and dashes. A semicolon
# usually separates list items, i.e. separate obligations, so it does not join parts.
_CLAUSE_GAP = re.compile("[\\s,:\N{EN DASH}\N{EM DASH}-]{0,4}")
_MARKDOWN_RULE = re.compile(r"^\|?\s*:?-{3,}")
_ROW_MARKER = re.compile(r"^\s*(?:-|Columns:)\s+")
# Learning objectives use "should" but place no obligation on the product or the work.
_OBJECTIVE = re.compile(
    r"\b(?:students?|learners?|participants?|you|readers?)\s+(?:should|will|shall)\s+be\s+able\s+to\b",
    re.IGNORECASE,
)
_OBJECTIVE_SECTION = re.compile(r"objective|learning outcome", re.IGNORECASE)


class ExtractedItem(BaseModel):
    quote: str
    type: RequirementType


class ExtractedItemWithParaphrase(BaseModel):
    quote: str
    paraphrase: str
    type: RequirementType


class ExtractionResult(BaseModel):
    requirements: list[ExtractedItem] = Field(default_factory=list)


class ExtractionResultWithParaphrase(BaseModel):
    requirements: list[ExtractedItemWithParaphrase] = Field(default_factory=list)


class Rejection(StrEnum):
    QUOTE_NOT_FOUND = "quote_not_found"
    NOT_REQUIREMENT = "not_requirement"


@dataclass(frozen=True)
class Candidate:
    kind: str
    paraphrase: str
    type: str
    page: int
    quote: str
    start: int
    end: int


@dataclass(frozen=True)
class Group:
    """Consecutive in-scope chunks of one top-level section, sent in one model call."""

    chunks: list[ChunkPayload]


@dataclass
class Plan:
    groups: list[Group]
    skipped: list[str] = field(default_factory=list)
    # Ratings and operating-conditions tables: every row with values is a limit by
    # definition, so these rows are read directly, without a model call.
    limit_tables: list[ChunkPayload] = field(default_factory=list)


@dataclass
class ExtractionOutcome:
    accepted: list[Candidate] = field(default_factory=list)
    rejected: dict[Rejection, int] = field(default_factory=lambda: dict.fromkeys(Rejection, 0))
    paraphrases_removed: int = 0


def _top_section(chunk: ChunkPayload) -> str:
    return chunk.section.split(" > ")[0]


def plan_extraction(chunks: list[ChunkPayload], skip_sections: list[str], group_chars: int) -> Plan:
    """Skip out-of-scope chunks and group the rest into model calls.

    `skipped` lists every skipped section path and table, for the log and the count
    shown to the user.
    """
    kept: list[ChunkPayload] = []
    skipped: list[str] = []
    limit_tables: list[ChunkPayload] = []
    for chunk in chunks:
        heading = skipped_section(chunk.section, skip_sections)
        table = non_requirement_table(chunk)
        if heading is not None:
            label = f"section '{chunk.section}'"
            if label not in skipped:
                skipped.append(label)
        elif table is not None:
            skipped.append(f"{table} table in '{chunk.section}' (page {chunk.page})")
        elif chunk.kind == "table" and is_limit_section(chunk.section):
            limit_tables.append(chunk)
        else:
            kept.append(chunk)
    for label in skipped:
        logger.info("Extraction skips %s", label)

    groups: list[Group] = []
    current: list[ChunkPayload] = []
    for chunk in kept:
        size = sum(len(c.text) for c in current) + len(chunk.text)
        if current and (_top_section(current[0]) != _top_section(chunk) or size > group_chars):
            groups.append(Group(current))
            current = []
        current.append(chunk)
    if current:
        groups.append(Group(current))
    return Plan(groups, skipped, limit_tables)


def readable_table(text: str) -> str:
    """Rewrite Markdown table rows as plain lines so the model never sees pipes.

    The header becomes "Columns: a; b; c" and each row "- x; y; z". Text outside
    tables is unchanged.
    """
    lines = []
    header_done = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            lines.append(line)
            header_done = False
            continue
        if _MARKDOWN_RULE.match(stripped):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        lines.append(("- " if header_done else "Columns: ") + "; ".join(cells))
        header_done = True
    return "\n".join(lines)


def build_prompt(group: Group) -> str:
    """User prompt for one call: each chunk with its section and page, tables as rows."""
    parts = []
    section = None
    for chunk in group.chunks:
        header = f"Section: {chunk.section}\n" if chunk.section and chunk.section != section else ""
        section = chunk.section
        parts.append(f"{header}Excerpt (page {chunk.page}):\n{readable_table(chunk.text)}")
    return "\n\n".join(parts)


def is_learning_objective(quote: str, chunk: ChunkPayload) -> bool:
    """Learning objectives are not requirements, even though they say "should"."""
    return bool(_OBJECTIVE.search(quote) or _OBJECTIVE_SECTION.search(chunk.section))


def clean_paraphrase(paraphrase: str, quote: str) -> str:
    """Remove decoding artefacts: colons the quote does not contain, and table pipes.

    A number broken by a colon ("10:00 ms" for "100 ms") cannot be repaired: the
    model also changes digits around the colon. The paraphrase is then replaced by
    the quote itself, which is exact page text.
    """
    if ":" not in quote and _BROKEN_NUMBER.search(paraphrase):
        return " ".join(quote.split())
    text = drop_foreign_colons(paraphrase, quote)
    return re.sub(r"\s+", " ", re.sub(r"\s*\|\s*", ", ", text)).strip(" ,")


def is_placeholder(text: str) -> bool:
    """True when the model returned the prompt's format placeholders instead of content."""
    norm = normalize(text)
    return "<" in text or any(fuzz.ratio(norm, normalize(p)) >= 80 for p in PLACEHOLDERS)


def _is_heading(quote: str, chunk: ChunkPayload) -> bool:
    norm = normalize(quote).rstrip(":")
    return any(norm == normalize(part) for part in chunk.section.split(" > ") if part)


def trusted_paraphrase(paraphrase: str, quote: str, threshold: float) -> str:
    """The cleaned paraphrase, or "" when it cannot be trusted.

    Removed when it is a placeholder or a fragment, adds must/shall to a quote that has
    none, strengthens "should" or "may", or uses words the quote does not support. The
    quote is the primary content, so the row itself is kept either way.
    """
    text = clean_paraphrase(paraphrase, quote)
    if (
        not text
        or is_placeholder(text)
        or is_fragment(text)
        or invents_obligation(text, quote)
        or not modality_preserved(text, quote)
        or not requirement_support(text, quote).passes(threshold)
    ):
        return ""
    return text


def untable(quote: str) -> str:
    """Undo the readable-row format the model was shown: "- a; b; c" becomes "a b c".

    The model often quotes a table row in the format of the prompt, which differs from
    the page text enough to fail the fuzzy threshold on short rows.
    """
    return re.sub(r"\s+", " ", _ROW_MARKER.sub("", quote).replace(";", " ")).strip()


def _locate_in_group(
    quote: str, group: Group, page_text: Callable[[int], str], services: Services
) -> tuple[ChunkPayload, int, int] | None:
    located = _locate_exactly_as_given(quote, group, page_text, services)
    if located is None and (_ROW_MARKER.match(quote) or ";" in quote):
        located = _locate_exactly_as_given(untable(quote), group, page_text, services)
    return located


def _locate_exactly_as_given(
    quote: str, group: Group, page_text: Callable[[int], str], services: Services
) -> tuple[ChunkPayload, int, int] | None:
    settings = services.settings
    for page in dict.fromkeys(c.page for c in group.chunks):
        match = locate_quote(
            quote, page_text(page), settings.quote_match_threshold, settings.quote_min_chars
        )
        if match is not None:
            return _context_chunk(quote, group, page), match.start, match.end
    return None


def _context_chunk(quote: str, group: Group, page: int) -> ChunkPayload:
    """The chunk a quote came from: a table on that page that contains it, else the
    first chunk on the page. Chunks of one page share the page text, so the match
    alone does not tell a table row from a sentence."""
    on_page = [c for c in group.chunks if c.page == page]
    needle = normalize(untable(quote))
    for chunk in on_page:
        if (
            chunk.kind == "table"
            and fuzz.partial_ratio(needle, normalize(untable(readable_table(chunk.text)))) >= 90
        ):
            return chunk
    return on_page[0]


def whole_lines(text: str, start: int, end: int) -> tuple[int, int]:
    """Widen a span to complete lines of the page text.

    The model often quotes only the label of a table row ("Supply voltage, TA = -55 °C
    to 150 °C"); on the page the row is one line that also holds its values and unit.
    """
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    line_end = len(text) if line_end == -1 else line_end
    while line_end > line_start and text[line_end - 1] in "\r \t":
        line_end -= 1
    return line_start, line_end


def judge(
    item: ExtractedItem | ExtractedItemWithParaphrase,
    group: Group,
    page_text: Callable[[int], str],
    services: Services,
) -> tuple[Candidate | Rejection, bool]:
    """Run one proposed item through the gates.

    Returns the candidate or the rejection reason, and whether a paraphrase was removed.
    """
    located = _locate_in_group(item.quote, group, page_text, services)
    if located is None:
        logger.info("Quote not on page: %r", item.quote)
        return Rejection.QUOTE_NOT_FOUND, False
    chunk, start, end = located
    text = page_text(chunk.page)
    if chunk.kind == "table":
        start, end = whole_lines(text, start, end)
    quote = text[start:end]
    kind = classify(quote, chunk.section)
    if (
        kind is None
        or is_learning_objective(quote, chunk)
        or is_placeholder(item.quote)
        or is_fragment(quote)
        or _is_heading(quote, chunk)
    ):
        logger.info("Not an obligation or limit (page %d): %r", chunk.page, display_quote(quote))
        return Rejection.NOT_REQUIREMENT, False
    proposed = getattr(item, "paraphrase", "")
    paraphrase = (
        trusted_paraphrase(proposed, quote, services.settings.requirement_support_threshold)
        if proposed
        else ""
    )
    removed = bool(proposed) and not paraphrase
    if removed:
        logger.info("Paraphrase removed (page %d): %r", chunk.page, proposed)
    # Instructions about testing, committing or submitting are process steps whatever
    # type the model proposed, so the same sentence always gets the same type.
    req_type = "process" if is_process_instruction(quote) else item.type
    return Candidate(kind.value, paraphrase, req_type, chunk.page, quote, start, end), removed


def _same_quote(a: Candidate, b: Candidate) -> bool:
    """Same page and one quote span lies (almost entirely) inside the other."""
    if a.page != b.page:
        return False
    overlap = min(a.end, b.end) - max(a.start, b.start)
    return overlap > 0 and overlap >= 0.8 * min(a.end - a.start, b.end - b.start)


def merge_sentence_parts(
    candidates: list[Candidate], page_text: Callable[[int], str]
) -> list[Candidate]:
    """Join candidates whose quotes are adjacent parts of the same sentence.

    The model sometimes splits one sentence into two items with two quotes ("Build a
    component with these props," + "and give it accessibilityState."). Quotes on the
    same page separated only by spaces or clause punctuation, with no sentence end in
    between, become one row whose quote spans both parts.
    """
    merged: list[Candidate] = []
    for candidate in sorted(candidates, key=lambda c: (c.page, c.start)):
        previous = merged[-1] if merged else None
        if (
            previous is not None
            and previous.page == candidate.page
            and candidate.start >= previous.end
            and not previous.quote.rstrip().endswith((".", "!", "?"))
        ):
            text = page_text(candidate.page)
            gap = text[previous.end : candidate.start]
            # Across a line break only a lower-case continuation belongs to the same
            # sentence; a capital or digit starts a new sentence or table row.
            new_line = "\n" in gap and not candidate.quote[:1].islower()
            if _CLAUSE_GAP.fullmatch(gap) and not new_line:
                paraphrases = [p for p in (previous.paraphrase, candidate.paraphrase) if p]
                merged[-1] = Candidate(
                    kind=previous.kind,
                    paraphrase=" ".join(paraphrases),
                    type=previous.type,
                    page=previous.page,
                    quote=text[previous.start : candidate.end],
                    start=previous.start,
                    end=candidate.end,
                )
                continue
        merged.append(candidate)
    return merged


def deduplicate(candidates: list[Candidate], threshold: float) -> list[Candidate]:
    """One row per quote: overlapping quotes, and near-identical quotes on other pages.

    The longer quote is kept. Quotes that differ in any number ("3.6 V" vs "5.5 V") are
    never merged.
    """
    kept: list[Candidate] = []
    for candidate in candidates:
        norm = normalize(candidate.quote)
        numbers = numbers_in(norm)
        duplicate_of = next(
            (
                i
                for i, other in enumerate(kept)
                if _same_quote(candidate, other)
                or (
                    numbers == numbers_in(other.quote)
                    and fuzz.token_sort_ratio(norm, normalize(other.quote)) >= threshold
                )
            ),
            None,
        )
        if duplicate_of is None:
            kept.append(candidate)
        elif candidate.end - candidate.start > kept[duplicate_of].end - kept[duplicate_of].start:
            kept[duplicate_of] = candidate
    return kept


def assign_codes(count: int) -> list[str]:
    """REQ-001, REQ-002, ... (more digits once there are over 999)."""
    width = max(3, len(str(count)))
    return [f"REQ-{n:0{width}d}" for n in range(1, count + 1)]


def _ask(services: Services, groups: list[Group]) -> Iterator[tuple[Group, BaseModel]]:
    """Model replies in group order; with EXTRACTION_CONCURRENCY > 1, calls overlap."""
    paraphrase = services.settings.extraction_paraphrase
    instructions = system_prompt(paraphrase)
    schema: type[BaseModel] = ExtractionResultWithParaphrase if paraphrase else ExtractionResult

    def call(group: Group) -> tuple[Group, BaseModel]:
        return group, services.llm.generate_structured(instructions, build_prompt(group), schema)

    workers = services.settings.extraction_concurrency
    if workers == 1:
        yield from map(call, groups)
        return
    with ThreadPoolExecutor(max_workers=workers) as pool:
        yield from pool.map(call, groups)


def limit_table_rows(
    chunk: ChunkPayload, page_text: Callable[[int], str], services: Services
) -> tuple[list[Candidate], int]:
    """Rows of a ratings table as limits; returns them and how many rows were not found
    on the page (a table parser can garble a multi-line cell)."""
    settings = services.settings
    text = page_text(chunk.page)
    found: list[Candidate] = []
    missing = 0
    for row in limit_rows(chunk.text):
        match = locate_quote(row, text, settings.quote_match_threshold, settings.quote_min_chars)
        if match is None:
            logger.info("Limit row not on page %d: %r", chunk.page, row)
            missing += 1
            continue
        start, end = whole_lines(text, match.start, match.end)
        quote = text[start:end]
        kind = classify(quote, chunk.section)
        if kind is not None:
            found.append(
                Candidate(kind.value, "", type_from_unit(quote), chunk.page, quote, start, end)
            )
    return found, missing


def extract_candidates(
    services: Services,
    plan: Plan,
    page_text: Callable[[int], str],
    on_progress: Callable[[int], None] | None = None,
) -> ExtractionOutcome:
    """Read ratings tables directly, ask the LLM group by group for the rest, and keep
    only verified obligations and limits."""
    outcome = ExtractionOutcome()
    for chunk in plan.limit_tables:
        rows, missing = limit_table_rows(chunk, page_text, services)
        outcome.accepted.extend(rows)
        outcome.rejected[Rejection.QUOTE_NOT_FOUND] += missing
    for done, (group, result) in enumerate(_ask(services, plan.groups), start=1):
        for item in result.requirements:  # type: ignore[attr-defined]
            verdict, removed = judge(item, group, page_text, services)
            outcome.paraphrases_removed += removed
            if isinstance(verdict, Rejection):
                outcome.rejected[verdict] += 1
            else:
                outcome.accepted.append(verdict)
        if on_progress:
            on_progress(done)
    return outcome


def start_extraction(services: Services, doc_id: int) -> None:
    """Claim the document for extraction; the work itself runs in run_extraction."""
    if not services.llm.health().reachable:
        raise ServiceUnavailableError(
            "The language model (Ollama) is not reachable, so extraction cannot start."
        )
    with session_scope(services.sessions) as session:
        document = session.get(Document, doc_id)
        if document is None:
            raise NotFoundError(f"Document {doc_id} does not exist.")
        if document.status != DocumentStatus.READY:
            raise ConflictError("The document is not ready yet; wait until ingestion finishes.")
        if document.extraction_status == ExtractionStatus.RUNNING:
            raise ConflictError("Extraction is already running for this document.")
        document.extraction_status = ExtractionStatus.RUNNING
        document.extraction_error = None
        document.extraction_done_chunks = 0
        document.extraction_total_chunks = 0
        document.extraction_started_at = utcnow()
        document.extraction_finished_at = None


def fail_interrupted_extractions(services: Services) -> int:
    """Mark extractions left running by a previous process as failed."""
    with session_scope(services.sessions) as session:
        stuck = session.scalars(
            select(Document).where(Document.extraction_status == ExtractionStatus.RUNNING)
        ).all()
        for document in stuck:
            document.extraction_status = ExtractionStatus.FAILED
            document.extraction_error = "Extraction was interrupted by a restart. Run it again."
        return len(stuck)


def page_text_reader(services: Services, doc_id: int) -> Callable[[int], str]:
    """Cached access to the text layer of a document's pages."""
    pdf = file_path(services, doc_id)
    pages: dict[int, str] = {}

    def page_text(page: int) -> str:
        if page not in pages:
            pages[page] = extract_page_text(pdf, page)
        return pages[page]

    return page_text


def run_extraction(services: Services, doc_id: int) -> None:
    """Extract, verify, deduplicate and save requirements; failures are stored, not raised."""
    try:
        settings = services.settings
        plan = plan_extraction(
            services.store.document_chunks(doc_id),
            settings.extraction_skip_sections,
            settings.extraction_group_chars,
        )
        _update(services, doc_id, extraction_total_chunks=len(plan.groups))
        page_text = page_text_reader(services, doc_id)
        started = time.monotonic()
        outcome = extract_candidates(
            services,
            plan,
            page_text,
            on_progress=lambda done: _update(services, doc_id, extraction_done_chunks=done),
        )
        unique = deduplicate(
            merge_sentence_parts(outcome.accepted, page_text), settings.dedup_threshold
        )
        _save(services, doc_id, unique, outcome, len(plan.skipped))
        logger.info(
            "Document %d: %d saved, rejected %s, %d skipped, %d calls in %.0f s",
            doc_id,
            len(unique),
            dict(outcome.rejected),
            len(plan.skipped),
            len(plan.groups),
            time.monotonic() - started,
        )
    except (LLMUnavailableError, LLMOutputError, VectorStoreUnavailableError) as exc:
        _fail(services, doc_id, str(exc))
    except Exception as exc:
        logger.exception("Unexpected extraction failure for document %d", doc_id)
        _fail(services, doc_id, f"Unexpected error during extraction: {exc}")


def _update(services: Services, doc_id: int, **fields: object) -> None:
    with session_scope(services.sessions) as session:
        document = session.get(Document, doc_id)
        if document is None:
            return
        for name, value in fields.items():
            setattr(document, name, value)


def _fail(services: Services, doc_id: int, message: str) -> None:
    logger.warning("Extraction for document %d failed: %s", doc_id, message)
    _update(
        services,
        doc_id,
        extraction_status=ExtractionStatus.FAILED,
        extraction_error=message,
        extraction_finished_at=utcnow(),
    )


def _save(
    services: Services,
    doc_id: int,
    candidates: list[Candidate],
    outcome: ExtractionOutcome,
    skipped: int,
) -> None:
    with session_scope(services.sessions) as session:
        document = session.get(Document, doc_id)
        if document is None:
            return
        session.execute(delete(Requirement).where(Requirement.document_id == doc_id))
        ordered = sorted(candidates, key=lambda c: (c.page, c.start))
        for code, candidate in zip(assign_codes(len(ordered)), ordered, strict=True):
            session.add(
                Requirement(
                    document_id=doc_id,
                    code=code,
                    kind=candidate.kind,
                    paraphrase=candidate.paraphrase,
                    type=candidate.type,
                    page=candidate.page,
                    quote=candidate.quote,
                    verified=True,
                )
            )
        document.extraction_status = ExtractionStatus.DONE
        document.rejected_quote_not_found = outcome.rejected[Rejection.QUOTE_NOT_FOUND]
        document.rejected_not_requirement = outcome.rejected[Rejection.NOT_REQUIREMENT]
        document.requirements_rejected = sum(outcome.rejected.values())
        document.extraction_skipped_sections = skipped
        document.paraphrases_removed = outcome.paraphrases_removed
        document.extraction_finished_at = utcnow()


def _elapsed(document: Document) -> float | None:
    if document.extraction_started_at is None:
        return None
    end = document.extraction_finished_at or utcnow()
    return max(0.0, (end - document.extraction_started_at).total_seconds())


def _requirement_out(requirement: Requirement) -> RequirementOut:
    out = RequirementOut.model_validate(requirement)
    return out.model_copy(update={"quote": display_quote(out.quote)})


def get_requirements(services: Services, doc_id: int) -> RequirementsResponse:
    """The saved requirement table with the extraction status."""
    with session_scope(services.sessions) as session:
        document = session.get(Document, doc_id)
        if document is None:
            raise NotFoundError(f"Document {doc_id} does not exist.")
        return RequirementsResponse(
            document_id=doc_id,
            status=document.extraction_status,
            error=document.extraction_error,
            done_chunks=document.extraction_done_chunks,
            total_chunks=document.extraction_total_chunks,
            rejected=document.requirements_rejected,
            rejected_quote_not_found=document.rejected_quote_not_found,
            rejected_not_requirement=document.rejected_not_requirement,
            skipped_sections=document.extraction_skipped_sections,
            paraphrases_removed=document.paraphrases_removed,
            elapsed_seconds=_elapsed(document),
            requirements=[_requirement_out(r) for r in document.requirements],
        )
