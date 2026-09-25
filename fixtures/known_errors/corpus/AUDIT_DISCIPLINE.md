# Audit discipline

What each round of adversarial review caught.

| round | what the audit caught |
|---|---|
| 1 | `diffusiongemma-26B` matched into `gemma-2` by substring; that card also reported accuracy on a 0-1 scale |
| 2 | Llama-4 cards order columns `[Recovery, base, quant]`, producing a fabricated -68pp delta; `MATH-500` silently matched as `Math-Lvl-5` |

Each was found by *trying to break a result that looked clean*, not by testing
that it worked.
