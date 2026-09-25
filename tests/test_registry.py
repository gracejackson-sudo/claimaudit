"""Claim-registry checking: tagged numbers against recomputed values."""
from __future__ import annotations
import json

import pytest

from claimaudit import cli, registry
from claimaudit.report import VERIFIED, FLAGGED, UNVERIFIABLE


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAIMAUDIT_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CLAIMAUDIT_LICENSE_KEY", raising=False)


def _run(tmp_path, doc, reg, name="claims.json", doc_name="a.md"):
    (tmp_path / doc_name).write_text(doc, encoding="utf-8")
    p = tmp_path / name
    p.write_text(json.dumps(reg) if name.endswith(".json") else reg, encoding="utf-8")
    return cli.run(str(tmp_path), only=["registry"], paid=True)[0]


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


def test_missing_registry_means_the_check_is_silent(tmp_path):
    (tmp_path / "a.md").write_text("Built on 817<!-- claim: n_rows = 817 --> rows.\n")
    assert cli.run(str(tmp_path), only=["registry"], paid=True)[0] == []


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


def test_registry_is_data_only_and_never_executed(tmp_path):
    reg, err = registry.load(str(tmp_path / "missing.json"))
    assert reg == {} and err
    # a value that looks like code is just a failed parse, never evaluated
    (tmp_path / "c.json").write_text(json.dumps({"x": "__import__('os').system('true')"}))
    reg, err = registry.load(str(tmp_path / "c.json"))
    assert reg == {} and err
