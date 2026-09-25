"""Find arXiv IDs, DOIs, URLs and .bib entries; check they resolve and, for
.bib entries with metadata, that authors and title match the public record.
Whether a paper actually supports the sentence citing it is NOT checked."""
from __future__ import annotations
import json, os, re, time, unicodedata, urllib.request, urllib.error, urllib.parse
from .report import Finding, VERIFIED, FLAGGED, UNVERIFIABLE

UA = "claimaudit/0.1 (+local citation check)"
ARXIV_RX = re.compile(r"(?i)(?:arxiv[:\s/]*(?:abs/|pdf/)?|arxiv\.org/(?:abs|pdf)/)(\d{4}\.\d{4,5})(?:v\d+)?")
DOI_RX = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>{}|\\^`\[\]]+)")
URL_RX = re.compile(r"https?://[^\s\"<>{}|\\^`\)\]]+")
BLOCKED = {401, 403, 405, 406, 429, 451, 503, 999}


# ------------------------------------------------------------------ fetching
def _cache_dir():
    d = os.environ.get("CLAIMAUDIT_CACHE") or os.path.join(os.path.expanduser("~"), ".cache", "claimaudit")
    os.makedirs(d, exist_ok=True)
    return d


def default_fetch(url, method="GET", timeout=15):
    """-> (status, body). status 0 means network failure."""
    key = os.path.join(_cache_dir(), re.sub(r"[^A-Za-z0-9]", "_", method + url)[:180] + ".json")
    try:
        if os.path.exists(key) and time.time() - os.path.getmtime(key) < 7 * 86400:
            d = json.load(open(key)); return d["s"], d["b"]
    except (OSError, ValueError):
        pass
    req = urllib.request.Request(url, method=method, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(400_000).decode("utf-8", "replace") if method == "GET" else ""
            res = (r.status, body)
    except urllib.error.HTTPError as e:
        res = (e.code, "")
    except Exception:
        return 0, ""
    if res[0] and res[0] < 500:
        try:
            json.dump({"s": res[0], "b": res[1]}, open(key, "w"))
        except OSError:
            pass
    return res


# ------------------------------------------------------------------ parsing
def _norm(s):
    s = re.sub(r"\\[`'^\"~=.]\{?(\w)\}?", r"\1", s)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z ]", "", s.lower()).strip()


def parse_bib(text):
    entries = []
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,\s]+)\s*,(.*?)(?=\n@|\Z)", text, re.S):
        body = m.group(3)
        f = {}
        for k, v in re.findall(r"(\w+)\s*=\s*(\{(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*\}|\"[^\"]*\"|\d+)", body):
            v = v.strip()
            f[k.lower()] = re.sub(r"\s+", " ", v[1:-1] if v[0] in "{\"" else v).strip()
        line = text[:m.start()].count("\n") + 1
        entries.append({"key": m.group(2), "fields": f, "line": line})
    return entries


def surnames(author_field):
    out = []
    for a in re.split(r"\s+and\s+", re.sub(r"\s+", " ", author_field)):
        a = a.strip()
        if not a or a.lower() in ("others", "et al."):
            continue
        last = a.split(",")[0] if "," in a else a.split()[-1]
        out.append(_norm(last.replace("{", "").replace("}", "")).split()[-1:] or [""])
    return [x[0] for x in out]


def _title_sim(a, b):
    ta, tb = set(re.findall(r"[a-z]{3,}", _norm(a))), set(re.findall(r"[a-z]{3,}", _norm(b)))
    return len(ta & tb) / max(1, len(ta | tb))


def parse_arxiv(xml):
    res = {}
    for e in re.findall(r"<entry>(.*?)</entry>", xml, re.S):
        m = re.search(r"arxiv\.org/abs/(\d{4}\.\d{4,5})", e)
        if not m:
            continue
        t = re.search(r"<title>(.*?)</title>", e, re.S)
        au = re.findall(r"<author>\s*<name>(.*?)</name>", e, re.S)
        res[m.group(1)] = {"title": re.sub(r"\s+", " ", t.group(1)).strip() if t else "",
                           "authors": [_norm(a).split()[-1] for a in au if _norm(a)]}
    return res


# ------------------------------------------------------------------ main
def _compare(entry, meta, ident):
    f = entry["fields"]
    probs = []
    if f.get("author") and meta.get("authors"):
        want = surnames(f["author"])
        have = [(_norm(h).split() or [""])[-1] for h in meta["authors"]]
        trunc = bool(re.search(r"\bothers\b|et al", f["author"], re.I))
        ok = have[:len(want)] == want if trunc else have == want
        if not ok:
            probs.append(f"authors differ: bib={want} record={have[:8]}")
    if f.get("title") and meta.get("title") and _title_sim(f["title"], meta["title"]) < 0.5:
        probs.append(f"title differs: bib='{f['title'][:60]}' record='{meta['title'][:60]}'")
    return probs


def check(text_files, bib_files, fetch=default_fetch, offline=False, max_urls=60):
    out = []
    arx_entries, doi_entries, seen_ids = [], [], {}
    # bib entries
    for rel, _p, text in bib_files:
        for e in parse_bib(text):
            f = e["fields"]
            aid = f.get("eprint") if re.fullmatch(r"\d{4}\.\d{4,5}", f.get("eprint", "")) else None
            if not aid:
                m = ARXIV_RX.search(f.get("journal", "") + " " + f.get("url", "") + " " + f.get("note", ""))
                aid = m.group(1) if m else None
            if aid:
                arx_entries.append((rel, e, aid))
            elif f.get("doi"):
                doi_entries.append((rel, e, f["doi"].strip()))
            elif f.get("url") or "url{" in f.get("note", ""):
                mm = URL_RX.search(f.get("note", ""))
                u = f.get("url") or (mm.group(0) if mm else None)
                if u:
                    seen_ids.setdefault(("url", u), (rel, e["line"]))
    # inline mentions
    for rel, _p, text in text_files:
        for i, ln in enumerate(text.split("\n"), 1):
            for m in ARXIV_RX.finditer(ln):
                seen_ids.setdefault(("arxiv", m.group(1)), (rel, i))
            for m in DOI_RX.finditer(ln):
                d = m.group(1).rstrip(".,;:)")
                seen_ids.setdefault(("doi", d), (rel, i))
            for m in URL_RX.finditer(ln):
                u = m.group(0).rstrip(".,;:")
                if "arxiv.org" in u or "doi.org" in u:
                    continue
                seen_ids.setdefault(("url", u), (rel, i))
    for rel, e, aid in arx_entries:
        seen_ids.setdefault(("arxiv", aid), (rel, e["line"]))

    if offline:
        for (kind, ident), (rel, line) in seen_ids.items():
            out.append(Finding("citation", UNVERIFIABLE, rel, line, f"{kind} {ident}: not checked (offline)"))
        return out

    # arXiv, batched
    ids = sorted(i for (k, i) in seen_ids if k == "arxiv")
    meta, net_fail = {}, set()
    for s in range(0, len(ids), 40):
        chunk = ids[s:s + 40]
        st, body = fetch("https://export.arxiv.org/api/query?max_results=50&id_list=" + ",".join(chunk))
        if st == 200:
            meta.update(parse_arxiv(body))
        elif st == 0:
            for i in chunk:
                net_fail.add(i)
                rel, line = seen_ids[("arxiv", i)]
                out.append(Finding("citation", UNVERIFIABLE, rel, line, f"arXiv:{i}: network unavailable"))
    bibby = {aid: (rel, e) for rel, e, aid in arx_entries}
    for i in ids:
        rel, line = seen_ids[("arxiv", i)]
        if i in bibby:
            rel, line = bibby[i][0], bibby[i][1]["line"]
        if i in net_fail:
            continue
        if i not in meta:
            out.append(Finding("citation", FLAGGED, rel, line, f"arXiv:{i}: not found on arXiv"))
            continue
        probs = _compare(bibby[i][1], meta[i], i) if i in bibby else []
        if probs:
            out.append(Finding("citation", FLAGGED, rel, line, f"arXiv:{i}: bib entry does not match arXiv record",
                               "; ".join(probs)))
        else:
            note = "matches bib authors/title" if i in bibby else "resolves (title: " + meta[i]["title"][:60] + ")"
            out.append(Finding("citation", VERIFIED, rel, line, f"arXiv:{i}: {note}"))

    # DOIs
    dois = {d: seen_ids[("doi", d)] for (k, d) in list(seen_ids) if k == "doi"}
    for rel, e, d in doi_entries:
        dois.setdefault(d, (rel, e["line"]))
    byd = {d: e for rel, e, d in doi_entries}
    for d, (rel, line) in dois.items():
        st, body = fetch("https://api.crossref.org/works/" + urllib.parse.quote(d, safe="/"))
        if st == 200:
            try:
                msg = json.loads(body)["message"]
                m2 = {"title": (msg.get("title") or [""])[0],
                      "authors": [_norm(a.get("family", "")) for a in msg.get("author", []) if a.get("family")]}
                probs = _compare(byd[d], m2, d) if d in byd else []
            except (ValueError, KeyError):
                probs = []
            if probs:
                out.append(Finding("citation", FLAGGED, rel, line, f"DOI {d}: bib entry does not match Crossref", "; ".join(probs)))
            else:
                out.append(Finding("citation", VERIFIED, rel, line, f"DOI {d}: resolves"))
        elif st == 404:
            st2, _ = fetch("https://doi.org/" + d, method="HEAD")
            out.append(Finding("citation", VERIFIED if st2 and st2 < 400 else FLAGGED, rel, line,
                               f"DOI {d}: " + ("resolves at doi.org (not in Crossref; metadata not compared)"
                                               if st2 and st2 < 400 else "does not resolve")))
        else:
            out.append(Finding("citation", UNVERIFIABLE, rel, line, f"DOI {d}: could not check (status {st})"))

    # URLs
    urls = [(u, v) for (k, u), v in seen_ids.items() if k == "url"][:max_urls]
    for u, (rel, line) in urls:
        st, _ = fetch(u, method="HEAD")
        if st in BLOCKED or st == 0 or st >= 500:
            st, _ = fetch(u, method="GET")
        if st and 200 <= st < 400:
            out.append(Finding("citation", VERIFIED, rel, line, f"URL resolves ({st}): {u[:90]}"))
        elif st in (404, 410):
            out.append(Finding("citation", FLAGGED, rel, line, f"URL is dead ({st}): {u[:90]}"))
        else:
            out.append(Finding("citation", UNVERIFIABLE, rel, line,
                               f"URL could not be checked (status {st or 'network error'}): {u[:90]}"))
    return out
