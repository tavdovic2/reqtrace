"""The ingestion noise filter: furniture and labels out, short content in."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ingestion import Block
from app.noise import NoiseSettings, carries_content, filter_noise, word_count
from app.services import Services
from tests.conftest import make_pdf, upload

DEFAULTS = NoiseSettings(min_words=1, repeated_min_pages=3, repeated_max_chars=60)
HEADER = "NX310 SNXD01A - MARCH 2024 - REVISED JUNE 2025"


def datasheet_blocks() -> list[Block]:
    """Four pages of a datasheet-like document with furniture, labels and short content."""
    blocks: list[Block] = []
    for page in range(1, 5):
        blocks.append(Block("text", HEADER, page))
        blocks.append(Block("text", "www.example.com", page))
    blocks += [
        Block("heading", "6.1 Absolute Maximum Ratings", 1, level=2),
        Block("table", "| VDD | 1.8 V |", 1),
        Block("text", "The supply voltage must stay between 1.8 V and 3.6 V.", 1),
        Block("heading", "10 Layout", 2, level=1),
        Block("text", "Ground the thermal pad.", 2),
        Block("text", "- Thermostats", 2),
        Block("heading", "12 Mechanical Data", 4, level=1),
        Block("text", "SCALE 5.500", 4),
        Block("text", "23-Oct-2026", 4),
        Block("text", "YBG0006-C01", 4),
        Block("text", "42", 4),
    ]
    return blocks


@pytest.mark.parametrize(
    "text",
    [HEADER, "www.example.com", "SCALE 5.500", "23-Oct-2026", "YBG0006-C01", "42"],
)
def test_noise_is_dropped(text: str) -> None:
    kept = {b.text for b in filter_noise(datasheet_blocks(), DEFAULTS).blocks}
    assert text not in kept


@pytest.mark.parametrize(
    "text",
    [
        "| VDD | 1.8 V |",
        "Ground the thermal pad.",
        "- Thermostats",
        "The supply voltage must stay between 1.8 V and 3.6 V.",
        "6.1 Absolute Maximum Ratings",
    ],
)
def test_short_content_is_kept(text: str) -> None:
    kept = {b.text for b in filter_noise(datasheet_blocks(), DEFAULTS).blocks}
    assert text in kept


def test_filter_reports_what_it_dropped() -> None:
    result = filter_noise(datasheet_blocks(), DEFAULTS)
    assert (result.repeated, result.low_content) == (8, 4)


def test_text_on_fewer_pages_than_the_threshold_is_not_furniture() -> None:
    blocks = [
        Block("text", "Return to Register Map.", 26),
        Block("text", "Return to Register Map.", 27),
    ]
    assert len(filter_noise(blocks, DEFAULTS).blocks) == 2


def test_repeated_text_that_is_long_is_not_furniture() -> None:
    condition = "at TA = 25°C, V+ = 3.3 V, and measurement taken in oil bath (unless noted)"
    blocks = [Block("text", condition, page) for page in (8, 9, 10)]
    assert len(filter_noise(blocks, DEFAULTS).blocks) == 3


def test_checks_can_be_turned_off() -> None:
    off = NoiseSettings(min_words=0, repeated_min_pages=0, repeated_max_chars=60)
    assert len(filter_noise(datasheet_blocks(), off).blocks) == len(datasheet_blocks())


def test_words_and_content_rules() -> None:
    assert word_count("SCALE 5.500") == 0
    assert word_count("Reel Width (W1)") == 2
    # Capitals count as words in a long paragraph such as a legal notice.
    assert word_count("TI PROVIDES TECHNICAL AND RELIABILITY DATA AS IS AND WITH ALL FAULTS") > 5
    assert carries_content("A 0.1-µF capacitor")
    assert carries_content("DO NOT TOUCH THE PAD.")
    assert not carries_content("DIE SIZE BALL GRIDARRAY")
    assert not carries_content("DRV0006B")
    assert carries_content("Supply voltage, V+ -0.3 6 V")
    assert carries_content("Accuracy ±0.1 °C")


def test_noise_is_not_indexed_but_short_content_is(
    client: TestClient, services: Services, tmp_path: Path
) -> None:
    pdf = make_pdf(tmp_path / "noise.pdf", [["placeholder"]] * 4)
    client.app.state.services.parser = lambda _path, on_page=None: datasheet_blocks()
    drawer = client.post("/drawers", json={"name": "Noise"}).json()
    document = upload(client, drawer["id"], pdf)

    indexed = " ".join(c.text for c in services.store.document_chunks(document["id"]))
    for noise in (HEADER, "www.example.com", "SCALE 5.500", "23-Oct-2026", "YBG0006-C01"):
        assert noise not in indexed
    for content in ("| VDD | 1.8 V |", "Ground the thermal pad.", "- Thermostats"):
        assert content in indexed


def test_instructions_that_differ_only_in_numbers_are_not_furniture() -> None:
    blocks = [Block("text", f"Commit the work of task {n}.", n) for n in range(1, 6)]
    blocks += [Block("text", f"Page {n} of 5", n) for n in range(1, 6)]
    kept = [b.text for b in filter_noise(blocks, DEFAULTS).blocks]
    assert kept == [f"Commit the work of task {n}." for n in range(1, 6)]
