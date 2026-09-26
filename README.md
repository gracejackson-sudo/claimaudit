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
| `overclaim` | flags priority constructions, universal negatives, proof language and absolute superlatives ("we are the first", "nobody", "proven", "best ever") for you to review. Ordinary comparatives ("outperforms baselines", "SOTA on eleven tasks") are left alone. It never decides whether the claim is true. | free |
| `source` | finds numeric claims in md/txt/tex files and looks for a matching value (a cell or a simple aggregate) in CSV/JSON files in the same folder | licensed |
| `citation` | finds arXiv IDs, DOIs and URLs; checks they resolve; for `.bib` entries, compares authors and title with the arXiv / Crossref record; `.bbl` files are read for identifiers only | licensed |
| `support` | **opt-in, experimental** (`--only support`). For sentences that cite an arXiv paper or DOI, looks for things the sentence asserts that are absent from the source's full text: a name, a date, a figure, a distinctive word such as "Mondrian". Reports `FLAGGED` or `UNVERIFIABLE` only, never `VERIFIED`, unless you also pass `--llm`. See "Citation support" below | licensed |
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

claimaudit is **not on PyPI**; `pip install claimaudit` will not find it.
Install from a copy of this source folder (Python 3.9 or newer, no
dependencies):

    cd claimaudit
    python3 -m pip install -e .
    claimaudit check path/to/folder

`python3 -m claimaudit check path/to/folder` does the same thing if the
`claimaudit` script is not on your `PATH`. More examples:

    claimaudit check paper.tex --only overclaim
    claimaudit check . --exclude 'data/*' --json

It reads `.md`, `.markdown`, `.txt`, `.tex`, `.rst`, `.bib`, `.bbl`, `.ipynb` and
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

### The authored-voice assumption

Every sentence the tool can read is audited as though the author is asserting
it. It cannot tell authored claims from quoted, templated, or machine-generated
text. That is not an `overclaim` quirk — it is true of every check that reads
prose.

On the fifteen-paper run this produced real flags on text the authors did not
write: a few-shot prompt exemplar ("The coin was flipped by no one.") was a
universal negative, a dataset row ("Jonas Valanciunas beat the buzzer.") was a
superiority claim, and a language model's own generated sample was a novelty
claim. The workaround today is `--exclude` on those files. There is no
automatic skip, because a rule safe enough to apply without a human would
also drop authored prose we have no way to recognise.

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
Crossref and the URLs found in your documents, and only from the `citation` and
`support` checks (`--offline` turns them off). The `support` check also downloads the
full text of the papers you cite. With `--llm`, and only then, a sentence from your
document and short excerpts of the cited paper are sent to api.anthropic.com. There is no license server and no
telemetry. Citation lookups are cached in `~/.cache/claimaudit`.

## Licensing

claimaudit is released under the [MIT license](LICENSE). That covers all of the
code, including the checks that ask for a key: the license grants use of every
check, and the key below is a convenience and a way to support the project, not
a legal condition of use.

The `overclaim` check is free. `source`, `citation`, `consistency`, `registry`,
`benchmark` and `seeds` need a license key. The landing page is
[`docs/index.html`](docs/index.html).

To buy, pay through the Stripe Payment Link:

<!-- Replace this with the Stripe Payment Link. One line. Same string in claimaudit/license.py and docs/index.html. -->
https://buy.stripe.com/8x2dRb5ND1oP75V1DWgEg00

After payment, Stripe redirects you to [`docs/success.html`](docs/success.html),
which shows the install steps and the key. For now that is **one shared
early-access key**, the same for every buyer, and anyone with that page's
address can see it. Then:

    claimaudit activate <KEY>

That writes the key to `~/.claimaudit/license.json` (or `$CLAIMAUDIT_HOME`).
Setting `CLAIMAUDIT_LICENSE_KEY` in the environment works too, which is what
CI uses. `claimaudit status` shows which tier you are on.

**This is an honor system, and the MIT license makes that explicit.** The key is a shared token, not a
cryptographic license. Any non-empty key unlocks the paid checks; an empty or
all-whitespace key does not. Nothing is checked against a server — `activate`
stores the key and sends nothing anywhere, and no check ever phones home.
Anyone who reads the code can bypass it.

## Citation support (`--only support`, experimental)

`citation` checks that a reference exists. `support` asks a harder question: for a
sentence that cites a source and says something specific about it, is that thing
there? It downloads the cited paper's full text (arXiv's HTML version, or the PDF
if you install `pypdf`) and looks for what the sentence asserts. It flags:

- a name the sentence gives for the source's authors who is not on the record,
  or a bibliography author list that disagrees with the record;
- a date the sentence gives for the source that the record contradicts;
- a proper noun, acronym or compound the sentence attributes to the source that
  appears nowhere in its text (the Vovk 2012 / "Mondrian" case);
- a specific figure that is not in the text;
- "X never mentions Y" when the text does mention Y.

**It cannot confirm support.** A word being in a paper says nothing about whether
the paper backs the claim, so the check never says `VERIFIED`; absence is
evidence and presence is not. Most sentences will come back `UNVERIFIABLE`,
including every overstatement whose words are all present (for example "recovery
above 99% for every scheme" against a source that says so only for one benchmark
average). Judging those needs a reader, or a reasoning layer that is not built.

**When it refuses to flag.** It only says a word is absent when it read the full
text and the text passed a self-check: the title's and abstract's words must be
found in it, the end of the document must be present, and no page may have failed.
A source it could not read that way, a citation with no arXiv id or DOI, and any
sentence citing several sources of which one is unreadable, all stay
`UNVERIFIABLE`. Because extraction can be wrong in ways that produce false
"not found" results, this was measured on ten real papers (see
`fixtures/citation_support/README.md`): about 1 in 250 rare words in a PDF's
extracted text is missed after normalization, and every miss examined was an
artifact of the reference text rather than a lost word. That is one sample of
arXiv papers; scanned or unusual PDFs are untested.

### Optional: a model reads the passage (`--llm`) &mdash; built, not yet validated on live runs

For sentences the plain check could not decide, `--only support --llm` sends the
sentence and a few short excerpts of the cited paper to the Anthropic API (key from
`ANTHROPIC_API_KEY`, model from `--llm-model` or `CLAIMAUDIT_MODEL`; nothing else leaves
your machine, and the key is only ever sent in a request header) and asks whether the
paper says what the sentence says. The answer is one of:

- `SUPPORTED` (reported as `VERIFIED`), `CONTRADICTED` (reported as `FLAGGED`), or
- `UNCLEAR — NEEDS HUMAN REVIEW`, which is the default for anything else.

It is built so that a confident wrong answer is hard to produce. The excerpts are chosen by
plain word overlap, with no model involved. `SUPPORTED` and `CONTRADICTED` both need a quote,
and the quote must be found, word for word, in the excerpts that were shown; an invented or
paraphrased quote is thrown away. A `SUPPORTED` answer must also carry every specific figure
in the sentence, share the sentence's own words, and survive a second call whose only job is
to find a passage that narrows or contradicts it. A failed call, an unreadable reply, a source
that was not fully read, or a sentence citing several sources all end as `UNCLEAR`.

What it cannot do: it cannot find a qualifying passage it was not shown; it cannot tell that a
quoted passage means something else in its context; it can never confirm that a paper "never
mentions" something; and text in the source aimed at the model can mislead it. `SUPPORTED`
means "a verbatim passage matches the sentence's words and figures and nothing shown narrowed
it": a receipt for a person to glance at, not a proof. A run costs a few calls per sentence;
`--llm-max-calls` (default 25) caps it and the token count is printed.

Status: the mechanics are tested against scripted models, including ones that invent quotes,
quote something irrelevant, ignore a figure, or obey text planted in the source, and passage
selection reached the passage a person had identified in 15 of 15 real cases. It has **not yet
been run against a live model on those cases**; `validation/llm_validate.py` does that, and
this section will say what it found once it has.

## Not in v0.1

Using a model to resolve ambiguous source matches for the `source` check is
planned, not built. (A model reading cited passages is `--llm`, above.)
