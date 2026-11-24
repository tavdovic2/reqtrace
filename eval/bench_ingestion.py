"""Measure ingestion time per phase on this machine, with the models already loaded.

Each run stores the PDF as a new document and runs the same indexing code as an upload
(parse, chunking and filtering, dense and sparse embedding, upsert), then deletes it.
It uses its own data directory and Qdrant collection, so the application's data is not
touched. Qdrant must be running (see Quick start).

Usage:
    python -m eval.bench_ingestion --pdf samples/tmp117.pdf --runs 3
"""

import argparse
import json
import statistics
import sys
import time
from dataclasses import asdict
from pathlib import Path

from app import documents
from app.config import Settings
from app.services import build_services

WORK_DIR = Path(__file__).parent / ".work" / "ingestion"
PHASES = ("parse", "chunking", "dense", "sparse", "upsert", "total")


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--output", type=Path, default=WORK_DIR / "bench.json")
    args = parser.parse_args(argv)
    if not args.pdf.exists():
        print(f"{args.pdf} not found.", file=sys.stderr)
        return 1

    settings = Settings(data_dir=WORK_DIR / "data", qdrant_collection="reqtrace_bench")
    services = build_services(settings)
    started = time.monotonic()
    documents.warm_up(services)
    print(f"Models loaded in {time.monotonic() - started:.1f} s", flush=True)
    drawer = documents.create_drawer(services, f"bench-{int(time.time())}", True)
    runs = []
    try:
        for run in range(1, args.runs + 1):
            data = args.pdf.read_bytes()
            doc_id = documents.store_upload(services, drawer.id, args.pdf.name, data)
            try:
                timings = documents.index_document(services, doc_id, drawer.id, args.pdf.name)
            finally:
                documents.delete_document(services, doc_id)
            runs.append(asdict(timings))
            print(f"run {run}: {timings.line()}", flush=True)
    finally:
        documents.delete_drawer(services, drawer.id)

    median = {phase: round(statistics.median(r[phase] for r in runs), 1) for phase in PHASES}
    median["chunks"] = runs[0]["chunks"]
    print("median: " + ", ".join(f"{k} {v}" for k, v in median.items()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps({"pdf": args.pdf.name, "runs": runs, "median": median}, indent=1),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
