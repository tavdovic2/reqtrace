from pathlib import Path

from eval.run_eval import (
    DEFAULT_QUESTIONS,
    QuestionResult,
    compute_metrics,
    keyword_match,
    load_questions,
    markdown_table,
)


def result(answerable: bool, hit: bool, answered: bool, correct: bool) -> QuestionResult:
    return QuestionResult("q", "hybrid", answerable, [1], hit, answered, correct, "", [])


def test_keyword_match_is_anchored_and_case_insensitive() -> None:
    assert keyword_match("The burst pressure is 5 x full scale.", ["5"])
    assert not keyword_match("The torque is 25 Nm.", ["5"])
    assert keyword_match("Accuracy: ±0.1 % FS", ["0.1"])
    assert not keyword_match("Thermal error ±0.15 %", ["0.1"])
    assert keyword_match("Current is 12 MA typical", ["12 mA"])
    assert keyword_match("No, fit it externally.", ["no|externally"])
    assert not keyword_match("It is not mentioned.", ["no"])
    assert not keyword_match("Supply 9 V", ["9", "32"])


def test_compute_metrics() -> None:
    results = [
        result(True, hit=True, answered=True, correct=True),
        result(True, hit=True, answered=True, correct=False),
        result(True, hit=False, answered=False, correct=False),
        result(True, hit=True, answered=False, correct=False),
        result(False, hit=False, answered=False, correct=False),
        result(False, hit=False, answered=True, correct=False),
    ]
    metrics = compute_metrics("hybrid", results)
    assert metrics.hit_rate == 0.75
    assert metrics.answer_accuracy == 0.25
    assert metrics.abstention_accuracy == 0.5
    assert (metrics.answerable, metrics.unanswerable) == (4, 2)


def test_markdown_table_format() -> None:
    metrics = [compute_metrics("dense", [result(True, True, True, True)])]
    table = markdown_table(metrics, {"LLM": "qwen3:4b"})
    assert "| dense | 100% | 100% | 0% |" in table
    assert "- LLM: qwen3:4b" in table


def test_question_set_is_well_formed() -> None:
    questions = load_questions(DEFAULT_QUESTIONS)
    answerable = [q for q in questions if q.answerable]
    unanswerable = [q for q in questions if not q.answerable]
    assert 25 <= len(answerable) <= 30
    assert len(unanswerable) >= 5
    assert len({q.id for q in questions}) == len(questions)
    for q in answerable:
        assert q.expected_keywords and q.expected_pages
    for q in unanswerable:
        assert not q.expected_keywords and not q.expected_pages


def test_expected_keywords_appear_on_expected_pages() -> None:
    """Guards the question set itself: every keyword must be in the source document."""
    from app.pdf import extract_page_text
    from app.verification import normalize

    pdf = Path(DEFAULT_QUESTIONS).parent / "data" / "nx310_datasheet.pdf"
    for q in load_questions(DEFAULT_QUESTIONS):
        if not q.answerable:
            continue
        pages_text = " ".join(normalize(extract_page_text(pdf, p)) for p in q.expected_pages)
        for keyword in q.expected_keywords:
            alternatives = [normalize(alt) for alt in keyword.split("|")]
            assert any(alt in pages_text for alt in alternatives), (q.id, keyword)
