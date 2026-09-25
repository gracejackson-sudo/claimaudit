"""Seed and variance claims: can the reported numbers describe the same runs?"""
from __future__ import annotations

import re

import pytest

from claimaudit import cli, seeds
from claimaudit.report import VERIFIED, FLAGGED, UNVERIFIABLE


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAIMAUDIT_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CLAIMAUDIT_LICENSE_KEY", raising=False)


def _check(text):
    return seeds.check([("a.md", "/a.md", text)])


def _flags(text):
    return [f for f in _check(text) if f.status == FLAGGED]


def _unver(text):
    return [f for f in _check(text) if f.status == UNVERIFIABLE]


# ------------------------------------------------------------ mean from values
def test_stated_mean_that_does_not_follow_from_the_values(tmp_path):
    f = _flags("Over 3 seeds: 91.2, 90.8, 91.5, giving 95.0 \u00b1 0.3.\n")
    assert any("average 91.1667" in x.message or "average 91.167" in x.message for x in f)


def test_stated_mean_that_does_follow_is_verified():
    f = _check("Over 3 seeds: 91.2, 90.8, 91.5, giving 91.2 \u00b1 0.35.\n")
    assert any(x.status == VERIFIED for x in f)
    assert not [x for x in f if x.status == FLAGGED]


def test_rounding_in_the_values_does_not_create_a_false_mean_error():
    """90.8, 91.2, 91.5 average 91.1667, and 91.2 is the honest rounding."""
    assert not [x for x in _flags("Across 3 runs: 90.8, 91.2, 91.5, so 91.2 \u00b1 0.36.\n")
                if "average" in x.message]


def test_plus_minus_written_as_ascii():
    assert _flags("Over 3 seeds: 91.2, 90.8, 91.5, giving 95.0 +/- 0.3.\n")


# ------------------------------------------------------------ spread from values
def test_stated_spread_that_does_not_follow_from_the_values():
    f = _flags("Over 3 seeds: 91.2, 90.8, 91.5, giving 91.2 \u00b1 9.0.\n")
    assert any("spread" in x.message for x in f)


def test_both_spread_conventions_are_accepted():
    """Sample std is 0.351, population std is 0.287. Neither may be flagged."""
    for stated in ("0.35", "0.29"):
        f = _flags(f"Over 3 seeds: 91.2, 90.8, 91.5, giving 91.2 \u00b1 {stated}.\n")
        assert not [x for x in f if "spread" in x.message], stated


# ------------------------------------------------------------ seed count
def test_seed_count_disagreeing_with_the_values_listed():
    f = _flags("Over 5 seeds: 91.2, 90.8, 91.5.\n")
    assert f and "5 runs are claimed but 3" in f[0].message


def test_matching_seed_count_is_silent():
    assert not [x for x in _flags("Over 3 seeds: 91.2, 90.8, 91.5.\n")
                if "claimed but" in x.message]


def test_seed_identifiers_are_not_measurements():
    """"seeds: 0, 1, 2" says which seeds ran, it is not three results."""
    assert _check("We use 5 seeds: 0, 1, 2.\n") == []
    assert _check("Random seeds: 13, 42, 1337.\n") == []


# ------------------------------------------------------------ standard error
def test_standard_error_inconsistent_with_the_deviation():
    f = _flags("Across 4 runs the standard deviation is 2.0 "
               "and the standard error is 1.5.\n")
    assert f and "standard error of 1" in f[0].message


def test_consistent_standard_error_is_silent():
    assert not _flags("Across 4 runs the standard deviation is 2.0 "
                      "and the standard error is 1.0.\n")


# ------------------------------------------------------------ overlap, reported not flagged
def test_overlapping_error_bars_called_significant_are_reported_as_unverifiable():
    u = _unver("Our 91.2 \u00b1 0.9 is significantly better than the baseline's 90.8 \u00b1 1.1.\n")
    assert u and "the two stated intervals overlap" in u[0].message


def test_it_is_not_called_an_error():
    f = _check("Our 91.2 \u00b1 0.9 is significantly better than the baseline's 90.8 \u00b1 1.1.\n")
    assert not [x for x in f if x.status == FLAGGED]


def test_a_named_test_means_we_say_nothing():
    """If they ran a test, overlapping bars are not our business."""
    assert _unver("Our 91.2 \u00b1 0.9 beats 90.8 \u00b1 1.1 significantly "
                  "(Wilcoxon, p < 0.01).\n") == []


def test_non_overlapping_bars_are_silent():
    assert _unver("Our 95.0 \u00b1 0.2 is significantly better than 90.8 \u00b1 0.3.\n") == []


def test_significance_without_two_intervals_is_silent():
    assert _unver("The improvement is significant.\n") == []


# ------------------------------------------------------------ must stay silent
def test_prose_with_no_seed_claims():
    assert _check("We trained the model on eight GPUs for three days.\n") == []


def test_a_lone_mean_and_spread_is_not_checkable():
    assert _check("We report 91.2 \u00b1 0.9 on the test set.\n") == []


def test_two_values_are_too_few_to_be_a_list():
    assert _check("Over 2 seeds: 91.2, 90.8.\n") == []


def test_division_by_one_run_is_skipped():
    assert not _flags("Across 1 runs the standard deviation is 2.0 "
                      "and the standard error is 9.0.\n")


# ------------------------------------------------- what the plus-minus means
FIVE = "Across 5 seeds: 90.0, 90.5, 91.0, 91.5, 92.0, mean 91.0 \u00b1 "


def test_a_standard_error_is_not_read_as_a_standard_deviation():
    """0.79/sqrt(5) is 0.35, and "mean +/- s.e.m." is ubiquitous."""
    assert not _flags(FIVE + "0.35 (standard error).\n")
    assert not _flags(FIVE + "0.35 s.e.m.\n")


def test_a_confidence_half_width_is_not_read_as_a_standard_deviation():
    """t(4) * 0.354 is 0.98."""
    assert not _flags(FIVE + "0.98 (95% CI).\n")


def test_a_named_convention_is_held_to():
    """Naming the convention is information, so it is used: 0.35 is the
    standard error here, not the confidence half-width."""
    f = _flags(FIVE + "0.35 (95% CI).\n")
    assert f and "confidence half-width" in f[0].message


def test_an_unstated_convention_accepts_any_of_the_three():
    assert not _flags(FIVE + "0.79.\n")     # standard deviation
    assert not _flags(FIVE + "0.35.\n")     # standard error
    assert not _flags(FIVE + "0.98.\n")     # confidence half-width
    assert _flags(FIVE + "9.9.\n")          # none of them


def test_the_spread_message_says_what_it_assumed():
    """Silence about the convention would be an overclaim."""
    f = _flags(FIVE + "9.9.\n")
    assert f and "does not say which" in f[0].evidence
    assert "standard deviation" in f[0].message and "standard error" in f[0].message


# ------------------------------------------------- the pair must be the list's
def test_an_unrelated_plus_minus_is_not_bound_to_the_values():
    assert _check("Per-seed accuracy: 90.0, 91.0, 92.0, "
                  "with a target of 95.0 \u00b1 1.0.\n") == []


def test_a_median_is_not_checked_as_a_mean():
    """1.0, 2.0, 9.0 have a median of 2.0, and the sentence is correct."""
    assert _check("Across 3 seeds: 1.0, 2.0, 9.0, median 2.0 \u00b1 4.4.\n") == []


def test_a_list_that_is_not_per_seed_is_not_counted_against_the_seeds():
    assert _check("We evaluate with 5 seeds and report per-epoch losses: "
                  "1.20, 1.10, 1.05, 1.01, 0.99, 0.98.\n") == []


def test_per_seed_lists_are_still_read():
    assert _flags("Per-seed accuracy: 90.0, 91.0, 92.0, giving 95.0 \u00b1 1.0.\n")


# ------------------------------------------------------------ cross references
def test_a_table_number_is_not_a_standard_error():
    assert _check("We report the standard deviation of 0.90 across 5 seeds, "
                  "and the standard error in Table 2 is 0.40.\n") == []


# ------------------------------------------------------------ wording
def test_a_physical_tolerance_is_not_an_error_bar():
    assert _check("The chamber was held at 20 \u00b1 2 degrees and the sensor "
                  "reads 21 \u00b1 1 degrees, a significant drift.\n") == []


def test_an_integer_list_skipped_as_seed_numbers_says_so(tmp_path):
    """Keeping the skip is defensible; keeping quiet about it is not."""
    u = _unver("Scores across 3 seeds: 88, 91, 94, mean 90 \u00b1 3.\n")
    assert u and "read as seed numbers" in u[0].message
    assert "not checked" in u[0].message


def test_the_skip_is_not_announced_when_there_is_nothing_to_check():
    assert _check("We use 5 seeds: 0, 1, 2.\n") == []
    assert _check("Random seeds: 13, 42, 1337.\n") == []


def test_a_single_run_is_not_described_in_the_plural():
    f = _flags("Over 1 seeds: 91.2, 90.8, 91.5.\n")
    assert f and "1 run is claimed but 3 values are listed" in f[0].message


# ------------------------------------------------------------ reading numbers
def test_a_number_of_four_or_more_digits_is_matched_whole():
    for raw in ("2023", "2048", "1000000", "1,000.0", "91.25"):
        assert re.match(seeds.N, raw).group(0) == raw, raw


def test_a_thousands_separator_is_not_a_value_separator():
    """raw.split(",") turned three values into six and flagged all of them."""
    assert seeds._values("1,000.0, 2,000.0, 3,000.0") == ["1,000.0", "2,000.0", "3,000.0"]


def test_thousands_separated_values_do_not_produce_false_flags():
    assert _flags("Over 3 seeds: 1,000.0, 2,000.0, 3,000.0, "
                  "giving 2,000.0 \u00b1 1,000.0.\n") == []


# ------------------------------------------------------------ plumbing
def test_it_is_a_paid_check(tmp_path):
    (tmp_path / "a.md").write_text("Over 5 seeds: 91.2, 90.8, 91.5.\n")
    free, skipped = cli.run(str(tmp_path), paid=False)
    assert not [f for f in free if f.check == "seeds"]
    assert "seeds" in skipped
    paid, _ = cli.run(str(tmp_path), paid=True)
    assert [f for f in paid if f.check == "seeds"]


def test_std_helper_conventions():
    assert seeds._std([1.0, 2.0, 3.0], 0) == pytest.approx(0.8164965809)
    assert seeds._std([1.0, 2.0, 3.0], 1) == pytest.approx(1.0)
    assert seeds._std([1.0], 1) is None
