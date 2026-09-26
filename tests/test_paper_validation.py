"""Regressions from the precision validation against fifteen real ML papers.

Every test here pins a false positive or a silent pass that the tool produced
on arXiv LaTeX source (Attention Is All You Need, BERT, GPT-3, ResNet, VGG,
Adam, ViT, RoBERTa, LLaMA, Chain-of-Thought, Lottery Ticket, Deep RL that
Matters, InstructGPT, LoRA, Distillation). The sentences quoted below are the
papers' own words, kept verbatim so that a later change that reintroduces the
flag fails here rather than in a re-measurement months from now.

Nothing in this file touches the network.
"""
from __future__ import annotations

import json

import pytest

from claimaudit import citations, cli, overclaim, scan
from claimaudit.claims import sentences
from claimaudit.report import FLAGGED, UNVERIFIABLE, VERIFIED


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAIMAUDIT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CLAIMAUDIT_CACHE", str(tmp_path / "cache"))
    monkeypatch.delenv("CLAIMAUDIT_LICENSE_KEY", raising=False)


def _write(d, files):
    for name, text in files.items():
        p = d / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return str(d)


def _flags(text, path="paper.tex"):
    """-> [(why, matched text)] from the overclaim check on one document."""
    return [(f.message.split(":")[0], f.message.split("'")[1])
            for f in overclaim.scan([(path, path, text)])]


def _whys(text, path="paper.tex"):
    return {why for why, _m in _flags(text, path)}


# ===================================================================
# A1/A2. arXiv refused the request, and the refusal was reported as
#        the paper not existing -- then cached for a week.
# ===================================================================

REAL_IDS = "BERT is arXiv:1810.04805 and RoBERTa is arXiv:1907.11692.\n"


@pytest.mark.parametrize("status", sorted(citations.BLOCKED))
def test_a_refusal_from_arxiv_is_never_reported_as_a_missing_paper(status):
    """1810.04805 and 1907.11692 both exist; a 406 does not make them vanish."""
    found = citations.check([("a.md", "a.md", REAL_IDS)], [],
                            fetch=lambda *a, **k: (status, ""))
    assert len(found) == 2
    for f in found:
        assert f.status == UNVERIFIABLE, f"status {status} produced {f.status}: {f.message}"
        assert "could not reach arXiv" in f.message
        assert "not found" not in f.message


def test_an_unanswerable_status_is_unverifiable_rather_than_a_guess():
    found = citations.check([("a.md", "a.md", REAL_IDS)], [],
                            fetch=lambda *a, **k: (418, ""))
    assert {f.status for f in found} == {UNVERIFIABLE}
    assert all("418" in f.message for f in found)


def test_a_paper_arxiv_really_does_not_have_is_still_flagged():
    """The fix must not turn the check off: a 200 that omits the id still counts."""
    found = citations.check([("a.md", "a.md", "See arXiv:2999.99999.\n")], [],
                            fetch=lambda *a, **k: (200, "<feed></feed>"))
    assert found[0].status == FLAGGED and "not found on arXiv" in found[0].message


def test_the_arxiv_query_asks_for_one_id_without_max_results():
    """The batched query shape arXiv answers with 406, and so learns nothing.

    Pinned because the failure is invisible from inside: a 406 now reports
    honestly, so the only thing stopping a silent regression to 'everything
    unverifiable' is this test.
    """
    asked = []

    def fetch(url, method="GET", timeout=15):
        asked.append(url)
        return 200, "<feed></feed>"

    citations.check([("a.md", "a.md", REAL_IDS)], [], fetch=fetch)
    assert len(asked) == 2
    for url in asked:
        assert "max_results" not in url
        assert url.count(",") == 0


def test_a_refusal_is_not_written_to_the_cache(tmp_path, monkeypatch):
    """A throttled minute must not become a week of confident wrong answers."""
    monkeypatch.setenv("CLAIMAUDIT_CACHE", str(tmp_path / "c"))
    monkeypatch.setattr(citations, "ARXIV_MIN_INTERVAL", 0)
    calls = []

    class Refused(Exception):
        pass

    def fake_urlopen(req, timeout=15, context=None):
        calls.append(req.full_url)
        raise citations.urllib.error.HTTPError(req.full_url, 406, "Not Acceptable", {}, None)

    monkeypatch.setattr(citations.urllib.request, "urlopen", fake_urlopen)
    url = citations.ARXIV_API + "1810.04805"
    assert citations.default_fetch(url) == (406, "")
    assert citations.default_fetch(url) == (406, "")
    assert len(calls) == 2, "the 406 was served from cache instead of being retried"


def _client_hello(ctx):
    """The bytes this context puts on the wire first, without a network."""
    import ssl
    inc, out = ssl.MemoryBIO(), ssl.MemoryBIO()
    tls = ctx.wrap_bio(inc, out, server_hostname="export.arxiv.org")
    with pytest.raises(ssl.SSLWantReadError):
        tls.do_handshake()
    return out.read()


def test_the_arxiv_request_offers_the_alpn_arxiv_serves(tmp_path, monkeypatch):
    """55 of 60 arXiv lookups came back 406 while curl got 200.

    Measured from this machine, interleaved, same ids and pacing: urllib as it
    was, 0 of 10; urllib with ALPN http/1.1, 20 of 20; curl sending urllib's
    exact headers over HTTP/1.1, 10 of 10; urllib with curl's headers, 0 of 10.
    The difference is the TLS ClientHello, so that is what is pinned here, on
    the context default_fetch really hands to urlopen.
    """
    import ssl
    monkeypatch.setenv("CLAIMAUDIT_CACHE", str(tmp_path / "c"))
    monkeypatch.setattr(citations, "ARXIV_MIN_INTERVAL", 0)
    sent = []

    def fake_urlopen(req, timeout=15, context=None):
        sent.append((req, context))
        raise OSError("no network in tests")

    monkeypatch.setattr(citations.urllib.request, "urlopen", fake_urlopen)
    citations.default_fetch(citations.ARXIV_API + "1810.04805")
    (req, ctx), = sent
    assert req.get_header("User-agent") == citations.UA
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    alpn = b"\x00\x10" + b"\x00\x0b" + b"\x00\x09" + b"\x08http/1.1"
    assert alpn in _client_hello(ctx)
    # What was refused: Python's default context offers no ALPN at all.
    assert b"http/1.1" not in _client_hello(ssl.create_default_context())


def test_an_answer_is_still_cached(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAIMAUDIT_CACHE", str(tmp_path / "c"))
    monkeypatch.setattr(citations, "ARXIV_MIN_INTERVAL", 0)
    calls = []

    class Resp:
        status = 200

        def read(self, n=None):
            return b"<feed></feed>"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=15, context=None):
        calls.append(req.full_url)
        return Resp()

    monkeypatch.setattr(citations.urllib.request, "urlopen", fake_urlopen)
    url = citations.ARXIV_API + "1810.04805"
    assert citations.default_fetch(url)[0] == 200
    assert citations.default_fetch(url)[0] == 200
    assert len(calls) == 1


def test_the_previous_generation_of_cached_verdicts_is_discarded(tmp_path, monkeypatch):
    """Entries written before the fix carry the wrong answers; ignore them."""
    import json
    import os
    import re

    cache = tmp_path / "c"
    cache.mkdir()
    monkeypatch.setenv("CLAIMAUDIT_CACHE", str(cache))
    monkeypatch.setattr(citations, "ARXIV_MIN_INTERVAL", 0)
    url = citations.ARXIV_API + "1810.04805"
    key = os.path.join(str(cache), re.sub(r"[^A-Za-z0-9]", "_", "GET" + url)[:180] + ".json")
    with open(key, "w") as fh:                      # a version-1 entry, no "v"
        json.dump({"s": 200, "b": "<feed>poisoned</feed>"}, fh)

    def fake_urlopen(req, timeout=15, context=None):
        raise OSError("no network")

    monkeypatch.setattr(citations.urllib.request, "urlopen", fake_urlopen)
    assert citations.default_fetch(url) == (0, ""), "a stale entry was served"


# ===================================================================
# A3. A PDF-only .tex wrapper reported as a clean document.
# ===================================================================

# arxiv.tex from the arXiv source of 1412.6980, byte for byte (it has no
# trailing newline). The hyperref block is what hid it: stripped, it reads as
# three "sentences", so a fixture without it tested a file that does not exist.
ADAM_WRAPPER = (
    "\\documentclass[a4paper]{article}\n"
    "\\pdfoutput=1\n"
    "\\usepackage{hyperref}\n"
    "\\hypersetup{\n"
    "  pdfinfo={\n"
    "    Title={Adam: A Method for Stochastic Optimization},\n"
    "    Author={Diederik P. Kingma, Jimmy Lei Ba}\n"
    "  }\n"
    "}\n"
    "\n"
    "\\usepackage{pdfpages}\n"
    "\\begin{document}\n"
    "\\includepdf[pages=1-last]{0_adam_main.pdf}\n"
    "\\end{document}"
)

INPUT_ONLY = r"""\documentclass{article}
\begin{document}
\input{paper_body}
\end{document}
"""


def test_the_adam_fixture_is_the_real_file():
    """Guard against the fixture drifting back to a convenient substitute."""
    import os
    for root in ("/tmp/papers", "/tmp/papers2"):
        p = os.path.join(root, "src", "1412.6980", "arxiv.tex")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                assert fh.read() == ADAM_WRAPPER
            return
    pytest.skip("the downloaded arXiv source of 1412.6980 is not on this machine")


def test_the_adam_preamble_does_read_as_sentences():
    """The trap itself: if this stops being true the fixture no longer tests it."""
    assert list(sentences(ADAM_WRAPPER, "arxiv.tex"))


def test_a_pdf_wrapper_is_unread_not_clean(tmp_path):
    """Adam (1412.6980) is a preamble and \\includepdf. Zero findings is a lie."""
    d = _write(tmp_path, {"arxiv.tex": ADAM_WRAPPER})
    found, _ = cli.run(d, paid=True, offline=True)
    scan_hits = [f for f in found if f.check == "scan"]
    assert scan_hits, "the wrapper passed as a clean document"
    assert all(f.status == UNVERIFIABLE for f in scan_hits)
    assert any("no prose could be read" in f.message for f in scan_hits)
    assert any("0_adam_main.pdf" in f.evidence and "includepdf" in f.evidence
               and "PDFs are not read" in f.evidence for f in scan_hits)
    assert cli.unread(found)
    assert not any(f.status == FLAGGED for f in found)


def test_a_pdf_wrapper_fails_the_cli_gate(tmp_path, capsys):
    """The re-measure saw exit 0 on Adam. An unread paper must not pass a gate."""
    d = _write(tmp_path, {"arxiv.tex": ADAM_WRAPPER})
    assert cli.main(["check", d, "--offline"]) == 1
    assert "0_adam_main.pdf" in capsys.readouterr().out


def test_a_body_that_is_only_an_included_pdf_figure_is_unread(tmp_path):
    tex = ADAM_WRAPPER.replace("\\includepdf[pages=1-last]{0_adam_main.pdf}",
                               "\\includegraphics[width=\\textwidth]{paper.pdf}")
    d = _write(tmp_path, {"arxiv.tex": tex})
    found, _ = cli.run(d, only=["overclaim"])
    assert any(f.extra.get("unread") and "paper.pdf" in f.evidence
               and "includegraphics" in f.evidence for f in found)


def test_prose_plus_an_included_pdf_is_still_audited(tmp_path):
    tex = ADAM_WRAPPER.replace(
        "\\includepdf[pages=1-last]{0_adam_main.pdf}",
        "Nobody would publish this.\n\\includepdf[pages=1-last]{0_adam_main.pdf}")
    d = _write(tmp_path, {"arxiv.tex": tex})
    found, _ = cli.run(d, only=["overclaim"])
    assert not cli.unread(found)
    assert any(f.check == "overclaim" and "nobody" in f.message.lower() for f in found)


def test_a_commented_out_includepdf_does_not_make_a_wrapper(tmp_path):
    tex = ("\\documentclass{article}\n\\begin{document}\n"
           "% \\includepdf{old.pdf}\nNobody would publish this.\n\\end{document}\n")
    d = _write(tmp_path, {"main.tex": tex})
    found, _ = cli.run(d, only=["overclaim"])
    assert not cli.unread(found)


def test_an_input_only_tex_file_is_unread(tmp_path):
    d = _write(tmp_path, {"main.tex": INPUT_ONLY})
    found, _ = cli.run(d, only=["overclaim"])
    assert any(f.check == "scan" and f.status == UNVERIFIABLE
               and "no prose could be read" in f.message for f in found)


def test_a_tex_file_with_real_sentences_is_not_called_unread(tmp_path):
    d = _write(tmp_path, {"main.tex": "\\section{Results}\nNobody would publish this.\n"})
    found, _ = cli.run(d, only=["overclaim"])
    assert not any(f.check == "scan" and f.extra.get("unread") for f in found)
    assert any(f.check == "overclaim" and "nobody" in f.message.lower() for f in found)


def test_an_empty_markdown_file_is_still_harmless(tmp_path):
    """A3 is about unread .tex, not a new rule that empty files are findings."""
    d = _write(tmp_path, {"empty.md": "", "blank.md": "   \n\n"})
    found, _ = cli.run(d, paid=True, offline=True)
    assert found == []


# ===================================================================
# A4. Most arXiv sources ship a .bbl, which was never opened.
# ===================================================================

# A typical natbib/plain .bbl: identifiers are reliable; the \newblock
# lines are not (title and venue look the same).
BBL = r"""\begin{thebibliography}{99}
\bibitem[Devlin et al.(2019)]{bert}
Jacob Devlin, Ming-Wei Chang, Kenton Lee, and Kristina Toutanova.
\newblock BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding.
\newblock {\em arXiv:1810.04805}, 2019.

\bibitem[Liu et al.(2019)]{roberta}
Yinhan Liu et al.
\newblock RoBERTa: A Robustly Optimized BERT Pretraining Approach.
\newblock DOI: 10.48550/arXiv.1907.11692, 2019.

\bibitem[Nobody(2020)]{opaque}
A. Nobody.
\newblock Something we cannot identify from this entry alone.
\newblock In {\em A Venue}, 2020.
\end{thebibliography}
"""


def test_a_bbl_is_discovered_alongside_bib(tmp_path):
    d = _write(tmp_path, {"main.tex": "See the references.\n", "refs.bbl": BBL})
    _base, _text, _data, bib, _p = scan.discover(d)
    assert any(rel.endswith(".bbl") for rel, _p in bib)


def test_bbl_identifiers_are_checked_and_nothing_else_is_invented():
    asked = []

    def fetch(url, method="GET", timeout=15):
        asked.append(url)
        if "1810.04805" in url:
            return 200, ("<feed><entry><id>http://arxiv.org/abs/1810.04805</id>"
                         "<title>BERT</title><author><name>Jacob Devlin</name></author></entry></feed>")
        if "10.48550" in url:
            return 200, json.dumps({"message": {"title": ["RoBERTa"], "author": [{"family": "Liu"}]}})
        return 0, ""

    found = citations.check([], [("refs.bbl", "refs.bbl", BBL)], fetch=fetch)
    assert any("1810.04805" in f.message and f.status == VERIFIED for f in found)
    assert any("10.48550" in f.message and f.status == VERIFIED for f in found)
    # The opaque entry has no identifier. Guessing a flag for it would be
    # a new false positive, which is the failure this whole task exists to stop.
    assert not any("opaque" in (f.message + f.evidence).lower() or "Nobody" in f.message
                   for f in found)
    assert not any("authors differ" in (f.evidence or "") for f in found)
    assert not any("title differ" in (f.evidence or "") for f in found)


def test_an_ambiguous_bbl_entry_is_skipped_not_flagged():
    text = r"""\bibitem{x}
A. Someone.
\newblock A title or maybe a venue.
\newblock 2018.
"""
    assert citations.parse_bbl(text) == []
    assert citations.check([], [("r.bbl", "r.bbl", text)],
                           fetch=lambda *a, **k: (200, "")) == []


# Verbatim from the .bbl files shipped in the arXiv sources of VGG
# (1409.1556, ilsvrc14.bbl) and GPT-3 (2005.14165, main.bbl). DBLP writes
# arXiv as a venue with a bare id; the re-measure found 23 of these skipped
# without a word, 16 in VGG and 7 in GPT-3.
VGG_CORR_BBL = r"""
\bibitem[Girshick et~al.(2014)Girshick, Donahue, Darrell, and
  Malik]{Girshick14a}
Girshick, R.~B., Donahue, J., Darrell, T., and Malik, J.
\newblock Rich feature hierarchies for accurate object detection and semantic
  segmentation.
\newblock \emph{CoRR}, abs/1311.2524v5, 2014.
\newblock Published in Proc. CVPR, 2014.


\bibitem[He et~al.(2014)He, Zhang, Ren, and Sun]{He14}
He, K., Zhang, X., Ren, S., and Sun, J.
\newblock Spatial pyramid pooling in deep convolutional networks for visual
  recognition.
\newblock \emph{CoRR}, abs/1406.4729v2, 2014.
"""

GPT3_CORR_BBL = r"""
\bibitem[CCE{\etalchar{+}}18]{Clark2018ThinkYH}
Peter Clark, Isaac Cowhey, Oren Etzioni, Tushar Khot, Ashish Sabharwal, Carissa
  Schoenick, and Oyvind Tafjord.
\newblock Think you have solved question answering? try arc, the ai2 reasoning
  challenge.
\newblock {\em ArXiv}, abs/1803.05457, 2018.

\bibitem[SDSE19]{schwartz2019}
Roy Schwartz, Jesse Dodge, Noah~A. Smith, and Oren Etzioni.
\newblock Green {AI}.
\newblock {\em CoRR}, abs/1907.10597, 2019.

\bibitem[TL05]{DBLP:journals/corr/abs-cs-0508103}
Peter~D. Turney and Michael~L. Littman.
\newblock Corpus-based learning of analogies and semantic relations.
\newblock {\em CoRR}, abs/cs/0508103, 2005.
"""


def _eprints(bbl):
    return {e["key"]: e["fields"].get("eprint") for e in citations.parse_bbl(bbl)}


def test_a_corr_abs_id_in_a_bbl_is_read_as_an_arxiv_id():
    assert _eprints(VGG_CORR_BBL) == {"Girshick14a": "1311.2524", "He14": "1406.4729"}


def test_an_arxiv_venue_abs_id_is_read_too():
    got = _eprints(GPT3_CORR_BBL)
    assert got == {"Clark2018ThinkYH": "1803.05457", "schwartz2019": "1907.10597"}


def test_an_old_style_abs_id_is_reported_not_skipped_or_guessed():
    assert citations.bbl_unparsed(GPT3_CORR_BBL) == [
        (14, "DBLP:journals/corr/abs-cs-0508103", "abs/cs/0508103")]
    found = citations.check([], [("main.bbl", "main.bbl", GPT3_CORR_BBL)], offline=True)
    note = [f for f in found if "not parsed" in f.message]
    assert len(note) == 1 and note[0].status == UNVERIFIABLE
    assert note[0].message.startswith("1 bibliography identifier was present but not parsed")
    assert "abs/cs/0508103" in note[0].evidence
    assert not any(f.status == FLAGGED for f in found)


def test_corr_ids_are_looked_up_and_resolve():
    def fetch(url, method="GET", timeout=15):
        aid = url.rsplit("=", 1)[1]
        return 200, (f"<feed><entry><id>http://arxiv.org/abs/{aid}v1</id>"
                     f"<title>T</title></entry></feed>")

    found = citations.check([], [("ilsvrc14.bbl", "ilsvrc14.bbl", VGG_CORR_BBL)], fetch=fetch)
    assert {f.message.split(":")[1] for f in found} == {"1311.2524", "1406.4729"}
    assert all(f.status == VERIFIED for f in found)


def test_abs_in_a_non_arxiv_url_is_neither_an_arxiv_id_nor_unparsed():
    bbl = (r"\bibitem{acm}" "\nA. Author.\n\\newblock A paper.\n"
           r"\newblock \url{https://dl.acm.org/doi/abs/10.1145/3491102.3517582}." "\n")
    assert citations.bbl_unparsed(bbl) == []
    assert "eprint" not in citations.parse_bbl(bbl)[0]["fields"]


def test_bbl_does_not_claim_authors_and_title_matched():
    """A .bbl has no parsed author/title, so the report must not say they matched."""
    atom = ("<feed><entry><id>http://arxiv.org/abs/1810.04805</id>"
            "<title>BERT</title><author><name>Jacob Devlin</name></author></entry></feed>")
    bbl = "\\bibitem{bert}\narXiv:1810.04805\n"
    found = citations.check([], [("r.bbl", "r.bbl", bbl)],
                            fetch=lambda *a, **k: (200, atom))
    assert found and found[0].status != FLAGGED
    assert "matches bib authors/title" not in found[0].message
    assert "resolves" in found[0].message


# ===================================================================
# A5. Unknown macros vanished and fused the surrounding words.
# ===================================================================

def _tex_sent(text):
    return " ".join(s for _l, s in sentences(text, "p.tex"))


def test_an_unknown_macro_leaves_a_readable_name():
    """ViT's \\gls{vit}/\\vit{} and LLaMA's \\llamathirteen were deleted,
    so the evidence read 's dominate ResNets' and '-13B outperforms'."""
    import re
    vit = _tex_sent(r"First, \vit{}s dominate ResNets on the same data.")
    assert re.search(r"\bvit", vit, re.I)
    assert not re.search(r"\bs dominate\b", vit)
    llama = _tex_sent(r"\llamathirteen-13B outperforms GPT-3 (175B).")
    assert "llamathirteen" in llama.lower()
    assert not llama.strip().startswith("-13B")


def test_a_macro_with_real_contents_still_unwraps():
    r"""\emph{817} must stay a number; the placeholder is for empty/unknown names."""
    joined = _tex_sent(r"We get \(90.1\%\) coverage across \emph{817} rows.")
    assert "90.1%" in joined and "817" in joined
    assert "emph" not in joined.lower()


# ===================================================================
# B1. "first" as a discourse marker, ordinal, or quantity.
# ===================================================================

def test_sentence_initial_first_comma_is_not_a_priority_claim():
    assert "priority claim" not in _whys(
        "First, chain of thought, in principle, allows models to decompose a problem.\n")


def test_we_first_need_is_not_a_priority_claim():
    assert "priority claim" not in _whys(
        "We first need to clarify what alignment means.\n")


def test_the_first_is_enumeration_not_priority():
    assert "priority claim" not in _whys(
        "The first is a multi-head self-attention mechanism, and the second is a feed-forward network.\n")


def test_first_hidden_layer_is_not_a_priority_claim():
    assert "priority claim" not in _whys(
        "the distribution of weights for the first hidden layer, second hidden layer, and output layer.\n")


def test_first_n_steps_is_a_quantity_not_priority():
    assert "priority claim" not in _whys(
        "The learning rate is warmed up over the first 10,000 steps.\n")


def test_a_real_priority_construction_still_flags():
    assert "priority claim" in _whys("This is the first method that scales to this regime.\n")
    assert "priority claim" in _whys("We are the first to show this on public data.\n")
    assert "priority claim" in _whys("Our method is the first to solve this.\n")


# ===================================================================
# B2. Table-backed comparatives are not superiority overclaims.
# ===================================================================

def test_sota_on_named_tasks_is_not_a_superiority_overclaim():
    """BERT: accurate, table-backed, and the previous rule's most common hit."""
    assert "superiority claim" not in _whys(
        "It obtains new state-of-the-art results on eleven NLP tasks.\n")


def test_improving_over_best_results_by_a_number_is_not_flagged():
    assert "superiority claim" not in _whys(
        "The Transformer improves over the existing best results on WMT 2014 by over 2 BLEU.\n")


def test_outperforms_baselines_is_not_a_superiority_overclaim():
    assert "superiority claim" not in _whys(
        "LoRA outperforms several baselines with comparable or fewer trainable parameters.\n")


def test_a_universal_comparative_still_flags():
    assert "superiority claim" in _whys("It never fails and beats everything.\n")
    assert "superiority claim" in _whys("This is the best model ever released.\n")


# ===================================================================
# B3. Word-sense misfires on "only" and "best".
# ===================================================================

def test_only_one_per_block_is_a_quantity_not_exclusivity():
    assert "exclusivity claim" not in _whys(
        "The block has only one per block but with an additional LayerNorm.\n")


def test_at_best_is_an_upper_bound_not_superiority():
    assert "superiority claim" not in _whys(
        "The runs reach minimum validation loss at best 3.5x faster.\n")


def test_best_judgment_is_not_superiority():
    assert "superiority claim" not in _whys(
        "When the rating is unclear you should use your best judgment.\n")


def test_only_one_that_still_flags_exclusivity():
    assert "exclusivity claim" in _whys(
        "This is the only one that works on the public split.\n")


# ===================================================================
# G. Stress-test regressions from real unseen ML/math papers (audit
#    2, 2026-09-26): patterns the priority and negation checks got
#    wrong outside the original 15-paper set.
# ===================================================================

def test_on_the_first_attempt_is_an_ordinal_not_a_priority_claim():
    """arXiv:2408.03314 (Snell et al.) uses this phrasing to number model
    attempts. Six such flags landed on one paper before this fix."""
    assert "priority claim" not in _whys(
        "On the first attempt the model takes the incorrect approach.\n")
    assert "priority claim" not in _whys(
        "On the first two attempts the model makes an error.\n")
    assert "priority claim" not in _whys(
        "The first attempt failed and the second succeeded.\n")


def test_the_first_attempt_to_do_something_still_flags_as_a_priority_claim():
    """The intended catch survives: an infinitive after 'attempt' keeps the
    priority reading. Without this the fix would loosen the check."""
    assert "priority claim" in _whys(
        "This is the first attempt to prove the conjecture rigorously.\n")


def test_not_the_only_way_is_a_negated_exclusivity_claim():
    """arXiv:2408.03314 line 70 fired here because the negation whitelist
    stopped at linking verbs and did not include articles between 'not'
    and the pattern."""
    flags = [(why, matched) for why, matched in _flags(
        "However, this approach is not the only way to use test-time compute.\n")]
    exc = [(w, m) for w, m in flags if w.startswith("exclusivity")]
    # It still fires (a flag is a prompt to review), but must be marked negated
    # so a reader can filter it out at a glance.
    assert exc, "the pattern still fires so a human can see it"
    from claimaudit import overclaim
    findings = overclaim.scan([("a.md", "a.md",
                                "However, this approach is not the only way to use test-time compute.\n")])
    assert any("(negated)" in f.message for f in findings), \
        "the finding should be marked (negated); the whitelist should allow 'not the'"


def test_not_a_proof_is_also_caught_as_negated():
    """'not a X' was another gap in the negation whitelist that let the
    proof-language pattern read a disclaimer as a claim."""
    from claimaudit import overclaim
    findings = overclaim.scan([("a.md", "a.md",
                                "The example is not a proof of the general case.\n")])
    proofs = [f for f in findings if "proof language" in f.message]
    assert proofs, "the pattern still fires"
    assert any("(negated)" in f.message for f in proofs)


# ===================================================================
# H. Scan-level: an oversize file must not be silently dropped.
# ===================================================================

def test_a_file_over_the_size_limit_surfaces_as_unread(tmp_path, monkeypatch):
    """Before this fix, scan.discover dropped anything above MAX_BYTES with
    no problem note, so a very large document produced a clean audit that
    never happened."""
    from claimaudit import scan
    monkeypatch.setattr(scan, "MAX_BYTES", 512)
    (tmp_path / "small.md").write_text("The 42 rows here.\n")
    big = tmp_path / "big.md"
    big.write_text("x " * 500)
    assert big.stat().st_size > 512
    _base, text, _data, _bib, problems = scan.discover(str(tmp_path))
    assert [r for r, _ in text] == ["small.md"]
    assert any(rel == "big.md" and "not audited" in why for rel, why in problems)


def test_a_file_over_the_size_limit_leaves_a_scan_finding(tmp_path, monkeypatch):
    """The run pipeline should turn the discovery problem into a visible
    scan finding so the whole audit does not look clean when it is not."""
    from claimaudit import scan
    monkeypatch.setattr(scan, "MAX_BYTES", 512)
    (tmp_path / "big.md").write_text("x " * 500)
    findings, _skipped = cli.run(str(tmp_path), paid=True, offline=True)
    unread = [f for f in findings if f.check == "scan" and f.extra.get("unread")]
    assert any("big.md" in f.file and "not audited" in f.message for f in unread)
