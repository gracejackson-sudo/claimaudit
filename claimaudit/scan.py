"""File discovery and text reading."""
from __future__ import annotations
import fnmatch, json, os, re, xml.etree.ElementTree as ET, zipfile

TEXT_EXT = {".md", ".markdown", ".txt", ".tex", ".rst", ".ipynb", ".docx"}
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


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def read_notebook(path):
    """Markdown cells only, in order, one blank line between cells.

    Code cells are skipped on purpose. Their outputs are full of numbers that
    nobody wrote as a claim, and treating them as prose would bury the real
    claims in noise. A reported line number counts lines of markdown, so it
    is the line within the cell text rather than within the .ipynb JSON.
    """
    with open(path, encoding="utf-8") as fh:
        nb = json.load(fh)
    if not isinstance(nb, dict):
        raise ValueError("notebook is not a JSON object")
    out = []
    for cell in nb.get("cells", []):
        if not isinstance(cell, dict) or cell.get("cell_type") != "markdown":
            continue
        src = cell.get("source", "")
        out.append("".join(src) if isinstance(src, list) else str(src))
    return "\n\n".join(out)


def read_docx(path):
    """Body paragraphs as lines. A reported line number is a paragraph number."""
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml")
    root = ET.fromstring(xml)
    paras = []
    for p in root.iter(W + "p"):
        paras.append("".join(t.text or "" for t in p.iter(W + "t")))
    return "\n".join(paras)


def read(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".ipynb":
        return read_notebook(path)
    if ext == ".docx":
        return read_docx(path)
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def read_safe(path):
    """-> (text, error). error is a string when the file could not be read.

    One unreadable file must not end the audit of every other file, and it
    must not pass unmentioned either.
    """
    try:
        return read(path), None
    except OSError as e:
        return None, e.strerror or str(e)
    except (ValueError, KeyError, ET.ParseError, zipfile.BadZipFile) as e:
        # A damaged notebook or .docx is a container problem, not an I/O one.
        return None, f"{type(e).__name__}: {e}"
