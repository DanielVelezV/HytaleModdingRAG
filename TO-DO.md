# TO-DO — Round 3 (after auditing the C-list)

Evidence and per-item verdicts are in `TO-FIX.md` under "ROUND 3". This is the checklist.
Run indexing jobs one at a time. Everything else is safe to do while one runs.

## Where things stand

- Fixed and verified: C2, C3, C4, C8, C13, C14.
- Code done but data stale: C10 (inherited methods) — API was rebuilt before the parser edit.
- Not done: C1 (mods never rebuilt), C7 (slots), C9 (reranker).
- D2 (snapshot CLI) fixed, D3 (eval expectations) fixed, D7 (async dead code) cleaned up.
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

- [ ] **D4. Exact-identifier boost.** Known class/method names from FTS; exact query-token match
      goes first, before rerank.
- [ ] **D5. Reranker: install it or delete it.** Silent no-op is the worst option.
- [ ] **D6. Per-source slots** in `search_hytale_docs` after rerank (≥2 api, ≥2 guides, ≥1 mod).

### Cleanup

- [ ] **D8.** Resolve `extends` to an FQN via imports/package in `java_parser._get_inherited_methods`
      (first suffix match is ambiguous for `Builder`, `Data`, `Config`); store `extends_fqn` and use it
      for subclass lookup.
