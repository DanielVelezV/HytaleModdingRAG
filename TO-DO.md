# TO-DO — Round 5 (after auditing the E-list)

Evidence in `TO-FIX.md` under "ROUND 5". E1–E4 are all verified done. 51 tests pass.

## The headline

The pipeline eval works, and it shows the hybrid layer HURTS:

| Mode | R@5 | R@10 | MRR |
|---|---|---|---|
| Dense-only | 73.3% | 76.7% | 0.487 |
| Pipeline (hybrid + boost + slots) | 50.0% | 70.0% | 0.374 |

Seven questions dense gets right are lost after keyword fusion; none improve. Cause: the FTS query is
an OR of common words, BM25 favours short method chunks, RRF weights keyword equal to dense, and no
per-class diversity cap. Details and three worked examples in TO-FIX.md.

Rule from here on: every ranking change is judged by `python eval/run_eval.py --pipeline`.
Keep it only if pipeline R@5 ≥ dense-only R@5. Record numbers in the F5 table in TO-FIX.md.

## Checklist

- [ ] **F4. Commit now.** 7 files uncommitted.
- [ ] **F1. Make keyword search precise.** Try, measure, keep the best:
  - [ ] a. weighted RRF (keyword weight 0.3, configurable)
  - [ ] b. AND between tokens, OR fallback when < 3 rows
  - [ ] c. match only fqn/class_name/method_name columns for natural-language queries
  - [ ] d. skip keyword fusion entirely when the query has no identifier-like token
- [ ] **F2. Per-class diversity:** max 2 chunks per fqn in the final list, prefer class_overview.
- [ ] **F3. Case-insensitive boost** for lowercase tokens ≥5 chars matching a class name. Measure.
- [ ] **F5. Record** dense vs pipeline R@5 after each change in TO-FIX.md.
