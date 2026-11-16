"""CSV and Excel export of a requirement table."""

import csv
import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

from app.schemas import RequirementOut

COLUMNS = ["ID", "Kind", "Quote (verbatim)", "Page", "Type", "Paraphrase (model)", "Verified"]
COLUMN_WIDTHS = [10, 11, 70, 6, 14, 50, 9]


def _rows(requirements: list[RequirementOut]) -> list[list[object]]:
    return [
        [r.code, r.kind, r.quote, r.page, r.type, r.paraphrase, "yes" if r.verified else "no"]
        for r in requirements
    ]


def to_csv(requirements: list[RequirementOut]) -> str:
    """CSV with a header row; UTF-8 BOM so Excel detects the encoding."""
    buffer = io.StringIO()
    buffer.write("﻿")
    writer = csv.writer(buffer)
    writer.writerow(COLUMNS)
    writer.writerows(_rows(requirements))
    return buffer.getvalue()


def to_xlsx(requirements: list[RequirementOut]) -> bytes:
    """Excel workbook with a bold, frozen header row and wrapped text."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Requirements"
    sheet.append(COLUMNS)
    for row in _rows(requirements):
        sheet.append(row)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for index, width in enumerate(COLUMN_WIDTHS, start=1):
        sheet.column_dimensions[chr(ord("A") + index - 1)].width = width
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    sheet.freeze_panes = "A2"
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
