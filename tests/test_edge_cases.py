"""Edge cases and an adversarial audit of claimaudit itself.

Two kinds of test live here.

* Plain tests pin behaviour that is a deliberate tradeoff. They are
  documentation: change them only on purpose.
* `xfail(strict=True)` tests state what the tool *should* do about a real
  defect found while auditing it. They are expected to fail today. When
  someone fixes the defect the test passes, strict mode turns that into a
  failure, and whoever did the work is told to remove the marker and record
  the change. Nothing here silently encodes a bug as correct.

None of these tests touch the network or the license server.
"""
from __future__ import annotations
import json
import os

import pytest

from claimaudit import cli, scan, sources, citations, claims
from claimaudit.claims import extract_numbers
from claimaudit.report import VERIFIED, FLAGGED, UNVERIFIABLE, render_text


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAIMAUDIT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CLAIMAUDIT_CACHE", str(tmp_path / "cache"))
    monkeypatch.delenv("CLAIMAUDIT_LICENSE_KEY", raising=False)


def _write(d, files):
    for name, text in files.items():
        p = d / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return str(d)


def offline_fetch(url, method="GET", timeout=15):
    return 0, ""


# ===================================================================
# A. Silent caps. The consistency check used to drop findings without
#    saying so. These cover the same class of defect elsewhere.
# ===================================================================

@pytest.mark.xfail(strict=True, reason="row cap is silent and can endorse a wrong number")
def test_row_cap_must_not_manufacture_a_false_verified(tmp_path, monkeypatch):
    """The worst failure this tool has: VERIFIED on a number that is wrong.

    A CSV longer than MAX_ROWS is read only up to the cap. The row count and
    every aggregate are then computed over the truncated set, so prose
    quoting the truncated figure is confirmed as correct.
    """
    monkeypatch.setattr(sources, "MAX_ROWS", 247)
    rows = "\n".join(f"m{i},{i}" for i in range(400))
    d = _write(tmp_path, {"a.md": "The big table has 247 scored rows.\n",
                          "big.csv": "model,score\n" + rows + "\n"})
    f, _ = cli.run(d, only=["source"], paid=True)
    assert f[0].status != VERIFIED, (
        "the file really has 400 rows; confirming 247 is worse than saying nothing")


@pytest.mark.xfail(strict=True, reason="row cap silently corrupts every aggregate")
def test_row_cap_must_not_corrupt_aggregates(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, "MAX_ROWS", 100)
    vals = [1.2345] * 100 + [99.0] * 100          # true mean 50.11725
    rows = "\n".join(f"c{i},{v}" for i, v in enumerate(vals))
    d = _write(tmp_path, {"a.md": "Mean latency in the metrics was 1.2345 ms.\n",
                          "metrics.csv": "checkpoint,latency\n" + rows + "\n"})
    f, _ = cli.run(d, only=["source"], paid=True)
    assert f[0].status != VERIFIED, "the true mean is 50.12, not 1.2345"


@pytest.mark.xfail(strict=True, reason="oversized files are dropped with no signal")
def test_oversized_file_skip_must_be_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(scan, "MAX_BYTES", 50)
    d = _write(tmp_path, {"small.md": "Tiny note.\n",
                          "huge.md": "This file exceeds the size limit. " * 5})
    findings, _ = cli.run(d, only=["overclaim"], paid=True)
    text = render_text(findings)
    assert "huge.md" in text or "skipped" in text.lower(), (
        "a file that was never read must not be indistinguishable from a clean one")


def test_url_cap_truncates_silently(tmp_path):
    """Documents current behaviour: --max-urls drops the rest without a word."""
    doc = " ".join(f"https://e{i}.example/x" for i in range(70))
    found = citations.check([("a.md", "a.md", doc)], [], fetch=lambda *a, **k: (200, ""),
                            max_urls=60)
    assert len(found) == 60          # 10 URLs never checked, and never mentioned


# ===================================================================
# B. Robustness. Input the tool will meet in a real repository.
# ===================================================================

@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root can read anything")
@pytest.mark.xfail(strict=True, reason="an unreadable file aborts the whole run")
def test_unreadable_file_must_not_abort_the_run(tmp_path):
    d = _write(tmp_path, {"ok.md": "The first proven method.\n",
                          "locked.md": "Some 42 rows claim.\n"})
    locked = tmp_path / "locked.md"
    locked.chmod(0o000)
    try:
        findings, _ = cli.run(d, only=["overclaim"], paid=True)
    except PermissionError:
        pytest.fail("one unreadable file kills the audit of every other file")
    finally:
        locked.chmod(0o644)
    assert findings


@pytest.mark.xfail(strict=True, reason="unparseable data files are ignored in silence")
def test_corrupt_data_file_must_be_reported(tmp_path):
    """'no matching value' and 'I could not read your data' are different facts."""
    d = _write(tmp_path, {"a.md": "Mean coverage in the results was 90.3%.\n",
                          "results.json": "{ this is not valid json "})
    findings, _ = cli.run(d, only=["source"], paid=True)
    blob = " ".join(f.message + f.evidence for f in findings)
    assert "parse" in blob.lower() or "could not read" in blob.lower()


@pytest.mark.xfail(strict=True, reason="scanning nothing looks exactly like a clean pass")
def test_scanning_zero_files_must_not_look_clean(tmp_path):
    """The most dangerous UX defect: silence reading as approval.

    Point the tool at a source folder, or typo a path component, and it
    prints an empty report and exits 0 — the same output as a document set
    with nothing wrong in it.
    """
    d = _write(tmp_path, {"main.py": "x = 1\n", "notes.docx": "not really a docx\n"})
    findings, skipped = cli.run(d, paid=True)
    assert not findings
    text = render_text(findings, skipped)
    assert "0 files" in text or "no files" in text.lower(), (
        "an empty report must say it found nothing to read")


def test_symlinked_directories_are_not_followed(tmp_path):
    """os.walk does not follow links, so a cycle cannot hang the walk."""
    d = tmp_path / "docs"
    d.mkdir()
    (d / "a.md").write_text("The 42 rows here.\n")
    try:
        os.symlink(str(d), str(d / "loop"))
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    _base, text, _data, _bib = scan.discover(str(d))
    assert [r for r, _ in text] == ["a.md"]


# ===================================================================
# C. Text and encoding.
# ===================================================================

def test_crlf_line_numbers_stay_correct():
    got = list(claims.sentences("Line one.\r\nLine two has 42 rows.\r\nLine three.\r\n", "a.md"))
    assert [l for l, _ in got] == [1, 2, 3]


@pytest.mark.xfail(strict=True, reason="read() uses utf-8, not utf-8-sig, so a BOM survives")
def test_byte_order_mark_does_not_leak_into_claims(tmp_path):
    p = tmp_path / "a.md"
    p.write_bytes("\ufeff# Heading\n\nThe 42 rows here.\n".encode("utf-8"))
    assert "\ufeff" not in scan.read(str(p))


def test_hidden_directories_are_skipped(tmp_path):
    """Deliberate, but it means docs under .github are never audited."""
    d = _write(tmp_path, {"ok.md": "Fine.\n", ".github/README.md": "The first proven method.\n"})
    _base, text, _data, _bib = scan.discover(d)
    assert [r for r, _ in text] == ["ok.md"]


def test_empty_and_whitespace_only_files_are_harmless(tmp_path):
    d = _write(tmp_path, {"empty.md": "", "blank.md": "   \n\n\t\n"})
    findings, _ = cli.run(d, paid=True, fetch=offline_fetch)
    assert findings == []


# ===================================================================
# D. Number extraction.
# ===================================================================

def test_unicode_minus_is_understood():
    assert [(n.raw, n.value) for n in extract_numbers("Delta was \u22120.39 pp.")] \
        == [("\u22120.39 pp", -0.39)]


@pytest.mark.xfail(strict=True, reason="scientific notation is not matched at all")
def test_scientific_notation_is_extracted():
    assert extract_numbers("We ran 1.5e6 evaluations.")


def test_four_digit_counts_that_look_like_years_are_dropped():
    """A real recall gap: '2026 rows' is a claim, but the year filter eats it."""
    assert extract_numbers("The corpus has 2026 rows.") == []
    assert [n.value for n in extract_numbers("The corpus has 2027 rows.")] == []


def test_currency_and_structural_numbers_stay_out():
    assert extract_numbers("It costs $19 one-time.") == []
    assert extract_numbers("See Table 4 and Figure 2.") == []


def test_percent_with_space_before_sign():
    assert [(n.raw, n.kind) for n in extract_numbers("Coverage was 90.1 % overall.")] \
        == [("90.1 %", "pct")]


# ===================================================================
# E. Report and CLI contracts.
# ===================================================================

def test_duplicate_and_padded_only_values_are_accepted(tmp_path, capsys):
    _write(tmp_path, {"a.md": "The first proven method.\n"})
    assert cli.main(["check", str(tmp_path), "--only", " overclaim , overclaim "]) == 1


def test_json_output_carries_every_finding(tmp_path, capsys):
    _write(tmp_path, {"a.md": "This is the first proven novel method that always beats everything.\n"})
    assert cli.main(["check", str(tmp_path), "--json", "--exit-zero"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["findings"]) == payload["summary"]["overclaim"][FLAGGED]
    assert payload["limits"]


def test_exit_code_is_one_when_anything_is_flagged(tmp_path):
    _write(tmp_path, {"a.md": "The first proven method.\n"})
    assert cli.main(["check", str(tmp_path)]) == 1
    assert cli.main(["check", str(tmp_path), "--exit-zero"]) == 0
