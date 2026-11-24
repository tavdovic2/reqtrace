import csv
import io
import re
import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from pydantic import BaseModel
from sqlalchemy import inspect

from app.db import create_session_factory
from app.export import COLUMNS, to_csv, to_xlsx
from app.extraction_scope import SectionRules
from app.pdf import extract_page_text
from app.requirements import (
    PLACEHOLDERS,
    Candidate,
    ExtractedItemWithParaphrase,
    ExtractionResultWithParaphrase,
    ScreenedItem,
    ScreenedResult,
    build_prompt,
    deduplicate,
    merge_sentence_parts,
    plan_extraction,
    readable_table,
    system_prompt,
)
from app.schemas import RequirementOut
from app.services import Services
from app.vector_store import ChunkPayload
from eval.extraction_fixtures import LAB, render
from tests.conftest import FakeLLM, make_pdf, upload

REAL_QUOTE = "The supply voltage shall not exceed 3.6 V."
LIMIT_QUOTE = "The maximum operating temperature is 85 C."
FABRICATED_QUOTE = "The sensor shall survive immersion in sea water for 30 days."
OLD_PROMPT_EXAMPLE = "The supply voltage of output A shall be between 9 and 32 V DC."


def item(quote: str, paraphrase: str = "", kind: str = "electrical") -> ExtractedItemWithParaphrase:
    return ExtractedItemWithParaphrase(quote=quote, paraphrase=paraphrase, type=kind)


def reply(*items: ExtractedItemWithParaphrase) -> Callable[[str], ExtractionResultWithParaphrase]:
    """Fake LLM reply: the items for any prompt that contains their quote's page."""
    return lambda _prompt: ExtractionResultWithParaphrase(requirements=list(items))


def extract(
    client: TestClient, fake_llm: FakeLLM, pdf: Path, *items: ExtractedItemWithParaphrase
) -> dict:
    drawer = client.post("/drawers", json={"name": f"D{len(fake_llm.prompts)}"}).json()
    document = upload(client, drawer["id"], pdf)
    fake_llm.structured_replies.extend([reply(*items)] * 10)
    assert client.post(f"/documents/{document['id']}/requirements").status_code == 202
    table = client.get(f"/documents/{document['id']}/requirements").json()
    assert table["status"] == "done", table["error"]
    return {**table, "document_id": document["id"]}


@pytest.fixture
def paraphrase_on(services: Services) -> None:
    services.settings.extraction_paraphrase = True


def test_out_of_scope_sections_are_skipped_before_any_model_call(
    client: TestClient, fake_llm: FakeLLM, sample_pdf: Path
) -> None:
    table = extract(client, fake_llm, sample_pdf, item(REAL_QUOTE))
    # Page 3 ("Ordering Information") is out of scope. Page 1 ("General Description") is
    # screened, and none of its sentences has obligation wording, so it needs no call.
    assert table["skipped_sections"] == 1
    assert len(fake_llm.prompts) == 1
    assert "Absolute Maximum Ratings" in fake_llm.prompts[0]


def test_obligation_is_saved_with_exact_quote_and_kind(
    client: TestClient, fake_llm: FakeLLM, sample_pdf: Path
) -> None:
    table = extract(client, fake_llm, sample_pdf, item("The supply voltage shall not exceed 3:6 V"))
    assert table["requirements"] == [
        {
            "code": "REQ-001",
            "kind": "obligation",
            "quote": REAL_QUOTE,
            "paraphrase": "",
            "type": "electrical",
            "page": 2,
            "verified": True,
            # A new row awaits review: verified (automatic) is not a decision.
            "status": "proposed",
            "edited_text": None,
            "reviewer_note": None,
            "reviewed_at": None,
            "not_found": False,
            "requirement_text": REAL_QUOTE,
            "id": table["requirements"][0]["id"],
        }
    ]
    assert table["requirements"][0]["quote"] in extract_page_text(sample_pdf, 2)
    assert table["elapsed_seconds"] is not None


def test_value_in_a_ratings_section_is_saved_as_limit(
    client: TestClient, fake_llm: FakeLLM, sample_pdf: Path
) -> None:
    table = extract(client, fake_llm, sample_pdf, item(LIMIT_QUOTE, kind="environmental"))
    assert [(r["kind"], r["quote"]) for r in table["requirements"]] == [("limit", LIMIT_QUOTE)]


def test_fabricated_quote_is_rejected_as_not_on_page(
    client: TestClient, fake_llm: FakeLLM, sample_pdf: Path
) -> None:
    table = extract(client, fake_llm, sample_pdf, item(FABRICATED_QUOTE))
    assert table["requirements"] == []
    assert (table["rejected_quote_not_found"], table["rejected_not_requirement"]) == (1, 0)


def test_descriptive_sentence_is_never_extracted(
    tmp_path: Path, client: TestClient, fake_llm: FakeLLM
) -> None:
    pdf = make_pdf(
        tmp_path / "layout.pdf",
        [
            [
                "Layout Guidelines",
                "A thin board lets the sensor follow temperature changes faster.",
                "Place the sensor close to the heat source.",
            ]
        ],
    )
    table = extract(
        client,
        fake_llm,
        pdf,
        item("A thin board lets the sensor follow temperature changes faster.", kind="other"),
        item("Place the sensor close to the heat source.", kind="other"),
    )
    assert [(r["kind"], r["quote"]) for r in table["requirements"]] == [
        ("obligation", "Place the sensor close to the heat source.")
    ]
    assert table["rejected_not_requirement"] == 1


@pytest.mark.usefixtures("paraphrase_on")
def test_paraphrase_that_invents_obligation_is_removed_and_row_kept(
    client: TestClient, fake_llm: FakeLLM, sample_pdf: Path
) -> None:
    table = extract(
        client,
        fake_llm,
        sample_pdf,
        item(LIMIT_QUOTE, "The maximum operating temperature must be 85 C."),
    )
    assert [(r["kind"], r["paraphrase"]) for r in table["requirements"]] == [("limit", "")]
    assert table["paraphrases_removed"] == 1
    assert all(
        not re.search(r"\b(must|shall)\b", r["paraphrase"])
        or re.search(r"\b(must|shall)\b", r["quote"])
        for r in table["requirements"]
    )


@pytest.mark.usefixtures("paraphrase_on")
def test_genuine_item_with_unsupported_paraphrase_is_kept_with_quote_only(
    client: TestClient, fake_llm: FakeLLM, sample_pdf: Path
) -> None:
    table = extract(
        client,
        fake_llm,
        sample_pdf,
        item(REAL_QUOTE, "Each chip must have a minimum height of 44."),
    )
    assert [(r["quote"], r["paraphrase"]) for r in table["requirements"]] == [(REAL_QUOTE, "")]
    assert table["rejected"] == 0


@pytest.mark.usefixtures("paraphrase_on")
def test_faithful_paraphrase_is_kept(
    client: TestClient, fake_llm: FakeLLM, sample_pdf: Path
) -> None:
    table = extract(
        client, fake_llm, sample_pdf, item(REAL_QUOTE, "The supply voltage shall not exceed 3.6 V.")
    )
    assert table["requirements"][0]["paraphrase"] == "The supply voltage shall not exceed 3.6 V."
    assert table["paraphrases_removed"] == 0


@pytest.mark.usefixtures("paraphrase_on")
def test_text_copied_from_a_prompt_example_never_reaches_the_table(
    client: TestClient, fake_llm: FakeLLM, sample_pdf: Path
) -> None:
    table = extract(
        client,
        fake_llm,
        sample_pdf,
        item(REAL_QUOTE, OLD_PROMPT_EXAMPLE),
        item(PLACEHOLDERS[0], PLACEHOLDERS[1], "other"),
    )
    assert [r["paraphrase"] for r in table["requirements"]] == [""]
    assert table["rejected_quote_not_found"] == 1


@pytest.mark.parametrize("paraphrase", [True, False])
def test_prompt_shows_the_format_with_placeholders_only(paraphrase: bool) -> None:
    prompt = system_prompt(paraphrase)
    format_line = prompt.split("Output format")[1]
    expected = PLACEHOLDERS if paraphrase else (PLACEHOLDERS[0], PLACEHOLDERS[2])
    assert tuple(re.findall(r'": "([^"]+)"', format_line)) == expected
    lowered = prompt.lower()
    assert "e.g." not in lowered and "for example" not in lowered
    assert "supply voltage" not in lowered
    assert ('"paraphrase"' in prompt) is paraphrase


def test_one_sentence_yields_one_row(
    client: TestClient, fake_llm: FakeLLM, sample_pdf: Path
) -> None:
    table = extract(
        client, fake_llm, sample_pdf, item(REAL_QUOTE), item("The supply voltage shall not exceed")
    )
    assert [r["quote"] for r in table["requirements"]] == [REAL_QUOTE]


def test_headings_and_learning_objectives_are_not_requirements(
    tmp_path: Path, client: TestClient, fake_llm: FakeLLM
) -> None:
    objective = "After this lab, students should be able to explain how list virtualisation works."
    task = "Commit after every task and push your work at the end of the session."
    pdf = make_pdf(tmp_path / "lab.pdf", [["Lab 6", objective, task]])
    table = extract(
        client,
        fake_llm,
        pdf,
        item(objective, kind="other"),
        item(task, kind="process"),
        item("Lab 6"),
    )
    assert [(r["kind"], r["type"]) for r in table["requirements"]] == [("obligation", "process")]
    assert table["rejected_not_requirement"] == 1
    assert table["rejected_quote_not_found"] == 1


def test_stored_quotes_are_shown_and_exported_on_one_line(
    tmp_path: Path, client: TestClient, fake_llm: FakeLLM
) -> None:
    long_sentence = (
        "Avoid using small-value pullup resistors on the SDA and ALERT pins because they increase "
        "the current drawn by the bus and make the measured temperature drift upwards over time."
    )
    pdf = make_pdf(tmp_path / "wrap.pdf", [["Layout Guidelines", long_sentence]])
    assert "\n" in extract_page_text(pdf, 1).split("Guidelines", 1)[1].strip()
    table = extract(client, fake_llm, pdf, item(long_sentence))
    quote = table["requirements"][0]["quote"]
    assert quote == long_sentence
    assert "\n" not in quote and "\r" not in quote
    exported = client.get(
        f"/documents/{table['document_id']}/requirements?format=csv&scope=all"
    ).text
    rows = list(csv.reader(io.StringIO(exported.lstrip("﻿"))))
    assert rows[1][COLUMNS.index("Quote (verbatim)")] == long_sentence


def test_types_from_a_mixed_fixture_survive_the_pipeline(
    tmp_path: Path, client: TestClient, fake_llm: FakeLLM
) -> None:
    """The lab fixture labels six different types; they must be stored as given."""
    pdf = render(LAB, tmp_path / "lab.pdf")

    def answer(prompt: str) -> ExtractionResultWithParaphrase:
        flat = " ".join(prompt.split())
        found = [
            item(e.sentence, kind=e.type)
            for e in LAB.genuine
            if " ".join(e.sentence.split()[:5]) in flat
        ]
        return ExtractionResultWithParaphrase(requirements=found)

    drawer = client.post("/drawers", json={"name": "Labs"}).json()
    document = upload(client, drawer["id"], pdf)
    fake_llm.structured_replies.extend([answer] * 20)
    client.post(f"/documents/{document['id']}/requirements")
    table = client.get(f"/documents/{document['id']}/requirements").json()
    assert {"process", "electrical", "safety", "interface"} <= {
        r["type"] for r in table["requirements"]
    }


def test_concurrent_calls_give_the_same_rows(
    client: TestClient, fake_llm: FakeLLM, services: Services, tmp_path: Path
) -> None:
    pages = [
        [f"Task {n}", f"Commit the work of task {n} before the end of the session."]
        for n in range(1, 6)
    ]
    pdf = make_pdf(tmp_path / "tasks.pdf", pages)
    services.settings.extraction_group_chars = 500

    def answer(prompt: str) -> ExtractionResultWithParaphrase:
        quotes = re.findall(r"Commit the work of task \d+ before the end of the session\.", prompt)
        return ExtractionResultWithParaphrase(
            requirements=[item(q, kind="process") for q in quotes]
        )

    results = []
    for workers in (1, 2):
        services.settings.extraction_concurrency = workers
        drawer = client.post("/drawers", json={"name": f"Tasks {workers}"}).json()
        document = upload(client, drawer["id"], pdf)
        fake_llm.structured_replies.extend([answer] * 10)
        client.post(f"/documents/{document['id']}/requirements")
        table = client.get(f"/documents/{document['id']}/requirements").json()
        results.append([(r["page"], r["quote"]) for r in table["requirements"]])
    assert results[0] == results[1]
    assert len(results[0]) == 5


def chunk(
    section: str, text: str, page: int = 1, kind: str = "text", index: int = 0
) -> ChunkPayload:
    return ChunkPayload(1, 1, "a.pdf", page, section, kind, text, index)


def test_plan_skips_sections_and_tables_and_groups_by_top_section() -> None:
    chunks = [
        chunk("1 Features", "High accuracy.", 1, index=0),
        chunk(
            "4 Revision History",
            "| Revision | Date | Change |\n| --- | --- | --- |\n| B | 2026 | x |",
            2,
            "table",
            1,
        ),
        chunk(
            "5 Specifications > 5.1 Absolute Maximum Ratings",
            "| Parameter | MIN | MAX |\n| --- | --- | --- |\n| V+ | -0.3 | 6 |",
            2,
            "table",
            2,
        ),
        chunk(
            "5 Specifications > 5.2 Recommended Operating Conditions",
            "| Parameter | MIN | MAX |\n| --- | --- | --- |\n| V+ | 1.8 | 5.5 |",
            2,
            "table",
            3,
        ),
        chunk("7 Application > 7.1 Layout", "Place the device near the source.", 4, index=4),
        chunk(
            "7 Application > 7.2 Troubleshooting",
            "| Problem | Fix |\n| --- | --- |\n| No ack | Check the address |",
            4,
            "table",
            5,
        ),
        chunk("7 Application > 7.3 Power", "Use a 0.1 uF capacitor." * 200, 5, index=6),
    ]
    plan = plan_extraction(chunks, SectionRules(), group_chars=1000)
    assert [[c.chunk_index for c in g.chunks] for g in plan.groups] == [[4], [6]]
    assert [c.chunk_index for c in plan.limit_tables] == [2, 3]
    assert len(plan.skipped) == 2
    assert any("troubleshooting" in s for s in plan.skipped)
    # "High accuracy." has no obligation wording: the section is screened, nothing is sent.
    assert plan.screened == ["section '1 Features'"]


def test_group_prompt_names_every_page_and_has_no_pipes() -> None:
    markdown = "| Prop | Meaning | Value |\n| --- | --- | --- |\n| x | Rows drawn first | 10 |"
    assert readable_table(markdown) == "Columns: Prop; Meaning; Value\n- x; Rows drawn first; 10"
    plan = plan_extraction(
        [
            chunk("6 Specs > 6.1 Pin Functions", markdown, 2, "table", 0),
            chunk("6 Specs > 6.2 Pins", "Text.", 3, index=1),
        ],
        SectionRules(skip=(), screen=(), screen_top=()),
        group_chars=5000,
    )
    prompt = build_prompt(plan.groups[0])
    assert "Excerpt (page 2)" in prompt and "Excerpt (page 3)" in prompt
    assert "|" not in prompt


def cand(quote: str, start: int, end: int, page: int = 2, paraphrase: str = "") -> Candidate:
    return Candidate("obligation", paraphrase, "electrical", page, quote, start, end)


def test_deduplicate_keeps_one_row_per_quote_and_never_merges_different_numbers() -> None:
    kept = deduplicate(
        [
            cand("The supply voltage shall not exceed 3.6 V.", 0, 40),
            cand("supply voltage shall not exceed 3.6 V.", 4, 40),
            cand("The supply voltage shall not exceed 5.5 V.", 200, 240),
            cand("The supply voltage shall not exceed 3.6 V.", 0, 40, page=3),
        ],
        threshold=92,
    )
    assert [(c.page, c.start) for c in kept] == [(2, 0), (2, 200)]


def test_two_parts_of_one_sentence_become_one_row() -> None:
    page = "Build a FilterChip with selected and label props, and give it accessibilityState. Commit often."
    first_end = page.index(",")
    second = page.index("and give")
    end = page.index(".") + 1
    parts = [
        cand(page[:first_end], 0, first_end, 1, "Build a FilterChip."),
        cand(page[second:end], second, end, 1),
        cand("Commit often.", page.index("Commit"), len(page), 1),
    ]
    merged = merge_sentence_parts(parts, lambda _page: page)
    assert [c.quote for c in merged] == [page[:end], "Commit often."]
    assert merged[0].paraphrase == "Build a FilterChip."


def test_consecutive_table_rows_and_list_items_stay_separate() -> None:
    rows_page = "Supply voltage 36 V DC\r\nBurst pressure 5 x full scale\r\n"
    second = rows_page.index("Burst")
    rows = [
        cand("Supply voltage 36 V DC", 0, 22, 3),
        cand(rows_page[second:-2], second, len(rows_page) - 2, 3),
    ]
    assert len(merge_sentence_parts(rows, lambda _page: rows_page)) == 2
    list_page = "Key features: measuring ranges up to 400 bar; accuracy of 0.1 % of full scale."
    split = list_page.index(";")
    accuracy = list_page.index("accuracy")
    items = [
        cand(list_page[14:split], 14, split, 1),
        cand(list_page[accuracy:], accuracy, len(list_page), 1),
    ]
    assert len(merge_sentence_parts(items, lambda _page: list_page)) == 2


def test_codes() -> None:
    from app.review import code_number, format_code

    assert [format_code(n) for n in (1, 2, 999, 1000)] == [
        "REQ-001",
        "REQ-002",
        "REQ-999",
        "REQ-1000",
    ]
    assert code_number("REQ-1000") == 1000


ROWS = [
    RequirementOut(
        id=1,
        code="REQ-001",
        kind="obligation",
        quote=REAL_QUOTE,
        paraphrase='Supply, max 3.6 V; "strict"',
        type="electrical",
        page=2,
        verified=True,
        status="accepted",
    )
]


def test_csv_export_keeps_verified_and_review_status_apart() -> None:
    content = to_csv(ROWS)
    assert content.startswith("﻿")
    rows = list(csv.reader(io.StringIO(content.lstrip("﻿"))))
    assert rows == [
        COLUMNS,
        [
            "REQ-001",
            "obligation",
            "electrical",
            'Supply, max 3.6 V; "strict"',
            "no",
            REAL_QUOTE,
            "2",
            'Supply, max 3.6 V; "strict"',
            "yes",
        ],
    ]
    assert "Verified (quote on page)" in COLUMNS and "Review status" not in COLUMNS


def test_xlsx_export_contains_rows() -> None:
    sheet = load_workbook(io.BytesIO(to_xlsx(ROWS))).active
    values = [[cell.value for cell in row] for row in sheet.iter_rows()]
    assert values[0] == COLUMNS
    assert values[1][:7] == [
        "REQ-001",
        "obligation",
        "electrical",
        'Supply, max 3.6 V; "strict"',
        "no",
        REAL_QUOTE,
        2,
    ]


def test_downloads_and_rerun(client: TestClient, fake_llm: FakeLLM, sample_pdf: Path) -> None:
    table = extract(client, fake_llm, sample_pdf, item(REAL_QUOTE))
    doc = table["document_id"]
    csv_response = client.get(f"/documents/{doc}/requirements?format=csv&scope=all")
    assert "sensor_requirements_all.csv" in csv_response.headers["content-disposition"]
    assert REAL_QUOTE in csv_response.text
    assert client.get(f"/documents/{doc}/requirements?format=xlsx").content[:2] == b"PK"
    fake_llm.structured_replies.extend([reply(item(REAL_QUOTE))] * 10)
    client.post(f"/documents/{doc}/requirements")
    rerun = client.get(f"/documents/{doc}/requirements").json()
    assert [r["code"] for r in rerun["requirements"]] == ["REQ-001"]


def test_invalid_llm_output_marks_extraction_failed(
    client: TestClient, fake_llm: FakeLLM, sample_pdf: Path
) -> None:
    from app.llm import LLMOutputError

    def broken(_prompt: str) -> ExtractionResultWithParaphrase:
        raise LLMOutputError("The language model returned invalid JSON twice (bad).")

    drawer = client.post("/drawers", json={"name": "Sensors"}).json()
    document = upload(client, drawer["id"], sample_pdf)
    fake_llm.structured_replies.append(broken)
    client.post(f"/documents/{document['id']}/requirements")
    table = client.get(f"/documents/{document['id']}/requirements").json()
    assert table["status"] == "failed"
    assert "invalid JSON" in table["error"]
    assert table["requirements"] == []


def test_extraction_on_unknown_document_is_404(client: TestClient) -> None:
    assert client.post("/documents/999/requirements").status_code == 404
    assert client.get("/documents/999/requirements").status_code == 404


def test_database_from_an_older_version_gets_new_columns(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE documents (id INTEGER PRIMARY KEY, drawer_id INTEGER, filename VARCHAR, "
            "page_count INTEGER, status VARCHAR, error TEXT, created_at DATETIME, expires_at DATETIME, "
            "extraction_status VARCHAR, extraction_error TEXT, extraction_done_chunks INTEGER, "
            "extraction_total_chunks INTEGER, requirements_rejected INTEGER)"
        )
        connection.execute(
            "CREATE TABLE requirements (id INTEGER PRIMARY KEY, document_id INTEGER, code VARCHAR, "
            "text TEXT, type VARCHAR, page INTEGER, quote TEXT, verified BOOLEAN, created_at DATETIME)"
        )
    factory = create_session_factory(f"sqlite:///{path.as_posix()}")
    inspector = inspect(factory.kw["bind"])
    document_columns = {c["name"] for c in inspector.get_columns("documents")}
    assert {
        "rejected_quote_not_found",
        "rejected_unsupported",
        "extraction_skipped_sections",
        "paraphrases_removed",
        "extraction_started_at",
    } <= document_columns
    assert "kind" in {c["name"] for c in inspector.get_columns("requirements")}


def test_table_row_quoted_in_the_prompt_format_is_found(
    tmp_path: Path, client: TestClient, fake_llm: FakeLLM
) -> None:
    pdf = make_pdf(
        tmp_path / "ratings.pdf",
        [["Absolute Maximum Ratings", "Parameter MIN MAX UNIT", "Supply voltage, V+ -0.3 6 V"]],
    )
    table = extract(client, fake_llm, pdf, item("- Supply voltage, V+; -0.3; 6; V"))
    assert [(r["kind"], r["quote"]) for r in table["requirements"]] == [
        ("limit", "Supply voltage, V+ -0.3 6 V")
    ]


def test_table_row_quote_is_widened_to_the_whole_row() -> None:
    from app.requirements import whole_lines

    page = "Parameter MIN NOM MAX UNIT\r\nV+ Supply voltage, TA = -55 C to 150 C 1.8 3.3 5.5 V\r\nTA Operating -55 150 C\r\n"
    start = page.index("Supply voltage")
    end = page.index("150 C") + len("150 C")
    a, b = whole_lines(page, start, end)
    assert page[a:b] == "V+ Supply voltage, TA = -55 C to 150 C 1.8 3.3 5.5 V"


def test_label_of_a_table_row_is_saved_with_the_values_of_the_row() -> None:
    """The model often quotes only the label cell; the stored quote is the whole row."""
    from app.requirements import Group, judge

    page = "Recommended Operating Conditions\r\nMIN NOM MAX UNIT\r\nV+ Supply voltage, TA = -55 C to 150 C 1.8 3.3 5.5 V\r\n"
    note = chunk(
        "6 Specs > 6.3 Recommended Operating Conditions",
        "Over free-air temperature range.",
        5,
        index=0,
    )
    rows = chunk(
        "6 Specs > 6.3 Recommended Operating Conditions",
        "| | | MIN | NOM | MAX | UNIT |\n| --- | --- | --- | --- | --- | --- |\n| V+ | Supply voltage, TA = -55 C to 150 C | 1.8 | 3.3 | 5.5 | V |",
        5,
        "table",
        1,
    )

    class _Settings:
        quote_match_threshold = 90
        quote_min_chars = 15
        requirement_support_threshold = 0.75

    class _Svc:
        settings = _Settings()

    verdict, _ = judge(
        item("Supply voltage, TA = -55 C to 150 C"), Group([note, rows]), lambda _p: page, _Svc()
    )  # type: ignore[arg-type]
    assert isinstance(verdict, Candidate)
    assert (verdict.kind, verdict.quote) == (
        "limit",
        "V+ Supply voltage, TA = -55 C to 150 C 1.8 3.3 5.5 V",
    )


def test_ratings_table_rows_are_limits_without_a_model_call(
    tmp_path: Path, client: TestClient, fake_llm: FakeLLM
) -> None:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Absolute Maximum Ratings", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    for line in (
        "Parameter MIN MAX UNIT",
        "Supply voltage, V+ -0.3 6 V",
        "Storage temperature -65 150 C",
    ):
        pdf.cell(0, 6, line, new_x="LMARGIN", new_y="NEXT")
    path = tmp_path / "ratings.pdf"
    pdf.output(str(path))

    def table_parser(_path: Path, on_page: object = None) -> list:
        from app.ingestion import Block

        markdown = "| Parameter | MIN | MAX | UNIT |\n| --- | --- | --- | --- |\n| Supply voltage, V+ | -0.3 | 6 | V |\n| Storage temperature | -65 | 150 | C |"
        return [Block("heading", "Absolute Maximum Ratings", 1), Block("table", markdown, 1)]

    client.app.state.services.parser = table_parser
    drawer = client.post("/drawers", json={"name": "Ratings"}).json()
    document = upload(client, drawer["id"], path)
    client.post(f"/documents/{document['id']}/requirements")
    table = client.get(f"/documents/{document['id']}/requirements").json()
    assert [(r["kind"], r["type"], r["quote"]) for r in table["requirements"]] == [
        ("limit", "electrical", "Supply voltage, V+ -0.3 6 V"),
        ("limit", "other", "Storage temperature -65 150 C"),
    ]
    assert fake_llm.prompts == []


def test_testing_and_committing_instructions_get_the_process_type() -> None:
    from app.requirements import Group, judge

    text = "Test long presses on your phone.\r\nAdd a loading indicator to the list.\r\n"
    task = chunk("Task 2", text, 1, index=0)

    class _Settings:
        quote_match_threshold = 90
        quote_min_chars = 15
        requirement_support_threshold = 0.75

    class _Svc:
        settings = _Settings()

    tested, _ = judge(
        item("Test long presses on your phone.", kind="functional"),
        Group([task]),
        lambda _p: text,
        _Svc(),  # type: ignore[arg-type]
    )
    added, _ = judge(
        item("Add a loading indicator to the list.", kind="functional"),
        Group([task]),
        lambda _p: text,
        _Svc(),  # type: ignore[arg-type]
    )
    assert isinstance(tested, Candidate) and tested.type == "process"
    assert isinstance(added, Candidate) and added.type == "functional"


def test_screened_section_sends_only_sentences_with_obligation_wording() -> None:
    chunks = [
        chunk(
            "7 Detailed Description > 7.5 Programming",
            "The device stores a unique ID. Do not write the EEPROM during a conversion.",
            18,
            index=0,
        ),
        chunk(
            "7 Detailed Description > 7.6 Register Map",
            "| Address | Register | Reset |\n| --- | --- | --- |\n| 00h | TEMP | 8000h |",
            25,
            "table",
            1,
        ),
        chunk(
            "10 Layout > 10.1 Layout Guidelines", "Place the device near the source.", 36, index=2
        ),
    ]
    plan = plan_extraction(chunks, SectionRules(), group_chars=3000)
    assert [(g.screened, [c.text for c in g.chunks]) for g in plan.groups] == [
        (False, ["Place the device near the source."]),
        (True, ["Do not write the EEPROM during a conversion."]),
    ]
    assert plan.screened == ["section '7 Detailed Description > 7.5 Programming'"]
    assert any("Register Map" in s for s in plan.skipped)


def test_obligation_in_a_descriptive_section_is_extracted_but_its_limits_are_not(
    client: TestClient, fake_llm: FakeLLM, tmp_path: Path
) -> None:
    obligation = "To keep the accuracy, do not place the TH-300 next to power parts."
    limit_like = "The TH-300 draws at least 5 mA during a conversion."
    pdf = make_pdf(
        tmp_path / "described.pdf",
        [
            ["3 Description", "The TH-300 is a digital sensor.", obligation, limit_like],
            ["9 Layout", "Place the TH-300 close to the heat source."],
        ],
    )

    def answer(prompt: str) -> BaseModel:
        # Screened sentences are numbered; the model names both picked sentences.
        if "[1]" in prompt:
            return ScreenedResult(
                obligations=[
                    ScreenedItem(sentence=1, type="other"),
                    ScreenedItem(sentence=2, type="electrical"),
                ]
            )
        return ExtractionResultWithParaphrase()

    drawer = client.post("/drawers", json={"name": "Described"}).json()
    document = upload(client, drawer["id"], pdf)
    fake_llm.structured_replies.extend([answer] * 5)
    client.post(f"/documents/{document['id']}/requirements")
    table = client.get(f"/documents/{document['id']}/requirements").json()
    assert [r["quote"] for r in table["requirements"]] == [obligation]
    screened_prompt = next(p for p in fake_llm.prompts if "[1]" in p)
    assert f"[1] {obligation}" in screened_prompt
    assert f"[2] {limit_like}" in screened_prompt
    assert "is a digital sensor" not in screened_prompt


def test_passive_recommendation_is_taken_by_rule_when_the_model_misses_it(
    client: TestClient, fake_llm: FakeLLM, tmp_path: Path
) -> None:
    recommendation = "In noisy systems, higher averaging numbers are recommended."
    pdf = make_pdf(tmp_path / "noise.pdf", [["8 Application", "Noise is low.", recommendation]])
    table = extract(client, fake_llm, pdf)
    assert [(r["kind"], r["quote"]) for r in table["requirements"]] == [
        ("obligation", recommendation)
    ]


def test_ratings_row_with_a_garbled_multi_line_cell_is_read_as_a_limit(
    services: Services,
) -> None:
    from app.requirements import limit_table_rows

    page = "5.1 Absolute Maximum Ratings\nVoltage at SCL, SDA and\nALERT\n-0.3 6 V\n"
    table_chunk = chunk(
        "5 Specifications > 5.1 Absolute Maximum Ratings",
        "| Parameter | MIN | MAX | UNIT |\n| --- | --- | --- | --- |\n"
        "| Voltage at SCL, ALERT | SDA and -0.3 | 6 | V |",
        2,
        "table",
    )
    rows, missing = limit_table_rows(table_chunk, lambda _page: page, services)
    assert missing == 0
    assert [(r.kind, r.quote) for r in rows] == [
        ("limit", "Voltage at SCL, SDA and\nALERT\n-0.3 6 V")
    ]


def test_screened_prompt_numbers_sentences_and_ignores_unknown_numbers(
    services: Services,
) -> None:
    from app.requirements import Group, _ask, build_screened_prompt, screened_sentences

    group = Group(
        [
            chunk("7 Detailed Description", "Do not write the EEPROM.\nAvoid conversions.", 18),
            chunk("7.6 Register Map", "Do not delete the ID register.", 30, index=1),
        ],
        screened=True,
    )
    prompt = build_screened_prompt(group)
    assert "Page 18:\n[1] Do not write the EEPROM.\n[2] Avoid conversions." in prompt
    assert "Page 30:\n[3] Do not delete the ID register." in prompt
    assert screened_sentences(group)[2] == "Do not delete the ID register."

    services.llm.structured_replies.append(  # type: ignore[attr-defined]
        ScreenedResult(
            obligations=[
                ScreenedItem(sentence=3, type="other"),
                ScreenedItem(sentence=9, type="other"),
                ScreenedItem(sentence=3, type="other"),
            ]
        )
    )
    [(_group, items)] = list(_ask(services, [group]))
    assert [i.quote for i in items] == ["Do not delete the ID register."]
