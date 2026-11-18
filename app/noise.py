"""Blocks that can never answer a question are kept out of the index.

Two kinds of noise come out of a PDF parser:

- repeated page furniture: the same short text on many pages (a page header, a
  footer with the vendor's web address, a document code);
- text with almost no words: labels from package drawings ("SCALE 5.500"), bare
  dates ("23-Oct-2026"), page numbers and part codes ("YBG0006-C01").

Short text that carries content is kept even with few words: a value with a unit
("VDD 1.8 V", "| Supply voltage | 5.5 V |") or an instruction ("Use 5 V.", "Ground the
pad."). Headings are never dropped, because the section path is built from them.
"""

import re
from collections import defaultdict
from dataclasses import dataclass

from app.extraction_scope import has_obligation_wording, is_imperative
from app.ingestion import Block

# A word is a run of letters (two or more) between spaces and edge punctuation. In short
# text, capitals-only tokens ("SCALE", "REVISED", "MAX") are labels, codes and table
# headers; in a longer paragraph written in capitals (a legal notice) they are words.
LABEL_MAX_CHARS = 60
_EDGE_PUNCTUATION = "()[]{}*•.,;:!?\"'“”‘’|"  # noqa: RUF001 (typographic quotes)
_LETTERS = re.compile(r"^[^\W\d_]{2,}$")
_DIGITS = re.compile(r"\d+")
_SPACE = re.compile(r"\s+")
# A number followed by a unit: 5.5 V, 0.1-µF, 400 kHz, ±0.1 °C, 25 Nm, 10 %. The number
# must stand on its own, so a part code such as "DRV0006B" is not read as "0006 B".
_VALUE_WITH_UNIT = re.compile(
    r"(?<![\w.])\d+(?:[.,]\d+)?\s*[-–]?\s*"  # noqa: RUF001 (a dash between number and unit)
    r"(?:%|°\s*[CF]|[kMGmµμunp]?(?:V|A|W|Hz|F|Ω|Ohms?|ohms?|s|bits?)"
    r"|[kMG]?Ω|ppm|dB|dBm|LSB|mm|cm|m|mils?|g|kg|N\s*m|Nm|K|°)(?![A-Za-z])"
)


@dataclass(frozen=True)
class NoiseSettings:
    min_words: int
    repeated_min_pages: int
    repeated_max_chars: int


@dataclass(frozen=True)
class FilterResult:
    blocks: list[Block]
    repeated: int
    low_content: int


def word_count(text: str) -> int:
    """Words of two or more letters; capitals-only tokens count only in longer text."""
    count_capitals = len(text.strip()) > LABEL_MAX_CHARS
    count = 0
    for token in text.split():
        token = token.strip(_EDGE_PUNCTUATION)
        if _LETTERS.match(token) and (count_capitals or not token.isupper()):
            count += 1
    return count


def carries_content(text: str) -> bool:
    """True for short text that still says something: a value with a unit or an instruction."""
    if _VALUE_WITH_UNIT.search(text):
        return True
    if has_obligation_wording(text) or is_imperative(text):
        return True
    # A warning in capitals ("DO NOT TOUCH THE PAD.") is still an instruction.
    return text.isupper() and is_imperative(text.capitalize())


def _exact_key(text: str) -> str:
    return _SPACE.sub(" ", text.strip().lower())


def _masked_key(text: str) -> str:
    """Page furniture often differs only in numbers ("Page 3 of 50", "Page 4 of 50")."""
    return _DIGITS.sub("#", _exact_key(text))


def repeated_furniture(blocks: list[Block], min_pages: int, max_chars: int) -> set[str]:
    """Keys of short text blocks that occur on at least `min_pages` different pages.

    Returned keys are exact texts (always furniture) and number-masked texts prefixed
    with "#:" (furniture only when the text carries no content, so "Commit task 1." and
    "Commit task 2." on different pages are not mistaken for a footer).
    """
    if min_pages <= 0:
        return set()
    exact: dict[str, set[int]] = defaultdict(set)
    masked: dict[str, set[int]] = defaultdict(set)
    for block in blocks:
        text = block.text.strip()
        if block.kind == "text" and text and len(text) <= max_chars:
            exact[_exact_key(text)].add(block.page)
            masked[_masked_key(text)].add(block.page)
    keys = {key for key, pages in exact.items() if len(pages) >= min_pages}
    keys |= {"#:" + key for key, pages in masked.items() if len(pages) >= min_pages}
    return keys


def _is_furniture(text: str, furniture: set[str]) -> bool:
    if _exact_key(text) in furniture:
        return True
    return "#:" + _masked_key(text) in furniture and not carries_content(text)


def filter_noise(blocks: list[Block], settings: NoiseSettings) -> FilterResult:
    """Drop repeated page furniture and blocks with almost no words; keep headings."""
    furniture = repeated_furniture(blocks, settings.repeated_min_pages, settings.repeated_max_chars)
    kept: list[Block] = []
    repeated = low_content = 0
    for block in blocks:
        text = block.text.strip()
        if block.kind == "heading" or not text:
            kept.append(block)
        elif block.kind == "text" and _is_furniture(text, furniture):
            repeated += 1
        elif word_count(text) < settings.min_words and not carries_content(text):
            low_content += 1
        else:
            kept.append(block)
    return FilterResult(kept, repeated, low_content)
