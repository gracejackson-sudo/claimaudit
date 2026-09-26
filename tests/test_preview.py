"""--top preview mode: rank FLAGGED findings so the first-time reader sees the
most-signal ones first, without changing what the checks decide."""
from __future__ import annotations

import pytest

from claimaudit import cli
from claimaudit.report import (Finding, FLAGGED, UNVERIFIABLE, VERIFIED,
                                render_text, _priority)


def _f(check, message, status=FLAGGED, file="paper.tex", line=1, evidence="", extra=None):
    return Finding(check, status, file, line, message, evidence, extra or {})


# ------------------------------------------------------------ _priority()


class TestPriorityOrdering:
    """The ordering that decides who is shown first. FLAGGED only; everything
    else must be below every FLAGGED finding so --top never surfaces a
    VERIFIED or UNVERIFIABLE where a FLAGGED one was hidden."""

    def test_unread_scan_files_come_first(self):
        scan = _f("scan", "not audited: 40MB > limit")
        cons = _f("consistency", "says 138 rows in one file and 131 in another")
        assert _priority(scan) < _priority(cons)

    def test_consistency_ranks_above_overclaim(self):
        cons = _f("consistency", "138 vs 131 rows")
        ov = _f("overclaim", "absolute: 'always'")
        assert _priority(cons) < _priority(ov)

    def test_bibliography_authors_differ_ranks_with_the_numeric_flags(self):
        cit = _f("citation", "arXiv:2403.03868: bib entry does not match arXiv record: authors differ")
        ov = _f("overclaim", "priority claim: 'first'")
        assert _priority(cit) < _priority(ov)

    def test_registry_prose_vs_tag_ranks_above_missing_from_registry(self):
        mismatch = _f("registry", "mae: the text says 0.75 but its tag says 0.7548")
        missing = _f("registry", "n_rows: tagged in the text but missing from claims.json")
        assert _priority(mismatch) < _priority(missing)

    def test_dead_url_ranks_below_authors_differ(self):
        authors = _f("citation", "bib entry does not match arXiv record: authors differ")
        dead = _f("citation", "URL is dead (404): https://example.org/x")
        assert _priority(authors) < _priority(dead)

    def test_negated_overclaim_ranks_below_non_negated(self):
        pos = _f("overclaim", "absolute: 'never'")
        neg = _f("overclaim", "absolute: 'never' (negated)")
        assert _priority(pos) < _priority(neg)

    def test_flagged_ranks_above_unverifiable_and_verified(self):
        low_flag = _f("overclaim", "certainty word: 'clearly' (negated)")
        u = _f("citation", "arXiv:1810.04805: could not reach arXiv", status=UNVERIFIABLE)
        v = _f("source", "42 matches data.csv: rows", status=VERIFIED)
        assert _priority(low_flag) < _priority(u)
        assert _priority(low_flag) < _priority(v)


# ------------------------------------------------------------ render_text(top=)


class TestTopReport:
    def _report(self, findings, top=5):
        return render_text(findings, ran=[f.check for f in findings], top=top)

    def test_the_preview_header_names_the_number_requested(self):
        r = self._report([_f("overclaim", "absolute: 'always'")], top=3)
        assert "preview: top 3" in r

    def test_the_top_flagged_are_chosen_by_priority(self):
        fs = [_f("overclaim", "absolute: 'always'", file="a.md", line=1),
              _f("consistency", "3 vs 5 rows", file="a.md", line=2),
              _f("overclaim", "certainty word: 'clearly'", file="a.md", line=3)]
        r = self._report(fs, top=1)
        assert "consistency" in r
        # only one shown
        assert r.count("[top") == 1
        assert "3 vs 5 rows" in r

    def test_scan_findings_are_always_shown_even_if_top_is_zero(self):
        fs = [_f("scan", "not audited: 40MB > limit", file="big.md", line=0),
              _f("overclaim", "absolute: 'always'", file="a.md", line=1)]
        r = self._report(fs, top=0)
        assert "[scan]" in r and "big.md" in r
        assert "no FLAGGED findings" in r

    def test_hidden_count_is_reported_when_shown_less_than_flagged(self):
        fs = [_f("overclaim", f"absolute: 'never' (#{i})", line=i) for i in range(5)]
        r = self._report(fs, top=2)
        assert "3 more flagged" in r

    def test_no_hidden_count_when_top_gte_total(self):
        fs = [_f("overclaim", "absolute: 'always'")]
        r = self._report(fs, top=10)
        assert "more flagged" not in r

    def test_unverifiable_and_verified_are_reported_but_not_listed(self):
        fs = [_f("overclaim", "absolute: 'always'"),
              _f("citation", "not checked (offline)", status=UNVERIFIABLE),
              _f("citation", "resolves", status=VERIFIED)]
        r = self._report(fs, top=5)
        assert "1 UNVERIFIABLE" in r
        assert "1 VERIFIED" in r
        # but their evidence should not be printed
        assert "resolves" not in r or r.count("resolves") == 0

    def test_summary_line_is_present_in_preview(self):
        fs = [_f("overclaim", "absolute: 'always'")]
        r = self._report(fs, top=3)
        assert "summary" in r and "overclaim" in r

    def test_preview_with_no_flagged_says_so(self):
        fs = [_f("citation", "resolves", status=VERIFIED)]
        r = self._report(fs, top=5)
        assert "no FLAGGED findings" in r


# ------------------------------------------------------------ CLI wiring


class TestCli:
    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAIMAUDIT_HOME", str(tmp_path / "home"))
        monkeypatch.setenv("CLAIMAUDIT_CACHE", str(tmp_path / "cache"))
        monkeypatch.delenv("CLAIMAUDIT_LICENSE_KEY", raising=False)

    def test_top_flag_is_visible_in_help(self, capsys):
        with pytest.raises(SystemExit):
            cli.main(["check", "--help"])
        assert "--top" in capsys.readouterr().out

    def test_top_and_json_together_error(self, tmp_path, capsys):
        (tmp_path / "a.md").write_text("Fine.\n")
        assert cli.main(["check", str(tmp_path), "--top", "5", "--json"]) == 2

    def test_negative_top_errors(self, tmp_path, capsys):
        (tmp_path / "a.md").write_text("Fine.\n")
        assert cli.main(["check", str(tmp_path), "--top", "-1"]) == 2

    def test_top_zero_is_valid_and_shows_summary(self, tmp_path, capsys):
        (tmp_path / "a.md").write_text("This method is always the best.\n")
        rc = cli.main(["check", str(tmp_path), "--only", "overclaim", "--top", "0"])
        out = capsys.readouterr().out
        assert "preview: top 0" in out
        assert "no FLAGGED findings" in out
        assert "summary" in out

    def test_top_ranks_paid_checks_ahead_of_overclaim(self, tmp_path, capsys, monkeypatch):
        """A paid buyer running --top on their repo should see the strong
        catches (consistency here) before the noisy overclaim ones."""
        monkeypatch.setenv("CLAIMAUDIT_LICENSE_KEY", "k")
        (tmp_path / "a.md").write_text(
            "The dataset has 138 rows in this careful sentence about the corpus.\n"
            "This is the first paper to always demonstrate anything.\n"
        )
        (tmp_path / "b.md").write_text(
            "The dataset has 131 rows in this careful sentence about the corpus.\n"
        )
        rc = cli.main(["check", str(tmp_path), "--offline",
                       "--only", "overclaim,consistency", "--top", "1"])
        out = capsys.readouterr().out
        assert "[top 1" in out
        # the shown finding is the consistency one, not the overclaim one
        top_block = out.split("[top")[1].split("summary")[0]
        assert "consistency" in top_block
        assert "overclaim" not in top_block
