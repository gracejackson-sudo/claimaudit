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
| `registry` | checks numbers you have tagged in the text against a registry of values your own pipeline computed | licensed |
| `benchmark` | checks that a stated gain matches the two numbers it is stated between | licensed |
| `seeds` | checks that a stated mean, spread and seed count can describe the same runs | licensed |

Every finding is `VERIFIED`, `FLAGGED` or `UNVERIFIABLE`:

* `VERIFIED` means *matched something in your files or a public record*. It does not mean the claim is correct.
* `FLAGGED` means *look at this*. It does not mean the claim is wrong.
* `UNVERIFIABLE` means there was nothing safe to compare against. The default report prints only a count; use `--show-unverifiable` for the list.

The text report lists at most 200 items per check. When it trims, it says how
many it is hiding, and the summary totals always count every finding. `--json`
carries the complete set.

The `[scan]` section also warns when much of what it read looks *collected*
rather than written by you — scraped pages, vendored docs, generated cards.
Auditing those treats someone else's words as your claims; on one real
repository they produced 85% of all findings. claimaudit never excludes them
for you, because every rule safe enough to apply automatically turned out not
to be: `.gitignore` is used for private drafts as often as for generated
files, and YAML frontmatter marks hand-written pages in Jekyll, Hugo, Quarto
and Obsidian. It reports the guess, says it is a guess, and prints the
`--exclude` you would need. A wrong guess costs you a line of output, never a
finding.

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

It reads `.md`, `.markdown`, `.txt`, `.tex`, `.rst`, `.bib`, `.ipynb` and
`.docx`. In a notebook only markdown cells are read: code-cell outputs are
full of numbers nobody wrote as a claim, and reading them would bury the real
ones. PDFs are not read at all, because doing it properly needs a dependency
and this tool has none beyond the standard library.

Exit code is 1 if anything is flagged, 0 if not, and 2 on usage errors — so CI
can tell "the documents have problems" from "the tool was pointed at nothing".
`--fail-on unverifiable` also fails when something could not be checked, and
`--fail-on never` always exits 0. A `.claimauditignore` file (one glob per
line) in the folder is also honored.

### Tagging claims against computed values

The `source` check has to guess which value in your data a number refers to,
which is why it verifies only about 4% of them. If you are willing to say
which value a number is, nothing has to be guessed. Tag it where you write it:

    Built on 817<!-- claim: n_rows = 817 --> published evaluations.

and in LaTeX, as a comment on its own line:

    % claim: target_mean = -0.39  (mean of the delta column)

Then have whatever script computes your results write a registry beside the
documents, as `claimaudit-claims.json` or `claimaudit-claims.csv` (or pass
`--registry`):

    {"n_rows": 817,
     "mae": {"value": 0.7545, "tolerance": 0.0001, "how": "leave-one-family-out"}}

claimaudit only reads that file. It never imports or executes anything, so a
registry cannot make the tool run your code. JSON and CSV are the only formats
for the same reason.

Three things get checked, and the first is the one people miss when they write
this themselves: the number in the prose against its own tag, so editing a
sentence and forgetting the tag is caught; the tag against the registry; and
that the label exists at all. Registry entries no document uses are reported
too, which usually means a claim was deleted and nobody noticed.

### Running it in CI and as a pre-commit hook

`.pre-commit-hooks.yaml` ships in the repo and `examples/github-action.yml` is
a workflow to copy. Both split the checks in two, and the split is the whole
point. Measured on one real 620-file repository:

| checks | flagged | time |
|---|---|---|
| `registry,benchmark,seeds` | 0 | 2.8–4.5s |
| `overclaim` | 592 | 2.0–3.1s |

The times are a range across repeated runs on one laptop, not a benchmark.
They move by half again between runs depending on what else the machine is
doing, so a single figure would be a tidier number than we actually have.

The first group compares numbers against numbers, so a failure means the
arithmetic really does not hold and it is fair to block a commit on it. The
second is a word list over ordinary prose. It is worth reading and hopeless as
a gate: a hook that is red on every commit teaches people to pass
`--no-verify`, after which it protects nothing. Citation checks are advisory
for a different reason — they fail when arXiv is having a bad day, and a
required check that breaks for reasons nobody can fix gets switched off.

`--json` output carries `schema_version`, which only changes if a consumer
would have to change with it.

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

### What the newer checks changed: nothing, on those eight

`registry`, `benchmark`, `seeds`, notebook and `.docx` reading were added
after that replay. Re-running the frozen corpus with all of them enabled
produces **the same 3 of 8 and not one extra flag**. The headline number is
unchanged and none of the new work should be quoted as improving it.

One caveat in the other direction, because it is a different question. If the
author tags the claim and their pipeline writes out the computed value, the
registry check catches E4 as well — the paper says the target has mean
-0.36pp where the data means -0.39pp. That is **4 of 8, and only for an author
who opts in**, which is a different claim from the headline and is measured by
a test (`test_what_annotating_the_corpus_adds`) rather than asserted. The
remaining four are not reachable this way: two are vague rather than wrong, so
there is no single value to bind to, one is a word choice, and one is a
citation.

The arithmetic checks are built to be quiet. On the same 620-file repository
`benchmark` fires once and `seeds` not at all, because that corpus rarely
states a gain and its two numbers in one sentence, and never lists per-seed
values. One false positive did turn up during development — `89.0% at 3.93pp
versus 88.3% at 5.48pp` read as a gap when it is two pairs of coverage and
width — and it is now a regression test. Everything is compared as intervals
over the precision the numbers are printed to, so a rounding difference cannot
be reported as an error. Both the sample and population conventions for spread
are accepted, because papers use both. So are all three readings of a `±` —
standard deviation, standard error and 95% confidence half-width — unless the
sentence says which it means, in which case that one is used and the report
says so.

Where two results with error bars are called a significant improvement and the
bars overlap, that is reported as `UNVERIFIABLE`, not flagged. Overlapping
bars do not prove the result wrong; they mean the sentence does not settle it
either way. If a test is named, nothing is said at all.

## Privacy

Files are read locally and never uploaded. Network requests go only to arXiv,
Crossref, the URLs found in your documents, and the license server. There is
no telemetry. Citation lookups are cached in `~/.cache/claimaudit`.

## Licensing

The `overclaim` check is free. `source`, `citation`, `consistency`, `registry`,
`benchmark` and `seeds` need a license key (`claimaudit activate <KEY>`), validated against Lemon Squeezy and
cached for offline use for up to 7 days. This is an honor-system gate: it runs
in readable Python on your machine and is not tamper-proof.

## Not in v0.1

An optional LLM-assisted mode (bring your own API key) to judge whether a
citation supports its sentence and to resolve ambiguous source matches is
planned, not built.
