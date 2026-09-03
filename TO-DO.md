# TO-DO — Round 3 (after auditing the C-list)

Evidence and per-item verdicts are in `TO-FIX.md` under "ROUND 3". This is the checklist.
Run indexing jobs one at a time. Everything else is safe to do while one runs.

## Where things stand

- Fixed and verified: C2, C3, C4, C8, C13, C14.
- Code done but data stale: C10 (inherited methods) — API was rebuilt before the parser edit.
- Not done: C1 (mods never rebuilt).
- D2 (snapshot CLI) fixed, D3 (eval expectations) fixed, D7 (async dead code) cleaned up.
- D4 (identifier boost), D5 (reranker deleted), D6 (per-source slots), D8 (FQN resolution) done.
- Current eval: needs re-run after D3 fixes.

## Checklist

### Do first

- [x] **D9. Commit.** `git add -A && git commit`. Zero commits exist.
- [x] **D2. Fix `cli.py snapshot`.** Signatures updated, `--source` removed, stale index record cleaned.
- [x] **D3. Fix eval expectations.** 16 nonexistent names replaced with verified ones. `--rerank` branch simplified.
- [x] **D7. Dead code cleanup.** Async duplicates deleted in decompiler, scraper, github_scraper. Empty dirs cleaned.
- [ ] **D1. Rebuild both stale collections** (sequentially):
  - [ ] `python cli.py index-mods --min-stars 2 --max-repos 30`
  - [ ] `python cli.py index-jar "C:\Users\User\Desktop\Hythaum\server\HytaleServer.jar" --force`
  - [ ] Verify: `fts_hytale_mods` table exists; `mods.embed_model` in `data/meta.json`;
        some `class_overview` chunks contain `// Inherited methods:`; `jar_hash` is no longer empty.
- [ ] Re-run `python eval/run_eval.py` and record baseline numbers.

### Then

- [x] **D4. Exact-identifier boost.** `_exact_identifier_boost()` in server.py checks query tokens against FTS class_name/method_name, fetches matching chunks and prepends them.
- [x] **D5. Reranker deleted.** `reranker.py` removed. All imports/calls removed from server.py, eval/run_eval.py, pyproject.toml.
- [x] **D6. Per-source slots** in `search_hytale_docs` via `_enforce_source_slots()` (≥2 api, ≥2 guides, ≥1 mod when available).

### Cleanup

- [x] **D8.** `_resolve_extends()` resolves extends to FQN via imports/package (5-step priority). 96% resolution rate. `extends_fqn` stored in metadata.
