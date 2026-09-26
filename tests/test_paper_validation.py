"""Regressions from the precision validation against fifteen real ML papers.

Every test here pins a false positive or a silent pass that the tool produced
on arXiv LaTeX source (Attention Is All You Need, BERT, GPT-3, ResNet, VGG,
Adam, ViT, RoBERTa, LLaMA, Chain-of-Thought, Lottery Ticket, Deep RL that
Matters, InstructGPT, LoRA, Distillation). The sentences quoted below are the
papers' own words, kept verbatim so that a later change that reintroduces the
flag fails here rather than in a re-measurement months from now.

Nothing in this file touches the network.
"""
from __future__ import annotations

import pytest

from claimaudit import citations, cli, overclaim, scan
from claimaudit.claims import sentences
from claimaudit.report import FLAGGED, UNVERIFIABLE


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


def _flags(text, path="paper.tex"):
    """-> [(why, matched text)] from the overclaim check on one document."""
    return [(f.message.split(":")[0], f.message.split("'")[1])
            for f in overclaim.scan([(path, path, text)])]


def _whys(text, path="paper.tex"):
    return {why for why, _m in _flags(text, path)}


# ===================================================================
# A1/A2. arXiv refused the request, and the refusal was reported as
#        the paper not existing -- then cached for a week.
# ===================================================================

REAL_IDS = "BERT is arXiv:1810.04805 and RoBERTa is arXiv:1907.11692.\n"


@pytest.mark.parametrize("status", sorted(citations.BLOCKED))
def test_a_refusal_from_arxiv_is_never_reported_as_a_missing_paper(status):
    """1810.04805 and 1907.11692 both exist; a 406 does not make them vanish."""
    found = citations.check([("a.md", "a.md", REAL_IDS)], [],
                            fetch=lambda *a, **k: (status, ""))
    assert len(found) == 2
    for f in found:
        assert f.status == UNVERIFIABLE, f"status {status} produced {f.status}: {f.message}"
        assert "could not reach arXiv" in f.message
        assert "not found" not in f.message


def test_an_unanswerable_status_is_unverifiable_rather_than_a_guess():
    found = citations.check([("a.md", "a.md", REAL_IDS)], [],
                            fetch=lambda *a, **k: (418, ""))
    assert {f.status for f in found} == {UNVERIFIABLE}
    assert all("418" in f.message for f in found)


def test_a_paper_arxiv_really_does_not_have_is_still_flagged():
    """The fix must not turn the check off: a 200 that omits the id still counts."""
    found = citations.check([("a.md", "a.md", "See arXiv:2999.99999.\n")], [],
                            fetch=lambda *a, **k: (200, "<feed></feed>"))
    assert found[0].status == FLAGGED and "not found on arXiv" in found[0].message


def test_the_arxiv_query_asks_for_one_id_without_max_results():
    """The batched query shape arXiv answers with 406, and so learns nothing.

    Pinned because the failure is invisible from inside: a 406 now reports
    honestly, so the only thing stopping a silent regression to 'everything
    unverifiable' is this test.
    """
    asked = []

    def fetch(url, method="GET", timeout=15):
        asked.append(url)
        return 200, "<feed></feed>"

    citations.check([("a.md", "a.md", REAL_IDS)], [], fetch=fetch)
    assert len(asked) == 2
    for url in asked:
        assert "max_results" not in url
        assert url.count(",") == 0


def test_a_refusal_is_not_written_to_the_cache(tmp_path, monkeypatch):
    """A throttled minute must not become a week of confident wrong answers."""
    monkeypatch.setenv("CLAIMAUDIT_CACHE", str(tmp_path / "c"))
    monkeypatch.setattr(citations, "ARXIV_MIN_INTERVAL", 0)
    calls = []

    class Refused(Exception):
        pass

    def fake_urlopen(req, timeout=15):
        calls.append(req.full_url)
        raise citations.urllib.error.HTTPError(req.full_url, 406, "Not Acceptable", {}, None)

    monkeypatch.setattr(citations.urllib.request, "urlopen", fake_urlopen)
    url = citations.ARXIV_API + "1810.04805"
    assert citations.default_fetch(url) == (406, "")
    assert citations.default_fetch(url) == (406, "")
    assert len(calls) == 2, "the 406 was served from cache instead of being retried"


def test_an_answer_is_still_cached(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAIMAUDIT_CACHE", str(tmp_path / "c"))
    monkeypatch.setattr(citations, "ARXIV_MIN_INTERVAL", 0)
    calls = []

    class Resp:
        status = 200

        def read(self, n=None):
            return b"<feed></feed>"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=15):
        calls.append(req.full_url)
        return Resp()

    monkeypatch.setattr(citations.urllib.request, "urlopen", fake_urlopen)
    url = citations.ARXIV_API + "1810.04805"
    assert citations.default_fetch(url)[0] == 200
    assert citations.default_fetch(url)[0] == 200
    assert len(calls) == 1


def test_the_previous_generation_of_cached_verdicts_is_discarded(tmp_path, monkeypatch):
    """Entries written before the fix carry the wrong answers; ignore them."""
    import json
    import os
    import re

    cache = tmp_path / "c"
    cache.mkdir()
    monkeypatch.setenv("CLAIMAUDIT_CACHE", str(cache))
    monkeypatch.setattr(citations, "ARXIV_MIN_INTERVAL", 0)
    url = citations.ARXIV_API + "1810.04805"
    key = os.path.join(str(cache), re.sub(r"[^A-Za-z0-9]", "_", "GET" + url)[:180] + ".json")
    with open(key, "w") as fh:                      # a version-1 entry, no "v"
        json.dump({"s": 200, "b": "<feed>poisoned</feed>"}, fh)

    def fake_urlopen(req, timeout=15):
        raise OSError("no network")

    monkeypatch.setattr(citations.urllib.request, "urlopen", fake_urlopen)
    assert citations.default_fetch(url) == (0, ""), "a stale entry was served"
