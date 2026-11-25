import re
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.language import detect_language
from app.qa import (
    NOT_FOUND_ANSWER,
    CitedAnswer,
    Claim,
    Evidence,
    Translation,
    merge_repeated_openings,
)
from app.retrieval import retrieve
from app.services import Services
from app.vector_store import ScoredChunk
from tests.conftest import FakeLLM, make_pdf, upload


@pytest.fixture
def two_drawers(client: TestClient, tmp_path: Path, sample_pdf: Path) -> dict[str, int]:
    sensors = client.post("/drawers", json={"name": "Sensors"}).json()
    motors = client.post("/drawers", json={"name": "Motors"}).json()
    motor_pdf = make_pdf(
        tmp_path / "motor.pdf",
        [["Motor Ratings", "The maximum operating temperature of the motor is 120 C."]],
    )
    sensor_doc = upload(client, sensors["id"], sample_pdf)
    motor_doc = upload(client, motors["id"], motor_pdf)
    return {
        "sensors": sensors["id"],
        "motors": motors["id"],
        "sensor_doc": sensor_doc["id"],
        "motor_doc": motor_doc["id"],
    }


QUESTION = "What is the maximum operating temperature?"
EXCERPT = re.compile(r"^\[(\d+)\] (.*?)(?=\n\n\[\d+\] |\n\nQuestion:)", re.MULTILINE | re.DOTALL)


def test_retrieval_without_filter_searches_every_drawer(
    services: Services, two_drawers: dict[str, int]
) -> None:
    chunks = retrieve(services, QUESTION)
    assert {c.payload.doc_id for c in chunks} == {
        two_drawers["sensor_doc"],
        two_drawers["motor_doc"],
    }


def test_retrieval_respects_drawer_filter(services: Services, two_drawers: dict[str, int]) -> None:
    chunks = retrieve(services, QUESTION, drawer_id=two_drawers["motors"])
    assert chunks
    assert {c.payload.drawer_id for c in chunks} == {two_drawers["motors"]}


def test_retrieval_respects_document_filter(
    services: Services, two_drawers: dict[str, int]
) -> None:
    chunks = retrieve(services, QUESTION, doc_ids=[two_drawers["sensor_doc"]])
    assert chunks
    assert {c.payload.doc_id for c in chunks} == {two_drawers["sensor_doc"]}


@pytest.mark.parametrize("mode", ["dense", "bm25", "hybrid"])
def test_every_mode_ranks_the_matching_page_first(
    services: Services, two_drawers: dict[str, int], mode: str
) -> None:
    chunks = retrieve(
        services, "supply voltage shall not exceed", doc_ids=[two_drawers["sensor_doc"]], mode=mode
    )
    assert chunks[0].payload.page == 2


def test_retrieval_returns_at_most_top_k(services: Services, two_drawers: dict[str, int]) -> None:
    assert len(retrieve(services, "the")) <= services.settings.retrieval_top_k


def answer(
    *claims: tuple[str, list[int]], evidence: list[tuple[int, str]] | None = None
) -> CitedAnswer:
    return CitedAnswer(
        evidence=[Evidence(excerpt=n, quote=q) for n, q in (evidence or [])],
        answered=bool(claims),
        claims=[Claim(statement=s, excerpts=e) for s, e in claims],
    )


def _excerpt_number(prompt: str, needle: str) -> int:
    """Number of the excerpt in the prompt that contains `needle`."""
    for match in EXCERPT.finditer(prompt):
        if needle in match.group(2):
            return int(match.group(1))
    raise AssertionError(f"{needle!r} not in prompt")


def test_ask_returns_answer_with_sources_from_cited_chunks_only(
    client: TestClient, fake_llm: FakeLLM, two_drawers: dict[str, int]
) -> None:
    def reply(prompt: str) -> CitedAnswer:
        number = _excerpt_number(prompt, "85 C")
        return answer(
            ("It is 85 C.", [number]),
            evidence=[(number, "The maximum operating temperature is 85 C.")],
        )

    fake_llm.structured_replies.append(reply)
    response = client.post("/ask", json={"question": QUESTION, "drawer_id": two_drawers["sensors"]})
    assert response.status_code == 200
    body = response.json()
    assert body["answered"] is True
    assert body["answer"] == "It is 85 C."
    assert len(body["sources"]) == 1
    source = body["sources"][0]
    assert (source["filename"], source["page"]) == ("sensor.pdf", 2)
    assert "85 C" in source["snippet"]
    assert source["highlight"] == "The maximum operating temperature is 85 C."
    assert "motor" not in fake_llm.prompts[0].lower()


def test_comparison_cites_every_supporting_excerpt(
    client: TestClient, fake_llm: FakeLLM, two_drawers: dict[str, int]
) -> None:
    def reply(prompt: str) -> CitedAnswer:
        sensor, motor = _excerpt_number(prompt, "85 C"), _excerpt_number(prompt, "120 C")
        return answer(("The sensor allows 85 C.", [sensor]), ("The motor allows 120 C.", [motor]))

    fake_llm.structured_replies.append(reply)
    body = client.post(
        "/ask", json={"question": "Compare the maximum temperatures of sensor and motor"}
    ).json()
    assert body["answer"] == "The sensor allows 85 C.\nThe motor allows 120 C."
    assert [line["text"] for line in body["lines"]] == [
        "The sensor allows 85 C.",
        "The motor allows 120 C.",
    ]
    cited = [[body["sources"][i]["filename"] for i in line["sources"]] for line in body["lines"]]
    assert cited == [["sensor.pdf"], ["motor.pdf"]]
    assert {(s["filename"], s["page"]) for s in body["sources"]} == {
        ("sensor.pdf", 2),
        ("motor.pdf", 1),
    }


def test_claim_without_supporting_excerpt_is_left_out(
    client: TestClient, fake_llm: FakeLLM, two_drawers: dict[str, int]
) -> None:
    def reply(prompt: str) -> CitedAnswer:
        sensor = _excerpt_number(prompt, "85 C")
        return answer(
            ("The sensor allows 85 C.", [sensor]),
            ("It also survives 150 C for an hour.", [sensor]),
            ("It is waterproof.", []),
        )

    fake_llm.structured_replies.append(reply)
    body = client.post("/ask", json={"question": QUESTION}).json()
    assert body["answer"] == "The sensor allows 85 C."


def test_highlight_is_none_when_evidence_is_not_on_the_page(
    client: TestClient, fake_llm: FakeLLM, two_drawers: dict[str, int]
) -> None:
    def reply(prompt: str) -> CitedAnswer:
        number = _excerpt_number(prompt, "85 C")
        return answer(
            ("It is 85 C.", [number]),
            evidence=[(number, "A sentence that the page does not contain.")],
        )

    fake_llm.structured_replies.append(reply)
    body = client.post("/ask", json={"question": QUESTION}).json()
    assert body["sources"][0]["highlight"] is None


@pytest.mark.parametrize(
    ("question", "language"),
    [
        ("What is the maximum operating temperature?", "English"),
        ("Koja je maksimalna radna temperatura?", "Bosnian"),
        ("Koliko struje troši uređaj?", "Bosnian"),
    ],
)
def test_answer_language_follows_the_question(
    client: TestClient, fake_llm: FakeLLM, two_drawers: dict[str, int], question: str, language: str
) -> None:
    if language == "Bosnian":
        fake_llm.structured_replies.append(
            Translation(english="What is the maximum operating temperature?")
        )
    fake_llm.structured_replies.append(answer())
    client.post("/ask", json={"question": question})
    assert f"written in {language} (the language of the question)" in fake_llm.systems[-1]


def test_detect_language() -> None:
    assert detect_language("What is the difference between FlatList and SectionList?") == "English"
    assert detect_language("Koja je razlika između FlatList i SectionList?") == "Bosnian"
    assert detect_language("FlatList SectionList") is None


def test_ask_not_found_when_model_abstains(
    client: TestClient, fake_llm: FakeLLM, two_drawers: dict[str, int]
) -> None:
    fake_llm.structured_replies.append(answer())
    body = client.post("/ask", json={"question": "What is the warranty period?"}).json()
    assert body == {
        "answer": NOT_FOUND_ANSWER,
        "answered": False,
        "sources": [],
        "lines": [],
        "debug": None,
    }


def test_ask_answer_without_valid_citation_is_not_shown(
    client: TestClient, fake_llm: FakeLLM, two_drawers: dict[str, int]
) -> None:
    fake_llm.structured_replies.append(answer(("Two years.", [42])))
    body = client.post("/ask", json={"question": "What is the warranty period?"}).json()
    assert body["answered"] is False
    assert body["sources"] == []


def test_ask_on_empty_drawer_does_not_call_llm(client: TestClient, fake_llm: FakeLLM) -> None:
    empty = client.post("/drawers", json={"name": "Empty"}).json()
    body = client.post("/ask", json={"question": QUESTION, "drawer_id": empty["id"]}).json()
    assert body["answered"] is False
    assert fake_llm.prompts == []


def test_ask_unknown_drawer_is_404(client: TestClient) -> None:
    response = client.post("/ask", json={"question": QUESTION, "drawer_id": 999})
    assert response.status_code == 404


def test_ask_blank_question_is_422(client: TestClient) -> None:
    assert client.post("/ask", json={"question": "  "}).status_code == 422


def test_answer_schema_asks_for_evidence_before_decision_and_claims() -> None:
    schema = CitedAnswer.model_json_schema()
    assert list(schema["properties"]) == ["evidence", "answered", "claims"]
    assert {"evidence", "answered"} <= set(schema["required"])


def test_numbers_from_the_question_do_not_drop_a_claim(
    client: TestClient, fake_llm: FakeLLM, two_drawers: dict[str, int]
) -> None:
    def reply(prompt: str) -> CitedAnswer:
        number = _excerpt_number(prompt, "85 C")
        return answer(("The maximum operating temperature of the TH-200 is 85 C.", [number]))

    fake_llm.structured_replies.append(reply)
    body = client.post(
        "/ask", json={"question": "What is the maximum operating temperature of the TH-200?"}
    ).json()
    assert body["answered"] is True


def test_claims_repeating_the_same_opening_are_merged_into_one_line() -> None:
    claims = [
        Claim(statement="Tačnost mjerenja je ±0.1 °C od -20 °C do 50 °C.", excerpts=[1]),
        Claim(statement="Tačnost mjerenja je ±0.15 °C od -40 °C do 70 °C.", excerpts=[2]),
        Claim(statement="Napajanje je 1.8 V do 5.5 V.", excerpts=[2]),
    ]
    merged = merge_repeated_openings(claims)
    assert [c.statement for c in merged] == [
        "Tačnost mjerenja je ±0.1 °C od -20 °C do 50 °C; ±0.15 °C od -40 °C do 70 °C.",
        "Napajanje je 1.8 V do 5.5 V.",
    ]
    assert merged[0].excerpts == [1, 2]


def test_citation_markers_in_a_statement_do_not_count_as_values() -> None:
    """The model wrote "(excerpts 2, 3)" into claims; the digits made the number check
    drop correct answers about the bypass capacitor."""
    from app.qa import strip_citation_markers

    assert (
        strip_citation_markers("The recommended value is 0.1 μF (excerpts 2, 3)")
        == "The recommended value is 0.1 μF"
    )
    assert strip_citation_markers("It is 100 nF [1].") == "It is 100 nF."
    assert (
        strip_citation_markers("The SDA pin (6) needs a pullup.")
        == "The SDA pin (6) needs a pullup."
    )


def _chunks(*texts: str) -> list:
    from app.vector_store import ChunkPayload, ScoredChunk

    return [
        ScoredChunk(ChunkPayload(1, 1, "a.pdf", n, "S", "text", t, n), 1.0)
        for n, t in enumerate(texts, 1)
    ]


@pytest.mark.parametrize(
    ("source", "statement"),
    [
        ("A 0.1-µF bypass capacitor is recommended.", "The recommended capacitor is 0.1 μF."),
        ("A recommended value for this capacitor is 100 nF.", "It is 100nF (excerpts 1)."),
        (
            "The address pin allows up to four devices on one bus.",
            "Up to 4 devices can share the bus.",
        ),
        ("Do četiri uređaja mogu dijeliti sabirnicu.", "Up to 4 devices."),
        ("Up to 4 devices can share the bus.", "Up to four devices can share the bus."),
    ],
)
def test_same_value_in_another_notation_supports_the_claim(source: str, statement: str) -> None:
    from app.qa import check_claims

    reply = CitedAnswer(
        evidence=[], answered=True, claims=[Claim(statement=statement, excerpts=[1])]
    )
    kept, discarded = check_claims(reply, _chunks(source), "question?")
    assert len(kept) == 1, discarded


def test_a_different_value_is_still_discarded_with_a_reason() -> None:
    from app.qa import check_claims

    reply = CitedAnswer(
        evidence=[], answered=True, claims=[Claim(statement="It is 0.2 μF.", excerpts=[1])]
    )
    kept, discarded = check_claims(
        reply, _chunks("A 0.1-µF capacitor is recommended."), "question?"
    )
    assert kept == []
    assert discarded[0].reason == "number 0.2 is not in the cited excerpts"


def test_bosnian_question_is_searched_with_its_english_translation(
    client: TestClient, fake_llm: FakeLLM, services: Services, tmp_path: Path
) -> None:
    from app.retrieval import retrieve

    pdf = make_pdf(
        tmp_path / "bus.pdf",
        [
            ["Overview", "The sensor measures temperature with high accuracy."],
            ["Addressing", "The address pin allows up to four devices to share a single bus."],
        ],
    )
    drawer = client.post("/drawers", json={"name": "Bus"}).json()
    upload(client, drawer["id"], pdf)
    fake_llm.structured_replies.append(
        Translation(english="How many devices can share a single bus?")
    )
    phrasings = __import__("app.qa", fromlist=["retrieval_phrasings"]).retrieval_phrasings(
        services, "Koliko uređaja može biti na jednoj sabirnici?"
    )
    assert phrasings == [
        "Koliko uređaja može biti na jednoj sabirnici?",
        "How many devices can share a single bus?",
    ]
    assert retrieve(services, phrasings, limit=1)[0].payload.page == 2


def test_english_question_is_not_translated(services: Services, fake_llm: FakeLLM) -> None:
    from app.qa import retrieval_phrasings

    assert retrieval_phrasings(services, "How many devices can share a bus?") == [
        "How many devices can share a bus?"
    ]
    assert fake_llm.prompts == []


def test_refused_question_explains_itself_with_debug(
    client: TestClient, fake_llm: FakeLLM, two_drawers: dict[str, int]
) -> None:
    def reply(prompt: str) -> CitedAnswer:
        return answer(("It survives 150 C.", [_excerpt_number(prompt, "85 C")]))

    fake_llm.structured_replies.append(reply)
    body = client.post("/ask", json={"question": QUESTION, "debug": True}).json()
    assert body["answered"] is False
    debug = body["debug"]
    assert debug["queries"] == [QUESTION]
    assert debug["model_answered"] is True
    assert [c["rank"] for c in debug["retrieved"]] == list(range(1, len(debug["retrieved"]) + 1))
    assert {"page", "score", "filename", "snippet"} <= set(debug["retrieved"][0])
    assert debug["discarded"] == [
        {
            "statement": "It survives 150 C.",
            "reason": "number 150 is not in the cited excerpts",
            "code": "number_missing",
            "numbers": ["150"],
        }
    ]


def test_debug_is_off_by_default(
    client: TestClient, fake_llm: FakeLLM, two_drawers: dict[str, int]
) -> None:
    fake_llm.structured_replies.append(answer())
    assert client.post("/ask", json={"question": QUESTION}).json()["debug"] is None


def test_diversify_caps_chunks_per_page() -> None:
    from app.retrieval import diversify

    chunks = _chunks("a", "b", "c", "d")
    same_page = [
        type(c)(
            type(c.payload)(1, 1, "a.pdf", 14 if i < 3 else 1, "S", "text", c.payload.text, i), 1.0
        )
        for i, c in enumerate(chunks)
    ]
    picked = diversify(same_page, limit=3, max_per_page=2)
    assert [c.payload.page for c in picked] == [14, 14, 1]


def test_uncited_claim_takes_the_citation_of_evidence_that_states_its_value() -> None:
    from app.qa import check_claims

    chunks = _chunks(
        "The recommended value for the pullup resistors is 5 kOhm.", "Requires a pullup resistor."
    )
    evidence = [
        Evidence(excerpt=1, quote="The recommended value for the pullup resistors is 5 kOhm.")
    ]
    reply = CitedAnswer(
        evidence=evidence,
        answered=True,
        claims=[
            Claim(statement="The recommended pullup is 5 kOhm.", excerpts=[]),
            Claim(statement="A pullup resistor is needed.", excerpts=[]),
            Claim(statement="The pullup is 10 kOhm.", excerpts=[]),
        ],
    )
    kept, discarded = check_claims(reply, chunks, "Which pullup?")
    assert [(k.statement, k.excerpts) for k in kept] == [("The recommended pullup is 5 kOhm.", [1])]
    assert [d.reason for d in discarded] == [
        "cites no retrieved excerpt",
        "cites no retrieved excerpt",
    ]


def test_diversify_skips_repeated_points_and_identical_text() -> None:
    from app.retrieval import diversify
    from app.vector_store import ChunkPayload, ScoredChunk

    def chunk(doc: int, page: int, text: str, point: str) -> ScoredChunk:
        return ScoredChunk(ChunkPayload(doc, doc, "t.pdf", page, "S", "text", text, 0), 1.0, point)

    candidates = [
        chunk(5, 40, "SCALE 5.500", "a"),
        chunk(6, 40, "SCALE  5.500", "b"),  # the same PDF in another drawer
        chunk(9, 40, "Scale 5.500", "c"),
        chunk(5, 40, "SCALE 5.500", "a"),  # the same point again
        chunk(5, 1, "Supply voltage 1.7 V to 5.5 V", "d"),
        chunk(6, 1, "Supply voltage 1.7 V to 5.5 V", "e"),
        chunk(5, 14, "Shutdown current 250 nA", "f"),
    ]
    picked = diversify(candidates, limit=8, max_per_page=2)
    assert [c.point_id for c in picked] == ["a", "d", "f"]


def test_same_pdf_in_two_drawers_fills_the_context_with_distinct_chunks(
    client: TestClient, services: Services, tmp_path: Path
) -> None:
    lines = [
        f"Section {n}: the operating temperature limit number {n} is {80 + n} C." for n in range(12)
    ]
    pdf = make_pdf(tmp_path / "dup.pdf", [lines[:6], lines[6:]])
    services.settings.chunk_max_chars = 120
    services.settings.chunk_overlap_chars = 0
    for name in ("Datasheet", "TI datasheets"):
        drawer = client.post("/drawers", json={"name": name}).json()
        upload(client, drawer["id"], pdf)

    chunks = retrieve(services, QUESTION)

    texts = [c.payload.text for c in chunks]
    assert len(texts) == len(set(texts))
    per_page: dict[tuple[int, int], int] = {}
    for c in chunks:
        key = (c.payload.doc_id, c.payload.page)
        per_page[key] = per_page.get(key, 0) + 1
    assert max(per_page.values()) <= services.settings.retrieval_max_per_page
    assert len({c.point_id for c in chunks}) == len(chunks)


def test_source_carries_every_evidence_passage_of_its_page(
    client: TestClient, fake_llm: FakeLLM, two_drawers: dict[str, int]
) -> None:
    def reply(prompt: str) -> CitedAnswer:
        number = _excerpt_number(prompt, "85 C")
        return answer(
            ("It is 85 C.", [number]),
            ("The supply must stay below 3.6 V.", [number]),
            evidence=[
                (number, "The maximum operating temperature is 85 C."),
                (number, "The supply voltage shall not exceed 3.6 V."),
                (number, "This sentence is not on the page at all."),
            ],
        )

    fake_llm.structured_replies.append(reply)
    body = client.post("/ask", json={"question": QUESTION, "drawer_id": two_drawers["sensors"]})
    source = body.json()["sources"][0]
    assert source["highlights"] == [
        "The maximum operating temperature is 85 C.",
        "The supply voltage shall not exceed 3.6 V.",
    ]
    assert source["highlight"] == source["highlights"][0]


def test_two_cited_chunks_of_one_page_give_one_source_with_both_passages() -> None:
    from app.qa import build_sources

    first, second = _chunks("Use a 100-nF capacitor.", "Place it near the V+ pin.")
    chunks = [first, ScoredChunk(replace(second.payload, page=first.payload.page), 0.5)]
    claims = [Claim(statement="A 100-nF capacitor near V+.", excerpts=[1, 2])]
    evidence = [
        Evidence(excerpt=1, quote="Use a 100-nF capacitor."),
        Evidence(excerpt=2, quote="Place it near the V+ pin."),
    ]
    sources = build_sources(chunks, claims, evidence, lambda _chunk, quotes: list(quotes))
    assert len(sources) == 1
    assert sources[0].highlights == ["Use a 100-nF capacitor.", "Place it near the V+ pin."]


def test_source_without_located_evidence_has_no_highlights() -> None:
    from app.qa import build_sources

    chunks = _chunks("Use a 100-nF capacitor.")
    claims = [Claim(statement="A 100-nF capacitor.", excerpts=[1])]
    sources = build_sources(chunks, claims, [], lambda _chunk, _quotes: [])
    assert (sources[0].highlights, sources[0].highlight) == ([], None)
