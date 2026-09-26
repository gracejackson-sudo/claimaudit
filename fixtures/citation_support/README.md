# Citation-support validation corpus

Real sources, recorded so the tests run offline. Rebuild with
`python validation/build_support_fixtures.py` (network; needs `pypdf`).

## What is stored

For each source: title, authors, year, abstract, which text tier was read, the
extraction's self-check numbers, and a **bag of token counts**. Not the text. That
is enough to ask "is this word in the paper" and cannot be turned back into it.

## What is real and what is reconstructed

| case | source | citing sentence | error |
|---|---|---|---|
| Vovk 2012 cited for "Mondrian" | real (arXiv 1209.2673; full text has no "Mondrian") | **reconstructed** from how it would have been cited | real, verified twice against the full text |
| Jin and Candes | real (arXiv 2403.03868; authors are Jin and Ren) | **reconstructed** (prose and bibliography variants) | real: the bibliography once named Candes |
| BenchPress "is from 2016" | real (arXiv 2606.24020, 2026) | **reconstructed** | real slip made by an outside reviewer in conversation, not in a document |
| BenchPress "233 benchmarks", "never mentions conformal" | real | **synthetic**, injected | injected, to test number and negation checks |
| Kurtic "above 99% for every scheme" | URL-only source | reconstructed | real overstatement; Layer A is not expected to catch it |
| controls | the paper's own citations and two docs' arXiv-linked sentences, verbatim | real | none known; a flag here is a false positive |

## Measured extraction reliability (validation/pdf_stress.py, ten arXiv papers)

Ground truth: the paper's arXiv HTML with formulas removed. Question: which words
that ARE in the paper would a PDF extraction make look absent?

| | rare words (8,524) | claim-like words: proper nouns, acronyms, compounds (2,824) |
|---|---|---|
| raw pypdf text, exact match | 6.0% missed | (not measured) |
| after normalization | 0.8% | |
| what the check actually does before saying "absent" | 0.4% | 1.0% |

Every one of the remaining misses was inspected. None is a word the PDF extraction
lost: they are arXiv-HTML front matter ("Affiliation", "Email"), BibTeX keys shown
where a bibliography failed to render, footnote numbers glued to words, an
ACM copyright footer absent from the PDF's text layer, and "8x48GB" rendered by the
HTML as "848GB". Sample size is small: ten papers, all from arXiv, LaTeX-generated.

## What the self-check catches (damage applied to real extracted text, 10 papers x 3 seeds)

| damage | refused |
|---|---|
| letter-spaced text | 30/30 |
| 3% garbled characters | 30/30 |
| text cut off at 40% | 30/30 |
| ligature glyphs lost | 15/30 (harm when not refused: 0.2%) |
| 30% of long words hyphen-split | 15/30 (harm when not refused: 0.2%) |
| 1%, 2%, 5% of words silently dropped | 0/30 each (harm: 0.7%, 1.2%, 2.7%) |
| 10%, 20%, 30% of words silently dropped | 4, 12, 23 of 30 (harm when not refused: 5.4%, 11.0%, 18.1%) |

**The limit, stated plainly:** random silent loss of up to about 10% of a text's
words is mostly NOT detected, and would make roughly half that fraction of present
words look absent. Measured real extraction loss is far below that, but the check
cannot see it if it happens. Truncation is caught only because the end-of-document
test exists; before it was added, a text cut at 40% was trusted and 34% of present
rare words would have been called absent.
