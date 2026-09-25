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


def summarize(findings):
    out = {}
    for f in findings:
        out.setdefault(f.check, {VERIFIED: 0, FLAGGED: 0, UNVERIFIABLE: 0})
        out[f.check][f.status] += 1
    return out


def render_text(findings, skipped=(), show_verified=False, show_unverifiable=False):
    lines = ["claimaudit report", "=" * 60, LIMITS, ""]
    order = {FLAGGED: 0, UNVERIFIABLE: 1, VERIFIED: 2}
    for chk in ("scan", "registry", "benchmark", "seeds", "source", "citation",
                "consistency", "overclaim"):
        fs = [f for f in findings if f.check == chk]
        if not fs:
            continue
        lines.append(f"[{chk}]")
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
            lines.append(f"  {f.status:<12} {loc}{f.message}")
            if f.evidence:
                lines.append(f"               {f.evidence}")
        if trimmed:
            lines.append(f"  ... showing {shown} of {shown + trimmed} {chk} item(s); "
                         f"{trimmed} more not listed. --json gives every one.")
        lines.append("")
    listed = [f for f in findings if f.check != "scan"]   # scan items are never collapsed
    nu = sum(1 for f in listed if f.status == UNVERIFIABLE)
    nv = sum(1 for f in listed if f.status == VERIFIED)
    if not show_unverifiable and nu:
        lines.append(f"{nu} UNVERIFIABLE item(s) not listed (no source to check against, or too little "
                     f"evidence to match safely); list them with --show-unverifiable.")
    if not show_verified and nv:
        lines.append(f"{nv} VERIFIED item(s) not listed; list them with --show-verified.")
    lines.append("")
    lines.append("summary")
    for chk, c in summarize(findings).items():
        lines.append(f"  {chk:<12} verified {c[VERIFIED]:>4}   flagged {c[FLAGGED]:>4}"
                     f"   unverifiable {c[UNVERIFIABLE]:>4}")
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


def render_json(findings, skipped=()):
    from . import __version__
    return json.dumps({"schema_version": SCHEMA_VERSION, "tool_version": __version__,
                       "limits": LIMITS, "counts": counts(findings),
                       "summary": summarize(findings),
                       "skipped": list(skipped),
                       "findings": [asdict(f) for f in findings]}, indent=2)
