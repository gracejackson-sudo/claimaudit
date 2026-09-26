"""Findings and report rendering."""
from __future__ import annotations
import json
from dataclasses import dataclass, asdict, field

VERIFIED, FLAGGED, UNVERIFIABLE = "VERIFIED", "FLAGGED", "UNVERIFIABLE"

# Longest list printed per check. Only the text report is trimmed, and it says
# so when it trims; --json always carries every finding.
MAX_LISTED = 200


@dataclass
class Finding:
    check: str          # claims | source | citation | overclaim | consistency
    status: str
    file: str
    line: int
    message: str
    evidence: str = ""
    extra: dict = field(default_factory=dict)


LIMITS = (
    "claimaudit is a first pass, not an audit. It uses pattern matching and "
    "exact numeric comparison. It cannot judge whether a claim is true, cannot "
    "follow a computation, and will miss most errors a careful reader would "
    "catch. VERIFIED means 'matched something in your files or a public "
    "record', not 'correct'. FLAGGED means 'look at this', not 'wrong'."
)


def summarize(findings, ran=()):
    """Counts per check.

    Checks that ran and found nothing are listed at zero rather than left
    out. A heading with nothing under it says neither "clean" nor "did not
    run", and the difference between those two is the whole point.
    """
    out = {c: {VERIFIED: 0, FLAGGED: 0, UNVERIFIABLE: 0} for c in ran}
    for f in findings:
        out.setdefault(f.check, {VERIFIED: 0, FLAGGED: 0, UNVERIFIABLE: 0})
        out[f.check][f.status] += 1
    return out


# Rank per FLAGGED finding for --top: lower means "show this first". The order
# is not "how likely the tool is right" -- every FLAGGED is a prompt to review,
# not a verdict. It is "how likely a reader will find something real by looking
# at this one over the others", based on which patterns rest on numeric or
# bibliographic evidence (rare and specific, so worth attention) versus which
# rest on word matches over ordinary prose (common and noisy).
#
# scan findings sit at the top because a file that was never audited is not a
# clean file, and the reader needs to know that first. UNVERIFIABLE and
# VERIFIED items are not surfaced by --top at all; the summary line and
# --show-unverifiable / --show-verified stay the way to reach them.
_TIER_A = 10   # numeric / bibliographic mismatches: the check earned this flag
_TIER_B = 30   # citation resolution failures: worth a look but off-site
_TIER_C = 50   # overclaim wording: pattern match over prose
_SCAN_UNREAD = 5


def _priority(f):
    """-> integer priority. Lower is higher priority in --top."""
    if f.status != FLAGGED:
        return 1000
    check = f.check
    msg = f.message.lower()
    if check == "scan":
        return _SCAN_UNREAD
    if check == "consistency":
        return _TIER_A
    if check == "benchmark":
        return _TIER_A + 1
    if check == "seeds":
        return _TIER_A + 2
    if check == "citation" and any(k in msg for k in ("authors differ", "title differs",
                                                       "does not match")):
        return _TIER_A + 3
    if check == "registry" and "the text says" in msg:
        return _TIER_A + 4
    if check == "source":
        return _TIER_A + 5
    if check == "support" and "contradicted" in msg:
        return _TIER_A + 6
    if check == "citation" and any(k in msg for k in ("url is dead", "not found",
                                                       "does not resolve")):
        return _TIER_B
    if check == "registry" and "missing from" in msg:
        return _TIER_B + 1
    if check == "overclaim":
        return _TIER_C + (10 if "(negated)" in f.message else 0)
    return _TIER_B + 5


def render_text(findings, skipped=(), show_verified=False, show_unverifiable=False, ran=(), top=None):
    """Text report. When `top` is set, only the top-N FLAGGED findings are
    listed (scan findings included so unread files are never hidden). The
    LIMITS banner and the summary counts still appear so the reader never
    loses the whole shape of the run."""
    if top is not None:
        return _render_top(findings, skipped, ran, top)
    lines = ["claimaudit report", "=" * 60, LIMITS, ""]
    order = {FLAGGED: 0, UNVERIFIABLE: 1, VERIFIED: 2}
    for chk in ("scan", "registry", "benchmark", "seeds", "source", "citation", "support",
                "consistency", "overclaim"):
        fs = [f for f in findings if f.check == chk]
        if not fs:
            continue
        body = []
        shown = trimmed = 0
        # "scan" reports files that were never read. Collapsing those into a
        # count would hide the fact that the audit is incomplete, so they are
        # always listed in full.
        always = chk == "scan"
        for f in sorted(fs, key=lambda f: (order[f.status], f.file, f.line)):
            if f.status == VERIFIED and not show_verified and not always:
                continue
            if f.status == UNVERIFIABLE and not show_unverifiable and not always:
                continue
            if shown >= MAX_LISTED:
                trimmed += 1
                continue
            shown += 1
            loc = f"{f.file}:{f.line}  " if f.file else ""
            body.append(f"  {f.status:<12} {loc}{f.message}")
            if f.evidence:
                body.append(f"               {f.evidence}")
        if trimmed:
            body.append(f"  ... showing {shown} of {shown + trimmed} {chk} item(s); "
                        f"{trimmed} more not listed. --json gives every one.")
        # Every item of this check was hidden, so its heading would stand alone.
        if body:
            lines.append(f"[{chk}]")
            lines += body
            lines.append("")
    listed = [f for f in findings if f.check != "scan"]   # scan items are never collapsed
    unused = [f for f in listed if f.status == UNVERIFIABLE
              and f.extra.get("reason") == "unused"]
    nu = sum(1 for f in listed if f.status == UNVERIFIABLE) - len(unused)
    nv = sum(1 for f in listed if f.status == VERIFIED)
    if not show_unverifiable and nu:
        lines.append(f"{nu} UNVERIFIABLE item(s) not listed (no source to check against, or too little "
                     f"evidence to match safely); list them with --show-unverifiable.")
    if not show_unverifiable and unused:
        lines.append(f"{len(unused)} registry entry/entries not listed (no document uses them); "
                     f"list them with --show-unverifiable.")
    if not show_verified and nv:
        lines.append(f"{nv} VERIFIED item(s) not listed; list them with --show-verified.")
    lines.append("")
    lines.append("summary")
    counted = summarize(findings, ran)
    for chk, c in counted.items():
        lines.append(f"  {chk:<12} verified {c[VERIFIED]:>4}   flagged {c[FLAGGED]:>4}"
                     f"   unverifiable {c[UNVERIFIABLE]:>4}")
    if not counted:
        lines.append("  (no check ran)")
    if skipped:
        lines.append("")
        lines.append("skipped (license required): " + ", ".join(skipped))
    return "\n".join(lines)


def _render_top(findings, skipped, ran, top):
    """The --top preview: scan section, then a ranked list of the most-signal
    FLAGGED findings, then the same summary the full report ends with."""
    lines = ["claimaudit report (preview: top " + str(top) + ")",
             "=" * 60, LIMITS, ""]

    scan_findings = [f for f in findings
                     if f.check == "scan" and f.status in (FLAGGED, UNVERIFIABLE)]
    if scan_findings:
        lines.append("[scan]")
        for f in sorted(scan_findings, key=lambda f: (f.file, f.line)):
            loc = f"{f.file}:{f.line}  " if f.file else ""
            lines.append(f"  {f.status:<12} {loc}{f.message}")
            if f.evidence:
                lines.append(f"               {f.evidence}")
        lines.append("")

    flagged = [f for f in findings if f.status == FLAGGED and f.check != "scan"]
    ranked = sorted(flagged, key=lambda f: (_priority(f), f.check, f.file, f.line))
    shown = ranked[:top] if top > 0 else []
    hidden = len(ranked) - len(shown)

    if shown:
        lines.append(f"[top {len(shown)} flagged — most likely to warrant a look]")
        for f in shown:
            loc = f"{f.file}:{f.line}  " if f.file else ""
            lines.append(f"  {f.check:<12} {loc}{f.message}")
            if f.evidence:
                lines.append(f"               {f.evidence}")
        lines.append("")
    else:
        lines.append("[top]  no FLAGGED findings.")
        lines.append("")

    if hidden > 0:
        lines.append(f"{hidden} more flagged finding(s) not shown here; "
                     f"drop --top for the full report or --json for every one.")

    non_scan = [f for f in findings if f.check != "scan"]
    nu = sum(1 for f in non_scan if f.status == UNVERIFIABLE)
    nv = sum(1 for f in non_scan if f.status == VERIFIED)
    parts = []
    if nu:
        parts.append(f"{nu} UNVERIFIABLE")
    if nv:
        parts.append(f"{nv} VERIFIED")
    if parts:
        lines.append("also this run: " + ", ".join(parts) + " (not surfaced by --top).")

    lines.append("")
    lines.append("summary")
    counted = summarize(findings, ran)
    for chk, c in counted.items():
        lines.append(f"  {chk:<12} verified {c[VERIFIED]:>4}   flagged {c[FLAGGED]:>4}"
                     f"   unverifiable {c[UNVERIFIABLE]:>4}")
    if not counted:
        lines.append("  (no check ran)")
    if skipped:
        lines.append("")
        lines.append("skipped (license required): " + ", ".join(skipped))
    return "\n".join(lines)


# Bumped only when a consumer would have to change. Adding a key is not a
# bump; removing or renaming one is. CI configs pin behaviour to this.
SCHEMA_VERSION = 1


def counts(findings):
    return {"flagged": sum(1 for f in findings if f.status == FLAGGED),
            "unverifiable": sum(1 for f in findings if f.status == UNVERIFIABLE),
            "verified": sum(1 for f in findings if f.status == VERIFIED),
            "total": len(findings)}


def render_json(findings, skipped=(), ran=()):
    from . import __version__
    return json.dumps({"schema_version": SCHEMA_VERSION, "tool_version": __version__,
                       "limits": LIMITS, "counts": counts(findings),
                       "summary": summarize(findings, ran),
                       "skipped": list(skipped), "error": None,
                       "findings": [asdict(f) for f in findings]}, indent=2)


def render_error_json(why):
    """A usage error, as JSON.

    `claimaudit check ... --json > claimaudit.json` is the shipped pattern.
    Printing nothing on exit 2 leaves a zero-byte file and a parse error
    where the cause should be.
    """
    from . import __version__
    return json.dumps({"schema_version": SCHEMA_VERSION, "tool_version": __version__,
                       "limits": LIMITS, "counts": counts([]), "summary": {},
                       "skipped": [], "error": why, "findings": []}, indent=2)
