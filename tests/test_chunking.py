import itertools

from app.ingestion import (
    Block,
    Chunk,
    chunk_blocks,
    compact_table,
    heading_level,
    split_table,
    split_text,
)


def test_text_under_one_heading_on_one_page_becomes_one_chunk() -> None:
    blocks = [
        Block("heading", "Electrical Characteristics", 3, level=1),
        Block("text", "Supply voltage is 3.3 V.", 3),
        Block("text", "Supply current is 2 mA.", 3),
    ]
    chunks = chunk_blocks(blocks, max_chars=500, overlap=50)
    assert chunks == [
        Chunk(
            "Supply voltage is 3.3 V.\nSupply current is 2 mA.",
            3,
            "Electrical Characteristics",
            "text",
        )
    ]


def test_section_continuing_on_next_page_is_split_at_the_page_boundary() -> None:
    blocks = [
        Block("heading", "Timing", 4, level=1),
        Block("text", "Start-up time is 5 ms.", 4),
        Block("text", "Conversion time is 12 ms.", 5),
    ]
    chunks = chunk_blocks(blocks, max_chars=500, overlap=50)
    assert [(c.page, c.text) for c in chunks] == [
        (4, "Start-up time is 5 ms."),
        (5, "Conversion time is 12 ms."),
    ]


def test_heading_path_follows_levels() -> None:
    blocks = [
        Block("heading", "Datasheet", 1, level=0),
        Block("heading", "Electrical", 1, level=1),
        Block("heading", "Supply", 1, level=2),
        Block("text", "VDD is 3.3 V.", 1),
        Block("heading", "Mechanical", 2, level=1),
        Block("text", "Package is DFN.", 2),
    ]
    chunks = chunk_blocks(blocks, max_chars=500, overlap=50)
    assert [c.section for c in chunks] == [
        "Datasheet > Electrical > Supply",
        "Datasheet > Mechanical",
    ]


def test_every_table_is_its_own_chunk_with_heading_prepended() -> None:
    table = "| Parameter | Max |\n|---|---|\n| Temperature | 85 C |"
    blocks = [
        Block("heading", "Ratings", 2, level=1),
        Block("text", "Exceeding these values may damage the device.", 2),
        Block("table", table, 2),
        Block("text", "Values apply at 25 C.", 2),
    ]
    chunks = chunk_blocks(blocks, max_chars=500, overlap=50)
    assert [c.kind for c in chunks] == ["text", "table", "text"]
    table_chunk = chunks[1]
    assert table_chunk.text == f"Ratings\n\n{table}"
    assert table_chunk.page == 2
    assert table_chunk.embedding_text == table_chunk.text


def test_text_chunk_embedding_text_includes_section() -> None:
    chunk = Chunk("VDD is 3.3 V.", 1, "Electrical > Supply", "text")
    assert chunk.embedding_text == "Electrical > Supply\nVDD is 3.3 V."


def test_long_text_is_split_with_overlap_and_size_limit() -> None:
    sentences = [f"Sentence number {i} describes one property of the sensor." for i in range(40)]
    text = " ".join(sentences)
    pieces = split_text(text, max_chars=300, overlap=60)
    assert len(pieces) > 1
    assert all(len(p) <= 300 for p in pieces)
    for previous, current in itertools.pairwise(pieces):
        assert previous[-30:].split()[-1] in current[:90]
    for sentence in sentences:
        assert any(sentence in p for p in pieces)


def test_split_text_handles_text_without_spaces() -> None:
    pieces = split_text("x" * 1000, max_chars=300, overlap=50)
    assert "".join(pieces).count("x") >= 1000
    assert all(len(p) <= 300 for p in pieces)


def test_long_table_is_split_by_rows_repeating_header() -> None:
    header = "| Pin | Function |\n|---|---|"
    rows = [f"| {i} | Function of pin number {i} |" for i in range(30)]
    parts = split_table("\n".join([header, *rows]), max_chars=300)
    assert len(parts) > 1
    for part in parts:
        assert part.startswith(header)
        assert len(part) <= 300
    joined = "\n".join(parts)
    assert all(row in joined for row in rows)


def test_heading_level_comes_from_numbering() -> None:
    assert heading_level("6 Communication Interface", 1) == 1
    assert heading_level("6.2 Modbus RTU", 1) == 2
    assert heading_level("6.2.1. Registers", 1) == 3
    assert heading_level("Ordering Information", 1) == 1


def test_empty_blocks_produce_no_chunks() -> None:
    assert chunk_blocks([Block("text", "   ", 1)], max_chars=100, overlap=10) == []


def test_compact_table_removes_padding() -> None:
    padded = "| Code   | Meaning      |\n|--------|--------------|\n| A      | 4 to 20 mA   |"
    assert compact_table(padded) == "| Code | Meaning |\n| --- | --- |\n| A | 4 to 20 mA |"


def test_unnumbered_heading_under_a_numbered_one_becomes_its_child() -> None:
    """Docling reports table captions and notes as level-1 headings; they must not
    replace the numbered section path."""
    blocks = [
        Block("heading", "7 Detailed Description", 1, level=1),
        Block("heading", "7.6 Register Maps", 1, level=2),
        Block("heading", "Table 7-8. High Limit Register Field Descriptions", 2, level=1),
        Block("text", "15:0 High limit R/W 6000h", 2),
        Block("heading", "Note", 2, level=1),
        Block("text", "The register resets at power-up.", 2),
        Block("heading", "8 Application and Implementation", 3, level=1),
        Block("text", "Place the device near the source.", 3),
    ]
    sections = [c.section for c in chunk_blocks(blocks, max_chars=500, overlap=50)]
    assert sections == [
        "7 Detailed Description > 7.6 Register Maps > Table 7-8. High Limit Register Field Descriptions",
        "7 Detailed Description > 7.6 Register Maps > Note",
        "8 Application and Implementation",
    ]


def test_unnumbered_headings_keep_their_levels_in_unnumbered_documents() -> None:
    blocks = [
        Block("heading", "Lab 6", 1, level=1),
        Block("heading", "Task 1", 1, level=1),
        Block("text", "Create the screen.", 1),
    ]
    assert [c.section for c in chunk_blocks(blocks, max_chars=500, overlap=50)] == ["Task 1"]


def test_repeated_chunk_text_is_indexed_once() -> None:
    from app.ingestion import Chunk, unique_chunks

    chunks = [
        Chunk("Over free-air temperature range (unless otherwise noted)", 6, "6.5", "text"),
        Chunk("Supply current 3.5 uA", 6, "6.5", "text"),
        Chunk("Over free-air  temperature range (unless otherwise noted)", 7, "6.5", "text"),
        Chunk("Return to Register Map.", 27, "7.6", "text"),
        Chunk("Return to Register Map.", 30, "7.6", "text"),
    ]
    assert [(c.text[:14], c.page) for c in unique_chunks(chunks)] == [
        ("Over free-air ", 6),
        ("Supply current", 6),
        ("Return to Regi", 27),
    ]
