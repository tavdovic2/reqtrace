from pathlib import Path

import pytest

from eval.datasets import DATASETS
from eval.matching import RequirementLabel
from eval.run_eval import (
    QuestionResult,
    compute_metrics,
    keyword_match,
    markdown_report,
    score_extraction,
)

TMP117 = DATASETS["tmp117"]


def result(
    answerable: bool, hit: bool, answered: bool, correct: bool, lang: str = "en"
) -> QuestionResult:
    return QuestionResult("q", "hybrid", answerable, [1], hit, answered, correct, "", [], lang)


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
        result(True, hit=True, answered=False, correct=False, lang="bs"),
        result(False, hit=False, answered=False, correct=False),
        result(False, hit=False, answered=True, correct=False, lang="bs"),
    ]
    metrics = compute_metrics("hybrid", results)
    assert metrics.hit_rate == 0.75
    assert metrics.answer_accuracy == 0.25
    assert metrics.abstention_accuracy == 0.5
    assert (metrics.answerable, metrics.unanswerable) == (4, 2)
    english, bosnian = metrics.by_language["en"], metrics.by_language["bs"]
    assert (english.answerable, english.answer_accuracy) == (3, 1 / 3)
    assert (english.unanswerable, english.refusal_accuracy) == (1, 1.0)
    assert (bosnian.answerable, bosnian.answer_accuracy) == (1, 0.0)
    assert (bosnian.unanswerable, bosnian.refusal_accuracy) == (1, 0.0)


def test_markdown_report_format() -> None:
    metrics = [compute_metrics("dense", [result(True, True, True, True)])]
    table = markdown_report(metrics, {"LLM": "qwen3:4b"})
    assert "| dense | 100% | 0% | 100% | 0% | 0.0 s |" in table
    assert "| dense | en | 100% (1) | 0% (0) |" in table
    assert "- LLM: qwen3:4b" in table


def test_extraction_is_scored_per_kind_with_missed_extra_and_wrong_kind() -> None:
    labels = [
        RequirementLabel("L1", 5, "limit", ["supply voltage", "-0.3", "6"]),
        RequirementLabel("O1", 33, "obligation", ["bypass capacitor is recommended"]),
        RequirementLabel("O2", 36, "obligation", ["do not solder down the thermal pad"]),
        RequirementLabel("T1", 7, "limit", ["data setup time", "100"], optional=True),
    ]
    rows = [
        ("limit", 5, "Supply voltage V+ –0.3 6 V"),
        ("obligation", 33, "A 0.1-µF bypass capacitor is recommended between V+ and GND."),
        ("obligation", 7, "tSU;DAT Data setup time 100 ns"),  # right item, wrong kind
        ("obligation", 12, "The device requires 1.5 ms to power up."),  # no label
        ("limit", 5, "Supply voltage V+ –0.3 5 V"),  # wrong value
    ]
    score = score_extraction(rows, labels, seconds=12.0)
    assert (score.rows, score.required, score.found) == (5, 3, 2)
    assert score.precision == 2 / 5
    assert score.by_kind["limit"].recall == 1.0
    assert score.by_kind["obligation"].recall == 0.5
    assert [m["id"] for m in score.missed] == ["O2"]
    assert [e["page"] for e in score.extra] == [12, 5]
    assert score.wrong_kind == [{"page": 7, "kind": "obligation", "labels": ["T1"]}]
    assert (score.optional_found, score.optional_total) == (0, 1)


def test_run_eval_skips_tmp117_without_the_pdf(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from dataclasses import replace

    from eval import run_eval

    missing = replace(TMP117, pdf=tmp_path / "tmp117.pdf")
    monkeypatch.setitem(run_eval.DATASETS, "tmp117", missing)
    assert run_eval.main(["--dataset", "tmp117"]) == 0
    out = capsys.readouterr().out
    assert "Skipping the tmp117 benchmark" in out
    assert "python -m eval.fetch_samples" in out
