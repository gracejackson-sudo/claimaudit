"""Stress test: does PDF text extraction ever make a word that IS in a paper look
absent? This is the one way the support check could do harm, so it is measured on
real papers rather than argued.

Ground truth: the paper's own arXiv HTML with formulas removed (the words the
author wrote). Test: for every rare word in that ground truth, does the tool's
PDF extraction still find it? A miss is a FALSE ABSENCE: the tool would have
been free to tell a user their citation lacks a word the paper contains.

Reported three ways so the safeguards can be judged one at a time:
  naive       exact tokens of raw pypdf text, no normalization
  normalized  the tool's normalization (ligatures, dashes, dehyphenation), exact tokens
  tool        what the tool actually does before calling a word absent
              (normalized + bibliography + letters-only search)
Run:  python validation/pdf_stress.py DIR_WITH pdf/ AND html/   (needs pypdf)
"""
import os, re, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
from claimaudit import fulltext as F


def rare_words(idx_counts, lo=1, hi=3, minlen=5):
    return sorted(t for t, n in idx_counts.items()
                  if lo <= n <= hi and len(t) >= minlen and re.fullmatch(r"[a-z]+", t))


def main(root):
    rows = []
    for fn in sorted(os.listdir(os.path.join(root, "pdf"))):
        pid = fn[:-4]
        hp = os.path.join(root, "html", pid + ".html")
        if not os.path.exists(hp):
            continue
        body, _ = F.html_to_text(open(hp, encoding="utf-8", errors="replace").read(), keep_math=False)
        truth = F.TextIndex.from_text(body)
        words = rare_words(truth.counts)
        data = open(os.path.join(root, "pdf", fn), "rb").read()
        got = F.pdf_to_text(data); raw = got[0] if got else None
        if not raw:
            rows.append((pid, None)); continue
        naive = set(F.tokens(raw.lower()))
        pdf_idx = F.TextIndex.from_text(raw)
        miss_naive = sum(1 for w in words if w not in naive)
        miss_norm = sum(1 for w in words if pdf_idx._count_norm(w) == 0)
        misses = [w for w in words if not pdf_idx.maybe_present(w)]
        rows.append((pid, len(words), miss_naive, miss_norm, len(misses), misses[:12], len(raw)))
    print(f"{'paper':<14}{'rare words':>11}{'naive miss':>12}{'norm miss':>11}{'tool miss':>11}")
    tot = [0, 0, 0, 0]
    for r in rows:
        if r[1] is None:
            print(f"{r[0]:<14} PDF could not be read"); continue
        pid, n, a, b, c, ex, _ = r
        print(f"{pid:<14}{n:>11}{a:>8} ({100*a/n:4.1f}%){b:>6} ({100*b/n:4.1f}%){c:>6} ({100*c/n:4.1f}%)")
        for i, v in enumerate((n, a, b, c)):
            tot[i] += v
    n, a, b, c = tot
    print(f"{'ALL':<14}{n:>11}{a:>8} ({100*a/n:4.1f}%){b:>6} ({100*b/n:4.1f}%){c:>6} ({100*c/n:4.1f}%)")
    for r in rows:
        if r[1] and r[4]:
            print(f"  still missed in {r[0]}: {r[5]}")


if __name__ == "__main__" and (len(sys.argv) < 3 or sys.argv[2] != "mutations"):
    main(sys.argv[1])


# ---------------------------------------------------------------------------
# Part 2: what the self-check can and cannot see. Damage real extracted text in
# known ways and record (a) whether the check refuses to trust it and (b) when it
# does NOT refuse, how many present words would wrongly be called absent.
# ---------------------------------------------------------------------------
import random


def _meta_from_html(h):
    t = re.search(r"<title>(.*?)</title>", h, re.S)
    title = re.sub(r"^\[\d+\.\d+(?:v\d+)?\]\s*", "", re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", t.group(1))).strip()) if t else ""
    a = re.search(r'ltx_abstract.*?</div>', h, re.S)
    abst = re.sub(r"^Abstract\.?", "", re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", a.group(0))).strip()) if a else ""
    return title, abst[:1500]


MUTATIONS = {
    "letter-spaced text": lambda t, r: re.sub(r"(?<=\w)(?=\w)", " ", t),
    "ligature glyphs lost (fi, fl, ff)": lambda t, r: re.sub(r"ffi|ffl|fi|fl|ff", "", t),
    "3% characters garbled": lambda t, r: "".join("�" if r.random() < 0.03 else c for c in t),
    "30% of long words hyphen-split": lambda t, r: re.sub(r"\b(\w{4})(\w{4,})\b", lambda m: m.group(1) + "-\n" + m.group(2) if r.random() < 0.3 else m.group(0), t),
    "text cut off at 40%": lambda t, r: t[: int(len(t) * 0.4)],
}


def _drop_words(rate):
    def f(t, r):                      # drop words but keep the line structure
        return "\n".join(" ".join(w for w in ln.split(" ") if r.random() >= rate) for ln in t.split("\n"))
    return f


def mutations(root):
    papers = []
    for fn in sorted(os.listdir(os.path.join(root, "pdf"))):
        pid = fn[:-4]
        hp = os.path.join(root, "html", pid + ".html")
        if not os.path.exists(hp):
            continue
        h = open(hp, encoding="utf-8", errors="replace").read()
        body, _ = F.html_to_text(h, keep_math=False)
        truth = F.TextIndex.from_text(body)
        words = [w for w in rare_words(truth.counts) if w not in ("affiliation", "thanks", "footnotemark", "footnotetext")]
        got = F.pdf_to_text(open(os.path.join(root, "pdf", fn), "rb").read())
        title, abst = _meta_from_html(h)
        papers.append((pid, got[0], words, title, abst))

    def run(name, fn):
        refused = judged = harm = harm_n = 0
        for pid, raw, words, title, abst in papers:
            for seed in range(3):
                mt = fn(raw, random.Random(hash((pid, seed)) & 0xffff))
                idx = F.TextIndex.from_text(mt)
                q = F.quality(idx, mt[:200000], title, abst, tail_ok=F.has_reference_tail(mt))
                judged += 1
                if not q["trusted"]:
                    refused += 1
                else:
                    harm += sum(1 for w in words if not idx.maybe_present(w))
                    harm_n += len(words)
        rate = f"{100*harm/harm_n:5.1f}%" if harm_n else "   n/a"
        print(f"  {name:<36} refused {refused:>2}/{judged:<2}   false-absence when NOT refused: {rate}")

    print("\nSELF-CHECK SENSITIVITY (10 papers x 3 damage seeds each)")
    print("  baseline (undamaged PDF text)".ljust(38), end="")
    run_base = 0
    for pid, raw, words, title, abst in papers:
        idx = F.TextIndex.from_text(raw)
        run_base += F.quality(idx, raw[:200000], title, abst, tail_ok=F.has_reference_tail(raw))["trusted"]
    print(f"trusted {run_base}/{len(papers)}")
    for name, fn in MUTATIONS.items():
        run(name, fn)
    print("  -- random loss of words, at increasing rates --")
    for rate in (0.01, 0.02, 0.05, 0.10, 0.20, 0.30):
        run(f"{int(rate*100)}% of words silently dropped", _drop_words(rate))


if __name__ == "__main__" and len(sys.argv) > 2 and sys.argv[2] == "mutations":
    mutations(sys.argv[1])


# ---------------------------------------------------------------------------
# Part 3: the words that matter. The 0.4% above is over ALL rare words. A claim's
# checkable words are proper nouns, acronyms and compounds, so measure those, and
# show every miss with its context so each can be classified by eye.
# ---------------------------------------------------------------------------
def candidates(root, show=True):
    from claimaudit import support
    tot = miss = 0
    rows = []
    for fn in sorted(os.listdir(os.path.join(root, "pdf"))):
        pid = fn[:-4]
        hp = os.path.join(root, "html", pid + ".html")
        if not os.path.exists(hp):
            continue
        body, _ = F.html_to_text(open(hp, encoding="utf-8", errors="replace").read(), keep_math=False)
        got = F.pdf_to_text(open(os.path.join(root, "pdf", fn), "rb").read())
        pidx = F.TextIndex.from_text(got[0])
        seen = {}
        for sent in re.split(r"(?<=[.!?])\s+", body):
            for w, k in support.candidate_terms(sent, [], []):
                seen.setdefault(w.lower(), (w, k, sent))
        for lw, (w, k, sent) in seen.items():
            tot += 1
            if not support._term_present(w, k, pidx):
                miss += 1
                rows.append((pid, w, k, re.sub(r"\s+", " ", sent)[:110]))
    print(f"\nCLAIM-LIKE WORDS (proper nouns, acronyms, compounds) found in the HTML but not in the PDF extraction: {miss} of {tot} ({100*miss/tot:.2f}%)")
    cls = {}

    def klass(pid, w):
        lw = w.lower()
        if lw in ("affiliation", "email"):
            return "front-matter template marker (HTML only)"
        if "footnotemark" in lw or re.fullmatch(r"[a-z]+(\d)\1", lw):
            return "footnote number glued to a word (HTML only)"
        if re.fullmatch(r"[a-z]+\d{4}[a-z]*", lw):
            return "raw BibTeX key shown by a broken HTML bibliography"
        if pid == "2204.12852":
            return "ACM copyright/venue footer, absent from the PDF text layer"
        return "OTHER: needs a look"
    for r in rows:
        cls.setdefault(klass(r[0], r[1]), []).append(r)
    for k, v in sorted(cls.items(), key=lambda kv: -len(kv[1])):
        print(f"  {len(v):>3}  {k}")
    if show:
        for r in rows:
            print(f"  {r[0]:<11}{r[1]:<24}{r[2]:<9}| {r[3]}")


if __name__ == "__main__" and len(sys.argv) > 2 and sys.argv[2] == "candidates":
    candidates(sys.argv[1])
