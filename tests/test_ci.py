"""CI and pre-commit integration: exit codes, JSON schema, hook definitions."""
from __future__ import annotations
import json
import os
import pathlib

import pytest

from claimaudit import cli, report

ROOT = pathlib.Path(__file__).resolve().parent.parent

FLAGGING = "Our method is the first to solve this.\n"
CLEAN = "We report results on the usual benchmarks.\n"


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAIMAUDIT_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CLAIMAUDIT_LICENSE_KEY", raising=False)


def _doc(tmp_path, body, name="a.md"):
    (tmp_path / name).write_text(body, encoding="utf-8")
    return str(tmp_path)


# ------------------------------------------------------------ exit codes
def test_flagged_fails_the_build_by_default(tmp_path):
    assert cli.main(["check", _doc(tmp_path, FLAGGING)]) == 1


def test_clean_run_exits_zero(tmp_path):
    assert cli.main(["check", _doc(tmp_path, CLEAN)]) == 0


def test_fail_on_never_exits_zero_despite_flags(tmp_path):
    assert cli.main(["check", _doc(tmp_path, FLAGGING), "--fail-on", "never"]) == 0


def test_exit_zero_still_works_as_an_alias(tmp_path):
    assert cli.main(["check", _doc(tmp_path, FLAGGING), "--exit-zero"]) == 0


def test_fail_on_unverifiable_is_stricter(tmp_path, monkeypatch):
    """A claim with nothing to check it against passes by default, fails here."""
    from claimaudit import license as lic
    monkeypatch.setattr(lic, "tier", lambda **kw: ("paid", "test"))
    d = _doc(tmp_path, "The dataset has 12345 rows.\n")
    assert cli.main(["check", d, "--offline", "--only", "source"]) == 0
    assert cli.main(["check", d, "--offline", "--only", "source",
                     "--fail-on", "unverifiable"]) == 1


def test_an_unknown_fail_on_level_is_rejected(tmp_path):
    with pytest.raises(SystemExit):
        cli.main(["check", _doc(tmp_path, CLEAN), "--fail-on", "sometimes"])


def test_a_bad_path_is_a_different_exit_code_than_a_finding(tmp_path):
    """CI needs to tell "tool misconfigured" apart from "documents have problems"."""
    assert cli.main(["check", str(tmp_path / "nope")]) == 2
    assert cli.main(["check", _doc(tmp_path, FLAGGING)]) == 1


# ------------------------------------------------------------ JSON schema
def _json_run(tmp_path, capsys, body=FLAGGING, extra=()):
    cli.main(["check", _doc(tmp_path, body), "--json", "--offline", *extra])
    return json.loads(capsys.readouterr().out)


def test_json_carries_a_schema_version(tmp_path, capsys):
    d = _json_run(tmp_path, capsys)
    assert d["schema_version"] == report.SCHEMA_VERSION == 1
    assert d["tool_version"]


def test_json_has_the_keys_a_ci_job_reads(tmp_path, capsys):
    d = _json_run(tmp_path, capsys)
    assert set(d) >= {"schema_version", "tool_version", "counts", "summary",
                      "skipped", "findings", "limits"}
    assert set(d["counts"]) == {"flagged", "unverifiable", "verified", "total"}
    for f in d["findings"]:
        assert set(f) >= {"check", "status", "file", "line", "message", "evidence"}


def test_counts_agree_with_the_findings_list(tmp_path, capsys):
    d = _json_run(tmp_path, capsys)
    assert d["counts"]["total"] == len(d["findings"])
    assert d["counts"]["flagged"] == sum(1 for f in d["findings"] if f["status"] == "FLAGGED")


def test_json_still_carries_the_limitations_text(tmp_path, capsys):
    """A machine-readable run must not quietly drop the caveat."""
    assert "not an audit" in _json_run(tmp_path, capsys)["limits"]


def test_json_lists_the_checks_the_licence_skipped(tmp_path, capsys):
    d = _json_run(tmp_path, capsys)
    assert "source" in d["skipped"] and "registry" in d["skipped"]


def test_json_output_is_the_only_thing_on_stdout(tmp_path, capsys):
    """Anything else and `claimaudit ... --json | jq` breaks."""
    cli.main(["check", _doc(tmp_path, FLAGGING), "--json", "--offline"])
    json.loads(capsys.readouterr().out)   # would raise if a notice were printed


# ------------------------------------------------------------ hook definitions
def _hooks():
    yaml = pytest.importorskip("yaml")
    return {h["id"]: h for h in yaml.safe_load((ROOT / ".pre-commit-hooks.yaml").read_text())}


def test_pre_commit_hooks_file_exists_and_parses():
    hooks = _hooks()
    assert set(hooks) == {"claimaudit", "claimaudit-advisory", "claimaudit-full"}
    for h in hooks.values():
        assert h["pass_filenames"] is False
        assert h["entry"].startswith("claimaudit check")


def test_the_blocking_hook_does_not_reach_the_network():
    gate = _hooks()["claimaudit"]
    assert "--offline" in gate["entry"] and "citation" not in gate["entry"]


def test_the_blocking_hook_excludes_the_noisy_wording_check():
    """Measured at 592 flags on a real repository: fine to read, not to gate.

    A hook that is red on every commit teaches people to pass --no-verify,
    after which it protects nothing.
    """
    hooks = _hooks()
    assert "overclaim" not in hooks["claimaudit"]["entry"]
    assert "--fail-on flagged" in hooks["claimaudit"]["entry"]
    assert "--fail-on never" in hooks["claimaudit-advisory"]["entry"]


def test_every_hook_names_only_checks_that_exist():
    from claimaudit import license as lic
    for h in _hooks().values():
        if "--only" not in h["entry"]:
            continue
        named = h["entry"].split("--only")[1].split()[0].split(",")
        assert set(named) <= set(lic.FREE_CHECKS + lic.PAID_CHECKS), named


def test_the_example_workflow_gates_on_arithmetic_and_not_on_wording():
    text = (ROOT / "examples" / "github-action.yml").read_text()
    blocking, advisory = text.split("  advisory:")
    assert "--fail-on flagged" in blocking
    gated = blocking.split("--only")[1].split()[0].split(",")
    assert "overclaim" not in gated and "citation" not in gated
    assert "--fail-on never" in advisory


def test_the_example_workflow_exists_and_names_real_checks():
    from claimaudit import license as lic
    text = (ROOT / "examples" / "github-action.yml").read_text()
    assert "claimaudit check" in text
    for chunk in text.split("--only")[1:]:
        for name in chunk.split()[0].split(","):
            assert name in lic.FREE_CHECKS + lic.PAID_CHECKS, name


def test_the_example_workflow_uses_only_flags_the_cli_accepts(tmp_path):
    import re
    text = (ROOT / "examples" / "github-action.yml").read_text()
    used = set(re.findall(r"(?<!-)--[a-z][a-z-]+", text))
    parser_flags = {"--only", "--registry", "--json", "--show-verified",
                    "--show-unverifiable", "--offline", "--strict", "--max-urls",
                    "--exclude", "--fail-on", "--exit-zero", "--version"}
    assert used <= parser_flags, used - parser_flags
