The workspace contains `stats.py`, a small statistics module with several bugs. Its functions return wrong results for common inputs.

Your task:

1. Diagnose every bug in `stats.py` (`mean`, `median`, `variance`).
2. Write a corrected module to `outputs/solution.py` exporting the same three functions with the same signatures. The functions must be correct for empty-free numeric lists, single-element lists, and lists with an even number of elements. `variance` must be the population variance (divide by N).
3. Write `outputs/NOTES.md` describing each bug you found and how you fixed it.

Do not modify the original `stats.py`.
