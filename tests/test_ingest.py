"""Reading notebooks and .docx, both with the standard library only."""
from __future__ import annotations
import json
import os
import subprocess
import sys
import zipfile

import pytest

from claimaudit import cli, scan

DOC_XML = """<?xml version="1.0"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:body>{}</w:body></w:document>"""


def _para(*runs):
    return "<w:p>" + "".join(f"<w:r><w:t>{r}</w:t></w:r>" for r in runs) + "</w:p>"


def _docx(path, *paras):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", DOC_XML.format("".join(paras)))
        z.writestr("[Content_Types].xml", "<Types/>")


def _nb(path, *cells):
    path.write_text(json.dumps({"cells": [dict(c) for c in cells],
                                "metadata": {}, "nbformat": 4}), encoding="utf-8")


def md(source):
    return {"cell_type": "markdown", "source": source}


def code(source, out="0.9999"):
    return {"cell_type": "code", "source": source,
            "outputs": [{"output_type": "stream", "text": [out]}]}


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAIMAUDIT_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CLAIMAUDIT_LICENSE_KEY", raising=False)


# ------------------------------------------------------------ notebooks
def test_markdown_cells_are_read(tmp_path):
    _nb(tmp_path / "a.ipynb", md(["# Results\n", "\n", "Our method is the best ever.\n"]))
    assert "best ever" in scan.read(str(tmp_path / "a.ipynb"))


def test_source_may_be_a_plain_string(tmp_path):
    _nb(tmp_path / "a.ipynb", md("Accuracy was 91.2%.\n"))
    assert "91.2" in scan.read(str(tmp_path / "a.ipynb"))


def test_code_cells_and_their_outputs_are_not_treated_as_prose(tmp_path):
    """Output numbers are not claims; reading them would bury the real ones."""
    _nb(tmp_path / "a.ipynb", md("Accuracy was 91.2%.\n"), code("print(acc)\n", out="0.9999"))
    text = scan.read(str(tmp_path / "a.ipynb"))
    assert "91.2" in text and "0.9999" not in text and "print" not in text


def test_cells_are_separated_so_claims_do_not_run_together(tmp_path):
    _nb(tmp_path / "a.ipynb", md("first"), md("second"))
    assert scan.read(str(tmp_path / "a.ipynb")) == "first\n\nsecond"


def test_overclaims_in_a_notebook_are_flagged_end_to_end(tmp_path):
    _nb(tmp_path / "a.ipynb", md("Our approach is the first to solve this.\n"))
    findings, _ = cli.run(str(tmp_path), only=["overclaim"])
    assert any(f.file == "a.ipynb" for f in findings)


def test_registry_tags_work_inside_notebooks(tmp_path):
    _nb(tmp_path / "a.ipynb", md("Built on 817<!-- claim: n_rows = 817 --> rows.\n"))
    (tmp_path / "claims.json").write_text(json.dumps({"n_rows": 900}))
    findings, _ = cli.run(str(tmp_path), only=["registry"], paid=True)
    assert findings[0].status == "FLAGGED" and "computes 900" in findings[0].message


def test_a_damaged_notebook_is_reported_not_crashed_on(tmp_path):
    (tmp_path / "a.ipynb").write_text("{ this is not json", encoding="utf-8")
    (tmp_path / "b.md").write_text("Fine file.\n", encoding="utf-8")
    findings, _ = cli.run(str(tmp_path), only=["overclaim"])
    bad = [f for f in findings if f.check == "scan" and f.file == "a.ipynb"]
    assert bad and "not be read" in bad[0].message


def test_a_notebook_with_no_markdown_cells_reads_as_empty(tmp_path):
    _nb(tmp_path / "a.ipynb", code("x = 1\n"))
    assert scan.read(str(tmp_path / "a.ipynb")) == ""


def test_an_all_code_notebook_is_reported_not_passed_over(tmp_path):
    """Real notebooks are often all code; an empty report would read as clean."""
    _nb(tmp_path / "a.ipynb", code("x = 1\n"), code("print(x)\n"))
    findings, _ = cli.run(str(tmp_path), only=["overclaim"])
    note = [f for f in findings if f.check == "scan" and f.file == "a.ipynb"]
    assert note and note[0].status == "UNVERIFIABLE"
    assert "no prose could be read" in note[0].message
    assert "only code cells" in note[0].evidence


def test_nbformat_v3_worksheets_are_read(tmp_path):
    (tmp_path / "a.ipynb").write_text(json.dumps(
        {"worksheets": [{"cells": [{"cell_type": "markdown", "source": ["Accuracy was 91.2%.\n"]},
                                   {"cell_type": "heading", "source": "Results"}]}],
         "metadata": {}, "nbformat": 3}), encoding="utf-8")
    text = scan.read(str(tmp_path / "a.ipynb"))
    assert "91.2" in text and "Results" in text


def test_a_json_file_that_is_not_a_notebook_is_reported_as_unread(tmp_path):
    for payload in ({"cells": {"0": {"cell_type": "markdown"}}},
                    {"cells": [{"source": "no cell_type here"}]},
                    {"name": "some other json shaped thing"}):
        (tmp_path / "a.ipynb").write_text(json.dumps(payload), encoding="utf-8")
        findings, _ = cli.run(str(tmp_path), only=["overclaim"])
        bad = [f for f in findings if f.check == "scan" and f.file == "a.ipynb"]
        assert bad and "not be read" in bad[0].message, payload


def test_an_empty_notebook_is_reported_rather_than_read_as_clean(tmp_path):
    _nb(tmp_path / "a.ipynb")
    findings, _ = cli.run(str(tmp_path), only=["overclaim"])
    note = [f for f in findings if f.check == "scan" and f.file == "a.ipynb"]
    assert note and "no cells" in note[0].evidence


def test_a_null_source_contributes_nothing_rather_than_the_word_None(tmp_path):
    _nb(tmp_path / "a.ipynb", {"cell_type": "markdown", "source": None},
        md("Accuracy was 91.2%.\n"))
    assert "None" not in scan.read(str(tmp_path / "a.ipynb"))


# ------------------------------------------------------------ docx
def test_docx_paragraphs_are_read(tmp_path):
    _docx(tmp_path / "a.docx", _para("Our method is the best ever."))
    assert "best ever" in scan.read(str(tmp_path / "a.docx"))


def test_runs_split_by_formatting_are_rejoined(tmp_path):
    """Word splits a number across runs when part of it is styled."""
    _docx(tmp_path / "a.docx", _para("Accuracy reached 9", "1.2", "%."))
    assert "91.2" in scan.read(str(tmp_path / "a.docx"))


def test_each_paragraph_is_its_own_line(tmp_path):
    _docx(tmp_path / "a.docx", _para("first"), _para("second"))
    assert scan.read(str(tmp_path / "a.docx")) == "first\nsecond"


def test_overclaims_in_a_docx_are_flagged_end_to_end(tmp_path):
    _docx(tmp_path / "a.docx", _para("Intro paragraph."),
          _para("Our approach is the first to solve this."))
    findings, _ = cli.run(str(tmp_path), only=["overclaim"])
    hit = [f for f in findings if f.file == "a.docx"]
    assert hit and hit[0].line == 2


def test_a_file_that_is_not_really_a_docx_is_reported(tmp_path):
    (tmp_path / "a.docx").write_bytes(b"PK not actually a zip")
    findings, _ = cli.run(str(tmp_path), only=["overclaim"])
    bad = [f for f in findings if f.check == "scan" and f.file == "a.docx"]
    assert bad and "not be read" in bad[0].message


def test_a_zip_without_a_word_document_is_reported(tmp_path):
    with zipfile.ZipFile(tmp_path / "a.docx", "w") as z:
        z.writestr("other.xml", "<x/>")
    findings, _ = cli.run(str(tmp_path), only=["overclaim"])
    assert [f for f in findings if f.check == "scan" and f.file == "a.docx"]


def test_a_line_break_separates_sentences(tmp_path):
    _docx(tmp_path / "a.docx",
          "<w:p><w:r><w:t>First line</w:t><w:br/><w:t>Second line</w:t></w:r></w:p>")
    assert scan.read(str(tmp_path / "a.docx")) == "First line\nSecond line"


def test_a_tab_separates_a_label_from_its_number(tmp_path):
    _docx(tmp_path / "a.docx",
          "<w:p><w:r><w:t>Model</w:t><w:tab/><w:t>0.75</w:t></w:r></w:p>")
    assert scan.read(str(tmp_path / "a.docx")) == "Model\t0.75"


def test_a_text_box_is_read_once_not_once_per_compatibility_branch(tmp_path):
    """Word writes the box under mc:Choice and again under mc:Fallback."""
    inner = "<w:txbxContent>" + _para("Our method is the best ever.") + "</w:txbxContent>"
    box = ("<w:p><w:r><mc:AlternateContent "
           "xmlns:mc=\"http://schemas.openxmlformats.org/markup-compatibility/2006\">"
           f"<mc:Choice Requires=\"wps\">{inner}</mc:Choice>"
           f"<mc:Fallback>{inner}</mc:Fallback>"
           "</mc:AlternateContent></w:r></w:p>")
    _docx(tmp_path / "a.docx", box)
    assert scan.read(str(tmp_path / "a.docx")) == "Our method is the best ever."
    findings, _ = cli.run(str(tmp_path), only=["overclaim"])
    assert len([f for f in findings if f.check == "overclaim"]) == 1


def test_a_docx_with_no_text_is_reported_rather_than_read_as_clean(tmp_path):
    _docx(tmp_path / "a.docx")
    findings, _ = cli.run(str(tmp_path), only=["overclaim"])
    note = [f for f in findings if f.check == "scan" and f.file == "a.docx"]
    assert note and note[0].status == "UNVERIFIABLE"
    assert "no paragraphs carrying text" in note[0].evidence


def test_the_nothing_found_message_lists_the_container_formats(tmp_path):
    """This is the message someone pointing at a notebook folder reads."""
    (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")
    findings, _ = cli.run(str(tmp_path))
    assert ".ipynb" in findings[0].message and ".docx" in findings[0].message


# ------------------------------------------------------------ invocation
def test_the_package_can_be_run_as_a_module(tmp_path):
    (tmp_path / "a.md").write_text("The first proven method.\n", encoding="utf-8")
    out = subprocess.run([sys.executable, "-m", "claimaudit", "check", str(tmp_path)],
                         capture_output=True, text=True,
                         env={**os.environ, "CLAIMAUDIT_HOME": str(tmp_path / "home")})
    assert out.returncode == 1 and "claimaudit report" in out.stdout
