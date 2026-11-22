"""Measure requirement extraction with the real LLM.

Two modes:

- fixtures (default): every synthetic fixture in `eval/extraction_fixtures.py` is
  parsed, planned and extracted as in the application. Rows are matched to the labelled
  ground truth by their quotes, and precision and recall are reported per kind
  (obligation, limit) and overall.
- `--pdf PATH`: an unlabelled document (for example a datasheet in `samples/`). Reports
  rows by kind, rejections by reason, skipped sections, model calls, time and tokens.

Usage:
    python -m eval.run_extraction_eval
    python -m eval.run_extraction_eval --pdf samples/tmp117.pdf
    python -m eval.run_extraction_eval --paraphrase on --concurrency 2
"""

import argparse
import json
import logging
import pickle
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz import fuzz

from app.config import Settings
from app.ingestion import Block, DoclingParser, chunk_blocks
from app.pdf import extract_page_text
from app.requirements import (
    Plan,
    deduplicate,
    extract_candidates,
    merge_sentence_parts,
    plan_extraction,
    section_rules,
)
from app.services import Services, build_services
from app.vector_store import ChunkPayload
from app.verification import display_quote, normalize
from eval.extraction_fixtures import FIXTURES, Fixture, render

EVAL_DIR = Path(__file__).parent
WORK_DIR = EVAL_DIR / ".work" / "extraction"
DEFAULT_OUTPUT = EVAL_DIR / "extraction_results.md"
MATCH_THRESHOLD = 90
KINDS = ("obligation", "limit")

logger = logging.getLogger("reqtrace.eval")


@dataclass(frozen=True)
class Row:
    quote: str
    type: str
    kind: str


@dataclass
class Run:
    rows: list[Row]
    plan: Plan
    seconds: float
    rejected: dict[str, int]
    paraphrases_removed: int


@dataclass
class FixtureScore:
    name: str
    run: Run
    expected: dict[str, int] = field(default_factory=dict)
    predicted: dict[str, int] = field(default_factory=dict)
    correct: dict[str, int] = field(default_factory=dict)
    found_any: int = 0
    matched_rows: int = 0
    false_positives: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    missed: list[str] = field(default_factory=list)
    wrong_kind: list[str] = field(default_factory=list)
    split_rows: int = 0


def ratio(numerator: int, denominator: int) -> str:
    return f"{numerator / denominator:.2f}" if denominator else "n/a"


def build_eval_services(args: argparse.Namespace) -> Services:
    overrides: dict[str, object] = {"data_dir": WORK_DIR / "data"}
    if args.paraphrase is not None:
        overrides["extraction_paraphrase"] = args.paraphrase == "on"
    if args.concurrency is not None:
        overrides["extraction_concurrency"] = args.concurrency
    if args.ollama_url:
        overrides["ollama_base_url"] = args.ollama_url
    return build_services(Settings(**overrides))


def parse(pdf: Path, cache: bool) -> list[Block]:
    """Docling blocks of a PDF; cached on disk for large documents."""
    cache_file = WORK_DIR / f"{pdf.stem}.blocks.pkl"
    if cache and cache_file.exists():
        return pickle.loads(cache_file.read_bytes())
    blocks = DoclingParser()(pdf)
    if cache:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_bytes(pickle.dumps(blocks))
    return blocks


def run_document(services: Services, pdf: Path, cache: bool = False) -> Run:
    """Plan and extract one PDF exactly as the application does."""
    settings = services.settings
    blocks = parse(pdf, cache)
    chunks = [
        ChunkPayload(0, 0, pdf.name, c.page, c.section, c.kind, c.text, i)
        for i, c in enumerate(
            chunk_blocks(blocks, settings.chunk_max_chars, settings.chunk_overlap_chars)
        )
    ]
    plan = plan_extraction(chunks, section_rules(settings), settings.extraction_group_chars)
    pages: dict[int, str] = {}

    def page_text(page: int) -> str:
        if page not in pages:
            pages[page] = extract_page_text(pdf, page)
        return pages[page]

    started = time.monotonic()
    outcome = extract_candidates(services, plan, page_text)
    unique = deduplicate(
        merge_sentence_parts(outcome.accepted, page_text), settings.dedup_threshold
    )
    return Run(
        rows=[Row(c.quote, c.type, c.kind) for c in unique],
        plan=plan,
        seconds=time.monotonic() - started,
        rejected={k.value: v for k, v in outcome.rejected.items()},
        paraphrases_removed=outcome.paraphrases_removed,
    )


def _best_match(quote: str, sentences: list[str]) -> str | None:
    norm = normalize(quote)
    scored = [(fuzz.partial_ratio(normalize(s), norm), s) for s in sentences]
    best = max(scored, default=(0.0, None))
    return best[1] if best[0] >= MATCH_THRESHOLD else None


def score_fixture(fixture: Fixture, run: Run) -> FixtureScore:
    """Match rows to the labelled items; precision and recall per kind."""
    score = FixtureScore(fixture.name, run)
    expected_kind = {e.sentence: e.kind for e in fixture.genuine}
    score.expected = dict(Counter(e.kind for e in fixture.genuine))
    score.predicted = dict(Counter(r.kind for r in run.rows))
    hits: Counter[str] = Counter()
    for row in run.rows:
        genuine = _best_match(row.quote, list(expected_kind))
        if genuine is None:
            wrong = _best_match(row.quote, fixture.not_requirements)
            (score.false_positives if wrong else score.unmatched).append(display_quote(row.quote))
            continue
        hits[genuine] += 1
        if hits[genuine] > 1:
            continue
        score.matched_rows += 1
        score.found_any += 1
        if row.kind == expected_kind[genuine]:
            score.correct[row.kind] = score.correct.get(row.kind, 0) + 1
        else:
            score.wrong_kind.append(f"{genuine} (as {row.kind})")
    score.split_rows = sum(count - 1 for count in hits.values())
    score.missed = [s for s in expected_kind if s not in hits]
    return score


def fixtures_report(scores: list[FixtureScore], label: str) -> str:
    lines = [
        f"### {label}",
        "",
        "| Fixture | Rows | Precision obligation | Recall obligation | Precision limit "
        "| Recall limit "
        "| Precision all | Recall all | Calls | s/call |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in scores:
        expected_total = sum(s.expected.values())
        calls = len(s.run.plan.groups)
        lines.append(
            f"| {s.name} | {len(s.run.rows)} "
            + "".join(
                f"| {ratio(s.correct.get(k, 0), s.predicted.get(k, 0))} "
                f"| {ratio(s.correct.get(k, 0), s.expected.get(k, 0))} "
                for k in KINDS
            )
            + f"| {ratio(s.matched_rows, len(s.run.rows))} | {ratio(s.found_any, expected_total)} "
            f"| {calls} | {s.run.seconds / calls if calls else 0:.1f} |"
        )
    lines.append("")
    for s in scores:
        for title, items in (
            ("missed", s.missed),
            ("wrong kind", s.wrong_kind),
            ("false positive", s.false_positives),
            ("unmatched", s.unmatched),
        ):
            lines.extend(f"- {s.name} {title}: {item!r}" for item in items)
        if s.split_rows:
            lines.append(f"- {s.name}: {s.split_rows} extra rows for already found items")
    return "\n".join(lines) + "\n"


def document_report(pdf: Path, run: Run, services: Services) -> str:
    calls = getattr(services.llm, "calls", [])
    kinds = Counter(r.kind for r in run.rows)
    groups = len(run.plan.groups)
    chunks = sum(len(g.chunks) for g in run.plan.groups)
    settings = services.settings
    lines = [
        f"### {pdf.name}",
        "",
        f"- Paraphrase: {'on' if settings.extraction_paraphrase else 'off'}; "
        f"concurrency: {settings.extraction_concurrency}",
        f"- Rows saved: {len(run.rows)} (obligation {kinds.get('obligation', 0)}, "
        f"limit {kinds.get('limit', 0)})",
        f"- Rejected: quote not on page {run.rejected['quote_not_found']}, "
        f"not an obligation or limit {run.rejected['not_requirement']}; paraphrases removed "
        f"{run.paraphrases_removed}",
        f"- Skipped before any model call: {len(run.plan.skipped)} sections and tables; "
        f"screened: {len(run.plan.screened)} sections",
        f"- Chunks sent: {chunks} in {groups} model calls; ratings tables read directly: "
        f"{len(run.plan.limit_tables)}",
        f"- Extraction time: {run.seconds:.0f} s "
        f"({run.seconds / groups if groups else 0:.1f} s per call)",
    ]
    if calls:
        lines.append(
            f"- Tokens: {sum(c.prompt_tokens for c in calls)} prompt, "
            f"{sum(c.output_tokens for c in calls)} output; thinking characters "
            f"{sum(c.thinking_chars for c in calls)}; "
            f"model time {sum(c.seconds for c in calls):.0f} s"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--pdf", type=Path, help="Measure an unlabelled document instead of the fixtures"
    )
    parser.add_argument("--paraphrase", choices=["on", "off"])
    parser.add_argument("--concurrency", type=int)
    parser.add_argument("--ollama-url")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")

    services = build_eval_services(args)
    if args.pdf:
        run = run_document(services, args.pdf, cache=True)
        text = document_report(args.pdf, run, services)
        rows_file = WORK_DIR / f"{args.pdf.stem}.rows.json"
        rows_file.write_text(
            json.dumps(
                [
                    {"kind": r.kind, "type": r.type, "quote": display_quote(r.quote)}
                    for r in run.rows
                ],
                indent=1,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        text += f"\nRows written to {rows_file}\n"
    else:
        scores = []
        for fixture in FIXTURES:
            pdf = render(fixture, WORK_DIR / f"{fixture.name}.pdf")
            scores.append(score_fixture(fixture, run_document(services, pdf)))
        label = f"paraphrase {'on' if services.settings.extraction_paraphrase else 'off'}"
        text = fixtures_report(scores, label)
        (args.output or DEFAULT_OUTPUT).write_text(text, encoding="utf-8")
    if args.output and args.pdf:
        args.output.write_text(text, encoding="utf-8")
    sys.stdout.write(text)


if __name__ == "__main__":
    main()
