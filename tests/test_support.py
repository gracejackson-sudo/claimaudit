"""Citation-support check (Layer A): does the source contain what the sentence says?

Three groups:
  * text extraction and its self-check (fulltext.py)      -- synthetic, offline
  * the checks and their false-positive traps (support.py) -- synthetic, offline
  * a validation corpus of real sources                    -- recorded, offline
    (fixtures/citation_support; rebuilt with validation/build_support_fixtures.py)

Nothing here touches the network.
"""
from __future__ import annotations
import glob, gzip, json, os, re
import pytest

from claimaudit import fulltext as ft, support
from claimaudit.report import FLAGGED, UNVERIFIABLE

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, os.pardir, "fixtures", "citation_support")
CASES = os.path.join(FIX, "cases")


# ----------------------------------------------------------------- helpers
def src(ident, text, authors=("Someone",), year=2020, title="A Study", abstract="", refs=""):
    """A trusted full-text source built from a few sentences (self-check bypassed)."""
    return ft.Source(ident, title, list(authors), year, abstract, tier="html",
                     index=ft.TextIndex.from_text(text, refs), qual={"trusted": True})


def pair_for(tex, bib="", ident_by_key=None):
    """Run the whole pipeline on one TeX snippet with a fake provider."""
    ident_by_key = ident_by_key or {}
    entries = "".join(
        f"@article{{{k},\n  title={{T}},\n  author={{{a}}},\n  eprint={{{i.split(':')[1]}}}\n}}\n"
        for k, (i, a) in ident_by_key.items())
    return [("paper.tex", "paper.tex", tex)], [("refs.bib", "refs.bib", entries + bib)]


def run(tex, sources, keys, authors_in_bib=None, extra_bib=""):
    """sources: {ident: Source}; keys: {bibkey: ident}. The bibliography agrees with the
    record unless a test says otherwise, so a test only trips the check it is about."""
    def bib_authors(ident):
        if authors_in_bib is not None:
            return authors_in_bib
        s = sources.get(ident)
        return " and ".join(f"{a.capitalize()}, X" for a in s.authors) if s and s.authors else "Doe, J"
    tf, bf = pair_for(tex, extra_bib, {k: (i, bib_authors(i)) for k, i in keys.items()})
    return support.check(tf, bf, provider=lambda i: sources.get(i) or ft.Source(i, note="not recorded"))


def one(findings):
    assert len(findings) == 1, [f.message for f in findings]
    return findings[0]


# ============================================================ fulltext.py
class TestNormalize:
    def test_ligatures_and_dashes(self):
        assert ft.normalize("\ufb01nd rank\u20112 low\u2013rank") == "find rank-2 low-rank"

    def test_accents_are_folded_and_spacing_accents_vanish(self):
        assert ft.normalize("Peköz") == "pekoz"
        assert ft.normalize("Pek\u00a8 oz") == "pekoz"          # what a PDF extractor writes
        assert ft.normalize("na\u00a8 \u0131ve") == "naive"      # spacing accent + dotless i

    def test_math_letter_does_not_glue_to_the_word_before(self):
        # NFKD would turn the script l into "l" and produce the token "everyl"
        idx = ft.TextIndex.from_text("because for every \u2113 in the set")
        assert idx.count("every") == 1

    def test_thousands_separator_from_tex(self):
        assert "500000" in ft.numbers(ft.normalize("over 500{,}000 evaluations"))


class TestIndex:
    def test_word_split_by_line_break_is_found(self):
        idx = ft.TextIndex.from_text("the Mon-\ndrian construction and Mon- drian again " + "word " * 5)
        assert idx.maybe_present("Mondrian")

    def test_word_split_at_a_column_break_is_found_by_its_first_half(self):
        idx = ft.TextIndex.from_text("the major quantization bottle-\nTable 11: latency " + "word " * 5)
        assert idx.maybe_present("bottleneck")

    def test_ligature_lost_by_the_pdf_font(self):
        idx = ft.TextIndex.from_text("the coecient was small " + "word " * 5)      # 'ffi' dropped
        assert idx.maybe_present("coefficient")

    def test_letters_glued_to_following_text(self):
        idx = ft.TextIndex.from_text("half of the total0%-50%inflation and becauseP(Zn) holds")
        assert idx.maybe_present("total") and idx.maybe_present("because")

    def test_absent_really_is_absent(self):
        idx = ft.TextIndex.from_text("conformal prediction with exchangeable data " * 20)
        assert not idx.maybe_present("Mondrian")
        assert idx.count("conformal") == 20

    def test_json_round_trip_keeps_the_answers(self):
        idx = ft.TextIndex.from_text("conformal rank-2 total 4.6 points " * 3)
        back = ft.TextIndex.from_json(json.loads(json.dumps(idx.to_json())))
        assert back.count("conformal") == 3 and "4.6" in back.nums and back.maybe_present("rank-2")

    def test_fixture_form_holds_no_running_text(self):
        d = ft.TextIndex.from_text("secret sentence order matters").to_json()
        assert "secret sentence" not in json.dumps(d)


class TestHtml:
    PAGE = ('<html><head><title>x</title></head><body><nav>Report issue: arXiv is now an independent nonprofit</nav>'
            '<div class="ltx_page_content"><article><p>We study conformal sets '
            '<math alttext="\\alpha=0.1"><mi>a</mi></math> here.</p>'
            '<section class="ltx_bibliography"><p>Vovk. Algorithmic learning. 2005.</p></section></article></div></body></html>')

    def test_site_chrome_is_not_part_of_the_paper(self):
        body, refs = ft.html_to_text(self.PAGE)
        assert "nonprofit" not in body and "conformal" in body

    def test_bibliography_is_kept_apart(self):
        body, refs = ft.html_to_text(self.PAGE)
        assert "Vovk" in refs and "Vovk" not in body

    def test_math_can_be_dropped(self):
        assert "alpha" in ft.html_to_text(self.PAGE)[0]
        assert "alpha" not in ft.html_to_text(self.PAGE, keep_math=False)[0]

    def test_page_without_the_arxiv_wrapper_is_still_read(self):
        body, _ = ft.html_to_text("<html><body><p>plain page about Mondrian</p></body></html>")
        assert "Mondrian" in body


GOOD = ("Conformal prediction gives prediction sets with finite sample coverage under exchangeability. "
        "We study calibration and selection in regression problems with heteroskedastic errors. ") * 120
REFS_TAIL = "\nReferences\n" + "\n".join(f"A. Author. Some paper {y}." for y in range(2001, 2015)) + "\n"


class TestSelfCheck:
    TITLE = "Conformal prediction with selection"
    ABST = ("We study calibration and selection in regression problems with heteroskedastic errors "
            "and prove finite sample coverage under exchangeability for prediction sets.")

    def q(self, text, **kw):
        return ft.quality(ft.TextIndex.from_text(text), text, self.TITLE, self.ABST, **kw)

    def test_a_good_extraction_is_trusted(self):
        assert self.q(GOOD + REFS_TAIL, tail_ok=ft.has_reference_tail(GOOD + REFS_TAIL))["trusted"]

    def test_letter_spaced_text_is_refused(self):
        spaced = re.sub(r"(?<=\w)(?=\w)", " ", GOOD + REFS_TAIL)
        assert not self.q(spaced, tail_ok=True)["trusted"]

    def test_garbled_text_is_refused(self):
        garbled = "".join("\ufffd" if i % 20 == 0 else c for i, c in enumerate(GOOD))
        assert not self.q(garbled, tail_ok=True)["trusted"]

    def test_text_cut_off_before_the_references_is_refused(self):
        cut = (GOOD + REFS_TAIL)[: len(GOOD) // 2]
        r = self.q(cut, tail_ok=ft.has_reference_tail(cut))
        assert not r["trusted"] and any("cut short" in x for x in r["reasons"])

    def test_missing_pages_are_refused(self):
        assert not self.q(GOOD + REFS_TAIL, pages=10, failed_pages=2, tail_ok=True)["trusted"]

    def test_too_little_text_is_refused(self):
        assert not self.q("Conformal prediction with selection. " * 5, tail_ok=True)["trusted"]

    def test_no_metadata_means_nothing_to_check_against(self):
        r = ft.quality(ft.TextIndex.from_text(GOOD), GOOD, "", "", tail_ok=True)
        assert not r["trusted"]

    def test_abstract_only_source_can_never_support_an_absence(self):
        s = ft.build_source("arxiv:1", "Conformal prediction with selection", ["Doe"], 2020, self.ABST)
        assert s.tier == "abstract" and not s.trusted

    def test_html_that_fails_its_check_falls_back_and_says_why(self):
        s = ft.build_source("arxiv:1", "Some Title Words Here", ["Doe"], 2020, "abstract words " * 20,
                            html="<html><body><p>tiny</p></body></html>")
        assert not s.trusted and "failed its self-check" in s.note

    def test_reference_tail_detection(self):
        assert ft.has_reference_tail("body\n\nReferences\n" + "X 2001. " * 10)
        assert not ft.has_reference_tail("body only, no reference list " * 50)
        assert not ft.has_reference_tail("body\nReferences\nnothing dated here")


# ============================================================ support.py
class TestFindPairs:
    def test_tex_sentence_with_cite_is_found(self):
        tf, bf = pair_for("Intro text here. Tong et al.~\\cite{t} show things. Other.", "",
                          {"t": ("arxiv:2606.01850", "Tong, Yujia")})
        pairs = support.find_pairs(tf, bf)
        assert len(pairs) == 1 and pairs[0].idents == ["arxiv:2606.01850"]

    def test_et_al_is_not_split_into_two_sentences(self):
        tf, bf = pair_for("Vovk et al.~\\cite{v} introduced it.", "", {"v": ("arxiv:1209.2673", "Vovk, V")})
        assert len(support.find_pairs(tf, bf)) == 1

    def test_named_authors_only_when_et_al_or_a_list(self):
        p = support._named_authors
        assert p("Vovk et al. \u27e6C:cite:v\u27e7") == ["Vovk"]
        assert p("Jin and Candes \u27e6C:cite:j\u27e7") == ["Jin", "Candes"]
        assert p("BenchPress \u27e6C:cite:z\u27e7") == []          # a system name, not a person

    def test_textual_citation_takes_names_from_the_bibliography(self):
        tf, bf = pair_for("\\citet{t} show things.", "", {"t": ("arxiv:2606.01850", "Tong, Yujia")})
        p = support.find_pairs(tf, bf)[0]
        assert p.textual and p.named == [] and p.attributive

    def test_subject_then_citation_then_verb_is_a_claim_about_the_source(self):
        tf, bf = pair_for("BenchPress~\\cite{z} compiles a matrix of 84 models.", "", {"z": ("arxiv:2606.24020", "Zeng, Y")})
        assert support.find_pairs(tf, bf)[0].attributive
        tf, bf = pair_for("Following split conformal regression~\\cite{z}, we calibrate.", "", {"z": ("arxiv:2606.24020", "Zeng, Y")})
        assert not support.find_pairs(tf, bf)[0].attributive

    def test_markdown_arxiv_link_is_a_citation(self):
        pairs = support.find_pairs([("a.md", "a.md", "BenchPress ([arXiv:2606.24020](https://arxiv.org/abs/2606.24020)) reports it.")], [])
        assert pairs and pairs[0].idents == ["arxiv:2606.24020"]

    def test_key_without_identifier_is_kept_as_none(self):
        tf = [("p.tex", "p.tex", "See~\\cite{blog} for it.")]
        bf = [("r.bib", "r.bib", "@misc{blog, title={x}, note={\\url{https://example.org/a}}}")]
        assert support.find_pairs(tf, bf)[0].idents == [None]


class TestTerms:
    def terms(self, s, named=()):
        return [w for w, _ in support.candidate_terms(s, list(named), [])]

    def test_proper_nouns_acronyms_and_compounds(self):
        t = self.terms("the Mondrian construction of GPTQ and the rank-2 structure")
        assert {"Mondrian", "GPTQ", "rank-2"} <= set(t)

    def test_ordinary_lowercase_words_are_never_candidates(self):
        assert self.terms("the guarantee does not transfer under selection") == []

    def test_sentence_initial_capital_is_not_a_proper_noun(self):
        assert "Calibrating" not in self.terms("Calibrating separately within categories is standard")

    def test_author_names_are_not_terms(self):
        assert "Vovk" not in self.terms("Vovk et al. describe it", named=["Vovk"])


class TestChecks:
    ID = "arxiv:2606.24020"

    def mk(self, text, **kw):
        return {self.ID: src(self.ID, text, **kw)}

    # -- absence of a distinctive word
    def test_missing_proper_noun_is_flagged(self):
        s = self.mk("Conformal prediction gives coverage. " * 40, authors=["Vovk"])
        f = one(run("The Mondrian construction of Vovk et al.~\\cite{a} is used.", s, {"a": self.ID}, "Vovk, V"))
        assert f.status == FLAGGED and "Mondrian" in f.message

    def test_present_word_is_not_flagged(self):
        s = self.mk("The Mondrian construction divides the calibration set. " * 40, authors=["Vovk"])
        f = one(run("The Mondrian construction of Vovk et al.~\\cite{a} is used.", s, {"a": self.ID}, "Vovk, V"))
        assert f.status == UNVERIFIABLE                      # and never VERIFIED: presence proves nothing

    def test_layer_a_never_returns_verified(self):
        s = self.mk("The Mondrian construction divides the calibration set. " * 40, authors=["Vovk"])
        for f in run("The Mondrian construction of Vovk et al.~\\cite{a} is used.", s, {"a": self.ID}, "Vovk, V"):
            assert f.status in (FLAGGED, UNVERIFIABLE)

    def test_untrusted_source_never_yields_an_absence_flag(self):
        s = {self.ID: ft.Source(self.ID, "T", ["Vovk"], 2012, "abs", tier="pdf",
                                index=ft.TextIndex.from_text("nothing relevant " * 200),
                                qual={"trusted": False})}
        f = one(run("The Mondrian construction of Vovk et al.~\\cite{a} is used.", s, {"a": self.ID}, "Vovk, V"))
        assert f.status == UNVERIFIABLE and "self-check" in f.message

    def test_abstract_only_source_never_yields_an_absence_flag(self):
        s = {self.ID: ft.Source(self.ID, "T", ["Vovk"], 2012, "abs", tier="abstract")}
        f = one(run("The Mondrian construction of Vovk et al.~\\cite{a} is used.", s, {"a": self.ID}, "Vovk, V"))
        assert f.status == UNVERIFIABLE

    def test_one_unreadable_source_blocks_the_flag(self):
        a, b = "arxiv:2606.24020", "arxiv:2606.01850"
        s = {a: src(a, "Conformal coverage. " * 50, authors=["Vovk"]),
             b: ft.Source(b, "T", ["Vovk"], 2012, "abs", tier="abstract")}
        f = one(run("The Mondrian construction of Vovk et al.~\\cite{x,y} is used.", s, {"x": a, "y": b}, "Vovk, V"))
        assert f.status == UNVERIFIABLE

    def test_term_found_in_any_cited_source_is_not_flagged(self):
        a, b = "arxiv:2606.24020", "arxiv:2606.01850"
        s = {a: src(a, "Conformal coverage. " * 50, authors=["Vovk"]),
             b: src(b, "The Mondrian design is described here. " * 50, authors=["Vovk"])}
        f = one(run("The Mondrian construction of Vovk et al.~\\cite{x,y} is used.", s, {"x": a, "y": b}, "Vovk, V"))
        assert f.status == UNVERIFIABLE

    def test_word_broken_by_layout_in_the_source_is_not_called_absent(self):
        s = self.mk("The Mon-\ndrian design divides the set. " * 40, authors=["Vovk"])
        f = one(run("The Mondrian construction of Vovk et al.~\\cite{a} is used.", s, {"a": self.ID}, "Vovk, V"))
        assert f.status == UNVERIFIABLE

    def test_plural_and_possessive_forms_match(self):
        s = self.mk("Mondrians are used. " * 60, authors=["Vovk"])
        f = one(run("The Mondrian's construction, per Vovk et al.~\\cite{a}, is used.", s, {"a": self.ID}, "Vovk, V"))
        assert f.status == UNVERIFIABLE

    # -- background citations and the author's own vocabulary
    def test_own_coinage_in_a_background_sentence_is_not_flagged(self):
        tex = ("\\paragraph{Mondrian calibration.} We use Mondrian calibration throughout. "
               "Our Mondrian intervals are computed per scheme, a Mondrian design~\\cite{a}.")
        s = self.mk("Conformal coverage. " * 50, authors=["Vovk"])
        assert all(f.status == UNVERIFIABLE for f in run(tex, s, {"a": self.ID}, "Vovk, V"))

    # -- things the sentence compares the source against are not claims about it
    def test_a_work_named_only_as_a_comparison_is_not_looked_for(self):
        s = self.mk("Conformal sets over labels. " * 40, authors=["Tong"])
        f = one(run("\\citet{a} apply conformal prediction to LLMs, a closer domain than BenchPress.", s, {"a": self.ID}))
        assert f.status == UNVERIFIABLE

    def test_but_a_name_before_the_comparison_is_still_checked(self):
        s = self.mk("Conformal sets over labels. " * 40, authors=["Tong"])
        f = one(run("The Mondrian sets~\\cite{a} apply conformal prediction, unlike others.", s, {"a": self.ID}))
        assert f.status == FLAGGED and "Mondrian" in f.message

    def test_a_name_that_labels_another_cited_source_is_not_looked_for_here(self):
        a, b = "arxiv:2606.24020", "arxiv:2606.01850"
        s = {a: src(a, "Rank two matrix completion. " * 40, authors=["Zeng"]),
             b: src(b, "Conformal sets over labels. " * 40, authors=["Tong"])}
        tex = "BenchPress~\\cite{x} predicts scores. Ours differs from what BenchPress does~\\cite{y}."
        fs = run(tex, s, {"x": a, "y": b})
        assert all(f.status != FLAGGED or "BenchPress" not in f.message or "2606.24020" in f.message for f in fs)

    # -- names
    def test_prose_names_someone_who_is_not_an_author(self):
        s = self.mk("Selection conditional coverage. " * 40, authors=["Jin", "Ren"])
        f = one(run("Jin and Cand\\`es~\\cite{a} give coverage.", s, {"a": self.ID}, "Jin, Y and Ren, Z"))
        assert f.status == FLAGGED and "Candes" in f.message

    def test_single_method_name_is_not_taken_for_an_author(self):
        s = self.mk("BenchPress predicts scores. " * 40, authors=["Zeng", "Papailiopoulos"])
        f = one(run("BenchPress~\\cite{a} predicts scores.", s, {"a": self.ID}, "Zeng, Y and Papailiopoulos, D"))
        assert f.status == UNVERIFIABLE

    def test_apostrophes_in_names_do_not_break_the_match(self):
        s = self.mk("Coverage. " * 40, authors=["Lei", "G\u2019Sell"])
        f = one(run("Coverage holds~\\cite{a}.", s, {"a": self.ID}, "Lei, Jing and G'Sell, Max"))
        assert f.status == UNVERIFIABLE

    def test_accented_author_name_matches_the_record(self):
        s = self.mk("Coverage. " * 40, authors=["Candes", "Ramdas"])
        f = one(run("Cand\\`es and Ramdas~\\cite{a} give coverage.", s, {"a": self.ID}, "Candes, E and Ramdas, A"))
        assert f.status == UNVERIFIABLE

    def test_bibliography_author_list_disagreeing_with_the_record_is_flagged(self):
        s = self.mk("Selection conditional coverage. " * 40, authors=["Jin", "Ren"])
        f = one(run("Coverage fails under selection~\\cite{a}.", s, {"a": self.ID}, "Jin, Ying and Candes, Emmanuel"))
        assert f.status == FLAGGED and "bibliography lists" in f.message

    # -- dates
    def test_wrong_year_is_flagged(self):
        s = self.mk("Rank two structure. " * 40, year=2026)
        f = one(run("BenchPress~\\cite{a} is from 2016.", s, {"a": self.ID}))
        assert f.status == FLAGGED and "2016" in f.message and "2026" in f.message

    def test_year_within_one_of_the_record_is_fine(self):
        s = self.mk("BenchPress rank two structure. " * 40, year=2026)
        f = one(run("BenchPress~\\cite{a} is from 2025.", s, {"a": self.ID}))
        assert f.status == UNVERIFIABLE

    def test_a_year_that_does_not_date_the_source_is_not_flagged(self):
        s = self.mk("GPTQ rank two structure. " * 40, year=2022)
        f = one(run("Since 2016, methods like GPTQ~\\cite{a} became common.", s, {"a": self.ID}))
        assert f.status == UNVERIFIABLE

    # -- numbers
    def test_figure_absent_from_the_source_is_flagged(self):
        s = self.mk("We compile 84 frontier models on 133 benchmarks. " * 20)
        f = one(run("Their matrix covers 84 models and 233 benchmarks~\\cite{a}.", s, {"a": self.ID}))
        assert f.status == FLAGGED and "233" in f.message

    def test_figure_present_with_thousands_separator_is_fine(self):
        s = self.mk("We ran over 500,000 evaluations of models. " * 20)
        f = one(run("They report over 500{,}000 evaluations~\\cite{a}.", s, {"a": self.ID}))
        assert f.status == UNVERIFIABLE

    def test_our_own_number_beside_a_citation_is_not_checked(self):
        s = self.mk("Coverage is discussed. " * 40, authors=["Tong"])
        f = one(run("We measure 90.1\\% coverage, in line with Tong et al.~\\cite{a}.", s, {"a": self.ID}))
        assert f.status == UNVERIFIABLE

    # -- claims of absence
    def test_claim_that_the_source_never_mentions_something_it_mentions_is_flagged(self):
        s = self.mk("We use conformal prediction. Conformal intervals are calibrated. " * 10)
        f = one(run("BenchPress~\\cite{a} never mentions conformal prediction.", s, {"a": self.ID}))
        assert f.status == FLAGGED and "conform" in f.message

    def test_claim_of_absence_that_holds_is_not_flagged(self):
        s = self.mk("We predict scores of frontier models on benchmarks. " * 30)
        f = one(run("Quantized checkpoints do not appear in the BenchPress paper~\\cite{a}.", s, {"a": self.ID}))
        assert f.status == UNVERIFIABLE

    def test_absence_claim_is_not_flagged_by_the_sources_own_name(self):
        s = self.mk("BenchPress predicts scores of frontier models. " * 30)
        f = one(run("Quantized checkpoints do not appear in the BenchPress paper~\\cite{a}.", s, {"a": self.ID}))
        assert f.status == UNVERIFIABLE

    # -- plumbing
    def test_unresolvable_key_is_unverifiable_not_silent(self):
        tf = [("p.tex", "p.tex", "Something is shown~\\cite{blog}.")]
        bf = [("r.bib", "r.bib", "@misc{blog, title={x}, note={\\url{https://example.org/a}}}")]
        f = one(support.check(tf, bf, provider=lambda i: None))
        assert f.status == UNVERIFIABLE and "no arXiv id or DOI" in f.message

    def test_offline_reports_not_checked(self):
        tf, bf = pair_for("Tong et al.~\\cite{t} show it.", "", {"t": ("arxiv:2606.01850", "Tong, Y")})
        f = one(support.check(tf, bf, offline=True))
        assert f.status == UNVERIFIABLE and "offline" in f.message

    def test_provider_that_raises_does_not_crash_the_audit(self):
        class Boom(support.NetworkProvider):
            def _load(self, ident):
                raise RuntimeError("boom")
        tf, bf = pair_for("Tong et al.~\\cite{t} show it.", "", {"t": ("arxiv:2606.01850", "Tong, Y")})
        f = one(support.check(tf, bf, provider=Boom()))
        assert f.status == UNVERIFIABLE

    def test_document_without_citations_yields_nothing(self):
        assert support.check([("p.tex", "p.tex", "Plain text, no citations.")], []) == []


class TestRecords:
    def test_parse_arxiv_abs_page(self):
        page = ('<meta name="citation_title" content="Confidence on the Focal"/>'
                '<meta name="citation_author" content="Jin, Ying"/><meta name="citation_author" content="Ren, Zhimei"/>'
                '<meta name="citation_date" content="2024/03/06"/>'
                '<blockquote class="abstract mathjax"><span class="descriptor">Abstract:</span> We study selection.</blockquote>')
        r = support.parse_arxiv_abs(page)
        assert r["authors"] == ["jin", "ren"] and r["year"] == 2024 and r["abstract"] == "We study selection."

    def test_api_throttled_falls_back_to_the_abstract_page(self):
        page = (b'<meta name="citation_title" content="T"/><meta name="citation_author" content="Doe, Jane"/>'
                b'<meta name="citation_date" content="2020/01/01"/><blockquote class="abstract">Abstract: hi</blockquote>')
        gb = lambda url, **kw: (200, page, False) if "/abs/" in url else (404, b"", False)
        p = support.NetworkProvider(fetch=lambda u, method="GET", timeout=15: (406, ""), get_bytes=gb,
                                    pdf=False, sleep=lambda s: None)
        s = p("arxiv:2001.00001")
        assert s.authors == ["doe"] and s.year == 2020


    def test_parse_arxiv_record(self):
        xml = ("<feed><entry><title>You Don't  Need\n to Run</title><summary> An abstract. </summary>"
               "<published>2026-06-22T00:00:00Z</published>"
               "<author><name>Yuchen Zeng</name></author><author><name>Dimitris Papailiopoulos</name></author></entry></feed>")
        r = support.parse_arxiv_record(xml)
        assert r["title"] == "You Don't Need to Run" and r["year"] == 2026 and r["authors"] == ["zeng", "papailiopoulos"]

    def test_truncated_download_is_reported_and_not_used(self):
        calls = []

        def gb(url, **kw):
            calls.append(url)
            return (200, b"<html>" + b"x" * 10, True) if "html" in url else (404, b"", False)

        def fetch(url, method="GET", timeout=15):
            return 200, ("<feed><entry><title>T</title><summary>abs</summary><published>2020-01-01</published>"
                         "<author><name>A B</name></author></entry></feed>")
        p = support.NetworkProvider(fetch=fetch, get_bytes=gb, pdf=False, sleep=lambda s: None)
        s = p("arxiv:2001.00001")
        assert s.tier == "abstract" and "larger than the download limit" in s.note


# ===================================================== real-source corpus
def _records():
    recs = {}
    for fn in glob.glob(os.path.join(FIX, "records", "*.json.gz")):
        with gzip.open(fn, "rt", encoding="utf-8") as fh:
            d = json.load(fh)
        idx = ft.TextIndex.from_json(d["index"]) if d.get("index") else None
        recs[d["ident"]] = ft.Source(d["ident"], d["title"], d["authors"], d["year"], d["abstract"],
                                     d["tier"], idx, d["qual"], d.get("note", ""))
    return recs


RECS = _records()
needs_corpus = pytest.mark.skipif(len(RECS) < 10, reason="recorded sources missing (validation/build_support_fixtures.py)")


def _files(path_tex, path_bib=None):
    def rd(p):
        with open(p, encoding="utf-8") as fh:
            return fh.read()
    tf = [(os.path.basename(path_tex), path_tex, rd(path_tex))]
    bf = [(os.path.basename(path_bib), path_bib, rd(path_bib))] if path_bib else []
    return tf, bf


def _check(tex, bib=None):
    tf, bf = _files(os.path.join(CASES, tex), os.path.join(CASES, bib) if bib else None)
    return support.check(tf, bf, provider=lambda i: RECS.get(i) or ft.Source(i, note="not recorded"))


@needs_corpus
class TestKnownErrors:
    """Real errors from this project, against real sources."""

    def test_vovk_2012_cited_for_mondrian(self):
        # The source is real and its full text has no occurrence of "Mondrian"
        # (checked twice this week). The citing sentence is a reconstruction.
        assert RECS["arxiv:1209.2673"].trusted, RECS["arxiv:1209.2673"].note
        fs = _check("must_flag/vovk_mondrian.tex", "must_flag/refs_vovk.bib")
        hit = [f for f in fs if f.status == FLAGGED]
        assert len(hit) == 1 and "Mondrian" in hit[0].message

    def test_jin_and_candes_named_in_prose(self):
        fs = _check("must_flag/jin_candes_prose.tex", "must_flag/refs_jin_correct.bib")
        assert [f.status for f in fs] == [FLAGGED] and "Candes" in fs[0].message

    def test_jin_and_candes_in_the_bibliography(self):
        fs = _check("must_flag/jin_candes_bib.tex", "must_flag/refs_jin_wrong.bib")
        assert [f.status for f in fs] == [FLAGGED] and "bibliography lists" in fs[0].message

    def test_correct_jin_and_ren_is_not_flagged(self):
        fs = _check("must_flag/jin_candes_bib.tex", "must_flag/refs_jin_correct.bib")
        assert all(f.status != FLAGGED for f in fs)

    def test_benchpress_injected_errors(self):
        # Real source, deliberately wrong sentences (the 2016 slip came from external feedback).
        fs = _check("must_flag/benchpress_synthetic.tex", "must_flag/refs_zeng.bib")
        assert [f.status for f in fs] == [FLAGGED, FLAGGED, FLAGGED]
        assert "2016" in fs[0].message and "233" in fs[1].message and "conform" in fs[2].message


@needs_corpus
class TestKnownLimits:
    """What Layer A is NOT expected to catch. Each must say UNVERIFIABLE, never VERIFIED."""

    def test_overbroad_claim_about_a_source_is_beyond_layer_a(self):
        # 'above 99% for every scheme' vs the article's 'v1 average': all the words and
        # numbers are present. Needs reading, i.e. Layer B. The source is URL-only here.
        fs = _check("limits/kurtic_strength.tex", "limits/refs_kurtic.bib")
        assert [f.status for f in fs] == [UNVERIFIABLE]

    def test_an_authors_own_coinage_cited_to_a_background_paper_is_not_flagged(self):
        fs = _check("limits/own_term.tex", "limits/refs_vovk.bib")
        assert all(f.status == UNVERIFIABLE for f in fs)


@needs_corpus
class TestControls:
    """Correct citations. A flag here is a false positive, the failure this whole
    check must avoid."""

    def test_the_papers_own_citations(self):
        fs = _check("controls/paper.tex", "controls/refs.bib")
        assert len(fs) >= 10, len(fs)                       # the corpus is not trivially empty
        bad = [(f.line, f.message, f.evidence) for f in fs if f.status == FLAGGED]
        assert not bad, bad

    def test_markdown_docs_that_state_facts_about_sources(self):
        for name in ("TOOL_SUMMARY.md", "NEGATIVE_RESULT.md", "negation_control.md"):
            p = os.path.join(CASES, "controls_md", name)
            with open(p, encoding="utf-8") as fh:
                text = fh.read()
            fs = support.check([(name, p, text)], [], provider=lambda i: RECS.get(i) or ft.Source(i, note="not recorded"))
            bad = [(f.line, f.message, f.evidence) for f in fs if f.status == FLAGGED]
            assert not bad, (name, bad)

    def test_absence_claim_that_is_true_is_not_flagged(self):
        p = os.path.join(CASES, "controls_md", "negation_control.md")
        with open(p, encoding="utf-8") as fh:
            fs = support.check([("negation_control.md", p, fh.read())], [],
                               provider=lambda i: RECS.get(i))
        assert fs and all(f.status == UNVERIFIABLE for f in fs)

    def test_some_controls_were_really_examined_by_absence_checks(self):
        """Guard against the controls passing only because nothing was looked at:
        at least a few control sentences must have had every cited source read in
        full and self-checked."""
        fs = _check("controls/paper.tex", "controls/refs.bib")
        examined = [f for f in fs if f.extra["sources"] and all(v["trusted"] for v in f.extra["sources"].values())]
        assert len(examined) >= 4, len(examined)
