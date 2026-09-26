"""Live validation of Layer B against the real cases. Costs a few cents of API use.

    ANTHROPIC_API_KEY=... python validation/llm_validate.py            # default model
    ANTHROPIC_API_KEY=... python validation/llm_validate.py --model claude-sonnet-5 --repeats 2

For each case in fixtures/citation_support/llm_cases.json it fetches the real source,
lets the judge read it, and records the verdict, the quote, the passages shown, and the
model's raw replies. Nothing is written except the results file. The key is read from the
environment and only ever sent to api.anthropic.com in a request header.

The hard rule being tested: a claim marked NOT_SUPPORTED must never come back SUPPORTED.
Everything else is reported, not judged.
"""
import argparse, json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import llm_cases
from claimaudit import support, judge as jd

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "fixtures", "citation_support", "llm_results.json")


class Recorder:
    """Wraps the client, keeping every request and reply so the run can be re-checked offline."""
    def __init__(self, client):
        self.client, self.log = client, []
        self.model, self.usage = client.model, client.usage

    def __call__(self, system, user, max_tokens=400):
        reply = self.client(system, user, max_tokens)
        self.log.append({"system_is_challenge": "sceptical" in system, "user": user, "reply": reply})
        return reply


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None)
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--only", default=None, help="comma list of case ids")
    ns = ap.parse_args()
    client = jd.AnthropicClient(model=ns.model)
    if not client.ready:
        print("ANTHROPIC_API_KEY is not set in this shell. Set it for this one command, e.g.\n"
              "  ANTHROPIC_API_KEY=sk-ant-... python validation/llm_validate.py", file=sys.stderr)
        return 2
    cases = llm_cases.load()
    if ns.only:
        keep = set(ns.only.split(","))
        cases = [c for c in cases if c["id"] in keep]
    prov = support.NetworkProvider()
    results = []
    for c in cases:
        src = prov(c["ident"])
        if not (src.trusted and src.text):
            results.append({"id": c["id"], "expect": c["expect"], "verdict": "NOT RUN", "note": src.note or "source not readable"})
            print(f"{c['id']:<22} NOT RUN  ({src.note[:60]})")
            continue
        for rep in range(ns.repeats):
            rec = Recorder(client)
            j = jd.Judge(rec, use_cache=False, max_calls=4)
            v = j.judge(llm_cases.pair_for(c), src)
            results.append({"id": c["id"], "repeat": rep, "expect": c["expect"], "origin": c["origin"],
                            "sentence": c["sentence"], "verdict": v.verdict, "quote": v.quote, "excerpt": v.excerpt,
                            "reason": v.reason, "note": v.note, "windows": v.windows, "calls": rec.log,
                            "model": client.model})
            tag = ""
            if c["expect"] == "NOT_SUPPORTED" and v.verdict == jd.SUPPORTED:
                tag = "   <-- FALSE SUPPORTED"
            print(f"{c['id']:<22}{c['expect']:<15}{jd.label(v):<32}{tag}")
    hard = [r for r in results if r["expect"] == "NOT_SUPPORTED" and r["verdict"] == jd.SUPPORTED]
    ran = [r for r in results if r["verdict"] != "NOT RUN"]
    ns_ = [r for r in ran if r["expect"] == "NOT_SUPPORTED"]
    ms = [r for r in ran if r["expect"] == "MAY_SUPPORT"]
    cnt = lambda rs, v: sum(1 for r in rs if r["verdict"] == v)
    print(f"\nmust-not-be-SUPPORTED cases ({len(ns_)} runs): SUPPORTED {cnt(ns_, 'SUPPORTED')}  "
          f"CONTRADICTED {cnt(ns_, 'CONTRADICTED')}  UNCLEAR {cnt(ns_, 'UNCLEAR')}")
    print(f"true claims ({len(ms)} runs):                  SUPPORTED {cnt(ms, 'SUPPORTED')}  "
          f"UNCLEAR {cnt(ms, 'UNCLEAR')}  CONTRADICTED {cnt(ms, 'CONTRADICTED')} (false alarm)")
    print(f"answers discarded by a guard: {sum(1 for r in ran if r['note'])}")
    print(f"usage: {client.usage['calls']} calls, {client.usage['input_tokens']} input / {client.usage['output_tokens']} output tokens, model {client.model}")
    print("HARD RULE " + ("VIOLATED" if hard else "held"))
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump({"model": client.model, "date": time.strftime("%Y-%m-%d"), "usage": client.usage, "results": results}, fh, indent=1)
    print("wrote", os.path.normpath(OUT))
    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main())
