"""Benchmark comparison arithmetic: does a sentence contradict itself?

The tests that matter most here are the ones asserting silence. A check that
reports a rounding difference as an error gets switched off, so the negative
cases are the real specification.
"""
from __future__ import annotations

import pytest

from claimaudit import benchmarks, cli
from claimaudit.report import VERIFIED, FLAGGED


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAIMAUDIT_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CLAIMAUDIT_LICENSE_KEY", raising=False)


def _check(text):
    return benchmarks.check([("a.md", "/a.md", text)])


def _flags(text):
    return [f for f in _check(text) if f.status == FLAGGED]


def _verified(text):
    return [f for f in _check(text) if f.status == VERIFIED]


# ------------------------------------------------------------ catches
def test_wrong_absolute_gain_is_flagged():
    f = _flags("We reach 91.2 against the baseline's 88.0, a gain of 4.2 points.\n")
    assert len(f) == 1
    assert "difference of 3.2" in f[0].message and "text says 4.2" in f[0].message


def test_wrong_gain_with_vs():
    assert _flags("Our model scores 76.5 vs 71.1, an improvement of 6.4 points.\n")


def test_wrong_gain_with_of_phrasing():
    assert _flags("Accuracy is 91.2% compared to 88.0%, a difference of 5.2 percentage points.\n")


def test_wrong_from_to_absolute():
    assert _flags("Accuracy improves from 88.0 to 91.2, a gain of 2.2 points.\n")


def test_wrong_relative_improvement():
    # 3.2/88.0 is 3.64%, not 12%
    assert _flags("Accuracy improves from 88.0 to 91.2, a 12.0% relative improvement.\n")


def test_wrong_reduction():
    assert _flags("Error falls from 10.0 to 5.0, a reduction of 2.0 points.\n")


def test_beats_by_phrasing():
    assert _flags("Our method beats the baseline, 91.2 vs 88.0, by 7.0 points.\n")


def test_direction_is_ignored_only_the_magnitude_is_checked():
    """A stated magnitude is checked against the magnitude of the difference."""
    assert not _flags("We drop from 91.2 to 88.0, a reduction of 3.2 points.\n")


# ------------------------------------------------------------ must stay silent
def test_correct_arithmetic_is_not_flagged():
    assert not _flags("We reach 91.2 against the baseline's 88.0, a gain of 3.2 points.\n")


def test_rounding_at_the_printed_precision_is_not_an_error():
    """91.2 - 88.0 could be anything in (3.10, 3.30); 3.3 is reachable."""
    assert not _flags("We reach 91.2 vs 88.0, a gain of 3.3 points.\n")
    assert not _flags("We reach 91.2 vs 88.0, a gain of 3.1 points.\n")


def test_coarse_numbers_widen_the_interval_rather_than_narrow_it():
    """91 vs 88 admits anything in (2, 4), so 3.9 must not be flagged."""
    assert not _flags("We reach 91 vs 88, a gain of 3.9 points.\n")


def test_one_extra_decimal_does_not_create_a_false_positive():
    assert not _flags("We reach 91.20 vs 88.00, a gain of 3.2 points.\n")


def test_relative_improvement_that_is_right_is_not_flagged():
    assert not _flags("Accuracy improves from 88.0 to 91.2, a 3.6% relative improvement.\n")


def test_missing_baseline_is_not_guessed_at():
    """The comparison value is in a table, not the sentence. Say nothing."""
    assert _check("Our method delivers a gain of 4.2 points over the baseline.\n") == []


def test_a_lone_number_is_not_a_comparison():
    assert _check("We reach 91.2 on the benchmark.\n") == []


def test_no_numbers_at_all():
    assert _check("Our method improves substantially over the baseline.\n") == []


def test_percentages_of_a_total_are_not_differences():
    assert _check("Of the 817 rows, 412 are held out for evaluation.\n") == []


def test_version_numbers_are_not_benchmark_scores():
    assert _check("We compare v1.2.3 against v1.4.0 in the appendix.\n") == []


def test_years_in_a_comparison_are_not_scores():
    assert _check("Results from 2019 compared to 2021 show the trend.\n") == []


def test_citation_brackets_do_not_become_operands():
    assert _check("Prior work [12] reports 88.0, compared to our 91.2 [3].\n") == []


def test_two_measurements_each_are_not_a_gap():
    """A real false positive found on an actual corpus.

    This is coverage and width for two methods, so 5.48 is the second
    width, not the gap between 3.93 and 88.3. The "at" is the tell.
    """
    assert not _flags("The original wins on coverage and width: 89.0% at 3.93pp "
                      "versus 88.3% at 5.48pp.\n")


def test_a_bare_delta_right_after_the_pair_is_still_read():
    """0.49 - 0.35 is 0.14, which is 14pp, so this one is consistent."""
    assert not _flags("Accuracy is 0.35 vs 0.49, about -14pp from this choice alone.\n")
    assert _flags("Accuracy is 0.35 vs 0.49, about -25pp from this choice alone.\n")


def test_proportions_against_percentage_points(tmp_path):
    assert _verified("Scores were 0.35 vs 0.49, a gain of 14 percentage points.\n")


def test_dividing_by_a_value_that_could_be_zero_is_skipped():
    assert _check("Error goes from 0 to 0.4, a 40% relative increase.\n") == []


# ------------------------------------------------------------ plumbing
def test_it_is_a_paid_check(tmp_path):
    (tmp_path / "a.md").write_text("We reach 91.2 vs 88.0, a gain of 9.9 points.\n")
    free, skipped = cli.run(str(tmp_path), paid=False)
    assert not [f for f in free if f.check == "benchmark"]
    assert "benchmark" in skipped
    paid, _ = cli.run(str(tmp_path), paid=True)
    assert [f for f in paid if f.check == "benchmark"]


def test_line_numbers_point_at_the_sentence(tmp_path):
    text = "# Results\n\nSome preamble here.\n\nWe reach 91.2 vs 88.0, a gain of 9.9 points.\n"
    assert _flags(text)[0].line == 5


def test_interval_helpers_are_symmetric():
    assert benchmarks._abs_iv(-3.3, -3.1) == (3.1, 3.3)
    assert benchmarks._abs_iv(-1.0, 2.0) == (0.0, 2.0)
    assert benchmarks._overlaps((1.0, 2.0), (2.0, 3.0))
    assert not benchmarks._overlaps((1.0, 2.0), (2.5, 3.0))
