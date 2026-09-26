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
MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"


PROSE_CELLS = ("markdown", "heading")      # heading cells are nbformat v3 prose
KNOWN_CELLS = PROSE_CELLS + ("code", "raw")


def _cells(nb):
    """Cells in order, from either notebook layout. Raises if there are none.

    nbformat 4 keeps them under "cells"; version 3 keeps them under
    worksheets[i].cells. A file we cannot find cells in at all is not a
    notebook, and must surface as an unread file rather than as empty prose.
    """
    if isinstance(nb.get("cells"), list):
        cells = nb["cells"]
    elif isinstance(nb.get("worksheets"), list):
        cells = []
        for ws in nb["worksheets"]:
            if isinstance(ws, dict) and isinstance(ws.get("cells"), list):
                cells += ws["cells"]
    else:
        raise ValueError("no 'cells' or 'worksheets' list, so this is not a notebook")
    typed = [c for c in cells if isinstance(c, dict) and c.get("cell_type") in KNOWN_CELLS]
    if cells and not typed:
        raise ValueError("no cell has a recognised cell_type, so this is not a notebook")
    return typed


def _source(src):
    """A cell's source as text. null and non-string parts contribute nothing."""
    if isinstance(src, list):
        return "".join(x for x in src if isinstance(x, str))
    return src if isinstance(src, str) else ""


def read_notebook(path):
    """Markdown cells only, in order, one blank line between cells.

    Code cells are skipped on purpose. Their outputs are full of numbers that
    nobody wrote as a claim, and treating them as prose would bury the real
    claims in noise. A reported line number counts lines of the concatenated
    markdown, so it is neither a line of the .ipynb JSON nor a line within the
    cell that carries the claim.
    """
    with open(path, encoding="utf-8") as fh:
        nb = json.load(fh)
    if not isinstance(nb, dict):
        raise ValueError("notebook is not a JSON object")
    return "\n\n".join(_source(c.get("source", "")) for c in _cells(nb)
                       if c.get("cell_type") in PROSE_CELLS)


def no_prose_reason(path, text=None):
    """Why a container file we could open yielded no prose. Best effort.

    Silence has to be explained: a notebook of nothing but code cells is not
    the same fact as a notebook whose prose says nothing worth flagging.
    """
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".tex":
            raw = text if text is not None else read(path)
            if re.search(r"\\includepdf\b", raw):
                return "it wraps a PDF with \\includepdf, and PDFs are not read"
            return "stripping the LaTeX produced no readable sentences"
        if ext == ".ipynb":
            with open(path, encoding="utf-8") as fh:
                cells = _cells(json.load(fh))
            kinds = [c.get("cell_type") for c in cells]
            if not kinds:
                return "it has no cells"
            if all(k == "code" for k in kinds):
                return (f"it contains only code cells ({len(kinds)} of them), "
                        f"and code is not read as prose")
            if not any(k in PROSE_CELLS for k in kinds):
                return f"none of its {len(kinds)} cells is a markdown cell"
            return "its markdown cells are all empty"
        if ext == ".docx":
            return "it has no paragraphs carrying text"
    except (OSError, ValueError, KeyError):
        pass
    return None


def _branch(el):
    """Children to walk. Word stores a text box twice, once per compatibility
    branch, so taking both duplicates every sentence inside it."""
    if el.tag == MC + "AlternateContent":
        return [c for c in el if c.tag == MC + "Choice"] \
            or [c for c in el if c.tag == MC + "Fallback"]
    return list(el)


def _para_text(p):
    """A paragraph's text, in document order, separators kept.

    Walking w:t alone fused sentences across a line break and welded labels
    onto numbers across a tab. Nested paragraphs -- text boxes, mostly -- are
    read here and then skipped as paragraphs of their own, so their text
    appears exactly once.
    """
    parts = []

    def walk(el):
        for c in _branch(el):
            if c.tag == W + "t":
                parts.append(c.text or "")
            elif c.tag in (W + "br", W + "cr"):
                parts.append("\n")
            elif c.tag == W + "tab":
                parts.append("\t")
            else:
                walk(c)

    walk(p)
    return "".join(parts)


def _paragraphs(el, out):
    for child in _branch(el):
        if child.tag == W + "p":
            out.append(_para_text(child))
        else:
            _paragraphs(child, out)


def read_docx(path):
    """Body paragraphs as lines. A reported line number is a paragraph number."""
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml")
    paras = []
    _paragraphs(ET.fromstring(xml), paras)
    return "\n".join(paras)


# Parts of a .docx that carry prose and that read_docx does not touch. Real
# citations live in footnotes, so a document whose footnotes were never
# opened must not come back looking the same as one that has none.
DOCX_PARTS = (
    (re.compile(r"^word/footnotes\.xml$"), "footnotes"),
    (re.compile(r"^word/endnotes\.xml$"), "endnotes"),
    (re.compile(r"^word/header\d*\.xml$"), "headers"),
    (re.compile(r"^word/footer\d*\.xml$"), "footers"),
    (re.compile(r"^word/comments\.xml$"), "comments"),
)


def docx_unread_parts(path):
    """-> names of prose-bearing parts the file contains but we do not read.

    Only the zip listing is consulted; nothing is parsed. Whether to read
    these is a separate decision with its own line-numbering and provenance
    questions. Saying they are there is what stops silence reading as
    approval over them.
    """
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
    except (OSError, zipfile.BadZipFile):
        return []
    out = []
    for pat, label in DOCX_PARTS:
        if label not in out and any(pat.match(n) for n in names):
            out.append(label)
    return out


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
