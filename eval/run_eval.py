"""Measure retrieval and answer quality on one document and a question set.

Reports, per search mode:
- retrieval hit rate at k: an expected page is among the retrieved chunks
- answer accuracy: the answer is given and contains every expected keyword
- abstention accuracy: unanswerable questions are answered with "not found"

The document is indexed into a separate Qdrant collection and data directory, so
an evaluation run never touches the application's own drawers.

Usage:
    python -m eval.run_eval                       # all modes, synthetic datasheet
    python -m eval.run_eval --mode hybrid
    python -m eval.run_eval --pdf samples/x.pdf --questions my_questions.jsonl
"""

import argparse
import json
import logging
import re
import shutil
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from app import documents
from app.config import Settings
from app.db import DocumentStatus
from app.qa import answer_question, retrieval_phrasings
from app.retrieval import retrieve
from app.schemas import AskRequest
from app.services import Services, build_services
from app.vector_store import SearchMode
from app.verification import normalize

EVAL_DIR = Path(__file__).parent
DEFAULT_PDF = EVAL_DIR / "data" / "nx310_datasheet.pdf"
DEFAULT_QUESTIONS = EVAL_DIR / "questions.jsonl"
DEFAULT_OUTPUT = EVAL_DIR / "results.md"
WORK_DIR = EVAL_DIR / ".work"
EVAL_COLLECTION = "reqtrace_eval"
MODES: tuple[SearchMode, ...] = ("dense", "bm25", "hybrid")

logger = logging.getLogger("reqtrace.eval")


@dataclass(frozen=True)
class Question:
    id: str
    question: str
    answerable: bool
    expected_keywords: list[str]
    expected_pages: list[int]


@dataclass(frozen=True)
class QuestionResult:
    id: str
    mode: str
    answerable: bool
    retrieved_pages: list[int]
    hit: bool
    answered: bool
    correct: bool
    answer: str
    cited_pages: list[int]


@dataclass(frozen=True)
class ModeMetrics:
    mode: str
    hit_rate: float
    answer_accuracy: float
    abstention_accuracy: float
    answerable: int
    unanswerable: int


def load_questions(path: Path) -> list[Question]:
    """Read the JSONL question set."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return [Question(**json.loads(line)) for line in lines if line.strip()]


def keyword_match(answer: str, keywords: list[str]) -> bool:
    """True when every keyword occurs in the answer; "a|b" accepts either alternative.

    Matching is case-insensitive and on whole words, so "5" does not match "25",
    "0.1" does not match "0.15" and "no" does not match "not".
    """
    text = normalize(answer)

    def found(keyword: str) -> bool:
        pattern = rf"(?<![\w.]){re.escape(normalize(keyword))}(?!\w)"
        return re.search(pattern, text) is not None

    return all(any(found(alt) for alt in kw.split("|")) for kw in keywords)


def compute_metrics(mode: str, results: list[QuestionResult]) -> ModeMetrics:
    """Aggregate per-question results into the three reported rates."""
    answerable = [r for r in results if r.answerable]
    unanswerable = [r for r in results if not r.answerable]

    def rate(items: list[QuestionResult], predicate: str) -> float:
        if not items:
            return 0.0
        return sum(1 for r in items if getattr(r, predicate)) / len(items)

    abstained = sum(1 for r in unanswerable if not r.answered)
    return ModeMetrics(
        mode=mode,
        hit_rate=rate(answerable, "hit"),
        answer_accuracy=rate(answerable, "correct"),
        abstention_accuracy=abstained / len(unanswerable) if unanswerable else 0.0,
        answerable=len(answerable),
        unanswerable=len(unanswerable),
    )


def markdown_table(metrics: list[ModeMetrics], context: dict[str, str]) -> str:
    """Results as a Markdown table with the run's context underneath."""
    lines = [
        "| Mode | Retrieval hit rate @5 | Answer accuracy | Abstention accuracy |",
        "|---|---|---|---|",
    ]
    for m in metrics:
        lines.append(
            f"| {m.mode} | {m.hit_rate:.0%} | {m.answer_accuracy:.0%} | "
            f"{m.abstention_accuracy:.0%} |"
        )
    if metrics:
        lines.append("")
        lines.append(
            f"{metrics[0].answerable} answerable and {metrics[0].unanswerable} unanswerable "
            "questions."
        )
    lines.append("")
    lines.extend(f"- {key}: {value}" for key, value in context.items())
    return "\n".join(lines) + "\n"


def prepare_index(pdf: Path) -> tuple[Services, int]:
    """Index the PDF from scratch into the evaluation collection."""
    shutil.rmtree(WORK_DIR, ignore_errors=True)
    settings = Settings(data_dir=WORK_DIR, qdrant_collection=EVAL_COLLECTION)
    services = build_services(settings)
    services.store.drop_collection()
    drawer = documents.create_drawer(services, "evaluation", is_temporary=False)
    doc_id = documents.store_upload(services, drawer.id, pdf.name, pdf.read_bytes())
    documents.ingest_document(services, doc_id)
    document = documents.get_document(services, doc_id)
    if document.status != DocumentStatus.READY:
        raise SystemExit(f"Ingestion failed: {document.error}")
    return services, doc_id


def evaluate_mode(
    services: Services, doc_id: int, questions: list[Question], mode: SearchMode
) -> list[QuestionResult]:
    """Run retrieval and answering for every question in one search mode."""
    results = []
    for question in questions:
        phrasings = retrieval_phrasings(services, question.question)
        # Hit rate is measured on the first five chunks, as in earlier runs.
        chunks = retrieve(services, phrasings, doc_ids=[doc_id], mode=mode)[:5]
        retrieved_pages = [c.payload.page for c in chunks]
        reply = answer_question(
            services, AskRequest(question=question.question, doc_ids=[doc_id]), mode
        )
        hit = any(page in question.expected_pages for page in retrieved_pages)
        correct = (
            question.answerable
            and reply.answered
            and keyword_match(reply.answer, question.expected_keywords)
        )
        results.append(
            QuestionResult(
                id=question.id,
                mode=mode,
                answerable=question.answerable,
                retrieved_pages=retrieved_pages,
                hit=hit,
                answered=reply.answered,
                correct=correct,
                answer=reply.answer,
                cited_pages=[s.page for s in reply.sources],
            )
        )
        logger.info("%s %s answered=%s correct=%s", mode, question.id, reply.answered, correct)
    return results


def main(argv: list[str] | None = None) -> None:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--mode", choices=[*MODES, "all"], default="all")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    questions = load_questions(args.questions)
    modes: tuple[SearchMode, ...] = MODES if args.mode == "all" else (args.mode,)
    started = time.monotonic()
    services, doc_id = prepare_index(args.pdf)

    all_results: list[QuestionResult] = []
    metrics: list[ModeMetrics] = []
    for mode in modes:
        results = evaluate_mode(services, doc_id, questions, mode)
        all_results.extend(results)
        metrics.append(compute_metrics(mode, results))

    settings = services.settings
    context = {
        "Document": args.pdf.name,
        "LLM": settings.llm_model,
        "Dense embeddings": settings.dense_model,
        "Date": datetime.now(UTC).strftime("%Y-%m-%d"),
        "Duration": f"{time.monotonic() - started:.0f} s",
    }
    table = markdown_table(metrics, context)
    args.output.write_text(table, encoding="utf-8")
    details = args.output.with_suffix(".json")
    details.write_text(
        json.dumps([asdict(r) for r in all_results], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    sys.stdout.write(table)
    sys.stdout.write(f"\nWrote {args.output} and {details}\n")


if __name__ == "__main__":
    main()
