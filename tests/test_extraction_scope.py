import pytest

from app.extraction_scope import (
    DEFAULT_SKIP_SECTIONS,
    Kind,
    classify,
    invents_obligation,
    is_imperative,
    is_process_instruction,
    non_requirement_table,
    skipped_section,
)
from app.vector_store import ChunkPayload


@pytest.mark.parametrize(
    "section",
    [
        "1 Features",
        "3 Description",
        "Revision History",
        "7 Detailed Description > 7.3 Feature Description > 7.3.1 Power Up",
        "7 Detailed Description > 7.6 Register Maps > 7.6.1 Config Register",
        "6 Specifications > 6.6 Typical Characteristics",
        "9 Device and Documentation Support > 9.1 Related Documentation",
        "IMPORTANT NOTICE AND DISCLAIMER",
        "11 Mechanical, Packaging, and Orderable Information",
    ],
)
def test_sections_without_obligations_or_limits_are_skipped(section: str) -> None:
    assert skipped_section(section, DEFAULT_SKIP_SECTIONS) is not None


@pytest.mark.parametrize(
    "section",
    [
        "6 Specifications > 6.1 Absolute Maximum Ratings",
        "6 Specifications > 6.3 Recommended Operating Conditions",
        "8 Application and Implementation > 8.4 Layout > 8.4.1 Layout Guidelines",
        "8 Application and Implementation > 8.3 Power Supply Recommendations",
        "Task 2: Filter chips",
        "",
    ],
)
def test_sections_that_can_hold_items_are_processed(section: str) -> None:
    assert skipped_section(section, DEFAULT_SKIP_SECTIONS) is None


def test_skip_list_is_configurable() -> None:
    assert skipped_section("Task 2: Filter chips", ["filter chips"]) == "task 2: filter chips"
    assert skipped_section("1 Features", []) is None


def table(header: str) -> ChunkPayload:
    text = f"{header}\n| --- | --- | --- |\n| a | b | c |"
    return ChunkPayload(1, 1, "a.pdf", 3, "S", "table", text, 0)


@pytest.mark.parametrize(
    ("header", "kind"),
    [
        ("| Step | Input | Expected output |", "test table"),
        ("| Test | Input | Output |", "test table"),
        ("| Revision | Date | Change |", "change log"),
        ("| Prop | Meaning | Default |", "default values"),
        ("| Bit | Field | Type | Reset | Description |", "bit fields"),
        ("| Address | Register | Reset |", "register map"),
        ("| Problem | Fix |", "troubleshooting"),
        ("| Symptom | Cause | Solution |", "troubleshooting"),
    ],
)
def test_non_requirement_tables_are_recognised_by_their_header(header: str, kind: str) -> None:
    assert non_requirement_table(table(header)) == kind


@pytest.mark.parametrize(
    "header",
    [
        "| Parameter | MIN | MAX | UNIT |",
        "| Parameter | MIN | NOM | MAX | UNIT |",
        "| Code | Meaning | Note |",
    ],
)
def test_ratings_and_condition_tables_are_processed(header: str) -> None:
    assert non_requirement_table(table(header)) is None


@pytest.mark.parametrize(
    "text",
    [
        "Avoid using small-value pullup resistors on the SDA pin.",
        "Use pullup resistors larger than 2 kOhm.",
        "To prevent oxidation, cover any exposed copper with solder mask.",
        "- Place the device close to the heat source.",
        "Commit after every task and push your work.",
        "Let the user open a trail by tapping its row.",
        "If the bus is long, add a series resistor.",
        "Below the summary row, add a row of chips: All, easy, medium and hard.",
        "Before the lecture, read the reading on lists.",
        "After the request fails, remove the spinner.",
        "When the list is empty, use the shared empty-state component.",
    ],
)
def test_imperatives_are_recognised(text: str) -> None:
    assert is_imperative(text)


@pytest.mark.parametrize(
    "text",
    [
        "The TS-100 acknowledges each data byte it receives.",
        "Thus, the difference between the limits acts like a hysteresis.",
        "See Figure 3 for the timing diagram.",
        "High-accuracy digital temperature sensor",
        "Each row shows the trail name.",
        "When a filter matches nothing, the empty state says so.",
        "In shutdown mode, power consumption is 250 nA.",
        "During a conversion, the supply current rises.",
    ],
)
def test_descriptions_are_not_imperatives(text: str) -> None:
    assert not is_imperative(text)


@pytest.mark.parametrize(
    ("quote", "section", "kind"),
    [
        ("A 0.1-µF bypass capacitor is recommended between V+ and GND.", "Layout", Kind.OBLIGATION),
        ("Do not route high-speed traces underneath the device.", "Layout", Kind.OBLIGATION),
        (
            "The supply voltage must not drop below 1.8 V.",
            "Absolute Maximum Ratings",
            Kind.OBLIGATION,
        ),
        ("You should add a loading indicator.", "Task 1", Kind.OBLIGATION),
        ("Voltage at SCL, SDA and ALERT -0.3 6 V", "5.1 Absolute Maximum Ratings", Kind.LIMIT),
        ("Supply voltage, V+ 1.8 3.3 5.5 V", "5.2 Recommended Operating Conditions", Kind.LIMIT),
        ("The input current must stay below 10 mA", "Pins", Kind.OBLIGATION),
        ("Load capacitance up to 400 pF", "Interface", Kind.LIMIT),
    ],
)
def test_classify_obligations_and_limits(quote: str, section: str, kind: Kind) -> None:
    assert classify(quote, section) == kind


@pytest.mark.parametrize(
    ("quote", "section"),
    [
        ("High-accuracy digital temperature sensor", "Layout"),
        ("The TS-100 is a low-power digital temperature sensor.", "Layout"),
        (
            "(1) Stresses beyond those listed may cause permanent damage.",
            "Absolute Maximum Ratings",
        ),
        ("Parameter MIN MAX UNIT", "Absolute Maximum Ratings"),
        ("The device converts every 15.5 ms.", "Detailed Description"),
        ("Input: long press on a trail card", "Task 2"),
        ("Your project now covers the list screen.", "Wrapping up"),
    ],
)
def test_descriptions_are_never_classified(quote: str, section: str) -> None:
    assert classify(quote, section) is None


def test_invented_obligation_is_detected() -> None:
    quote = "The TS-100 is a high-precision digital temperature sensor."
    assert invents_obligation(
        "The TS-100 must be a high-precision digital temperature sensor.", quote
    )
    assert invents_obligation(
        "Storage temperature shall be -65 to 150 °C.", "Storage temperature -65 150 °C"
    )
    assert not invents_obligation(
        "The designer must place the device close to the source.",
        "Place the device close to the source.",
    )
    assert not invents_obligation(
        "The supply shall not exceed 3.6 V.", "The supply voltage shall not exceed 3.6 V."
    )
    assert not invents_obligation(
        "Storage temperature is -65 to 150 °C.", "Storage temperature -65 150 °C"
    )


@pytest.mark.parametrize(
    "code",
    [
        "const trails = require('./trails.json');",
        "type Trail = { id: string; name: string; lengthKm: number }",
        "setTrades((list) => [...list, trade])",
    ],
)
def test_code_lines_are_never_obligations(code: str) -> None:
    assert classify(code, "Task 1") is None


def test_require_in_prose_is_still_obligation_wording() -> None:
    assert (
        classify("The bus termination requires a 120 Ohm resistor.", "Interface") == Kind.OBLIGATION
    )


@pytest.mark.parametrize(
    ("quote", "section"),
    [
        ("SCL, SDA, ALERT and ADD0", "Absolute Maximum Ratings"),
        ("Over free-air temperature range unless otherwise noted(1)", "Absolute Maximum Ratings"),
        ("(1) The maximum hold time could be 0.9 us for fast mode.", "Interface Timing"),
        ("Soldering the TMP117 pad reduces the response time.", "Layout"),
    ],
)
def test_names_footnotes_and_table_conditions_are_not_limits(quote: str, section: str) -> None:
    assert classify(quote, section) is None


def test_recommended_operating_conditions_is_a_table_name_not_a_recommendation() -> None:
    note = "Operation beyond the Recommended Operating Conditions is not implied."
    assert classify(note, "Absolute Maximum Ratings") is None
    assert classify("A 100-nF capacitor is recommended.", "Power") == Kind.OBLIGATION


def test_signal_names_in_capitals_are_not_imperatives() -> None:
    assert not is_imperative("STOP condition setup time")
    assert is_imperative("Stop the conversion before writing the register.")


def test_limit_rows_reads_data_rows_with_values_and_collapses_repeated_cells() -> None:
    from app.extraction_scope import limit_rows

    markdown = (
        "| | | MIN | MAX | UNIT |\n| --- | --- | --- | --- | --- |\n"
        "| Supply voltage | V+ | -0.3 | 6 | V |\n"
        "| Junction temperature, TJ | Junction temperature, TJ | -55 | 155 | °C |\n"
        "| (1) Stresses beyond those listed may cause damage. | | | | |\n"
        "| Notes | see below | | | |"
    )
    assert limit_rows(markdown) == [
        "Supply voltage V+ -0.3 6 V",
        "Junction temperature, TJ -55 155 °C",
    ]


@pytest.mark.parametrize(
    ("row", "kind"),
    [
        ("Supply voltage V+ -0.3 6 V", "electrical"),
        ("Storage temperature -65 150 °C", "environmental"),
        ("SCL operating frequency 1 400 kHz", "performance"),
        ("Pullup resistor 2 kΩ", "electrical"),
        ("ESD rating 2000", "other"),
        ("V+ Supply voltage, TA = -55 °C to 150 °C 1.8 3.3 5.5 V", "electrical"),
    ],
)
def test_type_from_unit(row: str, kind: str) -> None:
    from app.extraction_scope import type_from_unit

    assert type_from_unit(row) == kind


@pytest.mark.parametrize(
    "text",
    [
        "(1) Stresses beyond those listed may cause damage. These are ratings only, which do not imply functional operation.",
        "The averaging filter ensures stable readings.",
        "The device never stretches the clock.",
    ],
)
def test_do_not_never_and_ensure_inside_a_sentence_are_descriptions(text: str) -> None:
    assert classify(text, "Absolute Maximum Ratings") is None


def test_do_not_never_and_ensure_at_the_start_are_instructions() -> None:
    assert classify("Do not solder the thermal pad.", "Layout") == Kind.OBLIGATION
    assert classify("Never exceed the supply voltage.", "Layout") == Kind.OBLIGATION
    assert classify("Ensure that the SCL level stays below 10% of V+.", "Layout") == Kind.OBLIGATION


def test_introductory_phrase_with_a_described_subject_is_not_an_obligation() -> None:
    assert classify("When a filter matches nothing, the empty state says so.", "Task 2") is None
    assert classify("Below the summary row, add a row of chips.", "Task 2") == Kind.OBLIGATION


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Test long presses and dialogs on your phone.", True),
        ("Commit after every task and push your work.", True),
        ("Before the deadline, submit the repository link.", True),
        ("Add a loading indicator.", False),
        ("The test passes when the list renders.", False),
    ],
)
def test_process_instructions(text: str, expected: bool) -> None:
    assert is_process_instruction(text) is expected


def test_timing_rows_are_never_limits() -> None:
    assert classify("SCL clock frequency 1 400 kHz", "7.6 Timing Requirements") is None
    assert classify("Data hold time 0 ns", "Switching Characteristics") is None
    assert (
        classify("Supply voltage, V+ 1.8 3.3 5.5 V", "Recommended Operating Conditions")
        == Kind.LIMIT
    )


@pytest.mark.parametrize(
    "text",
    ["Check your work with the following steps.", "Configure the registers as follows:"],
)
def test_lead_in_sentences_are_not_requirements(text: str) -> None:
    assert classify(text, "Task 2") is None
