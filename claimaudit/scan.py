"""File discovery and text reading."""
from __future__ import annotations
import fnmatch, os

TEXT_EXT = {".md", ".markdown", ".txt", ".tex", ".rst"}
DATA_EXT = {".csv", ".json"}
BIB_EXT = {".bib"}
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".tox",
             ".mypy_cache", ".pytest_cache", "dist", "build", ".idea", ".vscode"}
MAX_BYTES = 30_000_000


def _ignored(rel, pats):
    rel = rel.replace(os.sep, "/")
    return any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(os.path.basename(rel), p)
               or rel.startswith(p.rstrip("*").rstrip("/") + "/") for p in pats)


def discover(root, exclude=()):
    text, data, bib = [], [], []
    root = os.path.abspath(root)
    if os.path.isfile(root):
        files = [(os.path.dirname(root), os.path.basename(root))]
        base = os.path.dirname(root)
    else:
        base = root
        files = []
        for d, dirs, fns in os.walk(root):
            dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS
                             and not x.startswith("."))
            files += [(d, f) for f in sorted(fns)]
    pats = list(exclude)
    ign = os.path.join(base, ".claimauditignore")
    if os.path.exists(ign):
        pats += [l.strip() for l in open(ign, encoding="utf-8") if l.strip() and not l.startswith("#")]
    for d, f in files:
        p = os.path.join(d, f)
        if _ignored(os.path.relpath(p, base), pats):
            continue
        try:
            if os.path.getsize(p) > MAX_BYTES:
                continue
        except OSError:
            continue
        ext = os.path.splitext(f)[1].lower()
        rel = os.path.relpath(p, base)
        if ext in TEXT_EXT:
            text.append((rel, p))
        elif ext in DATA_EXT:
            data.append((rel, p))
        elif ext in BIB_EXT:
            bib.append((rel, p))
    return base, text, data, bib


def read(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()
