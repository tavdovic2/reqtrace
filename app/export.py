"""CSV and Excel export of a requirement table.

Two scopes:

- **accepted** (default): only rows a person accepted. "Requirement" is the edited text
  where there is one, otherwise the original text (the paraphrase, else the quote). The
  verbatim quote and the page are always included.
- **all**: every row, with its review status, reviewer note, review time and whether
  the latest extraction still found it.

Both carry the type and the quality flags (codes, and their English explanations with the
words that set them).

"Verified" (the quote was found on its page, automatic) and "Review status" (a person's
decision) are separate columns and never merged.
"""

import csv
import io
from typing import Literal

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

from app.schemas import RequirementOut

ExportScope = Literal["accepted", "all"]

COLUMNS = [
    "ID",
    "Kind",
    "Type",
    "Requirement",
    "Edited",
    "Quote (verbatim)",
    "Page",
    "Paraphrase (model)",
    "Verified (quote on page)",
    "Flags",
    "Flag details",
]
REVIEW_COLUMNS = ["Review status", "Reviewer note", "Reviewed at (UTC)", "In latest extraction"]
COLUMN_WIDTHS = [10, 11, 14, 60, 8, 70, 6, 50, 12, 18, 60]
REVIEW_COLUMN_WIDTHS = [14, 40, 20, 12]


def _yes(value: bool) -> str:
    return "yes" if value else "no"


def flag_codes(requirement: RequirementOut) -> str:
    """The row's quality flag codes, comma-separated ("vague, compound")."""
    return ", ".join(flag.code for flag in requirement.flags)


def flag_details(requirement: RequirementOut) -> str:
    """Each flag with the text that set it, its explanation and its words, in English."""
    return "; ".join(
        f"{flag.code} ({flag.source}): {flag.explanation} Words: "
        + ", ".join(f'"{word.text}"' for word in flag.words)
        for flag in requirement.flags
    )


def select_rows(requirements: list[RequirementOut], scope: ExportScope) -> list[RequirementOut]:
    """The rows an export contains: accepted ones, or all."""
    return [r for r in requirements if scope == "all" or r.status == "accepted"]


def columns(scope: ExportScope) -> list[str]:
    return COLUMNS + (REVIEW_COLUMNS if scope == "all" else [])


def _rows(requirements: list[RequirementOut], scope: ExportScope) -> list[list[object]]:
    rows = []
    for r in select_rows(requirements, scope):
        row: list[object] = [
            r.code,
            r.kind,
            r.type,
            r.requirement_text or r.edited_text or r.paraphrase or r.quote,
            _yes(r.edited_text is not None),
            r.quote,
            r.page,
            r.paraphrase,
            _yes(r.verified),
            flag_codes(r),
            flag_details(r),
        ]
        if scope == "all":
            reviewed = r.reviewed_at.strftime("%Y-%m-%d %H:%M:%S") if r.reviewed_at else ""
            row += [r.status, r.reviewer_note or "", reviewed, _yes(not r.not_found)]
        rows.append(row)
    return rows


def to_csv(requirements: list[RequirementOut], scope: ExportScope = "accepted") -> str:
    """CSV with a header row; UTF-8 BOM so Excel detects the encoding."""
    buffer = io.StringIO()
    buffer.write("﻿")
    writer = csv.writer(buffer)
    writer.writerow(columns(scope))
    writer.writerows(_rows(requirements, scope))
    return buffer.getvalue()


def to_xlsx(requirements: list[RequirementOut], scope: ExportScope = "accepted") -> bytes:
    """Excel workbook with a bold, frozen header row and wrapped text."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Requirements"
    sheet.append(columns(scope))
    for row in _rows(requirements, scope):
        sheet.append(row)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    widths = COLUMN_WIDTHS + (REVIEW_COLUMN_WIDTHS if scope == "all" else [])
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    sheet.freeze_panes = "A2"
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
