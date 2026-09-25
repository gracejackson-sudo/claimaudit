# Does weight-outlier kurtosis predict LoRA-merge forgetting?

*Working note. Every number below is reproduced by
`gpu/analyze_lora_kurtosis.py`, which reads the two raw artifacts directly --
none is hand-typed. Not published.*

## The question

A thread hypothesized that models with more per-channel weight outliers should
be more fragile under post-training perturbation.
