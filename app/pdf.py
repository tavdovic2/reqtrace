"""Low-level PDF access with pypdfium2: validation, page count and raw page text."""

import logging
from pathlib import Path

import pypdfium2 as pdfium

logger = logging.getLogger(__name__)

PDF_MAGIC = b"%PDF-"


class InvalidPdfError(ValueError):
    """The file is not a readable PDF."""


def read_page_count(path: Path) -> int:
    """Open the PDF and return its page count; raises InvalidPdfError if it cannot be read."""
    try:
        pdf = pdfium.PdfDocument(str(path))
    except pdfium.PdfiumError as exc:
        raise InvalidPdfError(f"The file could not be opened as a PDF ({exc}).") from exc
    try:
        count = len(pdf)
    finally:
        pdf.close()
    if count == 0:
        raise InvalidPdfError("The PDF has no pages.")
    return count


def extract_page_text(path: Path, page: int) -> str:
    """Return the text layer of one page (1-based), straight from the PDF.

    Quote verification reads this instead of our own chunks, so a requirement is
    checked against the original document rather than against our parsing of it.
    """
    pdf = pdfium.PdfDocument(str(path))
    try:
        if page < 1 or page > len(pdf):
            return ""
        pdf_page = pdf[page - 1]
        text_page = pdf_page.get_textpage()
        try:
            return text_page.get_text_range()
        finally:
            text_page.close()
            pdf_page.close()
    finally:
        pdf.close()
