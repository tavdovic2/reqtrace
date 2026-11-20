"""Value-aware matching of answers against expected values, and of extracted
requirement rows against hand-written labels.

An expected keyword such as "120 g" is a value: a number with an optional unit. It
matches an answer that states the same number with the same unit in any notation:
"approximately 120 grams", "120g", "typ. 120 g". A different number never matches,
and units are never converted ("100 nF" does not match "0.1 µF"; list both as
alternatives with "|" when the document prints both).

A keyword that is not a value ("IP67", "Modbus", "as close as possible") is matched
as whole words, case-insensitively, as before.
"""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.verification import NUMBER_WORDS, normalize

# A number: optional sign, digits, optional decimal part with "." or ",".
_NUMBER = r"[-+±]?\d+(?:[.,]\d+)*"
# What may follow a number as its unit: a symbol or word, optionally "/unit".
_UNIT = r"(?:°\s?[cf]|[a-zµΩ°%][a-zµΩ°%]*)(?:\s?/\s?(?:°\s?[cf]|[a-zµΩ]+))?"
_VALUE = re.compile(
    rf"(?<![\w.,])(?P<number>{_NUMBER})(?:\s*-?\s*(?P<unit>{_UNIT}))?(?![\w])", re.IGNORECASE
)
_EXPECTED_VALUE = re.compile(rf"^\s*(?P<number>{_NUMBER})\s*-?\s*(?P<unit>{_UNIT})?\s*$", re.I)
# Words between a bare number and a following value that make them one range or row:
# "1.7 to 5.5 V", "1.8 3.3 5.5 V", "-55 °C to 150 °C" (dashes are already normalised).
_RANGE_GAP = re.compile(r"^\s*(?:to|do|and|i|-|,|/)?\s*$", re.IGNORECASE)
_WORD = re.compile(r"[^\W\d_]+")

# Canonical unit spellings. Keys are lower case; µ (U+00B5) and μ (U+03BC) are unified
# before lookup, and a leading "u" stands for micro.
_UNIT_ALIASES: dict[str, str] = {
    "v": "V",
    "volt": "V",
    "volts": "V",
    "mv": "mV",
    "kv": "kV",
    "a": "A",
    "ma": "mA",
    "µa": "µA",
    "na": "nA",
    "pa": "pA",
    "w": "W",
    "mw": "mW",
    "µw": "µW",
    "µwt": "µW",
    "uwt": "µW",
    "f": "F",
    "µf": "µF",
    "nf": "nF",
    "pf": "pF",
    "ω": "Ω",
    "ohm": "Ω",
    "ohms": "Ω",
    "kω": "kΩ",
    "kohm": "kΩ",
    "kohms": "kΩ",
    "k": "kΩ",
    "mω": "MΩ",
    "mohm": "MΩ",
    "hz": "Hz",
    "khz": "kHz",
    "mhz": "MHz",
    "s": "s",
    "sec": "s",
    "second": "s",
    "seconds": "s",
    "sekunda": "s",
    "sekunde": "s",
    "ms": "ms",
    "µs": "µs",
    "ns": "ns",
    "°c": "°C",
    "ºc": "°C",
    "c": "°C",
    "m°c": "m°C",
    "°f": "°F",
    "°c/w": "°C/W",
    "mj/°c": "mJ/°C",
    "m°c/v": "m°C/V",
    "g": "g",
    "gram": "g",
    "grams": "g",
    "grama": "g",
    "kg": "kg",
    "mm": "mm",
    "cm": "cm",
    "m": "m",
    "nm": "Nm",
    "%": "%",
    "ppm": "ppm",
    "db": "dB",
    "lsb": "LSB",
    "bit": "bit",
    "bits": "bit",
    "bita": "bit",
    "month": "months",
    "months": "months",
    "mjeseci": "months",
    "mjeseca": "months",
    "year": "years",
    "years": "years",
    "godina": "years",
    "godine": "years",
}


@dataclass(frozen=True)
class Value:
    number: Decimal
    negative: bool
    unit: str | None


def _canonical_unit(unit: str | None) -> str | None:
    if not unit:
        return None
    key = re.sub(r"\s+", "", unit).replace("μ", "µ").replace("º", "°").lower()
    if key.startswith("u") and key[1:] in {"a", "f", "v", "w", "s", "wt"}:
        key = "µ" + key[1:]
    return _UNIT_ALIASES.get(key)


def _parse_number(text: str) -> tuple[Decimal, bool] | None:
    """A number written with "." or "," as decimal mark, or "," for thousands."""
    negative = text.startswith("-")
    digits = text.lstrip("+-±")
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+", digits) and not digits.startswith("0,"):
        digits = digits.replace(",", "")  # 1,000 / 50,000
    elif digits.count(",") == 1 and "." not in digits:
        digits = digits.replace(",", ".")  # Bosnian decimal comma: 0,0078
    try:
        return Decimal(digits).normalize(), negative
    except InvalidOperation:
        return None


def values_in(text: str) -> list[Value]:
    """Every number in a text with the unit it carries.

    A bare number directly before another value, separated only by a range word or a
    space ("1.7 to 5.5 V", "1.8 3.3 5.5 V"), takes that value's unit. Number words
    ("four", "četiri") count as unitless numbers.
    """
    norm = normalize(text)
    found: list[tuple[int, int, Decimal, bool, str | None]] = []
    for match in _VALUE.finditer(norm):
        parsed = _parse_number(match.group("number"))
        if parsed is None:
            continue
        found.append((match.start(), match.end(), *parsed, _canonical_unit(match.group("unit"))))
    # Units spread backwards over ranges and table rows.
    for i in range(len(found) - 2, -1, -1):
        start, end, number, negative, unit = found[i]
        following = found[i + 1]
        if unit is None and following[4] and _RANGE_GAP.match(norm[end : following[0]]):
            found[i] = (start, end, number, negative, following[4])
    values = [Value(number, negative, unit) for _, _, number, negative, unit in found]
    for word in _WORD.findall(norm):
        if word in NUMBER_WORDS:
            values.append(Value(Decimal(NUMBER_WORDS[word]), False, None))
    return values


def expected_value(keyword: str) -> Value | None:
    """The value a keyword stands for, or None when it is text ("IP67", "Modbus")."""
    match = _EXPECTED_VALUE.match(normalize(keyword))
    if not match:
        return None
    parsed = _parse_number(match.group("number"))
    unit = _canonical_unit(match.group("unit"))
    if parsed is None or (match.group("unit") and unit is None):
        return None
    return Value(parsed[0], parsed[1], unit)


def value_found(expected: Value, values: list[Value]) -> bool:
    """True when one of `values` states the expected number (and unit, if one is given).

    A negative expected number needs a negative answer; a positive one also accepts
    "±0.1" or "-0.1 to 0.1".
    """
    for value in values:
        if value.number != expected.number:
            continue
        if expected.negative and not value.negative:
            continue
        if expected.unit is None or value.unit == expected.unit:
            return True
    return False


def _text_found(keyword: str, text: str) -> bool:
    pattern = rf"(?<![\w.]){re.escape(normalize(keyword))}(?!\w)"
    return re.search(pattern, text) is not None


def keyword_match(answer: str, keywords: list[str]) -> bool:
    """True when every keyword occurs in the answer; "a|b" accepts either alternative."""
    text = normalize(answer)
    values = values_in(answer)

    def found(alternative: str) -> bool:
        value = expected_value(alternative)
        if value is not None:
            return value_found(value, values)
        return _text_found(alternative, text)

    return all(any(found(alt) for alt in keyword.split("|")) for keyword in keywords)


# ---------------------------------------------------------------- requirement labels


@dataclass(frozen=True)
class RequirementLabel:
    """A hand-labelled obligation or limit: where it is and how to recognise it.

    `terms` are short phrases or values that all occur in a row that states it.
    Optional labels are true requirements that the documented scope leaves open
    (timing rows, generic bus-protocol rules, legal boilerplate): extracting one is
    not an error, missing one is not counted against recall.
    """

    id: str
    page: int
    kind: str
    terms: list[str]
    optional: bool = False
    note: str = ""


def row_matches(label: RequirementLabel, page: int, quote: str) -> bool:
    """True when an extracted row on `page` with `quote` states the labelled item."""
    if page != label.page:
        return False
    text = normalize(quote)
    values = values_in(quote)
    for term in label.terms:
        value = expected_value(term)
        if value is not None:
            if not value_found(Value(value.number, value.negative, None), values):
                return False
        elif not _text_found(term, text):
            return False
    return True
