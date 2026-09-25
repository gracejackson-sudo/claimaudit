"""Guess which directories hold collected documents rather than your writing.

Scraped pages, vendored docs and generated cards are commonly the bulk of the
text in a repository, and auditing them treats someone else's words as your
claims. On one real repository 61% of overclaim flags landed in scraped model
cards.

This module never excludes anything. It reports a guess and suggests the
`--exclude` that would act on it, because every rule that looked safe enough
to apply automatically turned out not to be:

* `.gitignore` is used to keep private drafts out of public repos as often as
  it is used to mark generated files, so honouring it would silently stop
  auditing prose the author cares most about.
* YAML frontmatter separates the two perfectly on some repositories and not at
  all on others, since Jekyll, Hugo, Quarto and Obsidian all put frontmatter
  on hand-written pages.

Getting the guess wrong therefore costs a line of output, never a missed
finding.
"""
from __future__ import annotations
import os
import re
from collections import Counter, defaultdict

MIN_FILES = 5           # too few to see a pattern in
FRONTMATTER_SHARE = 0.8
SKELETON_MIN_FILES = 20
SKELETON_SHARE = 0.2
HEADING = re.compile(r"^#{1,3} (.+)$", re.M)


def _skeleton(text):
    return tuple(HEADING.findall(text)[:6])


def detect(tfiles):
    """-> [(directory, file_count, [reasons])] for directories that look collected."""
    by_dir = defaultdict(list)
    for rel, _abs, text in tfiles:
        by_dir[os.path.dirname(rel)].append(text)

    out = []
    for d, texts in sorted(by_dir.items()):
        n = len(texts)
        if n < MIN_FILES:
            continue
        reasons = []
        fm = sum(1 for t in texts if t.lstrip().startswith("---"))
        if fm >= FRONTMATTER_SHARE * n:
            reasons.append(f"{fm} of {n} open with YAML frontmatter")
        if n >= SKELETON_MIN_FILES:
            shapes = Counter(s for s in (_skeleton(t) for t in texts) if s)
            dup = sum(c for c in shapes.values() if c > 1)
            if dup >= SKELETON_SHARE * n:
                reasons.append(f"{dup} of {n} repeat another file's heading structure")
        if reasons:
            out.append((d or ".", n, reasons))
    return out


def note(groups, tfiles, findings):
    """-> (message, evidence) describing the guess, or None if there is nothing to say."""
    if not groups:
        return None
    dirs = {d for d, _n, _r in groups}
    counted = sum(n for _d, n, _r in groups)
    inside = sum(1 for f in findings
                 if f.check != "scan" and (os.path.dirname(f.file) or ".") in dirs)
    total = sum(1 for f in findings if f.check != "scan")
    share = f", and account for {round(100 * inside / total)}% of the findings below" if total else ""

    msg = (f"{counted} of {len(tfiles)} documents look collected rather than written by you"
           f"{share}. This is a guess from the shape of the files, not a fact.")
    parts = [f"{d} ({n} files: {'; '.join(r)})" for d, n, r in groups[:3]]
    if len(groups) > 3:
        parts.append(f"and {len(groups) - 3} more")
    excl = " ".join(f"--exclude '{d}/*'" for d, _n, _r in groups[:3])
    return msg, "; ".join(parts) + f". To leave them out: {excl}"
