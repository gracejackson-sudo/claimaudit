"""Findings and report rendering."""
from __future__ import annotations
import json
from dataclasses import dataclass, asdict, field

VERIFIED, FLAGGED, UNVERIFIABLE = "VERIFIED", "FLAGGED", "UNVERIFIABLE"


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
    for chk in ("source", "citation", "consistency", "overclaim"):
        fs = [f for f in findings if f.check == chk]
        if not fs:
            continue
        lines.append(f"[{chk}]")
        for f in sorted(fs, key=lambda f: (order[f.status], f.file, f.line)):
            if f.status == VERIFIED and not show_verified:
                continue
            if f.status == UNVERIFIABLE and not show_unverifiable:
                continue
            lines.append(f"  {f.status:<12} {f.file}:{f.line}  {f.message}")
            if f.evidence:
                lines.append(f"               {f.evidence}")
        lines.append("")
    nu = sum(1 for f in findings if f.status == UNVERIFIABLE)
    nv = sum(1 for f in findings if f.status == VERIFIED)
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


def render_json(findings, skipped=()):
    return json.dumps({"limits": LIMITS, "summary": summarize(findings),
                       "skipped": list(skipped),
                       "findings": [asdict(f) for f in findings]}, indent=2)
