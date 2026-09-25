"""Check annotated claims against a registry of computed values.

The source check guesses which value in your data a number in prose refers to.
It is deliberately conservative, so it verifies only a small share of claims.
This module is the other half: when you are willing to say *which* value a
number is, nothing has to be guessed, and recall stops being the limit.

You tag the number where you write it:

    Built on 817<!-- claim: n_rows = 817 --> published evaluations.

and your own pipeline writes the computed values to a registry file:

    {"n_rows": 817, "mae_global": {"value": 0.7545, "tolerance": 0.0001,
                                   "how": "row-weighted, leave-one-family-out"}}

claimaudit only reads that file. It never runs your code, so a registry
cannot make the tool execute anything.

Three things are checked, and the first is the one a hand-rolled version
usually misses:

1. the number in the prose matches the value in its own tag, so editing the
   sentence and forgetting the tag is caught;
2. the tagged value matches the registry, so the prose and the computation
   agree;
3. the label exists in the registry at all.

Registry entries that no document uses are reported too: usually it means a
claim was deleted from the prose and nobody noticed.
"""
from __future__ import annotations
import csv
import json
import os
import re

from .report import Finding, VERIFIED, FLAGGED, UNVERIFIABLE

NUM = r"[-\u2212+]?[0-9][0-9,]*(?:\.[0-9]+)?(?:[eE][-+][0-9]+)?"
MD_ANN = re.compile(r"<!--\s*claim:\s*(?P<label>[^=\s][^=]*?)\s*=\s*"
                    r"(?P<val>" + NUM + r")\s*-->")
# Everything after % is comment in LaTeX, so trailing notes are allowed:
# "% claim: target_mean = -0.39  (mean of the delta column)" is fine.
TEX_ANN = re.compile(r"%\s*claim:\s*(?P<label>[^=\s][^=]*?)\s*=\s*"
                     r"(?P<val>" + NUM + r")(?=\s|$)", re.M)
# The prose number a tag is attached to, allowing for %, pp, units and markup.
BEFORE = re.compile(r"(?P<num>" + NUM + r")\s*(?:%|pp|x|\u00d7)?\s*[*_`)\]]*\s*$")
REGISTRY_NAMES = ("claimaudit-claims.json", "claimaudit-claims.csv",
                  "claims.json", "claims.csv")


def _f(s):
    return float(str(s).replace("\u2212", "-").replace(",", ""))


def _decimals(raw):
    raw = str(raw)
    return len(raw.split(".")[1].split("e")[0]) if "." in raw else 0


def _tol(raw):
    """Half a unit in the last printed place, so 0.75 tolerates 0.7545."""
    return 0.5 * 10 ** (-_decimals(raw)) * 1.0001 + 1e-9


def load(path):
    """-> ({label: (value, tolerance_or_None, how)}, error_message_or_None)."""
    try:
        if path.lower().endswith(".csv"):
            out = {}
            with open(path, encoding="utf-8-sig", newline="") as fh:
                for row in csv.DictReader(fh):
                    keys = {(k or "").strip().lower(): (v or "") for k, v in row.items()}
                    label = (keys.get("label") or keys.get("claim") or keys.get("key") or "").strip()
                    if not label:
                        continue
                    tol = keys.get("tolerance", "").strip()
                    out[label] = (_f(keys.get("value", "")), _f(tol) if tol else None,
                                  keys.get("how", "").strip())
            return out, None
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        if not isinstance(raw, dict):
            return {}, "registry must be a JSON object of label -> value"
        out = {}
        for label, v in raw.items():
            if isinstance(v, dict):
                tol = v.get("tolerance")
                out[label] = (_f(v.get("value")), _f(tol) if tol is not None else None,
                              str(v.get("how", "")))
            else:
                out[label] = (_f(v), None, "")
        return out, None
    except (OSError, ValueError, TypeError, KeyError) as e:
        return {}, f"{type(e).__name__}: {e}"


def discover(base, explicit=None):
    """-> path to the registry, or None."""
    if explicit:
        return explicit if os.path.exists(explicit) else None
    for name in REGISTRY_NAMES:
        p = os.path.join(base, name)
        if os.path.exists(p):
            return p
    return None


def annotations(text, is_tex=False):
    """Yield (line, label, tagged_raw, prose_raw_or_None) for each tag."""
    rx = TEX_ANN if is_tex else MD_ANN
    for m in rx.finditer(text):
        before = BEFORE.search(text[max(0, m.start() - 60):m.start()])
        yield (text.count("\n", 0, m.start()) + 1, m.group("label").strip(),
               m.group("val"), before.group("num") if before else None)


def check(tfiles, reg_path):
    """tfiles: [(rel, abs, raw_text)] -- raw, because tags live in comments."""
    out = []
    reg, err = load(reg_path)
    rel_reg = os.path.basename(reg_path)
    if err:
        return [Finding("registry", FLAGGED, rel_reg, 0,
                        f"could not read the claim registry, so no tagged claim was checked: {err}")]

    used = set()
    for rel, _abs, text in tfiles:
        for line, label, tagged, prose in annotations(text, rel.lower().endswith(".tex")):
            used.add(label)
            # 1. prose vs its own tag
            if prose is not None and abs(_f(prose) - _f(tagged)) > _tol(prose):
                out.append(Finding("registry", FLAGGED, rel, line,
                                   f"{label}: the text says {prose} but its tag says {tagged}",
                                   "the sentence was probably edited without updating the tag"))
                continue
            # 2. and 3. tag vs registry
            if label not in reg:
                out.append(Finding("registry", FLAGGED, rel, line,
                                   f"{label}: tagged in the text but missing from {rel_reg}",
                                   "nothing recomputes this number"))
                continue
            value, tol, how = reg[label]
            if abs(_f(tagged) - value) > (tol if tol is not None else _tol(tagged)):
                out.append(Finding("registry", FLAGGED, rel, line,
                                   f"{label}: the text says {tagged} but {rel_reg} computes {value:g}",
                                   how))
            else:
                out.append(Finding("registry", VERIFIED, rel, line,
                                   f"{label} = {tagged} matches {rel_reg}", how))

    for label in sorted(set(reg) - used):
        out.append(Finding("registry", UNVERIFIABLE, rel_reg, 0,
                           f"{label} is in the registry but no document uses it",
                           "the claim may have been deleted from the prose"))
    return out
