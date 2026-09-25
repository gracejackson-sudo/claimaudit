"""claimaudit command line."""
from __future__ import annotations
import argparse, sys

from . import __version__, scan, claims as claims_mod, sources, overclaim, consistency, citations, license as lic
from .report import render_text, render_json, Finding, FLAGGED, UNVERIFIABLE


def run(path, only=None, offline=False, strict=False, max_urls=60, paid=False,
        fetch=citations.default_fetch, exclude=()):
    """Run checks; returns (findings, skipped)."""
    import os
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    base, text, data, bib = scan.discover(path, exclude)
    findings = []
    tfiles, bfiles = [], []
    for dest, group in ((tfiles, text), (bfiles, bib)):
        for rel, p in group:
            body, err = scan.read_safe(p)
            if err is None:
                dest.append((rel, p, body))
            else:
                findings.append(Finding("scan", UNVERIFIABLE, rel, 0,
                                        f"could not be read, so it was not checked: {err}"))
    if not tfiles and not bfiles:
        findings.append(Finding("scan", FLAGGED, path, 0,
                                "no readable documents were found here, so nothing was checked "
                                "(claimaudit reads .md, .markdown, .txt, .tex, .rst and .bib)"))
    wanted = list(only) if only else list(lic.FREE_CHECKS + lic.PAID_CHECKS)
    skipped = [c for c in wanted if c in lic.PAID_CHECKS and not paid]
    wanted = [c for c in wanted if c not in skipped]
    cl = None
    if "overclaim" in wanted:
        findings += overclaim.scan(tfiles, strict=strict)
    if "source" in wanted or "consistency" in wanted:
        cl = claims_mod.extract(tfiles)
    if "source" in wanted:
        idx = sources.build_index(data)
        findings += [Finding("scan", UNVERIFIABLE, rel, 0, why) for rel, why in idx.problems]
        findings += sources.verify(cl, idx)
    if "consistency" in wanted:
        findings += consistency.check(cl)
    if "citation" in wanted:
        findings += citations.check(tfiles, bfiles, fetch=fetch, offline=offline, max_urls=max_urls)
    return findings, skipped


def main(argv=None):
    ap = argparse.ArgumentParser(prog="claimaudit",
                                 description="A first-pass audit of claims, citations and numbers. "
                                             "Surfaces things for a human to check; it is not an audit.")
    ap.add_argument("--version", action="version", version=f"claimaudit {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="audit a directory or file")
    c.add_argument("path")
    c.add_argument("--only", help="comma list: overclaim,source,citation,consistency")
    c.add_argument("--json", action="store_true")
    c.add_argument("--show-verified", action="store_true")
    c.add_argument("--show-unverifiable", action="store_true")
    c.add_argument("--offline", action="store_true", help="skip network lookups")
    c.add_argument("--strict", action="store_true", help="also flag every/all (noisy)")
    c.add_argument("--max-urls", type=int, default=60)
    c.add_argument("--exclude", action="append", default=[], help="glob to skip (repeatable); also reads .claimauditignore")
    c.add_argument("--exit-zero", action="store_true", help="exit 0 even if findings are flagged")
    a = sub.add_parser("activate", help="activate a license key")
    a.add_argument("key")
    sub.add_parser("status", help="show license status")
    ns = ap.parse_args(argv)

    if ns.cmd == "activate":
        ok, msg = lic.activate(ns.key)
        print(("activated" if ok else f"activation failed: {msg}"))
        return 0 if ok else 2
    t, msg = lic.tier()
    if ns.cmd == "status":
        print(f"tier: {t} ({msg})")
        if t == "free":
            print("buy a license: " + (lic.PURCHASE_URL or "(purchase link not configured in this build)"))
        return 0
    only = [x.strip() for x in ns.only.split(",")] if ns.only else None
    bad = [x for x in (only or []) if x not in lic.FREE_CHECKS + lic.PAID_CHECKS]
    if bad:
        print(f"unknown check(s): {bad}", file=sys.stderr)
        return 2
    try:
        findings, skipped = run(ns.path, only, ns.offline, ns.strict, ns.max_urls, paid=(t == "paid"), exclude=ns.exclude)
    except FileNotFoundError:
        print(f"path not found: {ns.path}", file=sys.stderr)
        return 2
    print(render_json(findings, skipped) if ns.json else render_text(findings, skipped, ns.show_verified, ns.show_unverifiable))
    if skipped and not ns.json:
        print(f"tier: {t} ({msg})")
        print("buy a license: " + (lic.PURCHASE_URL or "(purchase link not configured in this build)"))
    return 0 if ns.exit_zero or not any(f.status == FLAGGED for f in findings) else 1


if __name__ == "__main__":
    sys.exit(main())
