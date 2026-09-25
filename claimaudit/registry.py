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
claim was deleted from the prose and nobody noticed. So is anything the check
could not do: a tag it could not parse, an entry it could not read, and tags
with no registry to check them against. A tag you wrote and we ignored would
be the one silence that reads as approval.

Tags inside code -- fenced, indented, inline or a LaTeX verbatim environment --
are not claims. Documenting this format should not flag your own example.
"""
from __future__ import annotations
import csv
import json
import os
import re

from .report import Finding, VERIFIED, FLAGGED, UNVERIFIABLE

# An unsigned exponent is how people actually write these, so 1e5 is a number.
NUM = r"[-\u2212+]?[0-9][0-9,]*(?:\.[0-9]+)?(?:[eE][-+]?[0-9]+)?"
# Tags are matched permissively and parsed strictly, so that a tag the author
# meant as a claim is never dropped in silence just because it is malformed.
MD_ANN = re.compile(r"<!--\s*claim:(?P<body>[^\n]*?)(?P<close>-->|$)", re.M)
# Everything after % is comment in LaTeX, so trailing notes are allowed:
# "% claim: target_mean = -0.39  (mean of the delta column)" is fine.
TEX_ANN = re.compile(r"%\s*claim:(?P<body>[^\n]*)", re.M)
VALUE = re.compile(r"(?P<val>" + NUM + r")(?P<rest>.*)$", re.S)
# A value that is a bare word with no digits is the documented format example
# ("<!-- claim: key = value -->"), not a claim anyone expects to be checked.
PLACEHOLDER = re.compile(r"[<{(]?[A-Za-z_][A-Za-z_.\-]*[>})]?")
# The prose number a tag is attached to, allowing for %, pp, units and markup.
# Closing brackets are deliberately absent: "812 [12]<!-- ... -->" must read as
# no prose number rather than as the citation marker's 12.
BEFORE = re.compile(r"(?P<num>" + NUM + r")\s*(?:%|pp|x|\u00d7)?\s*[*_`]*\s*$")
REGISTRY_NAMES = ("claimaudit-claims.json", "claimaudit-claims.csv",
                  "claims.json", "claims.csv")


def _f(s):
    return float(str(s).replace("\u2212", "-").replace(",", ""))


EXPONENT = re.compile(r"[eE]([-+]?[0-9]+)")


def _decimals(raw):
    """Digits after the point in the mantissa only; an exponent is not one."""
    mant = EXPONENT.split(str(raw))[0]
    return len(mant.split(".")[1]) if "." in mant else 0


def _exponent(raw):
    m = EXPONENT.search(str(raw))
    return int(m.group(1)) if m else 0


def _tol(raw):
    """Half a unit in the last printed place, so 0.75 tolerates 0.7545.

    The last printed place of a number in scientific notation is scaled by its
    exponent: 2.5e+06 is stated to the nearest 1e5, not the nearest 0.1. Taking
    the mantissa's decimal count alone gave 0.05 there, which both flagged
    correctly rounded figures and -- far worse -- verified 1.0e-03 against 0.04.
    """
    return 0.5 * 10.0 ** (_exponent(raw) - _decimals(raw)) * 1.0001 + 1e-9


def _entry(label, v):
    """One registry entry -> ((value, tol, how), None) or (None, why_not)."""
    # bool is an int in Python, so it would otherwise load as a silent 0 or 1.
    if isinstance(v, bool):
        return None, f"{label}: a true/false value is not a number"
    if isinstance(v, dict):
        if "value" not in v:
            return None, f"{label}: the entry is an object with no 'value' key"
        tol = v.get("tolerance")
        try:
            return (_f(v["value"]), _f(tol) if tol is not None else None,
                    str(v.get("how", ""))), None
        except (ValueError, TypeError) as e:
            return None, f"{label}: {e}"
    try:
        return (_f(v), None, ""), None
    except (ValueError, TypeError) as e:
        return None, f"{label}: {e}"


_DUPES = object()       # a key no JSON document can contain


def _dupes(pairs):
    """object_pairs_hook: json.load would otherwise collapse repeated keys."""
    seen, repeated = {}, []
    for k, v in pairs:
        if k in seen:
            repeated.append(k)
        seen[k] = v
    if repeated:
        seen[_DUPES] = repeated
    return seen


def load(path):
    """-> (entries, fatal_error_or_None, problems).

    entries maps label -> (value, tolerance_or_None, how). problems is a list
    of (label_or_None, message): one bad entry must not stop the rest being
    checked, but it must not pass unmentioned either.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".json", ".csv"):
        return {}, (f"only .json and .csv claim registries are supported, "
                    f"and this is {ext or 'a file with no extension'}"), []
    try:
        return _load_csv(path) if ext == ".csv" else _load_json(path)
    except (OSError, ValueError, TypeError, KeyError) as e:
        return {}, f"{type(e).__name__}: {e}", []


def _load_csv(path):
    out, problems, seen = {}, [], set()
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rdr = csv.DictReader(fh)
        cols = {(c or "").strip().lower() for c in (rdr.fieldnames or ())}
        if not cols:
            return {}, "the CSV registry is empty", []
        if "value" not in cols:
            return {}, ("the CSV registry has no 'value' column "
                        f"(columns found: {', '.join(sorted(cols))})"), []
        if not cols & {"label", "claim", "key"}:
            return {}, ("the CSV registry has no 'label', 'claim' or 'key' column "
                        f"(columns found: {', '.join(sorted(cols))})"), []
        for row in rdr:
            keys = {(k or "").strip().lower(): (v or "") for k, v in row.items()}
            label = (keys.get("label") or keys.get("claim") or keys.get("key") or "").strip()
            if not label:
                continue
            if label in seen:
                problems.append((label, f"{label}: appears more than once, so which row "
                                        f"is authoritative is not knowable"))
                out.pop(label, None)
                continue
            seen.add(label)
            tol = keys.get("tolerance", "").strip()
            try:
                out[label] = (_f(keys.get("value", "")), _f(tol) if tol else None,
                              keys.get("how", "").strip())
            except (ValueError, TypeError) as e:
                problems.append((label, f"{label}: {e}"))
    return out, None, problems


def _load_json(path):
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh, object_pairs_hook=_dupes)
    if not isinstance(raw, dict):
        return {}, "registry must be a JSON object of label -> value", []
    repeated = raw.pop(_DUPES, [])
    out, problems = {}, []
    for label in repeated:
        problems.append((label, f"{label}: appears more than once, so which value "
                                f"is authoritative is not knowable"))
    for label, v in raw.items():
        if label in repeated:
            continue
        entry, why = _entry(label, v)
        if why:
            problems.append((label, why))
        else:
            out[label] = entry
    return out, None, problems


def discover(base, explicit=None):
    """-> path to the registry, or None."""
    if explicit:
        return explicit if os.path.exists(explicit) else None
    for name in REGISTRY_NAMES:
        p = os.path.join(base, name)
        if os.path.exists(p):
            return p
    return None


FENCE = re.compile(r"^\s{0,3}(```+|~~~+)")
INDENTED = re.compile(r"^(?: {4,}|\t)")
SPAN = re.compile(r"(`+)[^`\n]+\1")
TEX_VERBATIM = re.compile(r"\\begin\{(verbatim|lstlisting|minted|Verbatim)\*?\}"
                          r".*?(?:\\end\{\1\*?\}|\Z)", re.S)


def _blank(m):
    """Same length, same newlines, so line numbers and offsets still hold."""
    return re.sub(r"[^\n]", " ", m.group(0))


def mask_code(text, is_tex=False):
    """Blank out code regions, because a documented example is not a claim.

    Any README explaining this feature contains a tag; auditing it reports the
    example's label as missing from the registry. LaTeX keeps its indentation
    and backticks for prose, so only verbatim-style environments are masked
    there -- masking indented lines would swallow real tags.
    """
    if is_tex:
        return TEX_VERBATIM.sub(_blank, text)
    out, fence, indented, after_blank = [], None, False, True
    for line in text.split("\n"):
        m = FENCE.match(line)
        if fence is not None:
            drop = True
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence):
                fence = None
        elif m:
            fence, drop = m.group(1), True
        elif INDENTED.match(line) and (indented or after_blank):
            indented = drop = True
        else:
            drop = False
            if line.strip():
                indented = False
        out.append(re.sub(r".", " ", line) if drop else line)
        after_blank = not line.strip()
    return SPAN.sub(_blank, "\n".join(out))


def _parse(body, is_tex):
    """-> (label, value_raw, reason). reason is set when nothing is checkable.

    A reason of None with no label means "stay silent": the tag is the format
    example from the documentation rather than a claim about a number.
    """
    if "=" not in body:
        return None, None, "there is no '=' and so no value"
    label, _, val = body.partition("=")
    label, val = label.strip(), val.strip()
    if not label:
        return None, None, "the label is empty"
    if "=" in val:
        return None, None, f"the label or value contains '=' ({body.strip()!r})"
    if not val:
        return None, None, f"{label} has no value"
    m = VALUE.match(val)
    # LaTeX comments run to end of line, so a note after the value is normal.
    if m and (not m.group("rest").strip() or (is_tex and m.group("rest")[:1].isspace())):
        return label, m.group("val"), None
    if PLACEHOLDER.fullmatch(val):
        return None, None, None
    return None, None, f"{label} = {val!r} is not a number"


def annotations(text, is_tex=False):
    """Yield (line, label, tagged_raw, prose_raw_or_None, reason) for each tag.

    reason is None for a tag that parsed; label is then set. When reason is a
    string nothing about the tag is checkable and it is reported as such. Both
    label and reason are None for a tag that is deliberately ignored.
    """
    text = mask_code(text, is_tex)
    for m in (TEX_ANN if is_tex else MD_ANN).finditer(text):
        line = text.count("\n", 0, m.start()) + 1
        # A LaTeX comment needs no closing marker; an HTML one does.
        if not is_tex and not m.group("close"):
            yield (line, None, None, None, "the comment is never closed with '-->'")
            continue
        label, val, why = _parse(m.group("body"), is_tex)
        if label is None:
            yield (line, None, None, None, why)
            continue
        before = BEFORE.search(text[max(0, m.start() - 60):m.start()])
        yield (line, label, val, before.group("num") if before else None, None)


def no_registry(tfiles):
    """What to say when the check was asked for and no registry was found.

    Tagged claims with nowhere to check them against is the one case where
    silence would read as approval. No tags at all is not: the author never
    opted in, so there is nothing to report.
    """
    tagged = 0
    for rel, _abs, text in tfiles:
        for _line, label, _t, _p, why in annotations(text, rel.lower().endswith(".tex")):
            tagged += 1 if (label is not None or why) else 0
    if not tagged:
        return []
    return [Finding("registry", UNVERIFIABLE, "", 0,
                    f"{tagged} claim tag(s) were found but no claim registry was discovered, "
                    f"so none of them was checked",
                    "looked for " + ", ".join(REGISTRY_NAMES) + " beside the documents; "
                    "point at one with --registry")]


def check(tfiles, reg_path):
    """tfiles: [(rel, abs, raw_text)] -- raw, because tags live in comments."""
    out = []
    reg, err, problems = load(reg_path)
    rel_reg = os.path.basename(reg_path)
    if err:
        return [Finding("registry", FLAGGED, rel_reg, 0,
                        f"could not read the claim registry, so no tagged claim was checked: {err}")]
    unusable = {label: why for label, why in problems if label}
    out += [Finding("registry", UNVERIFIABLE, rel_reg, 0,
                    f"{why}, so this entry was skipped",
                    "every other entry was still checked") for _label, why in problems]

    used = set()
    for rel, _abs, text in tfiles:
        for line, label, tagged, prose, why in annotations(text, rel.lower().endswith(".tex")):
            if label is None:
                if why:
                    out.append(Finding("registry", UNVERIFIABLE, rel, line,
                                       f"a claim tag here could not be read, so nothing "
                                       f"about it was checked: {why}"))
                continue
            used.add(label)
            if label in unusable:
                out.append(Finding("registry", UNVERIFIABLE, rel, line,
                                   f"{label}: tagged here, but its {rel_reg} entry could "
                                   f"not be read, so the two were not compared",
                                   unusable[label]))
                continue
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
            # The two tolerances measure different things -- the author's is
            # computational uncertainty, ours is display rounding -- so an
            # author who supplies one must not thereby lose the other.
            allowed = _tol(tagged) if tol is None else max(tol, _tol(tagged))
            if abs(_f(tagged) - value) > allowed:
                out.append(Finding("registry", FLAGGED, rel, line,
                                   f"{label}: the text says {tagged} but {rel_reg} computes {value:g}",
                                   how))
            else:
                out.append(Finding("registry", VERIFIED, rel, line,
                                   f"{label} = {tagged} matches {rel_reg}", how))

    for label in sorted(set(reg) - used):
        out.append(Finding("registry", UNVERIFIABLE, rel_reg, 0,
                           f"{label} is in the registry but no document uses it",
                           "the claim may have been deleted from the prose",
                           {"reason": "unused"}))
    return out
