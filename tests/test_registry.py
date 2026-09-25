"""Claim-registry checking: tagged numbers against recomputed values."""
from __future__ import annotations
import json

import pytest

from claimaudit import cli, registry
from claimaudit.report import VERIFIED, FLAGGED, UNVERIFIABLE, render_text


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAIMAUDIT_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CLAIMAUDIT_LICENSE_KEY", raising=False)


def _run(tmp_path, doc, reg, name="claims.json", doc_name="a.md"):
    (tmp_path / doc_name).write_text(doc, encoding="utf-8")
    p = tmp_path / name
    p.write_text(reg if isinstance(reg, str) else json.dumps(reg), encoding="utf-8")
    return cli.run(str(tmp_path), only=["registry"], paid=True)[0]


def _registry_file(tmp_path, reg, name="claims.json"):
    p = tmp_path / name
    p.write_text(reg if isinstance(reg, str) else json.dumps(reg), encoding="utf-8")
    return p


def _by(findings, status):
    return [f for f in findings if f.status == status]


# ------------------------------------------------------------ the three checks
def test_agreeing_claim_is_verified(tmp_path):
    f = _run(tmp_path, "Built on 817<!-- claim: n_rows = 817 --> evaluations.\n", {"n_rows": 817})
    assert len(f) == 1 and f[0].status == VERIFIED and "n_rows" in f[0].message


def test_prose_edited_without_updating_its_tag_is_caught(tmp_path):
    """The check a hand-rolled registry script usually lacks."""
    f = _run(tmp_path, "Coverage hit 91.2<!-- claim: cov = 90.1 -->%.\n", {"cov": 90.1})
    assert f[0].status == FLAGGED
    assert "text says 91.2" in f[0].message and "tag says 90.1" in f[0].message


def test_tag_disagreeing_with_the_registry_is_caught(tmp_path):
    f = _run(tmp_path, "The MAE was 0.7545<!-- claim: mae = 0.7545 -->.\n", {"mae": 0.9999})
    assert f[0].status == FLAGGED and "computes 0.9999" in f[0].message


def test_label_missing_from_the_registry_is_caught(tmp_path):
    f = _run(tmp_path, "Delta was -39.5<!-- claim: ghost = -39.5 -->pp.\n", {"other": 1})
    assert f[0].status == FLAGGED and "missing from" in f[0].message


def test_registry_entry_no_document_uses_is_reported(tmp_path):
    f = _run(tmp_path, "Nothing tagged here.\n", {"orphan": 5})
    assert _by(f, UNVERIFIABLE)[0].message.startswith("orphan is in the registry")


# ------------------------------------------------------------ tolerance
def test_registry_tolerance_is_honoured(tmp_path):
    doc = "The MAE was 0.7545<!-- claim: mae = 0.7545 -->.\n"
    assert _run(tmp_path, doc, {"mae": {"value": 0.7546, "tolerance": 0.001}})[0].status == VERIFIED
    assert _run(tmp_path, doc, {"mae": {"value": 0.7546, "tolerance": 1e-9}})[0].status == FLAGGED


def test_default_tolerance_follows_printed_precision(tmp_path):
    # 0.75 is stated to two places, so 0.7545 rounds to it and agrees
    assert _run(tmp_path, "MAE 0.75<!-- claim: m = 0.75 -->.\n", {"m": 0.7545})[0].status == VERIFIED
    # stated to four places, it does not
    assert _run(tmp_path, "MAE 0.7500<!-- claim: m = 0.7500 -->.\n", {"m": 0.7545})[0].status == FLAGGED


def test_author_tolerance_does_not_disable_the_printed_place_allowance(tmp_path):
    """The two tolerances measure different things, so they combine.

    The registry format documented in the module docstring supplies a tight
    computational tolerance alongside a four-place value; prose quoting that
    value to two places must still verify.
    """
    doc = "MAE of 0.75<!-- claim: mae = 0.75 -->\n"
    reg = {"mae": {"value": 0.7545, "tolerance": 0.0001}}
    assert _run(tmp_path, doc, reg)[0].status == VERIFIED
    # and the author's tolerance still widens a comparison beyond printing
    assert _run(tmp_path, "MAE of 0.7500<!-- claim: mae = 0.7500 -->\n",
                {"mae": {"value": 0.7545, "tolerance": 0.01}})[0].status == VERIFIED
    assert _run(tmp_path, "MAE of 0.7500<!-- claim: mae = 0.7500 -->\n",
                {"mae": {"value": 0.7545, "tolerance": 0.0001}})[0].status == FLAGGED


def test_scientific_notation_tolerance_scales_with_the_exponent(tmp_path):
    # a 40x error must never read as verified: 1.0e-03 is not 0.04
    f = _run(tmp_path, "Loss fell to 1.0e-03<!-- claim: loss = 1.0e-03 -->.\n", {"loss": 0.04})
    assert f[0].status == FLAGGED
    # and a correctly rounded figure must not be flagged: 2500400 prints as 2.5e+06
    f = _run(tmp_path, "We ran 2.5e+06<!-- claim: n = 2.5e+06 --> steps.\n", {"n": 2500400})
    assert f[0].status == VERIFIED
    f = _run(tmp_path, "We ran 2.5e+06<!-- claim: n = 2.5e+06 --> steps.\n", {"n": 2600000})
    assert f[0].status == FLAGGED


def test_plain_decimal_tolerance_is_unchanged_by_the_exponent_fix(tmp_path):
    assert _run(tmp_path, "MAE 0.75<!-- claim: m = 0.75 -->.\n", {"m": 0.7545})[0].status == VERIFIED
    assert _run(tmp_path, "Rows: 817<!-- claim: n = 817 -->.\n", {"n": 817.4})[0].status == VERIFIED
    assert _run(tmp_path, "Rows: 817<!-- claim: n = 817 -->.\n", {"n": 819})[0].status == FLAGGED


def test_how_it_was_computed_is_carried_into_the_report(tmp_path):
    f = _run(tmp_path, "MAE 0.75<!-- claim: m = 0.75 -->.\n",
             {"m": {"value": 0.75, "how": "row-weighted, leave-one-family-out"}})
    assert "leave-one-family-out" in f[0].evidence


# ------------------------------------------------------------ formats
def test_csv_registry(tmp_path):
    csv_text = "label,value,tolerance,how\nn_rows,817,,counted from dataset.csv\n"
    f = _run(tmp_path, "Built on 817<!-- claim: n_rows = 817 --> rows.\n", csv_text, name="claims.csv")
    assert f[0].status == VERIFIED and "counted from" in f[0].evidence


def test_latex_percent_comment_annotation(tmp_path):
    (tmp_path / "p.tex").write_text("The mean is \\(-0.39\\)pp.\n% claim: target_mean = -0.39\n")
    (tmp_path / "claims.json").write_text(json.dumps({"target_mean": -0.39}))
    f = cli.run(str(tmp_path), only=["registry"], paid=True)[0]
    assert f[0].status == VERIFIED


def test_a_latex_tag_may_carry_a_trailing_note(tmp_path):
    """Everything after % is comment, so people write notes there."""
    (tmp_path / "p.tex").write_text("The mean is \\(-0.39\\)pp.\n"
                                    "% claim: target_mean = -0.39  (mean of the delta column)\n")
    (tmp_path / "claims.json").write_text(json.dumps({"target_mean": -0.39}))
    f = cli.run(str(tmp_path), only=["registry"], paid=True)[0]
    assert f[0].status == VERIFIED


def test_unreadable_registry_is_reported_not_ignored(tmp_path):
    f = _run(tmp_path, "Built on 817<!-- claim: n_rows = 817 --> rows.\n", "{ not json")
    assert len(f) == 1 and f[0].status == FLAGGED
    assert "could not read the claim registry" in f[0].message


def test_no_registry_and_no_tags_is_silent(tmp_path):
    """Nothing was tagged, so the author never opted in; nothing to report."""
    (tmp_path / "a.md").write_text("Built on 817 rows.\n")
    assert cli.run(str(tmp_path), only=["registry"], paid=True)[0] == []


def test_tags_with_no_registry_discovered_are_reported(tmp_path):
    (tmp_path / "a.md").write_text("Built on 817<!-- claim: n_rows = 817 --> rows.\n")
    f = cli.run(str(tmp_path), only=["registry"], paid=True)[0]
    assert len(f) == 1 and f[0].status == UNVERIFIABLE
    assert "1 claim tag(s) were found" in f[0].message
    assert "no claim registry was discovered" in f[0].message
    for name in registry.REGISTRY_NAMES:
        assert name in f[0].evidence


def test_one_bad_entry_does_not_abort_the_whole_registry(tmp_path):
    doc = "Rows: 5<!-- claim: b = 5 -->.\n"
    f = _run(tmp_path, doc, {"a": None, "b": 5})
    assert [x for x in f if x.status == VERIFIED and "b = 5" in x.message]
    bad = [x for x in _by(f, UNVERIFIABLE) if x.message.startswith("a:")]
    assert bad and "skipped" in bad[0].message


def test_entry_shapes_that_carry_no_number_are_named_and_skipped(tmp_path):
    for bad_entry in ({"a": True}, {"a": {"how": "no value key"}}, {"a": "abc"}):
        _, err, problems = registry.load(str(_registry_file(tmp_path, bad_entry)))
        assert err is None
        assert [p for p in problems if p[0] == "a"], bad_entry


def test_a_duplicate_label_is_reported_not_silently_last_wins(tmp_path):
    """json.load collapses repeated keys, so a false FLAG comes out of nowhere."""
    f = _run(tmp_path, "Rows: 5<!-- claim: b = 5 -->.\n", '{"b": 5, "b": 6}')
    assert _by(f, FLAGGED) == []
    assert [x for x in _by(f, UNVERIFIABLE) if "more than once" in x.message]


def test_a_duplicate_csv_row_is_reported(tmp_path):
    csv_text = "label,value\nb,5\nb,6\n"
    f = _run(tmp_path, "Rows: 5<!-- claim: b = 5 -->.\n", csv_text, name="claims.csv")
    assert _by(f, FLAGGED) == []
    assert [x for x in _by(f, UNVERIFIABLE) if "more than once" in x.message]


def test_an_unsupported_registry_format_says_so(tmp_path):
    (tmp_path / "a.md").write_text("Rows: 5<!-- claim: b = 5 -->.\n")
    (tmp_path / "claims.yaml").write_text("b: 5\n")
    f = cli.run(str(tmp_path), only=["registry"], paid=True,
                registry_path=str(tmp_path / "claims.yaml"))[0]
    assert f[0].status == FLAGGED and "only .json and .csv" in f[0].message


def test_a_csv_missing_its_value_column_names_the_column(tmp_path):
    f = _run(tmp_path, "Rows: 5<!-- claim: b = 5 -->.\n", "label,how\nb,counted\n",
             name="claims.csv")
    assert f[0].status == FLAGGED and "no 'value' column" in f[0].message


def test_explicit_registry_path_that_does_not_exist_is_flagged(tmp_path):
    (tmp_path / "a.md").write_text("text\n")
    f = cli.run(str(tmp_path), only=["registry"], paid=True,
                registry_path=str(tmp_path / "nope.json"))[0]
    assert f[0].status == FLAGGED and "no claim registry" in f[0].message


# ------------------------------------------------------------ real-world syntax
def test_syntax_seen_in_practice(tmp_path):
    """Labels with '::', four-decimal tags, bold markup, signs and units."""
    doc = ("measured losses to **-39.5<!-- claim: adv_worst_delta = -39.5000 -->pp**\n\n"
           "calibrated on is -8.86<!-- claim: worst::w4a16 = -8.8600 --> overall\n\n"
           "Built on 817<!-- claim: n_rows = 817.0000 --> evaluations\n")
    f = _run(tmp_path, doc, {"adv_worst_delta": -39.5, "worst::w4a16": -8.86, "n_rows": 817})
    assert [x.status for x in f] == [VERIFIED] * 3


def test_documentation_examples_are_not_treated_as_claims(tmp_path):
    """`<!-- claim: key = value -->` in a README explains the format."""
    f = _run(tmp_path, "Tag numbers like this: <!-- claim: key = value -->\n", {"k": 1})
    assert _by(f, FLAGGED) == []


# ------------------------------------------------------------ code is not prose
def test_a_tag_in_a_fenced_block_is_documentation_not_a_claim(tmp_path):
    doc = ("How to tag a number:\n\n"
           "```\nBuilt on 817<!-- claim: n_rows = 817 --> rows.\n```\n")
    assert _run(tmp_path, doc, {}) == []


def test_a_tag_in_an_indented_block_is_documentation_not_a_claim(tmp_path):
    doc = "How to tag a number:\n\n    Built on 817<!-- claim: n_rows = 817 --> rows.\n"
    assert _run(tmp_path, doc, {}) == []


def test_a_tag_in_an_inline_code_span_is_documentation_not_a_claim(tmp_path):
    doc = "Write `<!-- claim: n_rows = 817 -->` after the number.\n"
    assert _run(tmp_path, doc, {}) == []


def test_masking_code_does_not_move_the_line_numbers_of_real_claims(tmp_path):
    doc = ("```\n<!-- claim: example = 1 -->\n```\n\n"
           "Built on 817<!-- claim: n_rows = 900 --> rows.\n")
    f = _run(tmp_path, doc, {"n_rows": 900})
    assert len(f) == 1 and f[0].line == 5


def test_a_tag_in_a_latex_verbatim_block_is_not_a_claim(tmp_path):
    (tmp_path / "p.tex").write_text("\\begin{verbatim}\n% claim: target_mean = -0.39\n"
                                    "\\end{verbatim}\n")
    (tmp_path / "claims.json").write_text(json.dumps({}))
    assert cli.run(str(tmp_path), only=["registry"], paid=True)[0] == []


def test_latex_indentation_is_not_treated_as_a_code_block(tmp_path):
    """LaTeX bodies are routinely indented, so a real tag must survive it."""
    (tmp_path / "p.tex").write_text("\\begin{abstract}\n"
                                    "    The mean is \\(-0.39\\)pp.\n"
                                    "    % claim: target_mean = -0.39\n"
                                    "\\end{abstract}\n")
    (tmp_path / "claims.json").write_text(json.dumps({"target_mean": -0.39}))
    assert cli.run(str(tmp_path), only=["registry"], paid=True)[0][0].status == VERIFIED


# ------------------------------------------------------------ the prose number
def test_a_citation_marker_is_not_read_as_the_prose_number(tmp_path):
    """'the text says 12' about '812 [12]' would be an untrue statement."""
    doc = "Prior work reports 812 [12]<!-- claim: n_prior = 812 -->.\n"
    f = _run(tmp_path, doc, {"n_prior": 812})
    assert len(f) == 1 and f[0].status == VERIFIED


def test_a_parenthesised_number_is_treated_as_absent_not_guessed(tmp_path):
    doc = "The mean (see Table 1) was (-0.39)<!-- claim: mean = -0.39 -->.\n"
    f = _run(tmp_path, doc, {"mean": -0.39})
    assert len(f) == 1 and f[0].status == VERIFIED


def test_an_adjacent_prose_number_is_still_compared(tmp_path):
    f = _run(tmp_path, "Coverage hit 91.2<!-- claim: cov = 90.1 -->%.\n", {"cov": 90.1})
    assert f[0].status == FLAGGED and "text says 91.2" in f[0].message


# ------------------------------------------------------------ malformed tags
def test_an_unparseable_tag_is_reported_not_dropped(tmp_path):
    cases = [("a.md", "<!-- claim: bad = 0.1.2 -->"),
             ("a.md", "<!-- claim: a=b = 5 -->"),
             ("a.md", "<!-- claim:  = 5 -->"),
             ("a.md", "<!-- claim: ghost2 -->"),
             ("a.md", "<!-- claim: n_rows = 817"),
             ("a.tex", "% claim: bad = 0.1.2")]
    for i, (name, tag) in enumerate(cases):
        d = tmp_path / f"c{i}"
        d.mkdir()
        f = _run(d, f"Some prose {tag}\n", {}, doc_name=name)
        bad = _by(f, UNVERIFIABLE)
        assert bad and "could not be read" in bad[0].message, tag


def test_an_unsigned_exponent_is_a_valid_number(tmp_path):
    f = _run(tmp_path, "We ran 1e5<!-- claim: n = 1e5 --> steps.\n", {"n": 100000})
    assert len(f) == 1 and f[0].status == VERIFIED


# ------------------------------------------------------------ the report
def test_a_check_whose_every_item_is_hidden_prints_no_heading(tmp_path):
    f = _run(tmp_path, "Nothing tagged here.\n", {"orphan": 5})
    text = render_text(f)
    assert "[registry]" not in text
    assert "[registry]" in render_text(f, show_unverifiable=True)


def test_unused_registry_entries_get_their_own_footer_reason(tmp_path):
    f = _run(tmp_path, "Nothing tagged here.\n", {"orphan": 5})
    text = render_text(f)
    assert "no document uses them" in text
    assert "no source to check against" not in text


def test_registry_is_data_only_and_never_executed(tmp_path):
    reg, err, problems = registry.load(str(tmp_path / "missing.json"))
    assert reg == {} and err
    # a value that looks like code is just a failed parse, never evaluated
    (tmp_path / "c.json").write_text(json.dumps({"x": "__import__('os').system('true')"}))
    reg, err, problems = registry.load(str(tmp_path / "c.json"))
    assert reg == {} and problems and problems[0][0] == "x"
