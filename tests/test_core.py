import json, os
import pytest
from claimaudit import claims, sources, overclaim, consistency, citations, license as lic, cli
from claimaudit.claims import extract_numbers, sentences
from claimaudit.report import VERIFIED, FLAGGED, UNVERIFIABLE


def nums(s):
    return [(n.raw, n.kind, n.value) for n in extract_numbers(s)]


# ------------------------------------------------------------- extraction
def test_percent_pp_ratio_and_plain():
    got = nums("Coverage was 90.1% (118/131) with -39.5pp worst and 817 rows.")
    assert ("90.1%", "pct", 90.1) in got
    assert ("118/131", "ratio", 118.0) in got
    assert ("-39.5pp", "pp", -39.5) in got
    assert ("817", "plain", 817.0) in got


def test_structural_numbers_are_not_claims():
    assert nums("See Section 3, Figure 2 and Table 1 in 2026.") == []
    assert nums("Uses Llama-3.1 and W4A16 at v2.0.1.") == []


def test_urls_dois_arxiv_ids_dates_stripped():
    assert nums("arXiv:2606.24020 https://x.org/a/1234 10.1145/3477495.3531832 on 2026-09-25 [12]") == []


def test_small_ints_only_with_count_noun():
    assert nums("We ran 5 models.") == [("5", "plain", 5.0)]
    assert nums("We felt 5 things.") == []


def test_thousands_separator_and_ranges():
    assert ("500,000", "plain", 500000.0) in nums("Over 500,000 evaluations.")
    got = [v for _r, _k, v in nums("noise is 33--100% of variance")]
    assert 33.0 in got and 100.0 in got


def test_latex_cleanup_and_sentence_lines():
    tex = "% a comment 99%\nIntro text.\nWe get \\(90.1\\%\\) coverage\nacross \\emph{817} rows.\n"
    ss = list(sentences(tex, "p.tex"))
    joined = " ".join(s for _l, s in ss)
    assert "99" not in joined and "90.1%" in joined and "817" in joined
    assert [l for l, s in ss if "90.1%" in s] == [3]


def test_markdown_code_and_comments_ignored():
    md = "Text 12.5% here.\n\n```\nx = 77.7%\n```\n\n<!-- claim: 88.8% -->\n"
    ns = [n.raw for c in claims.extract([("a.md", "a.md", md)]) for n in c.nums]
    assert ns == ["12.5%"]


# ------------------------------------------------------------- sources
def _dir(tmp_path, files):
    for n, t in files.items():
        (tmp_path / n).write_text(t)
    return str(tmp_path)


def _findings(tmp_path, md, csvtxt, name="results.csv"):
    d = _dir(tmp_path, {"README.md": md, name: csvtxt})
    f, _ = cli.run(d, only=["source"], paid=True)
    return f


CSV = "model,coverage,rows\na,0.912,100\nb,0.894,120\nc,0.903,80\n"


def test_verified_needs_context_word(tmp_path):
    f = _findings(tmp_path, "Mean coverage in the results was 90.3%.\n", CSV)
    assert f[0].status == VERIFIED and "coverage" in f[0].evidence


def test_coincidental_match_is_not_verified(tmp_path):
    f = _findings(tmp_path, "Latency improved by 90.3%.\n", CSV)
    assert f[0].status == UNVERIFIABLE and "coincidence" in f[0].evidence


def test_flagged_when_sentence_points_at_file_but_value_missing(tmp_path):
    f = _findings(tmp_path, "Mean coverage in results.csv was 95.0%.\n", CSV)
    assert f[0].status == FLAGGED and "nearest" in f[0].evidence


def test_row_count_verified_when_specific_and_tiny_counts_are_not(tmp_path):
    big = "model,v\n" + "\n".join(f"m{i},{i}" for i in range(517)) + "\n"
    f = _findings(tmp_path, "The results have 517 rows.\n", big)
    assert f[0].status == VERIFIED
    (tmp_path / "README.md").write_text("The results have 518 rows.\n")
    g, _ = cli.run(str(tmp_path), only=["source"], paid=True)
    assert g[0].status != VERIFIED
    (tmp_path / "README.md").write_text("There are 3 rows in results.\n")
    (tmp_path / "results.csv").write_text("a,b\n1,2\n3,4\n5,6\n")
    h, _ = cli.run(str(tmp_path), only=["source"], paid=True)
    assert h[0].status == UNVERIFIABLE      # tiny numbers are too easy to match by chance


def test_rounding_tolerance_respects_printed_precision(tmp_path):
    good = _findings(tmp_path, "Mean coverage in the results was 90.3%.\n", CSV)
    assert good[0].status == VERIFIED
    (tmp_path / "README.md").write_text("Mean coverage in the results was 90.4%.\n")
    bad, _ = cli.run(str(tmp_path), only=["source"], paid=True)
    assert bad[0].status != VERIFIED


def test_json_source(tmp_path):
    d = _dir(tmp_path, {"a.md": "The pooled coverage is 90.96%.\n",
                        "out.json": json.dumps({"pooled": {"coverage": 90.96, "rows": 5719}})})
    f, _ = cli.run(d, only=["source"], paid=True)
    assert f[0].status == VERIFIED


# ------------------------------------------------------------- overclaim
def scan_md(md):
    return overclaim.scan([("a.md", "a.md", md)])


def test_overclaim_flags_and_notes_negation():
    fs = scan_md("This is the first proven method. It never fails and beats everything.\n")
    msgs = " ".join(f.message for f in fs)
    for w in ("'first'", "'proven'", "'never'", "'beats'"):
        assert w in msgs
    assert "(negated)" not in msgs
    assert any("(negated)" in f.message for f in scan_md("This is not yet proven.\n"))


def test_overclaim_ignores_code_and_benign_first():
    assert scan_md("```\nalways = True\n```\n") == []
    assert scan_md("The first step is to install.\n") == []


# ------------------------------------------------------------- consistency
def test_consistency_flags_changed_number_across_files():
    cs = claims.extract([("a.md", "a.md", "The tool moved 8 of 17 cells into insufficient evidence state.\n"),
                         ("b.md", "b.md", "The tool moved 9 of 17 cells into insufficient evidence state.\n")])
    fs = consistency.check(cs)
    assert len(fs) == 1 and fs[0].status == FLAGGED


def test_consistency_ignores_rounding_and_same_values():
    cs = claims.extract([("a.md", "a.md", "Pooled coverage across scored rows was 90.1% on the held out families.\n"),
                         ("b.md", "b.md", "Pooled coverage across scored rows was 90% on the held out families.\n")])
    assert consistency.check(cs) == []


def test_consistency_reports_every_mismatch_with_no_hidden_cap():
    """The check must not drop findings; the report may trim, but must say so."""
    from claimaudit.report import render_text, MAX_LISTED
    n = MAX_LISTED + 5
    files = []
    for i in range(n):
        w = "q" + chr(97 + i // 26) + chr(97 + i % 26)
        s = f"The {w}alpha {w}beta {w}gamma cohort reported %d rows.\n"
        files.append((f"a{i}.md", f"a{i}.md", s % (100 + i)))
        files.append((f"b{i}.md", f"b{i}.md", s % (500 + i)))
    fs = consistency.check(claims.extract(files))
    assert len(fs) == n
    txt = render_text(fs)
    assert f"showing {MAX_LISTED} of {n} consistency item(s); 5 more not listed" in txt
    assert sum(1 for l in txt.splitlines() if "similar sentence" in l) == MAX_LISTED
    assert str(n) in txt.split("summary")[1]   # the summary counts all of them


def test_consistency_ignores_unrelated_sentences():
    cs = claims.extract([("a.md", "a.md", "Latency dropped to 12.5% of baseline.\n"),
                         ("b.md", "b.md", "Revenue grew 40.2% last quarter.\n")])
    assert consistency.check(cs) == []


# ------------------------------------------------------------- citations
ATOM = """<feed><entry><id>http://arxiv.org/abs/2403.03868v2</id>
<title>Confidence on the Focal: Conformal Prediction with Selection-Conditional Coverage</title>
<author><name>Ying Jin</name></author><author><name>Zhimei Ren</name></author></entry></feed>"""


def fake_fetch_factory(table):
    def f(url, method="GET", timeout=15):
        for k, v in table.items():
            if url.startswith(k):
                return v
        return 0, ""
    return f


BIB_BAD = "@article{jin,\n title={Confidence on the Focal: Conformal Prediction with Selection-Conditional Coverage},\n author={Jin, Ying and Cand{\\`e}s, Emmanuel J.},\n eprint={2403.03868}\n}\n"
BIB_OK = BIB_BAD.replace("Cand{\\`e}s, Emmanuel J.", "Ren, Zhimei")


def test_bib_author_mismatch_is_flagged_like_the_real_error():
    fs = citations.check([], [("r.bib", "r.bib", BIB_BAD)], fetch=fake_fetch_factory({"https://export.arxiv.org": (200, ATOM)}))
    assert fs[0].status == FLAGGED and "authors differ" in fs[0].evidence


def test_bib_match_verified_and_missing_arxiv_flagged():
    ft = fake_fetch_factory({"https://export.arxiv.org": (200, ATOM)})
    assert citations.check([], [("r.bib", "r.bib", BIB_OK)], fetch=ft)[0].status == VERIFIED
    fs = citations.check([("a.md", "a.md", "See arXiv:2999.99999 for details.\n")], [], fetch=ft)
    assert fs[0].status == FLAGGED and "not found" in fs[0].message


def test_url_dead_blocked_and_ok():
    ft = fake_fetch_factory({"https://dead.example": (404, ""), "https://blocked.example": (403, ""),
                             "https://ok.example": (200, "")})
    txt = "a https://dead.example/x b https://blocked.example/y c https://ok.example/z\n"
    fs = {f.message.split(": ")[-1].split("/")[2]: f.status
          for f in citations.check([("a.md", "a.md", txt)], [], fetch=ft)}
    assert fs == {"dead.example": FLAGGED, "blocked.example": UNVERIFIABLE, "ok.example": VERIFIED}


def test_doi_crossref_mismatch_and_offline():
    msg = json.dumps({"message": {"title": ["On Survivorship Bias in MS MARCO"], "author": [{"family": "Gupta"}, {"family": "MacAvaney"}]}})
    bib = "@inproceedings{g,\n title={On Survivorship Bias in MS MARCO},\n author={Gupta, P. and Smith, Jo},\n doi={10.1145/3477495.3531832}\n}\n"
    fs = citations.check([], [("r.bib", "r.bib", bib)], fetch=fake_fetch_factory({"https://api.crossref.org": (200, msg)}))
    assert fs[0].status == FLAGGED
    off = citations.check([("a.md", "a.md", "see arXiv:2403.03868\n")], [], offline=True)
    assert off[0].status == UNVERIFIABLE


# ------------------------------------------------------------- license
@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAIMAUDIT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CLAIMAUDIT_CACHE", str(tmp_path / "cache"))
    monkeypatch.delenv("CLAIMAUDIT_LICENSE_KEY", raising=False)


@pytest.fixture
def no_network(monkeypatch):
    import socket, urllib.request
    def refuse(*a, **kw):
        raise AssertionError("the license gate must not touch the network")
    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "gethostname", refuse)


def test_free_without_key(no_network):
    assert lic.tier()[0] == "free"


def test_activate_stores_key_locally_and_unlocks(no_network, tmp_path):
    ok, msg = lic.activate("  K-123  ")
    assert ok and "no server" in msg
    stored = json.load(open(tmp_path / "home" / "license.json"))
    assert stored["key"] == "K-123" and set(stored) == {"key", "stored_at"}
    assert lic.tier()[0] == "paid"


def test_env_key_unlocks_without_a_stored_file(no_network, monkeypatch):
    monkeypatch.setenv("CLAIMAUDIT_LICENSE_KEY", "K")
    assert lic.tier()[0] == "paid"


def test_empty_or_blank_key_does_not_unlock(no_network, monkeypatch, tmp_path):
    assert not lic.activate("")[0]
    assert not lic.activate("   \n")[0]
    assert lic.tier()[0] == "free"
    monkeypatch.setenv("CLAIMAUDIT_LICENSE_KEY", "  ")
    assert lic.tier()[0] == "free"
    (tmp_path / "home" / "license.json").write_text('{"key": ""}')
    assert lic.tier()[0] == "free"
    (tmp_path / "home" / "license.json").write_text("not json")
    assert lic.tier()[0] == "free"


def test_no_lemon_squeezy_left_and_purchase_url_is_the_one_placeholder():
    import inspect
    src = inspect.getsource(lic).lower()
    assert "lemonsqueezy" not in src and "lemon squeezy" not in src
    assert not hasattr(lic, "PRODUCT_ID")
    assert lic.PURCHASE_URL.startswith("https://buy.stripe.com/")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for rel in ("README.md", os.path.join("docs", "index.html")):
        text = open(os.path.join(root, rel), encoding="utf-8").read()
        assert lic.PURCHASE_URL in text, rel
        assert "lemon" not in text.lower(), rel


def test_success_page_shows_the_shared_key_and_it_unlocks(no_network):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    page = open(os.path.join(root, "docs", "success.html"), encoding="utf-8").read()
    key = "claimaudit-early-2026"
    assert key in page
    assert "3 of 8" in page
    assert "shared" in page.lower()
    assert "not on PyPI" in page
    assert "email" not in page.lower()
    index = open(os.path.join(root, "docs", "index.html"), encoding="utf-8").read()
    assert "by email" not in index and "emailed" not in index
    assert lic.activate(key)[0]
    assert lic.tier()[0] == "paid"


# ------------------------------------------------------------- cli
def test_free_tier_runs_overclaim_only_and_skips_paid(tmp_path, capsys):
    (tmp_path / "a.md").write_text("This is the first proven method.\n")
    rc = cli.main(["check", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 1 and "overclaim" in out and "skipped (license required): source, citation, consistency" in out


def test_exit_zero_json_and_unknown_check(tmp_path, capsys):
    (tmp_path / "a.md").write_text("Fine text.\n")
    assert cli.main(["check", str(tmp_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["limits"]
    assert cli.main(["check", str(tmp_path), "--only", "bogus"]) == 2
    assert cli.main(["check", str(tmp_path / "nope")]) == 2


def test_exclude_globs_and_ignore_file(tmp_path):
    (tmp_path / "keep.md").write_text("The first method.\n")
    (tmp_path / "skip").mkdir(); (tmp_path / "skip" / "x.md").write_text("The first method.\n")
    (tmp_path / "y.md").write_text("The first method.\n")
    (tmp_path / ".claimauditignore").write_text("y.md\n")
    f, _ = cli.run(str(tmp_path), only=["overclaim"], exclude=["skip/*"])
    assert {x.file for x in f} == {"keep.md"}


def test_doi_in_markdown_link_is_cut_cleanly():
    txt = "[10.48550/arXiv.2506.15154](https://doi.org/10.48550/arXiv.2506.15154)\n"
    ft = fake_fetch_factory({"https://api.crossref.org": (404, ""), "https://doi.org": (200, "")})
    fs = citations.check([("a.md", "a.md", txt)], [], fetch=ft)
    assert [f.message for f in fs if "DOI" in f.message][0].startswith("DOI 10.48550/arXiv.2506.15154:")


def test_generic_aggregate_words_do_not_verify(tmp_path):
    f = _findings(tmp_path, "The mean was 90.0%.\n", CSV)
    assert f[0].status == UNVERIFIABLE


def test_exact_value_in_named_file_is_verified_not_flagged(tmp_path):
    f = _findings(tmp_path, "See results.csv: the maximum was 120.\n", CSV)
    assert f[0].status == VERIFIED


def test_loose_words_do_not_count_as_pointing_at_a_file(tmp_path):
    d = _dir(tmp_path, {"a.md": "We rejected 7 test rows.\n",
                        "rejected_rows.csv": "reason,n\nx,1\ny,2\n"})
    f, _ = cli.run(d, only=["source"], paid=True)
    assert f[0].status != FLAGGED


def test_named_file_rescues_value_without_word_overlap(tmp_path):
    d = _dir(tmp_path, {"a.md": "Per x2_9.csv, the peak was 120.\n",
                        "x2_9.csv": "alpha,beta\n1,120\n2,80\n"})
    f, _ = cli.run(d, only=["source"], paid=True)
    assert f[0].status == VERIFIED and "named in sentence" in f[0].evidence
    (tmp_path / "a.md").write_text("Per x2_9.csv, the peak was 121.\n")
    g, _ = cli.run(d, only=["source"], paid=True)
    assert g[0].status == FLAGGED


def test_default_report_shows_unverifiable_count_not_list(tmp_path, capsys):
    (tmp_path / "a.md").write_text("Latency fell by 12.5% after the change.\n")
    cli.run(str(tmp_path), only=["overclaim"], paid=True)
    from claimaudit.report import render_text
    f, _ = cli.run(str(tmp_path), only=["source"], paid=True)
    txt = render_text(f)
    assert "1 UNVERIFIABLE item(s) not listed" in txt and "12.5%" not in txt.split("summary")[0]
    assert "12.5%" in render_text(f, show_unverifiable=True)
