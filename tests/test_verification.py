import pytest

from app.verification import (
    drop_foreign_colons,
    is_fragment,
    locate_quote,
    modality_preserved,
    normalize,
    normalize_with_map,
    requirement_support,
)

PAGE = (
    "5 Performance\r\n"
    "The transmitter shall reach its specified accuracy within 500 ms after pow-\r\n"
    "er-up. During this start-up period the analog output is held at 3.6 mA.\r\n"
    "Parameter Value\r\n"
    "Supply voltage 36 V DC\r\n"
)


def locate(quote: str, page: str = PAGE, threshold: float = 90):
    return locate_quote(quote, page, threshold=threshold, min_chars=15)


def test_normalize_joins_hyphenation_and_collapses_whitespace() -> None:
    assert normalize("pow-\r\ner-up   is\n\nFAST") == "power-up is fast"


def test_normalize_unifies_typographic_characters() -> None:
    assert normalize("“High” – ﬁne") == '"high" - fine'


def test_normalize_map_points_back_to_source_characters() -> None:
    source = "  Pow-\nER  up "
    norm = normalize_with_map(source)
    assert norm.text == "power up"
    assert len(norm.origin) == len(norm.text)
    assert source[norm.origin[0]] == "P"
    assert source[norm.origin[3]] == "E"
    assert source[norm.origin[-1]] == "p"


def test_stored_quote_is_exact_span_of_page_text() -> None:
    match = locate(
        "The transmitter shall reach its specified accuracy within 500 ms after power-up."
    )
    assert match is not None
    span = PAGE[match.start : match.end]
    assert (
        span
        == "The transmitter shall reach its specified accuracy within 500 ms after pow-\r\ner-up."
    )


def test_transcription_errors_are_replaced_by_page_text() -> None:
    match = locate("During this start-up period the analog output is held at 3.6 m:A.")
    assert match is not None
    assert (
        PAGE[match.start : match.end]
        == "During this start-up period the analog output is held at 3.6 mA."
    )


def test_table_row_quoted_as_markdown_is_found() -> None:
    match = locate("| Supply voltage | 36 V DC |")
    assert match is not None
    assert PAGE[match.start : match.end] == "Supply voltage 36 V DC"


def test_quote_cut_off_mid_sentence_is_completed_from_the_page() -> None:
    match = locate("During this start-up period the analog output")
    assert match is not None
    assert PAGE[match.start : match.end].endswith("held at 3.6 mA.")


def test_fabricated_quote_is_not_found() -> None:
    assert (
        locate("The transmitter shall withstand a supply voltage of 48 V DC indefinitely.") is None
    )


def test_changed_value_fails_a_strict_threshold() -> None:
    quote = "The transmitter shall reach its specified accuracy within 50 ms after power-up."
    assert locate(quote, threshold=99) is None


def test_quote_from_another_page_is_not_found() -> None:
    other_page = "6 Communication Interface\nThe device shall respond within 10 ms."
    assert locate("The device shall respond within 10 ms.", other_page) is not None
    assert locate("The device shall respond within 10 ms.") is None


def test_too_short_quote_is_rejected_even_if_present() -> None:
    assert locate("36 V DC") is None


def test_empty_page_finds_nothing() -> None:
    assert locate("The transmitter shall reach its specified accuracy.", "") is None


# Calibration pairs for REQUIREMENT_SUPPORT_THRESHOLD (default 0.75). Faithful
# paraphrases score 1.0 once connective words are exempt; unrelated or invented
# requirements score at most 0.5 or miss a strict term (number or identifier).
SUPPORTED = [
    (
        "Commit after every task and push the work at the end of the session.",
        "Commit after every task and push your work at the end of the session.",
    ),
    (
        "The app shall create a TrailList screen showing all trails from trails.json.",
        "Create a TrailList screen that shows all trails from trails.json.",
    ),
    (
        "The user can open a trail by tapping its row.",
        "Let the user open a trail by tapping its row.",
    ),
    (
        "A loading indicator should be added while the trails are loading.",
        "You should add a loading indicator while the trails are loading.",
    ),
    (
        "The FilterChip component must have selected, label and onPress props and accessibilityState.",
        "Build a FilterChip component with selected, label and onPress props, and give it accessibilityState.",
    ),
    (
        "The supply voltage for output A shall be between 9 and 32 V DC.",
        "Supply voltage Output A (4 to 20 mA) 9 to 32 V DC",
    ),
    (
        "The G1/4 process connection shall be tightened to 25 Nm.",
        "The G1/4 process connection shall be tightened with a torque of 25 Nm.",
    ),
]
UNSUPPORTED = [
    (
        "The supply voltage of output A shall be between 9 and 32 V DC.",
        "Pressing an item toggles its selected state and the list re-renders only that item.",
    ),
    (
        "The initial number of items to render for the first screen shall be 10.",
        "initialBatch Rows drawn before the first frame 10",
    ),
    (
        "The list must update within 200 ms.",
        "The list must update within 100 ms after a chip is pressed.",
    ),
    (
        "The app shall store trails in a local database.",
        "Create a TrailList screen that shows all trails from trails.json.",
    ),
    ("The title shall be a string.", "type Trail = { id: string; name: string; lengthKm: number }"),
]


@pytest.mark.parametrize(("requirement", "quote"), SUPPORTED)
def test_faithful_paraphrase_is_supported(requirement: str, quote: str) -> None:
    assert requirement_support(requirement, quote).passes(0.75)


@pytest.mark.parametrize(("requirement", "quote"), UNSUPPORTED)
def test_unrelated_or_invented_requirement_is_not_supported(requirement: str, quote: str) -> None:
    assert not requirement_support(requirement, quote).passes(0.75)


def test_missing_number_or_identifier_fails_regardless_of_share() -> None:
    quote = "Build a FilterChip component with selected, label and onPress props."
    support = requirement_support(
        "Build a FilterChip component with selected, label and onLongPress props.", quote
    )
    assert support.missing_strict == ["onlongpress"]
    assert not support.passes(0.0)


def test_should_must_not_become_must() -> None:
    quote = "You should add a loading indicator while the trails are loading."
    assert modality_preserved("A loading indicator should be added.", quote)
    assert not modality_preserved("A loading indicator must be added.", quote)
    assert not modality_preserved("The app shall show a menu.", "The app may show a menu.")


def test_imperative_quote_allows_obligation_wording() -> None:
    assert modality_preserved(
        "The student must commit after every task.", "Commit after every task."
    )


def test_colons_absent_from_quote_are_removed() -> None:
    quote = "The list renders 200 generated items from Labs 4 and 5."
    assert drop_foreign_colons(
        "The list renders 2:00 generated items from Labs : 4 and 5.", quote
    ) == ("The list renders 200 generated items from Labs 4 and 5.")
    assert drop_foreign_colons("Timeout: 5 s", "Timeout: 5 s") == "Timeout: 5 s"


@pytest.mark.parametrize(
    "text", ["When ScrollView is still right:", "Task 2", "Press", "the list renders,"]
)
def test_fragments_are_detected(text: str) -> None:
    assert is_fragment(text)


def test_complete_sentence_is_not_a_fragment() -> None:
    assert not is_fragment("Let the user open a trail by tapping its row.")


def test_heading_is_not_merged_with_the_next_sentence() -> None:
    page = "Absolute Maximum Ratings\r\nThe maximum operating temperature is 85 C.\r\n"
    match = locate("Absolute Maximum Ratings", page)
    assert match is not None
    assert page[match.start : match.end] == "Absolute Maximum Ratings"


def test_table_row_match_does_not_spill_into_neighbouring_rows() -> None:
    page = (
        "Code Meaning\r\nA Output 4 to 20 mA, 2-wire, with HART 7\r\nV Output 0 to 10 V, 3-wire\r\n"
        "M Modbus RTU over RS-485, 4-wire\r\n"
    )
    match = locate("A; Output 4 to 20 mA, 2-wire; with HART 7", page)
    assert match is not None
    assert page[match.start : match.end] == "A Output 4 to 20 mA, 2-wire, with HART 7"
    match = locate("M; Modbus RTU over RS-485; 4-wire", page)
    assert match is not None
    assert page[match.start : match.end] == "M Modbus RTU over RS-485, 4-wire"


def test_words_from_neighbouring_rows_that_the_quote_also_uses_are_trimmed() -> None:
    page = (
        "Storage temperature -40 to +100 °C\r\nMedia temperature -40 to +125 °C\r\n"
        "Ingress protection, M12 connector IP67\r\nIngress protection, cable gland option IP69K\r\n"
    )
    match = locate("Media temperature; -40 to +125 °C", page)
    assert match is not None
    assert page[match.start : match.end] == "Media temperature -40 to +125 °C"
    match = locate("Ingress protection, M12 connector; IP67", page)
    assert match is not None
    assert page[match.start : match.end] == "Ingress protection, M12 connector IP67"
