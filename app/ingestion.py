"""PDF parsing with Docling and structure-aware chunking.

Parsing turns a PDF into a flat list of blocks (headings, text, tables) with page
numbers. Chunking is a pure function over those blocks so it can be tested
without Docling or any model download.
"""

import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

logger = logging.getLogger(__name__)

BlockKind = Literal["heading", "text", "table"]
ChunkKind = Literal["text", "table"]

SECTION_SEPARATOR = " > "
TABLE_RULE_CELL = re.compile(r":?-+:?")
NUMBERED_HEADING = re.compile(r"^((?:\d+\.)*\d+\.?)\s+\S")


class IngestionError(RuntimeError):
    """A document could not be turned into chunks; the message is shown to the user."""


@dataclass(frozen=True)
class Block:
    """One structural element of a parsed document."""

    kind: BlockKind
    text: str
    page: int
    level: int = 1


@dataclass(frozen=True)
class Chunk:
    """A retrievable unit. A chunk never spans pages, so its page citation is exact."""

    text: str
    page: int
    section: str
    kind: ChunkKind

    @property
    def embedding_text(self) -> str:
        """Text sent to the embedding models; text chunks get their heading path as context."""
        if self.kind == "text" and self.section:
            return f"{self.section}\n{self.text}"
        return self.text


# Called with (pages parsed so far, total pages) while a PDF is parsed.
PageProgress = Callable[[int, int], None]


class PdfParser(Protocol):
    def __call__(self, path: Path, on_page: PageProgress | None = None) -> list[Block]: ...


def split_text(text: str, max_chars: int, overlap: int) -> list[str]:
    """Split long text into pieces of at most max_chars, preferring sentence and word breaks.

    Consecutive pieces share roughly `overlap` characters so a fact cut at a boundary
    still appears whole in one of them.
    """
    text = text.strip()
    if len(text) <= max_chars:
        return [text] if text else []
    overlap = max(0, min(overlap, max_chars // 4))
    pieces: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            end = _best_break(text, start, end)
        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)
        if end >= len(text):
            break
        next_start = max(end - overlap, start + 1)
        space = text.find(" ", next_start, end)
        start = space + 1 if space != -1 else next_start
    return pieces


def _best_break(text: str, start: int, end: int) -> int:
    """Find a natural cut point in the second half of the window [start, end)."""
    floor = start + (end - start) // 2
    for separator in ("\n\n", "\n", ". ", "; ", ", ", " "):
        position = text.rfind(separator, floor, end)
        if position != -1:
            return position + len(separator)
    return end


def split_table(markdown: str, max_chars: int) -> list[str]:
    """Split a Markdown table by rows, repeating the header row in every part."""
    if len(markdown) <= max_chars:
        return [markdown]
    lines = markdown.strip().splitlines()
    header, rows = lines[:2], lines[2:]
    header_text = "\n".join(header)
    parts: list[str] = []
    current: list[str] = []
    for row in rows:
        candidate_len = len(header_text) + sum(len(r) + 1 for r in current) + len(row) + 1
        if current and candidate_len > max_chars:
            parts.append("\n".join([header_text, *current]))
            current = []
        current.append(row)
    if current or not parts:
        parts.append("\n".join([header_text, *current]))
    return parts


def heading_level(text: str, detected_level: int) -> int:
    """Heading depth, taken from numbering like "6.2" when present.

    Docling reports most PDF headings as level 1, which would flatten the section
    path; numbered headings carry their real depth in the number itself.
    """
    match = NUMBERED_HEADING.match(text.strip())
    if match:
        return match.group(1).rstrip(".").count(".") + 1
    return detected_level


def compact_table(markdown: str) -> str:
    """Remove the column padding Docling adds to Markdown tables; it only costs tokens."""
    lines = []
    for line in markdown.strip().splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if all(TABLE_RULE_CELL.fullmatch(cell) for cell in cells):
            cells = ["---"] * len(cells)
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


class _SectionTracker:
    """Keeps the current heading path while walking the document.

    Under a numbered heading ("8.4 Layout"), an unnumbered heading becomes its child.
    Docling also reports table captions, notes and page headers as headings, all at
    level 1; without this they would replace the real section path.
    """

    def __init__(self) -> None:
        self._stack: list[tuple[int, str, bool]] = []

    def enter(self, level: int, title: str) -> None:
        numbered = NUMBERED_HEADING.match(title.strip()) is not None
        numbered_levels = [lvl for lvl, _, is_numbered in self._stack if is_numbered]
        if not numbered and numbered_levels:
            level = max(numbered_levels) + 1
        while self._stack and self._stack[-1][0] >= level:
            self._stack.pop()
        self._stack.append((level, title, numbered))

    @property
    def path(self) -> str:
        return SECTION_SEPARATOR.join(title for _, title, _numbered in self._stack)


def chunk_blocks(blocks: list[Block], max_chars: int, overlap: int) -> list[Chunk]:
    """Group blocks into chunks: one per section and page, and one per table.

    Text under the same heading on the same page is merged and split only when it
    exceeds max_chars. Every table becomes its own chunk with the heading path
    prepended so the table is understandable on its own.
    """
    sections = _SectionTracker()
    chunks: list[Chunk] = []
    buffer: list[str] = []
    buffer_page = 0

    def flush() -> None:
        nonlocal buffer
        if buffer:
            for piece in split_text("\n".join(buffer), max_chars, overlap):
                chunks.append(Chunk(piece, buffer_page, sections.path, "text"))
        buffer = []

    for block in blocks:
        text = block.text.strip()
        if not text:
            continue
        if block.kind == "heading":
            flush()
            sections.enter(block.level, text)
        elif block.kind == "table":
            flush()
            for part in split_table(text, max_chars):
                body = f"{sections.path}\n\n{part}" if sections.path else part
                chunks.append(Chunk(body, block.page, sections.path, "table"))
        else:
            if buffer and block.page != buffer_page:
                flush()
            buffer_page = block.page
            buffer.append(text)
    flush()
    return chunks


class DoclingParser:
    """Parses PDFs with Docling. OCR is off: scanned PDFs are reported, not guessed at."""

    def __init__(self) -> None:
        self._converter: object | None = None
        self._lock = threading.Lock()

    def _get_converter(self) -> object:
        if self._converter is None:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.document_converter import DocumentConverter, PdfFormatOption

            options = PdfPipelineOptions(do_ocr=False, do_table_structure=True)
            self._converter = DocumentConverter(
                format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
            )
        return self._converter

    def warm_up(self) -> None:
        """Load the layout and table models now, so the first upload does not wait for them."""
        from docling.datamodel.base_models import InputFormat

        with self._lock:
            self._get_converter().initialize_pipeline(InputFormat.PDF)  # type: ignore[attr-defined]

    def __call__(self, path: Path, on_page: PageProgress | None = None) -> list[Block]:
        """Convert the PDF and return its body content as blocks in reading order."""
        from docling_core.types.doc import (
            ListItem,
            SectionHeaderItem,
            TableItem,
            TextItem,
            TitleItem,
        )

        def progress(event: object) -> None:
            if on_page is not None and getattr(event, "kind", "") == "page_completed":
                on_page(event.completed_pages, event.total_pages)  # type: ignore[attr-defined]

        with self._lock:
            try:
                result = self._get_converter().convert(  # type: ignore[attr-defined]
                    path, progress_callback=progress
                )
            except Exception as exc:
                logger.exception("Docling failed on %s", path)
                raise IngestionError(f"The PDF could not be parsed ({exc}).") from exc

        document = result.document
        blocks: list[Block] = []
        last_page = 1
        for item, _depth in document.iterate_items():
            prov = getattr(item, "prov", None)
            page = prov[0].page_no if prov else last_page
            last_page = page
            if isinstance(item, TitleItem):
                blocks.append(Block("heading", item.text, page, level=0))
            elif isinstance(item, SectionHeaderItem):
                blocks.append(
                    Block("heading", item.text, page, heading_level(item.text, item.level))
                )
            elif isinstance(item, TableItem):
                markdown = compact_table(item.export_to_markdown(doc=document))
                blocks.append(Block("table", markdown, page))
            elif isinstance(item, ListItem):
                blocks.append(Block("text", f"- {item.text}", page))
            elif isinstance(item, TextItem):
                blocks.append(Block("text", item.text, page))
        return blocks
