# The eight known errors — frozen regression corpus

## Read this first: what this is not

This corpus is **not** evidence for the "3 of 8" figure in the project README,
and it must not be cited as such.

The 3-of-8 result came from replaying the tool over the
`quant-delta-predictor` repository as it stood at commit `fd682b2`, before a
round of real fixes. **That repository state no longer exists.** The working
copy on disk is not a git repository, so `fd682b2` is not recoverable from it,
and the tree it contains is the post-fix state. The original measurement
cannot be repeated.

What this corpus *is*: a small set of documents that reproduces the same eight
errors, so that the tool's behaviour on them is pinned by a test. It is a
**drift tripwire**. If someone changes the scoring, the extraction or the word
lists, `tests/test_known_errors.py` fails, and that failure is a prompt to
re-validate honestly rather than to assume the change helped.

A reconstruction cannot tell you how the tool performs on a real repository.
It only tells you whether behaviour on these eight specific errors has
changed. Treat any count produced from it as a regression signal, not as a
measurement.

## Per-error provenance

Verbatim text was recovered from surviving artifacts where possible. Where it
was not, the error was rebuilt from its description in the handoff notes, and
that is stated plainly.

| id | error | text provenance |
|---|---|---|
| E1 | bibliography credits Candès for a paper by Jin & Ren | **verbatim** — the broken `@article{jin2024focal}` form; the true record is the recorded arXiv response in `records/` |
| E2 | "nobody would have published" | **verbatim** — recovered from the day-3 paper source, line 298 |
| E3 | stale figures across documents (138 vs 131 rows; 0.7323 vs 0.7545 MAE) | **values verbatim, sentences rewritten** — the real numbers and the real 138→131 and 0.7323→0.7545 transitions are documented in the post-fix `PROVENANCE.md` and `FINDINGS.md`, but the two near-identical sentences that made them a cross-file inconsistency did not survive, so they are reconstructed |
| E4 | target mean stated as −0.36 when the data means −0.39 | **verbatim** — recovered from the day-3 paper source, line 217; the −0.39 ground truth is the post-fix `paper/numbers.tex` |
| E5 | "thousands of paired measurements" when there were 817 | **reconstructed from description** — no artifact with this phrasing survives anywhere on disk. The 817 / 38 / 8 ground truth is verbatim from the post-fix repo |
| E6 | "none is hand-typed" was not true | **verbatim** — recovered from `KURTOSIS_LORA_FINDINGS.md` |
| E7 | "fabricated" describing a parser artifact | **verbatim** — recovered from the `AUDIT_DISCIPLINE.md` round-2 table row |
| E8 | "33–100% of variance on most benchmarks" | **verbatim** — recovered from the day-3 paper source, line 215 |

Sources referred to above, as of 25 Sept 2026:

* day-3 paper source — `ef day 3/EF-Day-3/00 - Paper source (neurips_main.tex)`,
  a genuine pre-fix artifact (E2 at line 298, E4 at 217, E8 at 215)
* post-fix repo — `quant_delta_predictor/`, for ground truth only
  (`paper/numbers.tex:39` gives `\TargetMean` as `-0.39`;
  `KURTOSIS_LORA_FINDINGS.md:6` for E6; `AUDIT_DISCIPLINE.md:12` for E7;
  `PROVENANCE.md:89` for the 138→131 transition)
* `records/arxiv_2403.03868.atom` — the arXiv API response for the E1 paper,
  fetched 25 Sept 2026. Authors: Ying Jin, Zhimei Ren. Stored so the citation
  check runs offline and deterministically

`corpus/out/target_deltas.csv` is synthetic. It exists so E4 is a fair test:
its `delta` column means exactly −0.39, so the correct value *is* present in
the data and computable, and the tool still does not catch the −0.36 in the
prose. The original had 817 rows; ten are enough to make the point.

## What the tool does on this corpus

Caught, 3 of 8: E1 by the citation check, E2 by the overclaim scan, E3 by the
consistency check (as two findings, one per stale number).

Missed, 5 of 8: E4, E5, E6, E7, E8. Each needs either a computed value or a
judgement about whether a sentence's characterisation matches reality. These
are what the unbuilt LLM-assist layer was scoped to handle.

Two of the misses are near misses worth knowing about, and neither has been
"fixed", because fixing them would change the headline number without any way
to re-validate it:

* **E6** — "none is hand-typed" is a universal negative, which is a category
  the overclaim scan does cover. The pattern is `none of`, so `none is` slips
  through by one word.
* **E5** — "thousands of" contains no digits, so no claim is extracted at all.
  Nothing downstream ever sees it.

There is also **one incidental flag** on this corpus that is not one of the
eight: the overclaim scan fires on "guarantee" in "the guarantee does not
transfer" — a sentence that is *disclaiming* a guarantee. The negation
detector only looks backwards from the match, so it misses a negation that
follows. `tests/test_known_errors.py` pins this count at 1 so that new
incidental noise also shows up as a failure.
