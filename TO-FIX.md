# TO-FIX — HytaleModdingRag audit (2026-09-03)

Audit of the MCP RAG server in this directory. Findings were verified by running the
parsers in `indexer.py` over 400 random decompiled Hytale files, scanning the ChromaDB
collections, and querying the live MCP server. Line numbers refer to the files as of
2026-09-03 06:35.

Overall verdict: solid v1. Architecture is right (decompiled jar = ground truth, guides =
how-to, GitHub mods = patterns, MCP instructions enforce that trust order). The problems
are retrieval quality and indexing pipeline behavior, not design.

Work through Part A (bugs) roughly in order, then Part B (improvements) in priority order.
Do NOT start any re-index while another process is already indexing (check with
`Get-Process python`). The `data/` directory is ~2 GB; never commit it.

---

## Part A — Verified bugs

### ~~A1. Constructors are never indexed  (HIGH)~~ DONE
- Where: `indexer.py:201` `_extract_methods`, regex group 4 requires a return type before the name.
- Evidence: 0 constructors captured across 400 sampled files (`ctor_captured: 0`).
- Fix: allow `Modifiers? ClassName(...)` with no return type. Tag chunk metadata `type: "constructor"`.
  Long-term this is solved by A4/B4 (real Java parser).

### ~~A2. Wildcard generics (`?`) break field and method regexes  (HIGH)~~ DONE
- Where: `indexer.py:163` (signatures), `indexer.py:173` (fields), `indexer.py:208` (methods).
  Character class `[\w<>\[\], ]` has no `?`.
- Evidence: `AssetRegistry.storeMap` (`Map<Class<? extends JsonAssetWithMap>, AssetStore<?, ?, ?>>`)
  is absent from the class overview. Any method returning a wildcard type is dropped.
- Fix: add `?` (and `&` for intersection bounds) to the character classes. Or B4.

### ~~A3. Class declaration line missing in ~9% of overviews, inheritance missed in ~12%  (HIGH)~~ DONE
- Where: `indexer.py:155` — `\s*(public|protected|private|abstract|final)?\s*(class|...)` allows only ONE
  modifier, so `public final class`, `public abstract class`, `public static final class` never match.
  `indexer.py:122` `_extract_inheritance` also fails on wildcard generics in `extends`/`implements`.
- Evidence: sample of 400 files: `overview_no_decl: 36`, `inheritance_missed: 48`.
- Fix: `(?:(?:public|protected|private|abstract|final|static|sealed|non-sealed|strictfp)\s+)*`
  and allow `?` in the generic capture. Or B4.

### ~~A4. Inner / nested classes are invisible  (HIGH)~~ DONE
- Where: `indexer.py:64` `parse_java_files` — one class per file, `class_name = java_file.stem`.
  CFR emits nested types inline in the outer file.
- Evidence: 518 nested type declarations in the 400-file sample. Their methods are attributed to the
  outer class; `get_class_info("InnerName")` returns "not found".
- Fix: emit a separate `class_overview` chunk per nested type with `fqn = Outer.Inner` and
  `class_name = Inner`, and attribute methods to the nearest enclosing type. Or B4.

### ~~A5. Private members are indexed as noise  (MEDIUM)~~ DONE
- Where: `indexer.py:206` and `indexer.py:172` include `private`.
- Evidence: live query `search_hytale_api("register a command")` returned
  `private void registerTaskRef()` from `ObjectiveTask` as result #2.
- Fix: skip `private` methods and fields by default (keep `public`, `protected`, package-private for
  overriding). If you keep them, add `visibility` metadata and let search default to
  `{"visibility": {"$ne": "private"}}`.

### ~~A6. Guide text is mangled (words glued, code blocks destroyed)  (CRITICAL)~~ DONE
- Where: `scraper.py:90` `el.get_text(strip=True)` and `scraper.py:80` `for el in main.children`.
- Evidence: stored content of "Creating custom blocks" reads
  `Enable asset packs inmanifest.jsonby settingIncludesAssetPacktotrue` followed by a JSON sample with
  all whitespace removed. Pages average 1.94 sections because only TOP-LEVEL children of `<main>` are
  walked, so headings nested inside wrapper divs are never seen. "NPC Meta" is one 199,101-char blob
  split by fixed 1500-char windows.
- Fix:
  1. Walk `main.find_all(["h1","h2","h3","h4","p","pre","ul","ol","table","blockquote"])` in document
     order, not `.children`. Skip elements nested inside an already-emitted `<pre>`/`<li>`.
  2. Render `<pre>`/`<code>` blocks as fenced code with original whitespace (`pre.get_text()` WITHOUT
     strip, wrapped in triple backticks). Use `get_text(separator=" ", strip=True)` for inline text and
     `"\n"` for lists/paragraphs.
  3. Drop boilerplate lines ("Edit on GitHub", "Written by...", breadcrumb "Server Plugins").
  4. Re-run `scrape_guides` afterwards and spot-check 5 pages with code.

### ~~A7. nomic-embed-text task prefixes are missing  (HIGH, cheap)~~ DONE
- Where: `indexer.py:22` `_OllamaEmbedding.__call__` embeds raw text for both documents and queries.
- Why: nomic-embed-text is trained with task prefixes. Documents should be embedded as
  `"search_document: " + text` and queries as `"search_query: " + text`. Omitting them hurts recall.
- Fix: Chroma 1.x `EmbeddingFunction` supports `embed_query()` separately from `__call__()`; implement
  `__call__` with `search_document:` and `embed_query` with `search_query:`. Requires a full re-index of
  all three collections (embeddings change). Do this together with the A6 re-scrape.

### ~~A8. Indexing blocks the MCP event loop  (HIGH)~~ DONE
- Where: `server.py:62` `index_jar`, `server.py:130` `scrape_guides`, `server.py:157` `index_github_mods`
  are `async def` but call blocking code: `subprocess.run` in `decompiler.py:63`, synchronous Chroma
  `add`, synchronous Ollama HTTP in `indexer.py:25`.
- Effect: the server cannot answer any tool call for the whole run (tens of minutes); MCP clients time
  out. (The owner is currently re-indexing via a `python -c` script for this reason.)
- Fix: see B6 (separate CLI + background job). Minimum: run the blocking work in
  `asyncio.to_thread(...)` and return immediately with a job id; expose progress in `get_index_status`.

### ~~A9. Rebuild deletes the collection before re-adding  (HIGH)~~ DONE
- Where: `indexer.py:377`, `:398`, `:417` — `collection.delete(where=...)` then batched `add`.
- Effect: the index is empty/partial for the whole run (observed: 2,360 -> 3,190 chunks mid-rebuild).
- Fix: index into a temp collection `hytale_api__building`, then on success delete the old collection
  and rename via `collection.modify(name=API_COLLECTION)`. Search keeps working during rebuilds.

### ~~A10. Embedding batch size 10  (MEDIUM)~~ DONE
- Where: `indexer.py:379`, `:400`, `:419` `batch_size = 10`.
- Effect: ~27k sequential Ollama round-trips for a 267k-chunk index.
- Fix: batch 64-128 (Ollama `/api/embed` accepts a list). Optionally run 2-4 concurrent batches.

### ~~A11. Snapshot format is JSON floats  (MEDIUM)~~ DONE
- Where: `snapshots.py:80-89`.
- Evidence: `hytale_api_20260903_113622_full-267k-all-packages.json.gz` is 1.9 GB; the process needed
  2.2 GB RAM. (Verified the file is complete: 267,611 ids. The 17 MB `..._113510_...` file is an orphaned
  failed attempt not listed in `index.json` — safe to delete.)
- Fix: write embeddings as `numpy.save` float32 `.npy` (267k x 768 x 4 B ~ 820 MB uncompressed) plus a
  small JSONL for ids/documents/metadatas. Stream in pages instead of holding everything in RAM.
  Or simply `shutil.copytree` the Chroma directory.

### ~~A12. `get_class_hierarchy` matching is fuzzy  (MEDIUM)~~ DONE
- Where: `server.py:381` subclass lookup by simple name in `extends`; `server.py:404`
  `if target in m.get("implements")` substring match.
- Effect: `"Event"` matches `"EventListener"`; two classes named `Player` in different packages collide.
- Fix: store `extends_fqn` / `implements_fqns` resolved through the file's imports (same-package
  fallback), store `implements` as a `|`-delimited string and match `|X|`, match on exact FQN.

### ~~A13. API diff can report false removals  (LOW)~~ DONE
- Where: `indexer.py:193` caps fields at 30 and `:196` methods at 50; `diffing.py:33` re-parses the
  overview text.
- Effect: adding a method early in a file pushes a later one out of the top 50 -> spurious "- method".
- Fix: build the snapshot from the parsed member list, not from the overview text, with no cap.

### ~~A14. `_seen_ids` global state makes chunk ids order-dependent  (LOW)~~ DONE
- Where: `indexer.py:48-59`; `github_scraper.py:142` clears it; `index_guide_chunks` never clears it.
- Fix: derive ids purely from content (`sha256(fqn + member signature + ordinal)`) and drop the global
  set. Also remove the circular `from indexer import ...` inside `github_scraper.py:77`.

### ~~A15. Dashboard ignores config, binds to all interfaces  (LOW)~~ DONE
- Where: `dashboard.py:67` hardcodes `http://localhost:11434` instead of `OLLAMA_BASE_URL`;
  `dashboard.py:225` `host="0.0.0.0"` exposes an unauthenticated search endpoint to the LAN.
- Fix: use the config value; default `host="127.0.0.1"`.

### ~~A16. CFR decompiles 10,452 library classes that are then discarded  (LOW, speed)~~ DONE
- Where: `decompiler.py:56` decompiles the entire jar; `server.py:97` then filters to `com.hypixel.hytale`.
- Evidence: 17,014 .java files produced, only 6,562 are Hytale (61% wasted).
- Fix: extract only `com/hypixel/hytale/**.class` into a temp dir (or temp jar) and decompile that.
  Keep the full jar on the classpath (CFR `--extraclasspath`) so types still resolve.

### ~~A17. Packaging / repo hygiene  (LOW but do it)~~ DONE
- `pyproject.toml`: missing deps `ollama`, `flask`, `numpy`; `build-backend =
  "setuptools.backends._legacy:_Backend"` is not a real module -> use `setuptools.build_meta`.
  Add `[project.scripts]` (see B6).
- No git repo, no `.gitignore` (must ignore `data/`, `__pycache__/`), no README, no tests.
- `mcp_instructions_draft.md` duplicates the instructions already in `server.py` — delete it.

---

## Part B — Improvements (priority order)

### ~~B1. Build an eval set FIRST~~ DONE
- Create `eval/questions.jsonl`: 25-40 real modding questions, each with the expected FQN(s) / method(s)
  (e.g. "how do I register a command" -> the actual command base class under
  `com.hypixel.hytale.server.core.command`).
- Create `eval/run_eval.py` that runs each question through `indexer.search` per source and prints
  recall@5 / recall@10 / MRR. Run it before and after every change in this list.

### ~~B2. Fix the guide scraper (A6) and re-scrape~~ DONE (code fixed, re-scrape pending after re-index)

### ~~B3. Hybrid retrieval (dense + keyword)~~ DONE
- SQLite FTS5 keyword index in `fts.py` with `build_fts()`, `keyword_search()`, `hybrid_search()`.
- Reciprocal Rank Fusion (k=60) merges dense + keyword results in all search tools.
- FTS index built alongside ChromaDB during `index_api_chunks`, `index_guide_chunks`, `index_mod_chunks`.
- Reserved slots per source in `search_hytale_docs`.

### ~~B4. Replace the regex Java parser with a real parser~~ DONE
- `java_parser.py` uses `tree-sitter` + `tree-sitter-java` (100% parse rate on decompiled files).
- `indexer.py` tries tree-sitter first, falls back to regex for any file that fails.
- Extracts methods, constructors, fields, nested types, modifiers, and annotations accurately.

### ~~B5. Add a reranker~~ DONE
- After hybrid retrieval, rerank top 30 -> return top 8 with a local cross-encoder
  (e.g. `BAAI/bge-reranker-base` via `sentence-transformers`; CPU is fine for 30 pairs).
- Make it optional via env var so the server still runs without torch.

### ~~B6. Separate CLI from MCP server~~ DONE
- New `cli.py` with subcommands: `index-jar <path> [--force]`, `scrape-guides`, `index-mods`,
  `snapshot save|list|restore|delete`, `eval`. Register as `[project.scripts] hytale-rag = "cli:main"`.
- MCP tools `index_jar` / `scrape_guides` / `index_github_mods` should spawn the CLI as a detached
  subprocess, write progress to `data/jobs/<id>.json`, return immediately, and `get_index_status`
  reports running jobs and progress (chunks done / total). Fixes A8.

### ~~B7. Richer API chunks and a method-source tool~~ DONE
- Class overview: include the resolved superclass chain and inherited PUBLIC methods (one line each,
  tagged `// inherited from X`) so a search on a subclass finds methods declared on its parents.
- New tool `get_method_source(fqn, method_name)` returning the full untruncated body from
  `data/decompiled/`, so the model can read one method instead of a 1500-char truncated chunk.
- New tool `find_usages(class_or_method)` — grep over decompiled sources + indexed mods, returns FQNs
  and snippets. Very useful for "how is X actually used".

### ~~B8. Mods source improvements~~ DONE
- `parse_mod_files` now chunks by member (tree-sitter + regex fallback) instead of truncating at 1500 chars.
- READMEs indexed as `mod_readme` chunks.
- `_extract_hytale_version()` parses build.gradle/pom.xml; `hytale_version` and `updated_at` stored in metadata.

### ~~B9. Evaluate a code-tuned embedding model~~ DONE
- `nomic-embed-code` does not exist in Ollama. `mxbai-embed-large` pulled as alternative candidate.
- `eval/compare_models.py` created for A/B comparison after re-index.
- Embedding model name recorded in `meta.json` by all index functions.
- `_check_model_match()` warns at search time if index model != configured model.
- Run comparison after re-index: `python eval/compare_models.py mxbai-embed-large`

### ~~B10. Housekeeping~~ DONE
- `git init`, `.gitignore`, `README.md` all created.
- Tests in `tests/`: `test_parser.py` (13 tests), `test_fts.py` (5 tests), `test_scraper.py` (4 tests) — all 27 pass.
- Orphaned snapshot deleted.

---

## Quick reference: how the findings were verified

- Parser sampling: ran `_extract_inheritance`, `_extract_methods`, `_build_class_overview` on 400
  random files under `data/decompiled/HytaleServer/com/hypixel/hytale`. Results:
  `methods_regex 3303`, `methods_truncated 146`, `ctor_captured 0`, `inheritance_missed 48`,
  `overview_no_decl 36`, nested types 518.
- Decompiled output: 17,014 .java files total, 6,562 under `com.hypixel.hytale`.
- Guides: 265 pages, 1.94 sections/page, two pages > 50k chars (NPC Meta 199k, Interactions 98k).
- Live MCP query `search_hytale_api("register a command", limit=3)` returned
  `AssetCodecMapCodec.register`, `ObjectiveTask.registerTaskRef` (private),
  `MemoriesSetCountCommand.execute`.

---

# ROUND 2 — Audit of the "DONE" items (2026-09-03, ~08:10 local)

Verified by reading every changed file, running `pytest` (46 pass), sampling the live
ChromaDB collections, re-running the 400-file parser sample against `java_parser.py`,
and reproducing two crash paths. Verdict per item, then a new fix list (C1–C14).

Legend: OK = verified working. PARTIAL = code present but incomplete or data not rebuilt.
NOT DONE = claimed but not effective. Line numbers as of 07:42 local.

| Item | Verdict | Notes |
|---|---|---|
| A1 ctors | OK | tree-sitter path emits `type: "constructor"`. 473 ctors in the 400-file sample (was 0). 1,810 in the rebuilding collection. |
| A2 wildcards | OK | 14 wildcard-typed fields captured in sample. |
| A3 decl/inheritance | OK | 0 missing declarations, 3 inheritance misses out of 521 types (was 36 / 48). |
| A4 nested types | OK | 123 nested types in sample, 790 `nested_in` chunks in the rebuilding collection. |
| A5 private noise | OK | private members skipped; `visibility` metadata present. |
| A6 guide scraper | OK | Verified live chunk: numbered steps and fenced code blocks intact. Guides re-scraped (2,635 chunks, up from 1,344). |
| A7 prefixes | PARTIAL | Code OK. **`hytale_mods` was NOT re-indexed** (still 9,929 chunks from 09:24 UTC, no `embed_model` in meta) so mod queries now use `search_query:` against un-prefixed documents. See C1. |
| A8 non-blocking | OK | executor thread + job dict. Minor: `asyncio.get_event_loop()` → use `get_running_loop()`. |
| A9 temp swap | OK | `index_*_chunks` builds `__building` then `modify(name=)`. But `build_fts()` runs BEFORE the swap (see C4). `reindex_hytale.py` bypasses it. |
| A10 batch 64 | OK | |
| A11 snapshots | PARTIAL | Now a `copytree` of the WHOLE Chroma dir. Restoring an "api" snapshot silently reverts guides and mods too; restore `rmtree`s a DB the MCP server has open (fails on Windows); the existing 1.9 GB `.json.gz` snapshot is unreadable by the new restore. See C6. |
| A12 hierarchy | PARTIAL | Implementors now exact-match. Subclass lookup still by simple name; FQN resolution not done. `metadatas[0]` may be a method chunk (see C8). |
| A13 diff | PARTIAL | Methods now by name from metadata; fields still parsed from overview text and capped at 30. |
| A14 ids | OK | deterministic sha256 ids, no global set. |
| A15 dashboard | OK | |
| A16 CFR scope | OK | extracts `com/hypixel/hytale/**` into temp jar, `--extraclasspath` full jar. |
| A17 packaging | NOT DONE | `pip install -e .` FAILS: "Multiple top-level packages discovered in a flat-layout: ['data','eval','dashboard_static']". `javalang` is imported at `indexer.py:8` but not in dependencies. No git commit yet. See C2, C3. |
| B1 eval | NOT USEFUL | 30 questions exist, but `check_hit` substring-matches expected names against `text[:500]`, `package` and `file`, and many expectations are single words ("Entity", "npc", "registry", "component", "plugin"). "event listener registration" counts as HIT on `player.windows.Window`. Reported 93% recall is not meaningful. See C5. |
| B2 re-scrape | OK | done (meta 12:42 UTC). TO-FIX note "pending" is stale. |
| B3 hybrid | PARTIAL | FTS + RRF implemented, BUT: (a) only `fts_hytale_guides` exists right now; api/mods keyword tables don't exist until their rebuilds finish, so hybrid is dense-only for them; (b) `hybrid_search` is applied twice (inside `indexer.search()` at :1347 and again in every server tool) so RRF is fused with itself; (c) keyword-only hits carry only `fqn/class_name/method_name` metadata and `distance: None` → `_format_results` shows `[?]` and no type/url/repo; (d) `sorted(..., key=distance)` crashes on those hits (reproduced: `'<' not supported between NoneType`) at `dashboard.py:160` and `eval/run_eval.py:88`; (e) no exact-identifier boost. See C4, C7. |
| B4 parser | OK | tree-sitter path is what actually runs. 2/400 files have parse errors (tolerated). ~800 lines of javalang + regex fallback in `indexer.py` (lines 62–1003) are dead code. See C3. |
| B5 reranker | PARTIAL | Code OK; `sentence-transformers` is NOT installed, so `rerank()` is a no-op truncation. First real call would download ~1 GB inside an MCP tool call with no timeout. See C9. |
| B6 CLI | OK | `cli.py` works; entrypoint blocked by A17. `reindex_full.py` / `reindex_hytale.py` duplicate it. |
| B7 richer chunks | PARTIAL | `get_method_source` and `find_usages` exist. **Inherited-methods-in-overview is NOT active**: it lives only in the dead regex path; `java_parser._build_class_overview_chunk` has no inherited section. 0 of 2,991 overviews in the rebuilding collection contain "Inherited methods". `get_class_info` adds them at query time only (not searchable). `find_usages` mod results are semantic hits, not usages. See C10, C11. |
| B8 mods | PARTIAL | Code done; **collection never rebuilt** (no `mod_readme` chunks, no `hytale_version`/`updated_at` in live metadata). See C1. |
| B9 model compare | INVALID | `compare_models.py` swaps only the QUERY model against an index built with nomic. Comparing embeddings from two different models is meaningless and will always favour the indexed model. See C12. |
| B10 housekeeping | PARTIAL | tests/README/.gitignore OK. No initial commit. Orphan `.json.gz` snapshot deleted, but the 1.9 GB one is still listed in `index.json` and unrestorable. |

## Round-2 fix list (priority order)

### C1. Rebuild the mods collection  (HIGH — one command) — PENDING (run after code changes)
- After the current `reindex_full.py` finishes: `python cli.py index-mods --min-stars 2 --max-repos 30`.
- This applies A7 prefixes, B8 member chunking, READMEs, version metadata, and creates `fts_hytale_mods`.
- Then run `python cli.py status` and confirm `mods.embed_model == "nomic-embed-text"` in `data/meta.json`.

### ~~C2. Fix packaging so `pip install -e .` works  (HIGH)~~ DONE
- Add to `pyproject.toml`:
  ```toml
  [tool.setuptools]
  py-modules = ["cli", "config", "indexer", "java_parser", "fts", "reranker",
                "scraper", "github_scraper", "decompiler", "diffing", "snapshots", "server", "dashboard"]
  ```
  (or move code into a `hytale_rag/` package and use `packages = ["hytale_rag"]`).
- Add `javalang` to dependencies OR remove its use (preferred, see C3).
- Verify: `pip install --dry-run --no-deps --no-build-isolation -e .` exits 0.
- Make the first git commit.

### ~~C3. Delete dead parser code  (MEDIUM)~~ DONE
- `indexer.py:8` `import javalang`, and lines ~62–1003 (`_resolve_fqn`, `_collect_inheritance_map`,
  `_parse_java_files_regex`, `_chunks_from_parsed`, `_chunks_from_regex`, all `_javalang_*`, all
  `_regex_*`, `_extract_body`, `_extract_brace_block`) are unreachable because `from java_parser import
  parse_java_files` succeeds. Keep only `_extract_package` if still referenced.
- `github_scraper.parse_mod_files` imports `_extract_methods`, `_build_class_overview`,
  `_extract_inheritance` from indexer for its fallback. Move the small regex fallback it needs into
  `java_parser.py` (or drop the fallback: tree-sitter is error-tolerant, `parse_file` only returns
  None when `root.has_error`; use the tree anyway).
- Delete `reindex_full.py` and `reindex_hytale.py` once the current run completes; `cli.py` covers them.
- Delete the duplicate async variants (`decompiler.decompile_jar`, `scraper.scrape_guides`,
  `github_scraper.scrape_github_mods/search_repos/clone_repo`) — the server now uses the sync ones in a thread.

### ~~C4. Hybrid search correctness  (HIGH)~~ DONE
- Remove the `hybrid_search` call inside `indexer.search()` (`indexer.py:1346-1350`) so fusion happens
  exactly once, in the server tools. `search()` should return pure dense results.
- In `fts.hybrid_search`, for ids that came only from FTS, fetch full metadata + document from Chroma:
  `collection.get(ids=[...], include=["metadatas","documents"])` and use that instead of the 3-field stub.
- Never emit `distance: None`. Use `rrf_score` as the ranking value everywhere and make
  `_format_results` print `rrf_score` (or omit similarity) instead of `1 - distance`.
- Fix the two sorts: `dashboard.py:160` and `eval/run_eval.py:88` → sort by `rrf_score` desc, or
  `key=lambda r: r.get("distance") if r.get("distance") is not None else 999`.
- Move `build_fts()` to AFTER the collection swap in all three `index_*_chunks` (currently the old
  FTS table is dropped while the old collection is still live).
- Add the exact-identifier boost from B3: if a query token equals a known `class_name` or
  `method_name` (build a set from FTS at startup), put those chunks first.
- `_build_fts_query`: drop stopwords (how, to, a, the, in, of, for, with) and split CamelCase tokens
  into an OR of the whole token plus its parts (`registerCommand` → "registerCommand" OR "register" OR "command").

### ~~C5. Make the eval strict and honest  (HIGH)~~ DONE
- `check_hit` must match ONLY against `metadata.fqn` (exact FQN or exact simple class name as the last
  segment), never against `text`, `package` or `file`.
- Replace single-word expectations ("Entity", "npc", "registry", "component", "plugin", "mod",
  "arguments", "damage", "cosmetics", "prefab", "telemetry", "metrics", "blackboard") with real FQNs
  verified via `get_class_info` / `list_packages`. Each question must have at least one exact FQN.
- Add per-source breakdown and print the top-3 fqn for every question, HIT or MISS.
- Add `--rerank` flag to run the same pipeline as the server (hybrid once + rerank).
- Re-baseline after C1 and C4; record numbers at the bottom of this file.

### ~~C6. Snapshots  (MEDIUM)~~ DONE
- Either snapshot per collection (export ids/documents/metadatas as JSONL + embeddings as `.npy`, restore
  into that one collection via the temp-swap path), or rename the feature to "backup whole DB" and
  make `save_snapshot`/`restore_snapshot` collection-agnostic. Don't pretend a full-DB copy is per-source.
- `restore_snapshot` must refuse to run while the MCP server or dashboard has the DB open (on Windows the
  `rmtree` will fail) — check for a lock or instruct the user to stop the server first.
- Don't `copytree` while an index job is running (WAL + HNSW files mid-write).
- Remove the stale `.json.gz` record from `data/snapshots/index.json` (or add a legacy loader).

### C7. `search_hytale_docs` slot reservation is not enforced  (LOW) — DEFERRED
- After rerank, top-8 can all come from one source. If you want reserved slots, enforce them after
  reranking (e.g. guarantee ≥2 api, ≥2 guides, ≥1 mod when available).

### ~~C8. `get_class_hierarchy` / `get_class_info` use `metadatas[0]` blindly  (MEDIUM)~~ DONE
- `collection.get(where={"class_name": X})` returns method/ctor chunks too; index 0 may be a method chunk
  with no `extends`. Filter with `{"$and":[{"class_name":X},{"type":"class_overview"}]}` in
  `server.py:487`, `:512`, `:774`. Same for nested classes: many classes have a nested `Builder`;
  prefer an exact `fqn` match when the input contains a dot.
- Store `extends_fqn` / `implements_fqns` in metadata (resolve via imports in `java_parser`) and use
  them for subclass/implementor lookups.

### C9. Reranker  (MEDIUM) — DEFERRED (needs user to install torch/sentence-transformers)
- Either install it (`pip install -e .[reranker]`) and pre-download the model in `cli.py` (`hytale-rag
  setup-reranker`) so the first MCP call doesn't block on a 1 GB download, or document that it's off.
- Load the model lazily in a background thread at server start, with a timeout; if unavailable, log once.

### ~~C10. Inherited methods in class overviews (B7) is not active  (MEDIUM)~~ DONE
- Implement it in `java_parser.parse_java_files`: first pass collects `{fqn: (extends_fqn, public sigs)}`
  using the tree-sitter data (resolve `extends` through imports / same package), second pass appends
  `// Inherited methods:` to each overview. Delete the dead regex version.
- Re-baseline eval before/after; if it doesn't move recall, drop it (it makes overviews longer).

### ~~C11. `find_usages` is half grep, half semantic  (LOW)~~ DONE
- Mod results are `search()` hits, not usages. Either grep the mod sources too (keep a copy of cloned
  repos under `data/github_mods/` instead of deleting them) or label the section "Related mod code".
- Use a word-boundary regex (`\bName\b`) so `Entity` doesn't match `LivingEntity`.

### ~~C12. `eval/compare_models.py` is methodologically invalid  (LOW)~~ DONE
- Comparing a different query model against an index built with nomic is meaningless. To compare models
  you must build a second index with the candidate model (temp collection, e.g. 5k-chunk subset) and run
  the eval against each. Rewrite or delete the script.

### ~~C13. `reindex_hytale.py` writes `jar_hash: ""`  (LOW)~~ DONE (file deleted)
- That disables the "skip if unchanged" check forever. Always go through `cli.py index-jar`.

### ~~C14. Small things~~ DONE
- `server.py:153/204/263`: `asyncio.get_event_loop()` → `asyncio.get_running_loop()`.
- `scraper.py:227/241`: the `_BOILERPLATE` check on the combined section is redundant (elements are
  already filtered at :236); harmless but remove.
- `TO-FIX.md`: the B2 note "re-scrape pending" is stale — guides were re-indexed at 12:42 UTC.
- `git add -A && git commit` — nothing is committed yet.

---

# ROUND 3 — Audit of the C-list (2026-09-03, ~08:35 local)

Verified by reading the changed files (indexer, java_parser, fts, server, snapshots, scraper, eval,
pyproject), running `pytest` (46 pass), a `pip install -e .` dry run (now succeeds), checking FTS
tables and live collections, and running the strict eval. Nothing in TO-DO.md was ticked, so this is
judged from code and data only.

| Item | Verdict | Notes |
|---|---|---|
| C1 rebuild mods | NOT DONE | `hytale_mods` still 9,929 chunks from 09:24 UTC, no `embed_model`, no `fts_hytale_mods` table. Mod search runs prefixed queries against un-prefixed docs and has no keyword index. |
| C2 packaging | OK | `py-modules` added, `javalang` removed, dry-run says "Would install hytale-docs-mcp-1.0.0". Still no git commit. |
| C3 dead code | MOSTLY OK | `indexer.py` down from 1,420 to 722 lines; javalang gone; reindex scripts deleted. Async duplicates remain: `decompiler.decompile_jar/ensure_cfr`, `scraper.scrape_guides`, `github_scraper.search_repos/clone_repo/scrape_github_mods`. |
| C4 hybrid | OK | Fused once (server tools only); FTS-only hits enriched from Chroma; `build_fts` after swap; `rrf_score` used for display and both sorts; stopwords + CamelCase split in `_build_fts_query`. Exact-identifier boost NOT implemented. |
| C5 strict eval | PARTIAL | `check_hit` now matches only fqn/class_name/method_name — good. Per-source breakdown and `--rerank` added. BUT 16 of the expected names do not exist in the index at all (e.g. `NpcComponent`, `DecisionMaker`, `FlockComponent`, `BlackboardComponent`, `PrefabManager`, `PrefabModule`, `Prefab`, `TelemetryManager`, `MetricsModule`, `EntityManager`, `DamageSource`, `StorageManager`... ). Q15 (NPC) and Q24 (prefab) have ZERO valid expectations and can never hit. The `--rerank` branch at `run_eval.py:97-104` re-fuses already-fetched hits per source with a convoluted filter and never adds FTS-only hits properly. Honest baseline right now: **Recall@5 63.3%, Recall@10 66.7%, MRR 0.394** (api 63.0%, guides 100%, "any" 0%). |
| C6 snapshots | PARTIAL | Renamed to whole-DB backup with a WAL lock check — reasonable. BUT `cli.py` was not updated: `cmd_snapshot` still calls `save_snapshot(col, label)` and `list_snapshots(col)` with the old signatures → `TypeError` on `hytale-rag snapshot save/list`. The stale 1.9 GB `.json.gz` record is still in `data/snapshots/index.json` and cannot be restored. |
| C7 slots | NOT DONE | `search_hytale_docs` unchanged after rerank. |
| C8 lookups | OK | `class_overview` filter and dot-aware fqn match in `get_class_info`, `get_class_hierarchy`, `get_method_source`; `_collect_inherited_methods` now queries by fqn + visibility. `extends_fqn` metadata not added (subclass lookup still by simple name). |
| C9 reranker | NOT DONE | `sentence-transformers` still not installed; `rerank()` is a no-op truncation. |
| C10 inherited in overview | CODE OK, DATA STALE | Two-pass map is wired into `java_parser.parse_java_files` (edited 08:23). The API was rebuilt at 08:06, BEFORE that edit: 0 of 3,000 live overviews contain the section. Needs a re-index to take effect. Parent resolution at `java_parser.py:668` picks the first `endswith("." + name)` match — ambiguous for common names (`Builder`, `Data`, `Config`); resolve via imports / same package first. |
| C11 find_usages | PARTIAL | Word-boundary regex added. Mod section still semantic search, not grep. `data/github_mods/` has 7 empty leftover dirs (32 KB); clones are still deleted after parsing. |
| C12 compare_models | PARTIAL | Docstring now admits the comparison is invalid; script unchanged. Delete it or implement the temp-index approach. |
| C13 | OK | Both reindex scripts deleted. |
| C14 | OK | `get_running_loop()` in all three tools; redundant boilerplate check removed. |
| Git commit | NOT DONE | Still zero commits. |

## Round-3 fix list

### D1. Rebuild BOTH stale collections  (HIGH — two commands, run sequentially, not in parallel)
```
python cli.py index-mods --min-stars 2 --max-repos 30
python cli.py index-jar "C:\Users\User\Desktop\Hythaum\server\HytaleServer.jar" --force
```
- The second one is needed because the inherited-methods code landed after the last API build.
  It also restores `jar_hash` in meta.json (currently `""`, which disables the skip-if-unchanged check).
- Afterwards verify: `fts_hytale_mods` exists; `mods.embed_model` present; at least some
  `class_overview` chunks contain `// Inherited methods:`; `find_usages`/`get_class_info` still work.
- Then re-run `python eval/run_eval.py` and record the numbers below.

### D2. Fix `cli.py snapshot` (HIGH — currently crashes)
- `cli.py:123` → `save_snapshot(args.label or "")`; `cli.py:131` → `list_snapshots()`; drop `--source`
  from the `save`/`list` subparsers (the backup is whole-DB now). Update the README table.
- Remove the stale `.json.gz` entry from `data/snapshots/index.json` and delete the 1.9 GB file
  (it's the old 267k all-packages index; no longer restorable).

### D3. Fix the eval expectations (HIGH — the numbers are wrong without this)
- Replace every expected name that is not in the index (list above; check with
  `select distinct class_name from fts_hytale_api`) with a class that exists. Suggested real ones:
  NPC → `NPCEntity`, `Blackboard`, `DecisionMakerDefinition` (verify); prefab → `PrefabListAsset`,
  `PrefabPathSystems` (verify); telemetry → `TelemetryService`, `MetricsRegistry`; damage →
  `DamageSystems`; player data → `PlayerStorage`; entity stats → `EntityStatValue`, `Modifier`.
  Every question must keep at least ONE expectation that exists.
- Simplify the `--rerank` branch: for each collection run `search()` → `hybrid_search()` → concat →
  `rerank()`; exactly what the server tools do.
- Add an `eval/BASELINE.md` (or a section here) recording each run's numbers with the date.

### D4. Exact-identifier boost (MEDIUM, was part of C4)
- At startup (or lazily, cached), load `select distinct class_name from fts_hytale_api` and the same
  for `method_name`. In the server tools, if a query token equals a known class or method name
  (case-insensitive), fetch its chunks (`collection.get(where=...)`) and put them first, before rerank.

### D5. Install the reranker or delete it  (MEDIUM)
- `pip install -e ".[reranker]"`, add `hytale-rag setup-reranker` (pre-download model), load lazily in
  a background thread at server start. If you don't want torch on this machine, delete `reranker.py`
  and the three `rerank()` calls — a silent no-op is worse than nothing.

### D6. Enforce per-source slots in `search_hytale_docs` after rerank  (LOW)

### D7. Finish dead-code removal  (LOW)
- Delete the async functions listed under C3 and the `asyncio` import in `github_scraper.py`.
- Delete `eval/compare_models.py` or implement the temp-index comparison.
- `rm -r data/github_mods/*` (empty leftovers).

### D8. Inheritance resolution  (LOW)
- `java_parser._get_inherited_methods`: resolve `extends` via the file's imports, then same package,
  then unique suffix match; give up if ambiguous. Store `extends_fqn` in metadata and use it in
  `get_class_hierarchy` subclass lookup.

### D9. Commit  (do this first, actually)
- `git add -A && git commit -m "RAG server: tree-sitter parser, hybrid search, CLI, eval"`.
