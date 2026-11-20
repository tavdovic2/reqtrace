"""Download the real datasheets used by the benchmark into samples/ and verify them.

The PDFs belong to their publishers and are never committed (samples/*.pdf is
git-ignored). Usage:

    python -m eval.fetch_samples            # download (if missing) and verify
    python -m eval.fetch_samples --verify   # only verify a file you saved by hand

If the download is refused, the script prints how to save the file by hand.
"""

import argparse
import hashlib
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from eval.datasets import DATASETS

TIMEOUT_SECONDS = 60
# ti.com refuses requests without a browser-like user agent.
USER_AGENT = "Mozilla/5.0 (compatible; reqTrace-benchmark/1.0)"


@dataclass(frozen=True)
class Sample:
    name: str
    url: str
    path: Path
    sha256: str
    revision_marker: str
    pages: int


TMP117 = Sample(
    name="TI TMP117 datasheet, Rev. D (SNOSD82D, September 2022)",
    url="https://www.ti.com/lit/ds/symlink/tmp117.pdf",
    path=DATASETS["tmp117"].pdf,
    sha256=DATASETS["tmp117"].sha256 or "",
    revision_marker="SNOSD82D",
    pages=50,
)
SAMPLES = [TMP117]


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(sample: Sample, path: Path) -> tuple[bool, str]:
    """(usable, message). Usable when the hash matches, or when only the hash differs.

    TI regenerates the packaging addendum of a datasheet (dates on the last pages)
    without changing its revision, so a file with the right revision and page count is
    still usable; results may then differ slightly from the published ones.
    """
    if not path.exists():
        return False, f"{path} does not exist."
    digest = file_sha256(path)
    if digest == sample.sha256:
        return True, f"{path} verified (SHA-256 {digest})."
    from app.pdf import extract_page_text, read_page_count

    try:
        pages = read_page_count(path)
        first_page = extract_page_text(path, 1)
    except Exception as exc:  # not a readable PDF (an HTML error page, for example)
        return False, f"{path} is not a readable PDF ({exc})."
    if sample.revision_marker in first_page and pages == sample.pages:
        return True, (
            f"{path} has SHA-256 {digest}, not the recorded {sample.sha256}, but it is the "
            f"same revision ({sample.revision_marker}, {pages} pages). TI updates the "
            "packaging pages without a new revision; results may differ slightly."
        )
    marker = "found" if sample.revision_marker in first_page else "missing"
    return False, (
        f"{path} is not the expected document: SHA-256 {digest}, {pages} pages, revision "
        f"marker {sample.revision_marker!r} {marker}."
    )


def manual_instructions(sample: Sample) -> str:
    return (
        f"Download it by hand:\n"
        f"  1. Open {sample.url} in a browser.\n"
        f"  2. Save the PDF as {sample.path}.\n"
        f"  3. Run: python -m eval.fetch_samples --verify\n"
        f"Expected: {sample.name}, {sample.pages} pages, SHA-256 {sample.sha256}."
    )


def download(sample: Sample) -> None:
    """Download to a temporary file next to the target, then move it into place."""
    sample.path.parent.mkdir(parents=True, exist_ok=True)
    partial = sample.path.with_suffix(".part")
    request = urllib.request.Request(sample.url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        partial.write_bytes(response.read())
    partial.replace(sample.path)


def fetch(sample: Sample, verify_only: bool) -> bool:
    if not verify_only and not sample.path.exists():
        sys.stdout.write(f"Downloading {sample.name} from {sample.url}\n")
        try:
            download(sample)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            sys.stdout.write(f"The download failed ({exc}).\n{manual_instructions(sample)}\n")
            return False
    usable, message = verify(sample, sample.path)
    sys.stdout.write(message + "\n")
    if not usable:
        sys.stdout.write(manual_instructions(sample) + "\n")
    return usable


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--verify", action="store_true", help="Only verify existing files")
    args = parser.parse_args(argv)
    ok = all(fetch(sample, args.verify) for sample in SAMPLES)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
