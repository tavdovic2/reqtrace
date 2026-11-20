"""Benchmark datasets: loaders, structure, checks against the source PDF, and the fetcher.

The real-datasheet checks need samples/tmp117.pdf and are skipped without it, so CI runs
without the PDF, the network, a GPU or Ollama.
"""

from dataclasses import replace
from pathlib import Path

import pytest

from app.pdf import extract_page_text
from eval.datasets import (
    DATASETS,
    Question,
    label_problems,
    labels_against_document,
    load_questions,
    load_requirements,
    question_set_problems,
    questions_against_document,
)
from tests.conftest import make_pdf

SYNTHETIC = DATASETS["synthetic"]
TMP117 = DATASETS["tmp117"]
NO_TMP117 = pytest.mark.skipif(
    not TMP117.pdf.exists(),
    reason="samples/tmp117.pdf is not present; run `python -m eval.fetch_samples` to check "
    "the real-datasheet question set and labels against the PDF",
)


@pytest.mark.parametrize("dataset", [SYNTHETIC, TMP117], ids=["synthetic", "tmp117"])
def test_question_sets_are_well_formed(dataset) -> None:
    questions = load_questions(dataset.questions)
    assert question_set_problems(questions) == []
    assert sum(not q.answerable for q in questions) >= 5


def test_language_is_detected_when_a_question_has_none(tmp_path: Path) -> None:
    path = tmp_path / "q.jsonl"
    path.write_text(
        '{"id": "a", "question": "Koliki je napon napajanja?", "answerable": false, '
        '"expected_keywords": [], "expected_pages": []}\n'
        '{"id": "b", "question": "What is the supply voltage?", "answerable": false, '
        '"expected_keywords": [], "expected_pages": []}\n',
        encoding="utf-8",
    )
    assert [q.lang for q in load_questions(path)] == ["bs", "en"]


def test_structural_problems_are_reported() -> None:
    questions = [
        Question("a", "?", True, [], [1], "en"),
        Question("a", "?", False, ["5 V"], [], "de"),
        Question("c", "?", True, ["5 V"], [60], "en"),
    ]
    assert question_set_problems(questions, page_count=50) == [
        "question ids are not unique",
        "a: an answerable question needs keywords and pages",
        "a: lang must be en or bs",
        "a: an unanswerable question has no keywords or pages",
        "c: page outside 1..50",
    ]


def test_tmp117_question_set_covers_the_issue_scope() -> None:
    questions = load_questions(TMP117.questions)
    assert 28 <= len(questions) <= 35
    assert sum(q.lang == "bs" for q in questions) >= 5
    assert sum(not q.answerable for q in questions) >= 5
    assert {"easy", "hard"} <= {q.difficulty for q in questions}
    texts = {q.question for q in questions}
    assert "Koliko uređaja može biti na jednoj I2C sabirnici?" in texts
    assert "What is the maximum SPI clock frequency?" in texts


def test_tmp117_labels_are_well_formed() -> None:
    labels = load_requirements(TMP117.requirements)
    assert label_problems(labels, page_count=50) == []
    assert {label.kind for label in labels if not label.optional} == {"obligation", "limit"}
    # Short identifying phrases only: the datasheet text is TI's.
    assert max(len(term) for label in labels for term in label.terms) <= 80


def test_synthetic_expected_answers_are_stated_on_expected_pages() -> None:
    """Guards the question set itself: every expected value is in the source document."""
    questions = load_questions(SYNTHETIC.questions)
    problems = questions_against_document(questions, lambda p: extract_page_text(SYNTHETIC.pdf, p))
    assert problems == []


@NO_TMP117
def test_tmp117_expected_answers_are_stated_on_expected_pages() -> None:
    questions = load_questions(TMP117.questions)
    problems = questions_against_document(questions, lambda p: extract_page_text(TMP117.pdf, p))
    assert problems == []


@NO_TMP117
def test_tmp117_labels_are_stated_on_their_pages() -> None:
    labels = load_requirements(TMP117.requirements)
    assert labels_against_document(labels, lambda p: extract_page_text(TMP117.pdf, p)) == []


def test_document_check_reports_a_wrong_value_and_a_wrong_page(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "d.pdf", [["Supply voltage 1.7 V to 5.5 V."], ["Page two."]])
    questions = [
        Question("ok", "?", True, ["1.7 V", "5.5 V"], [1], "en"),
        Question("value", "?", True, ["1.8 V"], [1], "en"),
        Question("page", "?", True, ["5.5 V"], [1, 2], "en"),
    ]
    problems = questions_against_document(questions, lambda p: extract_page_text(pdf, p))
    assert problems == [
        "value: '1.8 V' is not stated on pages [1]",
        "value: page 1 states none of ['1.8 V']",
        "page: page 2 states none of ['5.5 V']",
    ]


def test_fetcher_verifies_hash_and_revision(tmp_path: Path) -> None:
    from eval.fetch_samples import TMP117 as SAMPLE
    from eval.fetch_samples import file_sha256, verify

    same_revision = make_pdf(tmp_path / "rev.pdf", [["TMP117 SNOSD82D"], ["page two"]])
    sample = replace(SAMPLE, path=same_revision, pages=2)
    usable, message = verify(sample, same_revision)
    assert usable and "same revision" in message

    exact = replace(sample, sha256=file_sha256(same_revision))
    assert verify(exact, same_revision) == (
        True,
        f"{same_revision} verified (SHA-256 {exact.sha256}).",
    )

    other = make_pdf(tmp_path / "other.pdf", [["TMP117 SNOSD82E"], ["page two"]])
    usable, message = verify(sample, other)
    assert not usable and "missing" in message

    html = tmp_path / "error.pdf"
    html.write_text("<html>Access denied</html>", encoding="utf-8")
    assert verify(sample, html)[0] is False
    assert verify(sample, tmp_path / "absent.pdf") == (
        False,
        f"{tmp_path / 'absent.pdf'} does not exist.",
    )
