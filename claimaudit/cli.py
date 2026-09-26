"""claimaudit command line."""
from __future__ import annotations
import argparse, sys

from . import (__version__, scan, claims as claims_mod, sources, overclaim, consistency,
               citations, collected, registry, benchmarks, seeds, support, judge as judge_mod, license as lic)
from .report import (render_text, render_json, render_error_json, Finding,
                     FLAGGED, UNVERIFIABLE)


LLM_WARNING = (
    "=" * 72 + "\n"
    "  EXPERIMENTAL. One live-model run recorded: Claude Sonnet 5,\n"
    "  2026-09-26, 16 cases. The hard rule held (no wrong claim came back\n"
    "  SUPPORTED). Yield on true claims was 1 of 8 SUPPORTED; the rest\n"
    "  UNCLEAR. One run at temperature 0 is not extensive validation.\n"
    "  Treat every answer, including SUPPORTED, as a lead to check by hand.\n"
    + "=" * 72)


def _and(items):
    items = list(items)
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def requested_checks(only):
    if only:
        return list(only)
    return [c for c in lic.FREE_CHECKS + lic.PAID_CHECKS if c not in lic.OPT_IN_CHECKS]


def unread(findings):
    """Files the audit never saw. Not the same fact as a clean file."""
    return [f for f in findings if f.check == "scan" and f.extra.get("unread")]


def run(path, only=None, offline=False, strict=False, max_urls=60, paid=False,
        fetch=citations.default_fetch, exclude=(), registry_path=None,
        llm=False, llm_max_calls=25, llm_model=None, llm_client=None, usage_out=None):
    """Run checks; returns (findings, skipped)."""
    import os
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    base, text, data, bib, scan_problems = scan.discover(path, exclude)
    findings = []
    for rel, why in scan_problems:
        findings.append(Finding("scan", UNVERIFIABLE, rel, 0, why, "", {"unread": True}))
    tfiles, bfiles = [], []
    for dest, group in ((tfiles, text), (bfiles, bib)):
        for rel, p in group:
            body, err = scan.read_safe(p)
            if err is None:
                dest.append((rel, p, body))
                # A container we opened but got no prose out of looks exactly
                # like a clean document, so it has to say why it is empty.
                # body.strip() is not enough: a .tex wrapper still has a
                # preamble, and after stripping it has zero sentences -- or
                # a few, from hyperref metadata, that are not prose at all.
                tex_silent = p.lower().endswith(".tex") and (
                    not any(True for _ in claims_mod.sentences(body or "", rel))
                    or scan.wraps_pdf_only(body or "")
                )
                if not body.strip() or tex_silent:
                    why = scan.no_prose_reason(p, body)
                    if why:
                        findings.append(Finding("scan", UNVERIFIABLE, rel, 0,
                                                "no prose could be read from this file, "
                                                "so none of it was checked", why,
                                                {"unread": True}))
                parts = scan.docx_unread_parts(p) if p.lower().endswith(".docx") else []
                if parts:
                    findings.append(Finding(
                        "scan", UNVERIFIABLE, rel, 0,
                        f"this document also has {_and(parts)}, which were not read, "
                        f"so nothing in them was checked",
                        "citations and notes commonly live in footnotes; the body of "
                        "the document is all that claimaudit reads"))
            else:
                findings.append(Finding("scan", UNVERIFIABLE, rel, 0,
                                        f"could not be read, so it was not checked: {err}",
                                        "", {"unread": True}))
    if not tfiles and not bfiles:
        findings.append(Finding("scan", FLAGGED, path, 0,
                                "no readable documents were found here, so nothing was checked "
                                "(claimaudit reads .md, .markdown, .txt, .tex, .rst, .ipynb, "
                                ".docx, .bib and .bbl)"))
    wanted = requested_checks(only)
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
    if "support" in wanted:
        jd = None
        if llm and not offline:
            client = llm_client or judge_mod.AnthropicClient(model=llm_model)
            if client.ready:
                jd = judge_mod.Judge(client, max_calls=llm_max_calls)
            else:
                findings.append(Finding("scan", UNVERIFIABLE, "", 0,
                                        "--llm was given but ANTHROPIC_API_KEY is not set, so no sentence was judged by a model"))
        findings += support.check(tfiles, bfiles, offline=offline, max_sources=max_urls, judge=jd)
        if jd is not None and usage_out is not None:
            usage_out.update(jd.client.usage, model=jd.client.model, cached=(jd.calls == 0))
    if "benchmark" in wanted:
        findings += benchmarks.check(tfiles)
    if "seeds" in wanted:
        findings += seeds.check(tfiles)
    if "registry" in wanted:
        reg = registry.discover(base, registry_path)
        if reg:
            findings += registry.check(tfiles, reg)
        elif registry_path:
            findings.append(Finding("registry", FLAGGED, registry_path, 0,
                                    "no claim registry at this path"))
        else:
            findings += registry.no_registry(tfiles)
    said = collected.note(collected.detect(tfiles), tfiles, findings)
    if said:
        findings.append(Finding("scan", UNVERIFIABLE, "", 0, said[0], said[1]))
    return findings, skipped


def main(argv=None):
    ap = argparse.ArgumentParser(prog="claimaudit",
                                 description="A first-pass audit of claims, citations and numbers. "
                                             "Surfaces things for a human to check; it is not an audit.")
    ap.add_argument("--version", action="version", version=f"claimaudit {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="audit a directory or file")
    c.add_argument("path")
    c.add_argument("--only", help="comma list: overclaim,source,citation,"
                                  "consistency,registry,benchmark,seeds,support "
                                  "(support is off unless named: it downloads full texts)")
    c.add_argument("--registry", help="claim registry (JSON or CSV); default: "
                                      "claimaudit-claims.json/.csv or claims.json/.csv in the folder")
    c.add_argument("--json", action="store_true")
    c.add_argument("--show-verified", action="store_true")
    c.add_argument("--show-unverifiable", action="store_true")
    c.add_argument("--offline", action="store_true", help="skip network lookups")
    c.add_argument("--strict", action="store_true", help="also flag every/all (noisy)")
    c.add_argument("--max-urls", type=int, default=60)
    c.add_argument("--llm", action="store_true",
                   help="EXPERIMENTAL. One recorded live run so far (Claude Sonnet 5, 2026-09-26, 16 cases): the hard "
                        "rule held, yield on true claims was 1 of 8 SUPPORTED; the rest UNCLEAR. One run is not "
                        "extensive validation. With the support check: have a model (Anthropic API, key from "
                        "ANTHROPIC_API_KEY) read the cited passage for sentences the plain check could not decide. "
                        "Sends the sentence and short excerpts of the cited paper to api.anthropic.com. A SUPPORTED "
                        "answer must carry a quote that is found verbatim in the passages shown. Treat every answer, "
                        "including SUPPORTED, as a lead to check by hand")
    c.add_argument("--llm-max-calls", type=int, default=25, help="most model calls per run (default 25)")
    c.add_argument("--llm-model", default=None, help="model id (default: $CLAIMAUDIT_MODEL or " + judge_mod.DEFAULT_MODEL + ")")
    c.add_argument("--exclude", action="append", default=[], help="glob to skip (repeatable); also reads .claimauditignore")
    c.add_argument("--fail-on", choices=("flagged", "unverifiable", "never"), default="flagged",
                   help="what makes the exit code 1. 'flagged' (default) is the one to gate a "
                        "build on. 'unverifiable' also fails when something could not be "
                        "checked, which is strict and noisy. 'never' exits 0 whatever "
                        "the findings say. None of the three can make a file that "
                        "could not be read, or a run in which no check was licensed "
                        "to run, look like a pass")
    c.add_argument("--exit-zero", action="store_true",
                   help="alias for --fail-on never")
    a = sub.add_parser("activate", help="store a license key locally (no server check)")
    a.add_argument("key")
    sub.add_parser("status", help="show license status")
    ns = ap.parse_args(argv)

    if ns.cmd == "activate":
        ok, msg = lic.activate(ns.key)
        print((f"activated: {msg}" if ok else f"activation failed: {msg}"))
        return 0 if ok else 2
    t, msg = lic.tier()
    if ns.cmd == "status":
        print(f"tier: {t} ({msg})")
        if t == "free":
            print("buy a license: " + (lic.PURCHASE_URL or "(purchase link not configured in this build)"))
        return 0
    def fail(why):
        """Exit 2 without leaving a --json consumer a zero-byte file."""
        if ns.json:
            print(render_error_json(why))
        else:
            print(why, file=sys.stderr)
        return 2

    only = [x.strip() for x in ns.only.split(",")] if ns.only else None
    bad = [x for x in (only or []) if x not in lic.FREE_CHECKS + lic.PAID_CHECKS]
    if bad:
        return fail(f"unknown check(s): {bad}")
    try:
        usage = {}
        if ns.llm and not (only and "support" in only):
            return fail("--llm needs the support check: add --only support")
        if ns.llm:
            print(LLM_WARNING, file=sys.stderr)
        if ns.llm and not ns.offline:
            print("--llm: sentences and short excerpts of the cited papers will be sent to api.anthropic.com "
                  f"(at most {ns.llm_max_calls} calls)", file=sys.stderr)
        findings, skipped = run(ns.path, only, ns.offline, ns.strict, ns.max_urls,
                                paid=(t == "paid"), exclude=ns.exclude, registry_path=ns.registry,
                                llm=ns.llm, llm_max_calls=ns.llm_max_calls, llm_model=ns.llm_model,
                                usage_out=usage)
        if ns.llm:
            print("--llm results are experimental (one recorded live run, not extensive validation): check every one by hand.", file=sys.stderr)
        if usage.get("calls"):
            print(f"--llm: {usage['calls']} call(s) to {usage['model']}, {usage['input_tokens']} input and "
                  f"{usage['output_tokens']} output tokens", file=sys.stderr)
    except FileNotFoundError:
        return fail(f"path not found: {ns.path}")
    wanted = requested_checks(only)
    ran = [c for c in wanted if c not in skipped]
    print(render_json(findings, skipped, ran) if ns.json
          else render_text(findings, skipped, ns.show_verified, ns.show_unverifiable, ran))
    if skipped and not ns.json:
        print(f"tier: {t} ({msg})")
        print("buy a license: " + (lic.PURCHASE_URL or "(purchase link not configured in this build)"))
    # Nothing ran at all. A green result here says the documents are fine
    # when in fact they were never looked at, which is the one thing a gate
    # must never do -- so --fail-on does not get a say.
    if not ran:
        print("no check ran: every check requested needs a license, so nothing "
              "was audited", file=sys.stderr)
        return 2
    # Likewise a file that could not be read. The audit is incomplete
    # whatever the other findings say.
    if unread(findings):
        print(f"{len(unread(findings))} file(s) could not be audited; see the "
              f"scan section above", file=sys.stderr)
        return 1
    level = "never" if ns.exit_zero else ns.fail_on
    if level == "never":
        return 0
    fails = (FLAGGED, UNVERIFIABLE) if level == "unverifiable" else (FLAGGED,)
    return 1 if any(f.status in fails for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
