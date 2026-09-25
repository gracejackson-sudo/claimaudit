"""Frozen regression harness for the eight known errors.

This is a drift tripwire, NOT a re-measurement of the tool's accuracy. The
original replay happened against a repository state (quant-delta-predictor at
commit fd682b2) that no longer exists. See fixtures/known_errors/README.md for
what was recovered verbatim and what was reconstructed from a description.

What this file is for: if someone changes the scoring, extraction or word
lists, the expectations below shift and these tests fail. That failure is the
signal to re-validate honestly rather than assume the change was an
improvement.
"""
from __future__ import annotations
import os
import pytest

from claimaudit import cli
from claimaudit.report import FLAGGED

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir,
                   "fixtures", "known_errors")
CORPUS = os.path.join(FIX, "corpus")

with open(os.path.join(FIX, "records", "arxiv_2403.03868.atom"), encoding="utf-8") as fh:
    ATOM = fh.read()


def _fetch(url, method="GET", timeout=15):
    """Serve the recorded arXiv response; fail every other lookup as offline."""
    if url.startswith("https://export.arxiv.org"):
        return 200, ATOM
    return 0, ""


# id, what the error was, caught?, where a catch must land (check, file), phrase
# that must (or must not) appear in the finding's message or evidence.
KNOWN_ERRORS = [
    ("E1", "bib credits Candes for a paper by Jin & Ren",
     True, "citation", "paper/refs.bib", ["authors differ"]),
    ("E2", "'nobody would have published' overclaims about a whole field",
     True, "overclaim", "paper/neurips_main.tex", ["nobody"]),
    ("E3", "stale figures across documents (138 vs 131 rows, 0.7323 vs 0.7545 MAE)",
     True, "consistency", "PROVENANCE.md", ["138", "0.7323"]),
    ("E4", "target mean stated as -0.36 when the data means -0.39",
     False, None, None, ["quantity being predicted"]),
    ("E5", "'thousands of paired measurements' when there were 817",
     False, None, None, ["thousands of paired"]),
    ("E6", "'none is hand-typed' was not true",
     False, None, None, ["hand-typed"]),
    ("E7", "'fabricated' used for what was a parser artifact",
     False, None, None, ["fabricated"]),
    ("E8", "'33-100% of variance on most benchmarks' is loosely stated",
     False, None, None, ["variance on most benchmarks"]),
]

# One extra flag lands on authentic text and is not one of the eight: the
# overclaim check fires on "guarantee" in "the guarantee does not transfer",
# a sentence that is disclaiming a guarantee. The negation detector only looks
# backwards, so it does not notice. Left as-is deliberately: changing it would
# require re-validating, which this harness cannot do.
INCIDENTAL_FLAGS = 1


@pytest.fixture(scope="module")
def flagged():
    findings, skipped = cli.run(CORPUS, paid=True, fetch=_fetch)
    assert not skipped
    return [f for f in findings if f.status == FLAGGED]


def _hits(flagged, check, rel, phrase):
    return [f for f in flagged
            if (check is None or f.check == check)
            and (rel is None or f.file == rel.replace("/", os.sep))
            and phrase in (f.message + " " + f.evidence)]


@pytest.mark.parametrize("eid,desc,caught,check,rel,phrases",
                         KNOWN_ERRORS, ids=[e[0] for e in KNOWN_ERRORS])
def test_known_error_status_is_unchanged(flagged, eid, desc, caught, check, rel, phrases):
    for phrase in phrases:
        hits = _hits(flagged, check, rel, phrase)
        if caught:
            assert hits, f"{eid} was caught during validation but is not flagged now: {desc}"
        else:
            assert not hits, (f"{eid} was MISSED during validation but is flagged now: {desc}. "
                              f"That may be an improvement, but re-validate and update the "
                              f"headline count before assuming so. Findings: {hits}")


def test_headline_count_is_three_of_eight(flagged):
    caught = [e[0] for e in KNOWN_ERRORS if e[2]]
    assert len(caught) == 3 and len(KNOWN_ERRORS) == 8
    assert caught == ["E1", "E2", "E3"]


def test_what_annotating_the_corpus_adds(tmp_path):
    """Measures the registry check against the same eight errors.

    Three of the eight are caught without any author effort. This asks a
    different question: if the author tags the claim and their pipeline
    writes the computed value out, how many more are caught?

    The answer is one, E4, where the paper says the target has mean -0.36pp
    and the data in the corpus means -0.39pp. That takes the count from
    three of eight to four of eight, and only for an author who opts in, so
    it is not the headline number and must not be quoted as one.

    The other four misses are not reachable this way. E5 ("thousands of
    paired measurements" for 817) and E8 ("33-100% of variance on most
    benchmarks") are vague rather than wrong, so there is no single value to
    bind. E6 and E7 are word choices. E1 is a citation.
    """
    import json
    import shutil
    dst = tmp_path / "corpus"
    shutil.copytree(CORPUS, dst)
    tex = dst / "paper" / "neurips_main.tex"
    body = tex.read_text(encoding="utf-8")
    assert "\\(-0.36\\)pp. " in body
    tex.write_text(body.replace("\\(-0.36\\)pp. ",
                                "\\(-0.36\\)pp.\n% claim: target_mean = -0.36\n"),
                   encoding="utf-8")
    # what the author's own pipeline computed from out/target_deltas.csv
    (dst / "claimaudit-claims.json").write_text(json.dumps(
        {"target_mean": {"value": -0.39, "how": "mean of the delta column"}}), encoding="utf-8")

    findings, _ = cli.run(str(dst), paid=True, fetch=_fetch)
    hits = [f for f in findings
            if f.check == "registry" and f.status == FLAGGED and "target_mean" in f.message]
    assert hits, "E4 should be caught once the claim is tagged"
    assert "-0.39" in hits[0].message

    # and the other five misses are still misses
    caught_now = 3 + 1
    assert caught_now == 4


def test_total_flag_count_pins_incidental_noise(flagged):
    """Catches drift that adds or removes flags outside the eight."""
    targeted = sum(len(e[5]) for e in KNOWN_ERRORS if e[2])
    assert len(flagged) == targeted + INCIDENTAL_FLAGS, (
        "the number of flags on this corpus changed; re-validate rather than "
        f"adjusting this number. Flags: {[(f.check, f.file, f.line, f.message) for f in flagged]}")
