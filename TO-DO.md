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

- [x] **F4. Commit now.** Done (commit 9ff28ac).
- [x] **F1. Make keyword search precise.** Applied F1a + F1d:
  - [x] a. weighted RRF (keyword weight 0.3) — pipeline R@5 50% → 60%
  - [ ] ~~b. AND between tokens~~ — not needed
  - [ ] ~~c. match only name columns~~ — not needed
  - [x] d. skip keyword fusion when query has no identifier-like token — pipeline R@5 60% → 73.3%
- [x] **F2. Per-class diversity:** max 2 chunks per fqn, no reordering.
- [ ] ~~**F3. Case-insensitive boost**~~ — skipped, pipeline already matches dense-only.
- [x] **F5. Record** — see table below.

## Results

| Change | Dense R@5 | Pipeline R@5 | Pipeline MRR |
|---|---|---|---|
| Round 5 baseline | 73.3% | 50.0% | 0.374 |
| F1a weighted RRF (0.3) + F2 dedup | 73.3% | 60.0% | 0.399 |
| + F1d skip FTS for NL queries | 73.3% | 63.3% | 0.438 |
| + dedup sort bug fix | 73.3% | **73.3%** | **0.505** |
