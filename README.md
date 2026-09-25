# claimaudit

A **first-pass** checker for the claims, numbers and citations in a folder of
documents. It surfaces things for a human to look at. It is not an audit, it
does not judge whether anything is true, and it will miss most errors a careful
reader would catch.

It was built by generalizing a week of manual audit work. That work relied on
human judgment plus long back-and-forth reasoning. This tool automates only the
mechanical parts of it.

## What it checks

| check | what it does | tier |
|---|---|---|
| `overclaim` | flags absolute or superlative words ("first", "proven", "always", "nobody", "beats", ...) for you to review. It never decides whether the claim is true. | free |
| `source` | finds numeric claims in md/txt/tex files and looks for a matching value (a cell or a simple aggregate) in CSV/JSON files in the same folder | licensed |
| `citation` | finds arXiv IDs, DOIs and URLs; checks they resolve; for `.bib` entries, compares authors and title with the arXiv / Crossref record | licensed |
| `consistency` | flags similar sentences in different files that state different numbers | licensed |

Every finding is `VERIFIED`, `FLAGGED` or `UNVERIFIABLE`:

* `VERIFIED` means *matched something in your files or a public record*. It does not mean the claim is correct.
* `FLAGGED` means *look at this*. It does not mean the claim is wrong.
* `UNVERIFIABLE` means there was nothing safe to compare against. The default report prints only a count; use `--show-unverifiable` for the list.

The text report lists at most 200 items per check. When it trims, it says how
many it is hiding, and the summary totals always count every finding. `--json`
carries the complete set.

A `[scan]` section appears when something could not be read: a file the
process has no permission to open, or a CSV too long to index. Those items are
always listed in full rather than collapsed into a count, because a file that
was never read must not look like a file that passed. If no readable documents
are found at all, that is reported and the exit code is 1 — pointing the tool
at the wrong folder should not look like a clean result.

## Install and use

    pip install claimaudit
    claimaudit check path/to/folder
    claimaudit check paper.tex --only overclaim
    claimaudit check . --exclude 'data/*' --json

Exit code is 1 if anything is flagged (use `--exit-zero` for CI that should not fail), 2 on usage errors.
A `.claimauditignore` file (one glob per line) in the folder is also honored.

## What it can and cannot do (measured, small samples)

We replayed it on one repository as it stood before a round of fixes, against
eight errors we already knew were real:

* **Caught (3 of 8):** a wrong co-author in a bibliography entry, "nobody" language in a paper, and stale numbers that differed between two documents.
* **Missed (5 of 8):** a wrong mean quoted in prose, "thousands of measurements" when there were 817, an overclaim about its own verification, a loaded word ("fabricated"), and a loosely stated variance range. These need judgment or computation it does not attempt.

That replay was a one-off and **cannot be repeated**: the repository state it
ran against has not been preserved, so the 3-of-8 figure stands as a report of
something we did once, not as a benchmark anyone can re-run. A reconstruction
of the same eight errors is checked in under `fixtures/known_errors/` with
`tests/test_known_errors.py` pinning the tool's behaviour on each one. That
exists to catch drift if the scoring changes — it is not independent evidence
for 3 of 8, and `fixtures/known_errors/README.md` says so and records which
parts of it are verbatim and which were rebuilt from a description.

Numeric verification is deliberately conservative. On that repository only
about 4% of numeric claims were VERIFIED, mostly where the repo had a registry
file of computed values. In a hand check of 16 randomly sampled VERIFIED items,
15 were legitimate matches. That is one repository and a small sample, so treat
both numbers as anecdotes, not benchmarks. An earlier, looser version produced
mostly coincidental matches; the current rule requires more matching words for
less specific numbers (short numbers match by chance).

It does **not**: follow a calculation, read numbers out of figures or PDFs,
check that a cited paper supports the sentence citing it, or verify anything
that has no matching CSV/JSON in the folder.

## Privacy

Files are read locally and never uploaded. Network requests go only to arXiv,
Crossref, the URLs found in your documents, and the license server. There is
no telemetry. Citation lookups are cached in `~/.cache/claimaudit`.

## Licensing

The `overclaim` check is free. `source`, `citation` and `consistency` need a
license key (`claimaudit activate <KEY>`), validated against Lemon Squeezy and
cached for offline use for up to 7 days. This is an honor-system gate: it runs
in readable Python on your machine and is not tamper-proof.

## Not in v0.1

An optional LLM-assisted mode (bring your own API key) to judge whether a
citation supports its sentence and to resolve ambiguous source matches is
planned, not built.
