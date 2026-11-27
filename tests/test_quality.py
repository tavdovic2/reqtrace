"""Quality flags and rule-based types."""

import pytest

from app.categories import TYPES, assign_type, rule_type
from app.quality import check, row_flags
from eval.type_fixture import TYPE_FIXTURE


def codes(text: str) -> list[str]:
    return [flag.code for flag in check(text, "quote")]


def words(text: str, code: str) -> list[str]:
    return [t.text for f in check(text, "quote") if f.code == code for t in f.triggers]


# ---------- vague ----------


@pytest.mark.parametrize(
    ("text", "word"),
    [
        (
            "Place the bypass capacitor as close as possible to the supply pins.",
            "as close as possible",
        ),
        ("Use the minimal acceptable power supply voltage.", "minimal"),
        (
            "Remove bottom solder mask and cover exposed copper with gold if possible.",
            "if possible",
        ),
        ("The screen must load fast.", "fast"),
        ("Provide adequate cooling and an appropriate enclosure.", "adequate"),
        ("Add more decoupling as needed, etc.", "as needed"),
        ("The heater usually needs a sufficient warm-up.", "usually"),
    ],
)
def test_vague_wording_is_flagged_with_its_words(text: str, word: str) -> None:
    assert "vague" in codes(text)
    assert word in words(text, "vague")


@pytest.mark.parametrize(
    "text",
    [
        "The device supports Fast-mode and Fast-mode Plus on the bus.",
        "Use the fast table mode for documents with simple tables.",
        "Select the high-speed mode before the first transfer.",
        "Set the FAST bit in the configuration register.",
        "The supply voltage shall not exceed 3.6 V.",
        "A smaller thermal mass results in a faster thermal response.",
    ],
)
def test_names_and_precise_wording_are_not_vague(text: str) -> None:
    assert "vague" not in codes(text)


# ---------- missing_unit ----------


@pytest.mark.parametrize(
    ("text", "number"),
    [
        ("Wait at least 2 after power-up before the first read.", "2"),
        ("The supply voltage must not exceed 3.6.", "3.6"),
        ("Use a pullup resistor of at least 2.", "2"),
    ],
)
def test_a_quantity_without_a_unit_is_flagged(text: str, number: str) -> None:
    assert words(text, "missing_unit") == [number]


@pytest.mark.parametrize(
    "text",
    [
        "Up to four devices can share the bus.",
        "Use 8-sample averaging in noisy systems.",
        "See Table 7-2 and Figure 6-7; Section 5.1 explains the timing.",
        "Before Lab 5, read bits 15:12 of CONV[2:0].",
        "Use the TMP117 Rev. D with a 4:1 ratio and a 10% margin.",
        "Supply voltage V+ -0.3 6 V",
        "V+ Supply voltage, TA = -55 °C to 150 °C 1.8 3.3 5.5 V",
        "The list must update within 100 ms after a chip is pressed.",
        "The supply must stay between 1.8 and 5.5 V.",
        "Set the address to 0x48 or 1001000b.",
        "It is suitable for safety functions up to SIL 2 according to IEC 61508.",
    ],
)
def test_counts_references_bits_ratios_and_units_are_not_flagged(text: str) -> None:
    assert "missing_unit" not in codes(text)


# ---------- compound ----------


@pytest.mark.parametrize(
    "text",
    [
        "Commit after every task and push your work at the end of the session.",
        "Avoid small pullup resistors on the SDA pin. Instead, use resistors larger than 2 kΩ.",
        "The sensor must be powered from 3.3 V; the enclosure must withstand 50 °C.",
    ],
)
def test_several_obligations_in_one_row_are_compound(text: str) -> None:
    assert "compound" in codes(text)


@pytest.mark.parametrize(
    "text",
    [
        "Add a header, empty, loading and error states to the screen.",
        "Place the bypass capacitor close to the supply and ground pins.",
        "The supply voltage shall not exceed 3.6 V.",
        "Connect the ADD0 pin to GND, V+, SDA or SCL.",
    ],
)
def test_one_obligation_with_a_list_is_not_compound(text: str) -> None:
    assert "compound" not in codes(text)


# ---------- which text is checked ----------


def test_an_edit_replaces_the_original_wording_so_a_fixed_row_loses_its_flag() -> None:
    quote = "Place the capacitor as close as possible to the supply pin."
    assert [f.code for f in row_flags(quote, "", None)] == ["vague"]
    assert row_flags(quote, "", "Place the capacitor within 2 mm of the supply pin.") == []
    flags = row_flags(quote, "", "Place the capacitor near the pin if possible.")
    assert [(f.code, f.source) for f in flags] == [("vague", "text")]


def test_paraphrase_adds_flags_the_quote_does_not_have() -> None:
    flags = row_flags("Use a 100-nF capacitor.", "Use a suitable capacitor.", None)
    assert [(f.code, f.source) for f in flags] == [("vague", "text")]


def test_trigger_spans_point_into_the_checked_text() -> None:
    text = "Wait at least 2 after power-up."
    [flag] = check(text, "quote")
    trigger = flag.triggers[0]
    assert text[trigger.start : trigger.end] == trigger.text == "2"


# ---------- types ----------


def test_rule_types_on_the_labelled_fixture() -> None:
    wrong = [
        (item.sentence, item.type, rule_type(item.sentence))
        for item in TYPE_FIXTURE
        if (rule_type(item.sentence) or "other") != item.type
    ]
    assert wrong == []
    assert {item.type for item in TYPE_FIXTURE} == set(TYPES)


@pytest.mark.parametrize(
    "text",
    [
        "Use PCB with minimal thickness.",
        "Cover bottom of the PCB with copper plane.",
        "Place PCB vertically along air flow.",
        "Remove the top solder mask.",
        "If the thermal pad is soldered, it must be left floating or grounded.",
        "Place the power-supply bypass capacitor as close as possible to the supply pins.",
    ],
)
def test_pcb_layout_rules_are_layout(text: str) -> None:
    assert rule_type(text) == "layout"


def test_board_alone_is_not_layout() -> None:
    assert rule_type("The board is placed horizontally in the climate chamber.") is None


def test_model_type_is_used_only_when_no_rule_matches() -> None:
    assert assign_type("Keep the original packaging.", "other") == "other"
    assert assign_type("Keep the original packaging.", "functional") == "functional"
    assert assign_type("Use PCB with minimal thickness.", "functional") == "layout"
    assert assign_type("Keep the packaging.", "made-up") == "other"


def test_compound_marks_the_obligation_words_not_whole_sentences() -> None:
    text = "Avoid small pullup resistors. The recommended value must be 5 kOhm."
    assert words(text, "compound") == ["Avoid", "recommended"]
    text = "A pullup resistor is required. A 100-nF capacitor is recommended."
    assert words(text, "compound") == ["required", "recommended"]
    text = "If practical, use one-shot mode. If conversion is continuous, use cycle mode."
    assert words(text, "compound") == ["use", "use"]
