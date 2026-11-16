"""Question answering grounded in retrieved chunks, with citations to file and page.

The model answers as a list of claims, each citing the excerpts that state it.
Claims without a valid citation, or with numbers that none of their cited
excerpts contain, are dropped. The sources are exactly the excerpts that the
remaining claims cite.
"""

import logging
import re
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, Field

from app.db import Drawer, session_scope
from app.documents import file_path
from app.errors import NotFoundError
from app.language import detect_language
from app.pdf import extract_page_text
from app.retrieval import retrieve
from app.schemas import (
    AnswerLine,
    AskDebug,
    AskRequest,
    AskResponse,
    DiscardedClaim,
    RetrievedChunk,
    Source,
)
from app.services import Services
from app.vector_store import ScoredChunk, SearchMode
from app.verification import locate_quote, numbers_in, numbers_with_words

logger = logging.getLogger(__name__)

NOT_FOUND_ANSWER = "I could not find the answer in the selected documents."
_CITATION_MARKER = re.compile(
    r"\s*(?:\((?:excerpts?|sources?|izvodi?|izvoda|ref\.?)\s*\d+(?:\s*(?:,|and|i|&|-)\s*\d+)*\s*\)"
    r"|\[\s*\d+(?:\s*,\s*\d+)*\s*\])",
    re.IGNORECASE,
)
SNIPPET_CHARS = 300

SYSTEM_PROMPT_TEMPLATE = """You answer questions about technical documents using ONLY the \
numbered excerpts you are given. Never use outside knowledge and never guess.
Reply with JSON fields in this order:
- "evidence": for every excerpt that helps to answer the question, its number and the sentence \
or table row copied from it. A question that compares or combines several things needs \
evidence from every excerpt that covers one of them.
- "answered": true only if the evidence contains the answer; false if the excerpts do not say it.
- "claims": the answer as one or more short statements, each written in {language}. Give exact \
values and units. When the excerpts give several values for different conditions or modes, \
state each value with its condition. Each statement lists in "excerpts" the numbers of the \
excerpts that state it; do not write excerpt numbers inside the statement. Leave out anything \
that no excerpt states."""

TRANSLATION_PROMPT = """Translate the user's question into English for a search engine. Keep \
part numbers, units and technical terms. Put only the English question in "english"."""


class Evidence(BaseModel):
    excerpt: int
    quote: str


class Claim(BaseModel):
    statement: str
    excerpts: list[int] = Field(default_factory=list)


class Translation(BaseModel):
    english: str


class CitedAnswer(BaseModel):
    """Structured reply requested from the LLM.

    Field order matters for a small model under constrained decoding: it first
    copies evidence from each relevant excerpt, then decides whether that evidence
    answers the question, and only then writes claims, each with its own citations.
    """

    evidence: list[Evidence]
    answered: bool
    claims: list[Claim] = Field(default_factory=list)


def system_prompt(question: str) -> str:
    """System prompt naming the answer language explicitly when it can be detected."""
    language = detect_language(question)
    target = (
        f"{language} (the language of the question)" if language else "the language of the question"
    )
    return SYSTEM_PROMPT_TEMPLATE.format(language=target)


def format_context(chunks: list[ScoredChunk]) -> str:
    """Number the chunks so the model can cite them as [1], [2], ..."""
    parts = []
    for number, chunk in enumerate(chunks, start=1):
        p = chunk.payload
        header = f"[{number}] {p.filename}, page {p.page}"
        if p.section:
            header += f", section: {p.section}"
        parts.append(f"{header}\n{p.text}")
    return "\n\n".join(parts)


def build_prompt(question: str, chunks: list[ScoredChunk]) -> str:
    """User prompt with the excerpts followed by the question."""
    return f"Excerpts:\n\n{format_context(chunks)}\n\nQuestion: {question}"


def _snippet(text: str) -> str:
    flat = re.sub(r"\s+", " ", text).strip()
    return flat if len(flat) <= SNIPPET_CHARS else flat[: SNIPPET_CHARS - 1].rstrip() + "…"


def strip_citation_markers(statement: str) -> str:
    """Remove excerpt references the model wrote into a statement ("(excerpts 2, 3)",
    "[1]"). They are not part of the claim, and their digits would be checked as values."""
    return _CITATION_MARKER.sub("", statement).strip()


def _citations_from_evidence(
    statement: str, evidence: list[Evidence], chunks: list[ScoredChunk]
) -> list[int]:
    """Excerpts for a claim the model left uncited: those of its own evidence whose text
    contains every number of the claim. A claim without numbers gets none, so it is
    still discarded; the number check that follows applies as usual."""
    numbers = numbers_in(statement)
    if not numbers:
        return []
    candidates = dict.fromkeys(e.excerpt for e in evidence if 1 <= e.excerpt <= len(chunks))
    return [n for n in candidates if set(numbers) <= numbers_with_words(chunks[n - 1].payload.text)]


def check_claims(
    reply: CitedAnswer, chunks: list[ScoredChunk], question: str
) -> tuple[list[Claim], list[DiscardedClaim]]:
    """Claims that cite at least one valid excerpt and whose numbers those excerpts contain,
    and the discarded ones with the reason.

    Numbers from the question itself (a part number such as "NX-310") are not claims
    about the document and are allowed. A source that writes a number as a word
    ("four") supports the digit. Citation numbers outside the excerpt list are ignored.
    """
    question_numbers = set(numbers_in(question))
    kept: list[Claim] = []
    discarded: list[DiscardedClaim] = []
    for claim in reply.claims:
        cited = [n for n in dict.fromkeys(claim.excerpts) if 1 <= n <= len(chunks)]
        statement = strip_citation_markers(claim.statement)
        if not statement:
            continue
        if not cited:
            cited = _citations_from_evidence(statement, reply.evidence, chunks)
        if not cited:
            discarded.append(
                DiscardedClaim(
                    statement=statement, reason="cites no retrieved excerpt", code="no_citation"
                )
            )
            continue
        cited_numbers = question_numbers | {
            num for n in cited for num in numbers_with_words(chunks[n - 1].payload.text)
        }
        missing = [num for num in numbers_in(statement) if num not in cited_numbers]
        if missing:
            logger.info("Dropped a claim with numbers its excerpts do not contain: %r", statement)
            reason = f"number {', '.join(missing)} is not in the cited excerpts"
            discarded.append(
                DiscardedClaim(
                    statement=statement, reason=reason, code="number_missing", numbers=missing
                )
            )
            continue
        kept.append(Claim(statement=statement, excerpts=cited))
    return kept, discarded


def supported_claims(reply: CitedAnswer, chunks: list[ScoredChunk], question: str) -> list[Claim]:
    """The claims that survive `check_claims`."""
    return check_claims(reply, chunks, question)[0]


def retrieval_phrasings(services: Services, question: str) -> list[str]:
    """The question, plus an English translation when it is asked in Bosnian.

    BM25 cannot match Bosnian words against an English document and the multilingual
    dense model ranks the right chunk much lower across languages (rank 18 instead of 1
    in a TMP117 test), so the translation is searched together with the original.
    """
    if detect_language(question) != "Bosnian":
        return [question]
    # Structured output: as free text, qwen3:4b sometimes wrote its reasoning instead of
    # the translation.
    reply = services.llm.generate_structured(TRANSLATION_PROMPT, question, Translation)
    english = " ".join(reply.english.split())[:300]
    return [question, english] if english and english.lower() != question.lower() else [question]


def build_sources(
    chunks: list[ScoredChunk],
    claims: list[Claim],
    evidence: list[Evidence],
    locate: Callable[[ScoredChunk, list[str]], str | None],
) -> list[Source]:
    """One source per cited document page, in order of first citation.

    `highlight` is the evidence passage as it appears in the page text, or None
    when it cannot be found there.
    """
    quotes: dict[int, list[str]] = {}
    for item in evidence:
        quotes.setdefault(item.excerpt, []).append(item.quote)
    sources: list[Source] = []
    seen: set[tuple[int, int]] = set()
    for number in (n for claim in claims for n in claim.excerpts):
        chunk = chunks[number - 1]
        key = (chunk.payload.doc_id, chunk.payload.page)
        if key in seen:
            continue
        seen.add(key)
        sources.append(
            Source(
                filename=chunk.payload.filename,
                doc_id=chunk.payload.doc_id,
                page=chunk.payload.page,
                snippet=_snippet(chunk.payload.text),
                highlight=locate(chunk, quotes.get(number, [])),
            )
        )
    return sources


def merge_repeated_openings(claims: list[Claim], min_words: int = 3) -> list[Claim]:
    """Join consecutive claims that only repeat the same opening words.

    "Accuracy is 0.1 °C from -20 to 50 °C." and "Accuracy is 0.15 °C from -40 to 70 °C."
    become "Accuracy is 0.1 °C from -20 to 50 °C; 0.15 °C from -40 to 70 °C." with the
    citations of both.
    """
    merged: list[Claim] = []
    for claim in claims:
        if merged:
            previous = merged[-1]
            first, second = previous.statement.split(), claim.statement.split()
            shared = 0
            while (
                shared < min(len(first), len(second))
                and first[shared].lower() == second[shared].lower()
            ):
                shared += 1
            if shared >= min_words and shared < len(second):
                rest = " ".join(second[shared:])
                text = f"{previous.statement.rstrip('. ')}; {rest}"
                excerpts = list(dict.fromkeys([*previous.excerpts, *claim.excerpts]))
                merged[-1] = Claim(statement=text, excerpts=excerpts)
                continue
        merged.append(claim)
    return merged


def answer_lines(
    claims: list[Claim], chunks: list[ScoredChunk], sources: list[Source]
) -> list[AnswerLine]:
    """Each claim with the indexes of the sources (document pages) it cites."""
    index = {(s.doc_id, s.page): i for i, s in enumerate(sources)}
    lines = []
    for claim in claims:
        cited = [
            index[(chunks[n - 1].payload.doc_id, chunks[n - 1].payload.page)]
            for n in claim.excerpts
        ]
        lines.append(AnswerLine(text=claim.statement, sources=list(dict.fromkeys(cited))))
    return lines


def _page_locator(services: Services) -> Callable[[ScoredChunk, list[str]], str | None]:
    """Find an evidence quote in the cited page's text layer; return the exact span."""
    settings = services.settings
    pages: dict[tuple[int, int], str] = {}

    def locate(chunk: ScoredChunk, candidates: list[str]) -> str | None:
        key = (chunk.payload.doc_id, chunk.payload.page)
        if key not in pages:
            path: Path = file_path(services, chunk.payload.doc_id)
            pages[key] = extract_page_text(path, chunk.payload.page) if path.exists() else ""
        for quote in candidates:
            match = locate_quote(
                quote, pages[key], settings.quote_match_threshold, settings.quote_min_chars
            )
            if match is not None:
                return pages[key][match.start : match.end]
        return None

    return locate


def _not_found(debug: AskDebug | None = None) -> AskResponse:
    return AskResponse(answer=NOT_FOUND_ANSWER, answered=False, sources=[], debug=debug)


def _debug(
    phrasings: list[str],
    chunks: list[ScoredChunk],
    model_answered: bool | None,
    discarded: list[DiscardedClaim],
) -> AskDebug:
    retrieved = [
        RetrievedChunk(
            rank=rank,
            filename=chunk.payload.filename,
            doc_id=chunk.payload.doc_id,
            page=chunk.payload.page,
            section=chunk.payload.section,
            score=round(chunk.score, 4),
            snippet=_snippet(chunk.payload.text),
        )
        for rank, chunk in enumerate(chunks, start=1)
    ]
    return AskDebug(
        queries=phrasings, retrieved=retrieved, model_answered=model_answered, discarded=discarded
    )


def answer_question(
    services: Services, request: AskRequest, mode: SearchMode = "hybrid"
) -> AskResponse:
    """Retrieve the top chunks and let the LLM answer only from them.

    An answer is shown only if at least one claim survives with a valid citation;
    an uncited claim cannot be checked, so it is not shown.
    """
    if request.drawer_id is not None:
        with session_scope(services.sessions) as session:
            if session.get(Drawer, request.drawer_id) is None:
                raise NotFoundError(f"Drawer {request.drawer_id} does not exist.")

    phrasings = retrieval_phrasings(services, request.question)
    chunks = retrieve(services, phrasings, request.drawer_id, request.doc_ids, mode)
    if not chunks:
        return _not_found(_debug(phrasings, [], None, []) if request.debug else None)

    reply = services.llm.generate_structured(
        system_prompt(request.question), build_prompt(request.question, chunks), CitedAnswer
    )
    claims, discarded = (
        check_claims(reply, chunks, request.question) if reply.answered else ([], [])
    )
    debug = _debug(phrasings, chunks, reply.answered, discarded) if request.debug else None
    if not claims:
        if reply.answered:
            logger.info("Discarded an answer without supported claims: %r", reply.claims)
        return _not_found(debug)
    claims = merge_repeated_openings(claims)
    sources = build_sources(chunks, claims, reply.evidence, _page_locator(services))
    lines = answer_lines(claims, chunks, sources)
    answer = "\n".join(line.text for line in lines)
    return AskResponse(answer=answer, answered=True, sources=sources, lines=lines, debug=debug)
