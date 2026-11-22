import pytest

from app.extraction_scope import (
    Kind,
    Scope,
    SectionRules,
    classify,
    heading_text,
    invents_obligation,
    is_imperative,
    is_passive_recommendation,
    is_process_instruction,
    non_requirement_table,
    screened_text,
    worth_screening,
)
from app.vector_store import ChunkPayload

RULES = SectionRules()

# Heading paths in the style of typical datasheet tables of contents, with the scope each
# gets. SKIP: front and back matter, legal text, packaging (nothing is read). SCREEN:
# descriptive sections (only sentences with obligation wording go to the model). FULL:
# everything else.
TI_STYLE = [
    ("1 Features", Scope.SCREEN),
    ("2 Applications", Scope.SCREEN),
    ("3 Description", Scope.SCREEN),
    ("3 Description > Device Information (1)", Scope.SCREEN),
    ("3 Description > Table of Contents", Scope.SKIP),
    ("4 Revision History", Scope.SKIP),
    ("4 Revision History > Changes from Revision C (March 2019) to Revision D", Scope.SKIP),
    ("5 Pin Configuration and Functions", Scope.FULL),
    ("6 Specifications > 6.1 Absolute Maximum Ratings", Scope.FULL),
    ("6 Specifications > 6.2 ESD Ratings", Scope.SCREEN),
    ("6 Specifications > 6.3 Recommended Operating Conditions", Scope.FULL),
    ("6 Specifications > 6.4 Thermal Information", Scope.SCREEN),
    ("6 Specifications > 6.5 Electrical Characteristics", Scope.SCREEN),
    ("6 Specifications > 6.6 Switching Characteristics", Scope.SCREEN),
    ("6 Specifications > 6.7 Two-Wire Interface Timing", Scope.FULL),
    ("6 Specifications > 6.8 Timing Diagram", Scope.SCREEN),
    ("6 Specifications > 6.9 Typical Characteristics (continued)", Scope.SCREEN),
    ("7 Detailed Description > 7.1 Overview", Scope.SCREEN),
    ("7 Detailed Description > 7.3 Feature Description > 7.3.1 Power Up", Scope.SCREEN),
    ("7 Detailed Description > 7.5 Programming > 7.5.1 EEPROM Programming", Scope.SCREEN),
    ("7 Detailed Description > 7.6 Register Map > 7.6.7 EEPROM1 Register", Scope.SCREEN),
    ("8 Application and Implementation > 8.1 Application Information", Scope.FULL),
    ("8 Application and Implementation > 8.2 Typical Application", Scope.FULL),
    ("9 Power Supply Recommendations", Scope.FULL),
    ("10 Layout > 10.1 Layout Guidelines", Scope.FULL),
    ("11 Device and Documentation Support > 11.4 Trademarks", Scope.SKIP),
    ("11 Device and Documentation Support > 11.5 Electrostatic Discharge Caution", Scope.SKIP),
    ("12 Mechanical, Packaging, and Orderable Information > PACKAGE OUTLINE", Scope.SKIP),
    ("12 Mechanical, Packaging, and Orderable Information > IMPORTANT NOTICE", Scope.SKIP),
]
ST_STYLE = [
    ("1 Description", Scope.SCREEN),
    ("2 Pin description", Scope.FULL),
    ("3 Functional description > 3.1 Power-on reset", Scope.SCREEN),
    ("4 Electrical characteristics > 4.1 Absolute maximum ratings", Scope.FULL),
    ("4 Electrical characteristics > 4.2 Operating conditions", Scope.FULL),
    ("4 Electrical characteristics > 4.3 DC characteristics", Scope.SCREEN),
    ("5 Application information", Scope.FULL),
    ("6 Package information > 6.1 SO8 package information", Scope.SKIP),
    ("7 Ordering information", Scope.SKIP),
    ("8 Revision history", Scope.SKIP),
    ("List of tables", Scope.SKIP),
]
MICROCHIP_STYLE = [
    ("Features", Scope.SCREEN),
    ("Description", Scope.SCREEN),
    ("1.0 Electrical Characteristics", Scope.SCREEN),
    ("1.0 Electrical Characteristics > Absolute Maximum Ratings", Scope.FULL),
    ("2.0 Typical Performance Curves", Scope.FULL),
    ("4.0 Device Overview > 4.1 Register Description", Scope.SCREEN),
    ("5.0 Applications Information > 5.1 Layout Considerations", Scope.FULL),
    ("6.0 Packaging Information > 6.1 Package Marking Information", Scope.SKIP),
    ("Appendix A: Revision History", Scope.FULL),
    ("Product Identification System", Scope.SKIP),
]


@pytest.mark.parametrize(("section", "scope"), TI_STYLE + ST_STYLE + MICROCHIP_STYLE)
def test_section_scope_on_datasheet_tables_of_contents(section: str, scope: Scope) -> None:
    assert RULES.scope(section)[0] is scope


@pytest.mark.parametrize(
    "section",
    [
        "7 Detailed Description",
        "7 Detailed Description > 7.3 Feature Description",
        "8 Application and Implementation > 8.2 Typical Application",
    ],
)
def test_description_and_features_never_match_inside_a_longer_heading(section: str) -> None:
    # The rules for "Description", "Features" and "Applications" are top-level ones;
    # "Detailed Description" is screened by its own rule, not by matching "description".
    decided_by = RULES.scope(section)[1]
    assert decided_by is None or decided_by.endswith("Detailed Description")


def test_top_level_rules_apply_only_to_the_first_heading() -> None:
    assert RULES.scope("3 Description")[0] is Scope.SCREEN
    assert RULES.scope("8 Application and Implementation > Description")[0] is Scope.FULL
    assert RULES.scope("Task 2 > Features")[0] is Scope.FULL


def test_heading_text_drops_number_continued_and_footnote_marks() -> None:
    assert heading_text("6.9 Typical Characteristics (continued)") == "typical characteristics"
    assert heading_text("Device Information (1)") == "device information"
    assert heading_text("A. Legal Notice:") == "legal notice"


@pytest.mark.parametrize(
    "section",
    [
        "6 Specifications > 6.1 Absolute Maximum Ratings",
        "8 Application and Implementation > 8.4 Layout > 8.4.1 Layout Guidelines",
        "Task 2: Filter chips",
        "",
    ],
)
def test_sections_that_can_hold_items_are_read_in_full(section: str) -> None:
    assert RULES.scope(section) == (Scope.FULL, None)


def test_section_rules_are_configurable() -> None:
    rules = SectionRules(skip=("task 2: filter chips",), screen=(), screen_top=())
    assert rules.scope("Task 2: Filter chips") == (Scope.SKIP, "Task 2: Filter chips")
    assert SectionRules(skip=(), screen=(), screen_top=()).scope("1 Features")[0] is Scope.FULL


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


@pytest.mark.parametrize(
    "text",
    [
        # After a purpose phrase without a comma.
        "To minimize leakage to surrounding air locate the sensor where the air is still.",
        "For best accuracy place the sensor close to the heat source.",
        "To support traceability do not delete the EEPROM1 register.",
        # Verbs outside the verb list, recognised by their form and what follows them.
        "Miniaturize the board to reduce thermal mass.",
        "If the board has unused internal layers, extend these layers under the sensor.",
        "Design PCB soldering pads bigger than usual.",
        "Only reprogram the general-purpose locations if traceability is not needed.",
        "After programming the data, issue a general-call reset command.",
        "To avoid the timeout, maintain a clock of at least 1 kHz.",
    ],
)
def test_instructions_without_a_comma_or_outside_the_verb_list(text: str) -> None:
    assert is_imperative(text)
    assert classify(text, "10 Layout") == Kind.OBLIGATION


@pytest.mark.parametrize(
    "text",
    [
        "When a filter matches nothing the empty state says so.",
        "To support traceability the device stores a unique ID.",
        "For a write operation the controller sends the address byte.",
        "Like the TS-100, the device uses a two-wire interface.",
        "Given the low supply current, self-heating is negligible.",
        "Only the TS-100 supports this mode.",
        "Reading the result register clears the flag.",
        "Typical PCB layouts use two layers.",
        "Uses the same pinout as the TS-100.",
        "Parameter MIN MAX UNIT",
        "This register is a 16-bit, read-only register that stores the result.",
    ],
)
def test_descriptions_stay_out_with_the_lexical_check(text: str) -> None:
    assert not is_imperative(text)
    assert classify(text, "7 Detailed Description") is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("A 0.1-µF bypass capacitor is recommended between V+ and GND.", True),
        ("Higher averaging numbers are recommended in noisy systems.", True),
        ("It is strongly recommended to solder the thermal pad.", True),
        ("Pullup resistors should be used on long buses.", True),
        ("The ADD0 pin should not be left floating.", True),
        ("It should be noted that the result is in two's complement.", False),
        ("Operation beyond the Recommended Operating Conditions is not implied.", False),
        ("The result should be 8000h after reset.", False),
    ],
)
def test_passive_recommendations(text: str, expected: bool) -> None:
    assert is_passive_recommendation(text) is expected
    if expected:
        assert classify(text, "8 Application") == Kind.OBLIGATION


def test_screening_keeps_only_sentences_that_could_be_obligations() -> None:
    text = (
        "The TS-100 measures temperature with a diode. Wait at least 2 ms after power-up "
        "before the first read.\n"
        "- Do not write the configuration register during a conversion.\n"
        "| Register | Reset |\n"
        "It reports the result with 16 bits."
    )
    assert screened_text(text).splitlines() == [
        "Wait at least 2 ms after power-up before the first read.",
        "Do not write the configuration register during a conversion.",
    ]
    assert worth_screening("The bus must be controlled by a master device.")
    assert worth_screening("Avoid conversions while the EEPROM is unlocked.")
    assert not worth_screening("The device acknowledges each data byte.")
