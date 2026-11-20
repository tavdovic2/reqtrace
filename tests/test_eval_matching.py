"""Value-aware answer matching and requirement label matching (eval/matching.py)."""

import pytest

from eval.matching import RequirementLabel, keyword_match, row_matches, values_in


@pytest.mark.parametrize(
    ("answer", "keyword"),
    [
        ("The transmitter weighs approximately 120 grams.", "120 g"),
        ("Weight: 120g (typ.)", "120 g"),
        ("A 0.1-µF capacitor is recommended.", "0.1 µF"),
        ("A 0.1-μF capacitor is recommended.", "0.1 µF"),  # Greek mu
        ("Use 0.1 uF.", "0.1 µF"),
        ("The recommended value is 5 kOhm.", "5 kΩ"),
        ("The recommended value is 5 kΩ.", "5 kΩ"),
        ("typically 3.5 µA", "3.5 µA"),
        ("up to 400 KHz", "400 kHz"),
        ("1 second", "1 s"),
        ("The supply range is 1.7 to 5.5 V.", "1.7 V"),
        ("from –55 °C to 150 °C", "-55 °C"),
        ("Rezolucija je 0,0078 °C.", "0.0078 °C"),
        ("Do četiri uređaja.", "4"),
        ("at least 1,000 writes", "1000"),
        ("±0.1 °C (maximum)", "0.1 °C"),
        ("The address is 1001000x.", "1001000"),
        ("about 0.10 °C", "0.1 °C"),
    ],
)
def test_notation_differences_match(answer: str, keyword: str) -> None:
    assert keyword_match(answer, [keyword])


@pytest.mark.parametrize(
    ("answer", "keyword"),
    [
        ("approximately 125 grams", "120 g"),
        ("0.15 °C", "0.1 °C"),
        ("100 nF", "0.1 µF"),  # units are never converted
        ("0.1 mF", "0.1 µF"),
        ("5 V", "5 kΩ"),
        ("The resolution is 0.0078.", "0.0078 °C"),  # unit required when expected
        ("55 °C to 150 °C", "-55 °C"),  # sign required when expected
        ("250 nA in shutdown mode", "150 nA"),
        ("SNOSD82D revision", "82"),
        ("I2C interface", "2"),
    ],
)
def test_wrong_values_never_match(answer: str, keyword: str) -> None:
    assert not keyword_match(answer, [keyword])


def test_alternatives_and_text_keywords() -> None:
    assert keyword_match("Use 100 nF.", ["100 nF|0.1 µF"])
    assert keyword_match("Place it as close as possible to V+.", ["as close as possible"])
    assert not keyword_match("It is rated IP65.", ["IP67"])
    assert keyword_match("1.8 V to 5.5 V", ["1.8 V", "5.5 V"])
    assert not keyword_match("1.8 V", ["1.8 V", "5.5 V"])


def test_units_spread_over_table_rows_and_ranges() -> None:
    values = {(str(v.number), v.unit) for v in values_in("Supply voltage 1.8 3.3 5.5 V")}
    assert {("1.8", "V"), ("3.3", "V"), ("5.5", "V")} <= values
    words = values_in("eight averages, 15.5 ms")
    assert {str(v.number): v.unit for v in words}["15.5"] == "ms"


def test_row_matching_needs_the_page_and_all_terms() -> None:
    label = RequirementLabel("L1", 5, "limit", ["storage temperature", "-65", "155"])
    assert row_matches(label, 5, "Storage temperature, Tstg –65 155 °C")
    assert not row_matches(label, 6, "Storage temperature, Tstg –65 155 °C")
    assert not row_matches(label, 5, "Storage temperature, Tstg –65 150 °C")
    assert not row_matches(label, 5, "Operating junction temperature, TJ –55 155 °C")
