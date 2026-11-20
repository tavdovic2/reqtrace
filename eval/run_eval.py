"""Measure retrieval, answer and extraction quality on a benchmark dataset.

Datasets (see eval/datasets.py):

- ``synthetic`` (default): the generated 8-page datasheet shipped with the repository;
- ``tmp117``: the public TI TMP117 datasheet. Fetch it first with
  ``python -m eval.fetch_samples``; without the PDF the benchmark is skipped.

Reported per search mode:

- retrieval hit rate: an expected page is among the first 5 retrieved chunks, and among
  all chunks the model sees (``RETRIEVAL_TOP_K``);
- answer accuracy: the answer is given and states every expected value;
- refusal accuracy: unanswerable questions are answered with "not found";
- both accuracies split by question language, and the average answer time.

With requirement labels (tmp117), extraction precision and recall per kind are measured
too, with the missed and extra items. The document is indexed into a separate Qdrant
collection and data directory, so a run never touches the application's own drawers.

Usage:
    python -m eval.run_eval                          # synthetic, all modes
    python -m eval.run_eval --dataset tmp117         # real datasheet
    python -m eval.run_eval --dataset tmp117 --mode hybrid
    python -m eval.run_eval --pdf samples/x.pdf --questions my_questions.jsonl
"""

import argparse
import hashlib
import json
import logging
import shutil
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from app import documents, requirements
from app.config import Settings
from app.db import DocumentStatus
from app.qa import answer_question
from app.schemas import AskRequest
from app.services import Services, build_services
from app.vector_store import SearchMode
from eval.datasets import (
    DATASETS,
    RESULTS_DIR,
    Dataset,
    Question,
    load_questions,
    load_requirements,
)
from eval.matching import RequirementLabel, keyword_match, row_matches

EVAL_DIR = Path(__file__).parent
DEFAULT_QUESTIONS = DATASETS["synthetic"].questions
WORK_DIR = EVAL_DIR / ".work"
EVAL_COLLECTION = "reqtrace_eval"
MODES: tuple[SearchMode, ...] = ("dense", "bm25", "hybrid")
HIT_AT = 5
QUOTE_PREVIEW_CHARS = 70

logger = logging.getLogger("reqtrace.eval")

__all__ = ["keyword_match", "load_questions"]


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
    lang: str = "en"
    hit_at_k: bool = False
    seconds: float = 0.0


@dataclass(frozen=True)
class LanguageMetrics:
    answerable: int
    answer_accuracy: float
    unanswerable: int
    refusal_accuracy: float


@dataclass(frozen=True)
class ModeMetrics:
    mode: str
    hit_rate: float
    answer_accuracy: float
    abstention_accuracy: float
    answerable: int
    unanswerable: int
    hit_rate_at_k: float = 0.0
    avg_answer_seconds: float = 0.0
    by_language: dict[str, LanguageMetrics] = field(default_factory=dict)


@dataclass(frozen=True)
class KindScore:
    rows: int
    matched_rows: int
    precision: float
    required: int
    found: int
    recall: float


@dataclass(frozen=True)
class ExtractionScore:
    rows: int
    precision: float
    required: int
    found: int
    recall: float
    optional_found: int
    optional_total: int
    by_kind: dict[str, KindScore]
    missed: list[dict]
    extra: list[dict]
    wrong_kind: list[dict]
    seconds: float


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def compute_metrics(mode: str, results: list[QuestionResult]) -> ModeMetrics:
    """Aggregate per-question results into the reported rates."""
    answerable = [r for r in results if r.answerable]
    unanswerable = [r for r in results if not r.answerable]

    def language(lang: str) -> LanguageMetrics:
        ans = [r for r in answerable if r.lang == lang]
        una = [r for r in unanswerable if r.lang == lang]
        return LanguageMetrics(
            answerable=len(ans),
            answer_accuracy=_rate(sum(r.correct for r in ans), len(ans)),
            unanswerable=len(una),
            refusal_accuracy=_rate(sum(not r.answered for r in una), len(una)),
        )

    return ModeMetrics(
        mode=mode,
        hit_rate=_rate(sum(r.hit for r in answerable), len(answerable)),
        answer_accuracy=_rate(sum(r.correct for r in answerable), len(answerable)),
        abstention_accuracy=_rate(sum(not r.answered for r in unanswerable), len(unanswerable)),
        answerable=len(answerable),
        unanswerable=len(unanswerable),
        hit_rate_at_k=_rate(sum(r.hit_at_k for r in answerable), len(answerable)),
        avg_answer_seconds=_rate(sum(r.seconds for r in results), len(results)),
        by_language={lang: language(lang) for lang in sorted({r.lang for r in results})},
    )


def score_extraction(
    rows: list[tuple[str, int, str]], labels: list[RequirementLabel], seconds: float = 0.0
) -> ExtractionScore:
    """Match extracted rows (kind, page, quote) to labels; precision and recall per kind.

    A row is correct when it states a labelled item of the same kind; a row that states
    a labelled item of the other kind is listed under ``wrong_kind``. Recall counts only
    required labels; optional ones are reported separately.
    """
    matches = [
        [label for label in labels if row_matches(label, page, quote)] for _, page, quote in rows
    ]
    correct = [
        any(label.kind == kind for label in found)
        for (kind, _, _), found in zip(rows, matches, strict=True)
    ]
    found_ids = {
        label.id
        for (kind, _, _), found in zip(rows, matches, strict=True)
        for label in found
        if label.kind == kind
    }
    required = [label for label in labels if not label.optional]
    optional = [label for label in labels if label.optional]

    def preview(quote: str) -> str:
        flat = " ".join(quote.split())
        return flat if len(flat) <= QUOTE_PREVIEW_CHARS else flat[:QUOTE_PREVIEW_CHARS] + "…"

    by_kind = {}
    for kind in ("obligation", "limit"):
        kind_rows = [i for i, (k, _, _) in enumerate(rows) if k == kind]
        kind_required = [label for label in required if label.kind == kind]
        kind_found = [label for label in kind_required if label.id in found_ids]
        by_kind[kind] = KindScore(
            rows=len(kind_rows),
            matched_rows=sum(correct[i] for i in kind_rows),
            precision=_rate(sum(correct[i] for i in kind_rows), len(kind_rows)),
            required=len(kind_required),
            found=len(kind_found),
            recall=_rate(len(kind_found), len(kind_required)),
        )
    return ExtractionScore(
        rows=len(rows),
        precision=_rate(sum(correct), len(rows)),
        required=len(required),
        found=sum(1 for label in required if label.id in found_ids),
        recall=_rate(sum(1 for label in required if label.id in found_ids), len(required)),
        optional_found=sum(1 for label in optional if label.id in found_ids),
        optional_total=len(optional),
        by_kind=by_kind,
        missed=[
            {"id": label.id, "page": label.page, "kind": label.kind, "note": label.note}
            for label in required
            if label.id not in found_ids
        ],
        extra=[
            {"page": page, "kind": kind, "quote_start": preview(quote)}
            for (kind, page, quote), found in zip(rows, matches, strict=True)
            if not found
        ],
        wrong_kind=[
            {"page": page, "kind": kind, "labels": [label.id for label in found]}
            for (kind, page, quote), found, ok in zip(rows, matches, correct, strict=True)
            if found and not ok
        ],
        seconds=seconds,
    )


def _pct(value: float) -> str:
    return f"{value:.0%}"


def markdown_report(
    metrics: list[ModeMetrics],
    context: dict[str, str],
    extraction: ExtractionScore | None = None,
    top_k: int = 8,
) -> str:
    """Results as Markdown tables with the run's context underneath."""
    lines = [
        f"| Mode | Hit rate @{HIT_AT} | Hit rate @{top_k} | Answer accuracy | Refusal accuracy "
        "| Avg answer time |",
        "|---|---|---|---|---|---|",
    ]
    for m in metrics:
        lines.append(
            f"| {m.mode} | {_pct(m.hit_rate)} | {_pct(m.hit_rate_at_k)} | "
            f"{_pct(m.answer_accuracy)} | {_pct(m.abstention_accuracy)} | "
            f"{m.avg_answer_seconds:.1f} s |"
        )
    if metrics:
        lines += [
            "",
            f"{metrics[0].answerable} answerable and {metrics[0].unanswerable} unanswerable "
            "questions.",
            "",
            "| Mode | Language | Answer accuracy | Refusal accuracy |",
            "|---|---|---|---|",
        ]
        for m in metrics:
            for lang, lm in m.by_language.items():
                lines.append(
                    f"| {m.mode} | {lang} | {_pct(lm.answer_accuracy)} ({lm.answerable}) | "
                    f"{_pct(lm.refusal_accuracy)} ({lm.unanswerable}) |"
                )
    if extraction is not None:
        lines += [
            "",
            "| Extraction | Rows | Precision | Required labels | Found | Recall |",
            "|---|---|---|---|---|---|",
        ]
        for kind, ks in extraction.by_kind.items():
            lines.append(
                f"| {kind} | {ks.rows} | {_pct(ks.precision)} | {ks.required} | {ks.found} | "
                f"{_pct(ks.recall)} |"
            )
        lines.append(
            f"| all | {extraction.rows} | {_pct(extraction.precision)} | {extraction.required} "
            f"| {extraction.found} | {_pct(extraction.recall)} |"
        )
        lines += [
            "",
            f"Optional labels found: {extraction.optional_found} of {extraction.optional_total}. "
            f"Extraction time: {extraction.seconds:.0f} s.",
            "",
            "Missed required labels: "
            + (", ".join(f"{m['id']} (p{m['page']})" for m in extraction.missed) or "none"),
            "",
            "Extra rows (no label): "
            + (
                "; ".join(f"p{e['page']} {e['kind']}: {e['quote_start']}" for e in extraction.extra)
                or "none"
            ),
        ]
        if extraction.wrong_kind:
            lines += [
                "",
                "Rows of the wrong kind: "
                + "; ".join(
                    f"p{w['page']} {w['kind']} ({', '.join(w['labels'])})"
                    for w in extraction.wrong_kind
                ),
            ]
    lines.append("")
    lines.extend(f"- {key}: {value}" for key, value in context.items())
    return "\n".join(lines) + "\n"


def prepare_index(pdf: Path) -> tuple[Services, int, float]:
    """Index the PDF from scratch into the evaluation collection; returns the time taken."""
    shutil.rmtree(WORK_DIR, ignore_errors=True)
    settings = Settings(data_dir=WORK_DIR, qdrant_collection=EVAL_COLLECTION)
    services = build_services(settings)
    services.store.drop_collection()
    drawer = documents.create_drawer(services, "evaluation", is_temporary=False)
    doc_id = documents.store_upload(services, drawer.id, pdf.name, pdf.read_bytes())
    documents.warm_up(services)
    started = time.monotonic()
    documents.ingest_document(services, doc_id)
    seconds = time.monotonic() - started
    document = documents.get_document(services, doc_id)
    if document.status != DocumentStatus.READY:
        raise SystemExit(f"Ingestion failed: {document.error}")
    return services, doc_id, seconds


def evaluate_mode(
    services: Services, doc_id: int, questions: list[Question], mode: SearchMode
) -> list[QuestionResult]:
    """Answer every question in one search mode; retrieval comes from the debug details."""
    results = []
    for question in questions:
        started = time.monotonic()
        reply = answer_question(
            services, AskRequest(question=question.question, doc_ids=[doc_id], debug=True), mode
        )
        seconds = time.monotonic() - started
        retrieved = [c.page for c in reply.debug.retrieved] if reply.debug else []
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
                retrieved_pages=retrieved,
                hit=any(page in question.expected_pages for page in retrieved[:HIT_AT]),
                answered=reply.answered,
                correct=correct,
                answer=reply.answer,
                cited_pages=[s.page for s in reply.sources],
                lang=question.lang,
                hit_at_k=any(page in question.expected_pages for page in retrieved),
                seconds=round(seconds, 2),
            )
        )
        logger.info("%s %s answered=%s correct=%s", mode, question.id, reply.answered, correct)
    return results


def evaluate_extraction(
    services: Services, doc_id: int, labels: list[RequirementLabel]
) -> ExtractionScore:
    """Run requirement extraction on the indexed document and score it against labels."""
    requirements.start_extraction(services, doc_id)
    started = time.monotonic()
    requirements.run_extraction(services, doc_id)
    seconds = time.monotonic() - started
    table = requirements.get_requirements(services, doc_id)
    if table.status != "done":
        raise SystemExit(f"Extraction failed: {table.error}")
    rows = [(r.kind, r.page, r.quote) for r in table.requirements]
    return score_extraction(rows, labels, seconds)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve_dataset(args: argparse.Namespace) -> Dataset:
    """The dataset to run, with --pdf/--questions overriding its files."""
    dataset = DATASETS[args.dataset]
    if args.pdf or args.questions:
        return Dataset(
            name="custom" if args.pdf else dataset.name,
            pdf=args.pdf or dataset.pdf,
            questions=args.questions or dataset.questions,
            requirements=dataset.requirements if not args.pdf else None,
        )
    return dataset


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point; returns the process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", choices=sorted(DATASETS), default="synthetic")
    parser.add_argument("--pdf", type=Path, help="Use another PDF (custom run)")
    parser.add_argument("--questions", type=Path, help="Use another question file")
    parser.add_argument("--mode", choices=[*MODES, "all"], default="all")
    parser.add_argument("--no-extraction", action="store_true", help="Skip extraction scoring")
    parser.add_argument("--output", type=Path, help="Markdown output (JSON is written next to it)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    dataset = resolve_dataset(args)
    if not dataset.pdf.exists():
        sys.stdout.write(
            f"Skipping the {dataset.name} benchmark: {dataset.pdf} is not present.\n"
            + (f"Fetch it with: {dataset.fetch_hint}\n" if dataset.fetch_hint else "")
        )
        return 0
    digest = sha256(dataset.pdf)
    if dataset.sha256 and digest != dataset.sha256:
        logger.warning(
            "%s has SHA-256 %s, not the expected %s; results may not be comparable",
            dataset.pdf,
            digest,
            dataset.sha256,
        )

    questions = load_questions(dataset.questions)
    labels = (
        load_requirements(dataset.requirements)
        if dataset.requirements and not args.no_extraction
        else []
    )
    modes: tuple[SearchMode, ...] = MODES if args.mode == "all" else (args.mode,)
    started = time.monotonic()
    services, doc_id, ingest_seconds = prepare_index(dataset.pdf)

    all_results: list[QuestionResult] = []
    metrics: list[ModeMetrics] = []
    for mode in modes:
        results = evaluate_mode(services, doc_id, questions, mode)
        all_results.extend(results)
        metrics.append(compute_metrics(mode, results))
    extraction = evaluate_extraction(services, doc_id, labels) if labels else None

    settings = services.settings
    context = {
        "Dataset": f"{dataset.name} ({dataset.pdf.name}, SHA-256 {digest[:12]}…)",
        "LLM": settings.llm_model,
        "Dense embeddings": settings.dense_model,
        "Chunks given to the model": str(settings.retrieval_top_k),
        "Date": datetime.now(UTC).strftime("%Y-%m-%d"),
        "Ingestion": f"{ingest_seconds:.0f} s",
        "Duration": f"{time.monotonic() - started:.0f} s",
    }
    report = markdown_report(metrics, context, extraction, settings.retrieval_top_k)
    output = args.output or RESULTS_DIR / f"{dataset.name}.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8")
    details = output.with_suffix(".json")
    details.write_text(
        json.dumps(
            {
                "context": context,
                "metrics": [asdict(m) for m in metrics],
                "extraction": asdict(extraction) if extraction else None,
                "questions": [asdict(r) for r in all_results],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    sys.stdout.write(report)
    sys.stdout.write(f"\nWrote {output} and {details}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
