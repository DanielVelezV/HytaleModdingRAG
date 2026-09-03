import asyncio
import re
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from mcp.server.mcpserver import MCPServer

from config import API_COLLECTION, GUIDES_COLLECTION, MODS_COLLECTION, DECOMPILED_DIR

_jobs: dict[str, dict] = {}

mcp = MCPServer(
    "hytale-docs",
    instructions="""\
Hytale Modding RAG — your knowledge base for Hytale server modding.

## Source hierarchy (in order of trust)

1. DECOMPILED JAVA API (source of truth) — Extracted directly from HytaleServer.jar. This is the ONLY source guaranteed to be up-to-date. Always verify class names, method signatures, fields, and packages against this source. If any other source contradicts the decompiled API, the decompiled API wins.

2. COMMUNITY GUIDES (implementation guidance) — Scraped from hytalemodding.dev. Explains HOW to implement features, best practices, patterns, and setup steps. Guides may reference APIs that have changed — always cross-check method signatures and class names against the decompiled API before using them in answers.

3. GITHUB MOD EXAMPLES (real-world reference) — Code from open-source Hytale mods on GitHub. Shows how the community has actually implemented features. Useful for illustrating patterns with working code snippets. These may use outdated APIs or non-ideal patterns — always validate against the decompiled API and prefer guide-recommended approaches over arbitrary GitHub code.

## When to use this MCP

Whenever the user asks about Hytale modding, plugins, server-side development, Java API classes, methods, events, commands, blocks, items, entities, or how to implement any Hytale mod feature.

## Tool selection

- search_hytale_docs: DEFAULT. Searches all three sources ranked by relevance. Use for general questions.
- search_hytale_api: When the user needs the actual Java API — class definitions, method signatures, fields, packages. This is where ground truth lives.
- search_hytale_guides: When the user asks "how to", "best way to", or needs implementation patterns and tutorials.
- search_hytale_mods: When the user wants to see how a real mod did something. Use to illustrate with code snippets, not as authoritative reference.
- get_class_info: When the user names a specific class. Returns the full overview with all fields and method signatures.
- get_class_hierarchy: When exploring type relationships — parent classes, subclasses, interfaces, implementors.
- get_method_source: When you need the full untruncated method body — not the 1500-char chunk, but the complete decompiled source.
- find_usages: When you need to see where a class or method is actually used across the codebase and mods.
- list_packages: To explore the API structure or find where a feature lives.
- get_index_status: Check what's indexed and when.
- index_jar: Re-index when the user has a new HytaleServer.jar after a Hytale update. Smart re-indexing skips if the jar hash hasn't changed.
- scrape_guides: Re-scrape hytalemodding.dev for updated guides.
- index_github_mods: Refresh GitHub mod examples.

## Search strategy

- Use Java terminology for API searches: "BlockType", "registerCommand", "EventListener" work better than plain English.
- Use natural language for guide searches: "how to create a custom item" works better than class names.
- For mod searches, describe the feature: "discord integration", "custom command with arguments".
- If search returns a class name, follow up with get_class_info for the full picture.
- Use get_class_hierarchy to understand type relationships and find related classes.

## Answering modding questions

1. ALWAYS search before answering — never guess Hytale APIs from general Java knowledge.
2. Verify every class name, method, and signature against the decompiled API before including it in an answer.
3. Use guides to explain the approach and pattern, but confirm the exact API calls against the decompiled source.
4. When showing GitHub mod examples, credit the repository and note that the code is illustrative — the decompiled API is the authority on current method signatures.
5. Always include the full package path (e.g. com.hypixel.hytale.server.core.command) so the user knows what to import.
6. If a guide or mod example uses an API that doesn't match the decompiled source, flag the discrepancy and use the decompiled version.
""",
)


def _run_index_jar(jar_path: str, force: bool, job_id: str):
    from decompiler import decompile_jar
    from indexer import parse_java_files, parse_json_configs, index_api_chunks, compute_jar_hash
    from diffing import snapshot_api, rotate_snapshot, diff_api, save_diff, PREV_SNAPSHOT_FILE
    from config import HYTALE_PACKAGE_PREFIX

    try:
        _jobs[job_id]["status"] = "decompiling"
        jar_name = Path(jar_path).name
        jar_hash = compute_jar_hash(jar_path)

        rotate_snapshot()
        snapshot_api()

        output_dir = decompile_jar(jar_path)
        java_files = list(output_dir.rglob("*.java"))

        _jobs[job_id]["status"] = "parsing"
        all_chunks = parse_java_files(output_dir)
        config_chunks = parse_json_configs(output_dir)

        chunks = [
            c for c in all_chunks
            if c["metadata"].get("package", "").startswith(HYTALE_PACKAGE_PREFIX)
        ]
        chunks.extend(config_chunks)

        _jobs[job_id]["status"] = "indexing"
        _jobs[job_id]["total_chunks"] = len(chunks)
        index_api_chunks(chunks, jar_name=jar_name, jar_hash=jar_hash)

        new_snap = snapshot_api()
        diff_summary = ""
        if PREV_SNAPSHOT_FILE.exists():
            diff_result = diff_api(PREV_SNAPSHOT_FILE, new_snap)
            save_diff(diff_result)
            diff_summary = diff_result.get("summary", "")

        _jobs[job_id]["status"] = "done"
        _jobs[job_id]["result"] = (
            f"Indexed {jar_name}: {len(java_files)} files, "
            f"{len(chunks)} chunks, {len(config_chunks)} configs"
        )
        if diff_summary:
            _jobs[job_id]["result"] += f"\n{diff_summary}"
    except Exception as e:
        _jobs[job_id]["status"] = "failed"
        _jobs[job_id]["error"] = str(e)


@mcp.tool()
async def index_jar(jar_path: str, force: bool = False) -> str:
    """Decompile a HytaleServer.jar and index all Java classes into the RAG database.

    Smart re-indexing: compares the jar's SHA256 hash against the last indexed version.
    If unchanged, skips decompilation and re-indexing. Use force=True to re-index anyway.
    Runs in the background — use get_index_status to check progress.

    Args:
        jar_path: Absolute path to the HytaleServer.jar file
        force: Force re-index even if the jar hasn't changed (default False)
    """
    from indexer import check_jar_changed

    jar_name = Path(jar_path).name

    if not force and not check_jar_changed(jar_path):
        return (
            f"{jar_name} has not changed since last indexing (hash matches).\n"
            f"Use force=True to re-index anyway."
        )

    running = [j for j in _jobs.values() if j["status"] not in ("done", "failed")]
    if running:
        return f"An indexing job is already running: {running[0]['type']} ({running[0]['status']})"

    job_id = str(uuid.uuid4())[:8]
    _jobs[job_id] = {
        "type": "index_jar",
        "status": "starting",
        "started_at": time.time(),
        "jar": jar_name,
        "total_chunks": 0,
    }

    asyncio.get_running_loop().run_in_executor(None, _run_index_jar, jar_path, force, job_id)

    return (
        f"Indexing {jar_name} started in background (job {job_id}).\n"
        f"Use get_index_status to check progress. The server remains responsive."
    )


def _run_scrape_guides(job_id: str):
    from scraper import scrape_guides
    from indexer import chunk_guides, index_guide_chunks

    try:
        _jobs[job_id]["status"] = "scraping"
        pages = scrape_guides()
        if not pages:
            _jobs[job_id]["status"] = "failed"
            _jobs[job_id]["error"] = "No guide pages found"
            return

        _jobs[job_id]["status"] = "indexing"
        chunks = chunk_guides(pages)
        _jobs[job_id]["total_chunks"] = len(chunks)
        index_guide_chunks(chunks)

        _jobs[job_id]["status"] = "done"
        _jobs[job_id]["result"] = f"Scraped {len(pages)} pages, indexed {len(chunks)} chunks"
    except Exception as e:
        _jobs[job_id]["status"] = "failed"
        _jobs[job_id]["error"] = str(e)


@mcp.tool()
async def scrape_guides() -> str:
    """Scrape and index the Hytale modding guides from hytalemodding.dev.

    Crawls all pages under hytalemodding.dev/en/docs, extracts content,
    and indexes it into ChromaDB for search. Runs in background — use get_index_status to check.
    """
    running = [j for j in _jobs.values() if j["status"] not in ("done", "failed")]
    if running:
        return f"An indexing job is already running: {running[0]['type']} ({running[0]['status']})"

    job_id = str(uuid.uuid4())[:8]
    _jobs[job_id] = {
        "type": "scrape_guides",
        "status": "starting",
        "started_at": time.time(),
        "total_chunks": 0,
    }

    asyncio.get_running_loop().run_in_executor(None, _run_scrape_guides, job_id)

    return (
        f"Guide scraping started in background (job {job_id}).\n"
        f"Use get_index_status to check progress."
    )


def _run_index_github_mods(min_stars: int, max_repos: int, job_id: str):
    from github_scraper import scrape_github_mods
    from indexer import index_mod_chunks

    try:
        _jobs[job_id]["status"] = "cloning"
        chunks, indexed_repos = scrape_github_mods(
            min_stars=min_stars,
            max_repos=max_repos,
        )

        if not chunks:
            _jobs[job_id]["status"] = "failed"
            _jobs[job_id]["error"] = "No mod repositories found or all clones failed"
            return

        _jobs[job_id]["status"] = "indexing"
        _jobs[job_id]["total_chunks"] = len(chunks)
        index_mod_chunks(chunks, indexed_repos)

        _jobs[job_id]["status"] = "done"
        _jobs[job_id]["result"] = (
            f"Indexed {len(indexed_repos)} repos, {len(chunks)} chunks"
        )
    except Exception as e:
        _jobs[job_id]["status"] = "failed"
        _jobs[job_id]["error"] = str(e)


@mcp.tool()
async def index_github_mods(min_stars: int = 2, max_repos: int = 30) -> str:
    """Search GitHub for open-source Hytale mods/plugins and index their Java source code.

    Runs in background — use get_index_status to check progress.

    Args:
        min_stars: Minimum GitHub stars to filter repos (default 2)
        max_repos: Maximum number of repos to index (default 30)
    """
    running = [j for j in _jobs.values() if j["status"] not in ("done", "failed")]
    if running:
        return f"An indexing job is already running: {running[0]['type']} ({running[0]['status']})"

    job_id = str(uuid.uuid4())[:8]
    _jobs[job_id] = {
        "type": "index_github_mods",
        "status": "starting",
        "started_at": time.time(),
        "total_chunks": 0,
    }

    asyncio.get_running_loop().run_in_executor(None, _run_index_github_mods, min_stars, max_repos, job_id)

    return (
        f"GitHub mod indexing started in background (job {job_id}).\n"
        f"Use get_index_status to check progress."
    )


_BOOST_SKIP = frozenset({
    "how", "the", "use", "get", "set", "run", "can", "has", "are", "all",
    "not", "for", "new", "try", "out", "add", "may", "let", "put", "end",
    "any", "did", "see", "say", "was", "way", "own", "now", "why", "yet",
    "its", "per", "via", "also", "just", "each", "into", "when", "what",
    "does", "from", "with", "that", "this", "have", "been", "will", "used",
})


def _exact_identifier_boost(query: str, collection_names: list[str], results: list[dict]) -> list[dict]:
    """Fetch exact class/method name matches from the index and place them first."""
    from indexer import get_collection

    class_tokens = [
        t for t in re.findall(r'\b[A-Z][a-zA-Z0-9]{2,}\b', query)
        if t.lower() not in _BOOST_SKIP
    ]
    method_tokens = re.findall(r'\b[a-z][a-zA-Z0-9]*[A-Z][a-zA-Z0-9]*\b', query)

    if not class_tokens and not method_tokens:
        return results

    existing_ids = {r.get("id") for r in results}
    boosted = []

    for col_name in collection_names:
        try:
            collection = get_collection(col_name)
        except Exception:
            continue

        for token in class_tokens:
            hits = collection.get(
                where={"$and": [{"class_name": token}, {"type": "class_overview"}]},
                include=["documents", "metadatas"],
            )
            for i, doc_id in enumerate(hits["ids"]):
                if doc_id not in existing_ids:
                    boosted.append({
                        "id": doc_id,
                        "text": hits["documents"][i],
                        "metadata": hits["metadatas"][i],
                        "rrf_score": 1.0,
                    })
                    existing_ids.add(doc_id)

        for token in method_tokens[:3]:
            hits = collection.get(
                where={"$and": [{"method_name": token}, {"type": "method"}]},
                include=["documents", "metadatas"],
                limit=3,
            )
            for i, doc_id in enumerate(hits["ids"]):
                if doc_id not in existing_ids:
                    boosted.append({
                        "id": doc_id,
                        "text": hits["documents"][i],
                        "metadata": hits["metadatas"][i],
                        "rrf_score": 1.0,
                    })
                    existing_ids.add(doc_id)

    return boosted + results


def _enforce_source_slots(results: list[dict], limit: int) -> list[dict]:
    """Guarantee minimum per-source representation: >=2 api, >=2 guides, >=1 mod."""
    SLOTS = {"api": 2, "guide": 2, "mod": 1}

    by_source: dict[str, list[dict]] = {}
    for r in results:
        src = r.get("metadata", {}).get("source", "")
        by_source.setdefault(src, []).append(r)

    selected = []
    used_ids: set[str] = set()

    for src, minimum in SLOTS.items():
        for r in by_source.get(src, [])[:minimum]:
            rid = r.get("id")
            if rid and rid not in used_ids:
                selected.append(r)
                used_ids.add(rid)

    for r in results:
        if len(selected) >= limit:
            break
        rid = r.get("id")
        if rid and rid not in used_ids:
            selected.append(r)
            used_ids.add(rid)

    return selected[:limit]


@mcp.tool()
def search_hytale_docs(query: str, limit: int = 8) -> str:
    """Search across ALL Hytale documentation — decompiled Java API, community guides, AND real mod examples from GitHub.

    Use this for general questions like "how do I register a block" or
    "what events are available". Returns the most relevant chunks from all sources.

    Args:
        query: Natural language search query
        limit: Max results to return (default 8)
    """
    from indexer import search
    from fts import hybrid_search

    fetch = max(30, limit * 3)
    api_dense = search(query, API_COLLECTION, n_results=fetch)
    guide_dense = search(query, GUIDES_COLLECTION, n_results=fetch)
    mod_dense = search(query, MODS_COLLECTION, n_results=fetch)

    api_results = hybrid_search(query, API_COLLECTION, api_dense, n_results=fetch // 2)
    guide_results = hybrid_search(query, GUIDES_COLLECTION, guide_dense, n_results=fetch // 3)
    mod_results = hybrid_search(query, MODS_COLLECTION, mod_dense, n_results=fetch // 4)

    combined = api_results + guide_results + mod_results
    combined = _exact_identifier_boost(
        query, [API_COLLECTION, GUIDES_COLLECTION, MODS_COLLECTION], combined,
    )
    combined.sort(key=lambda r: r.get("rrf_score", 0), reverse=True)
    all_results = _enforce_source_slots(combined, limit)

    if not all_results:
        return "No results found. Make sure you've run index_jar, scrape_guides, and/or index_github_mods first."

    return _format_results(all_results)


@mcp.tool()
def search_hytale_api(query: str, limit: int = 8, package: str = "", type: str = "") -> str:
    """Search only the decompiled Java API (classes, methods, fields).

    Use this for specific API lookups like "BlockType class methods" or
    "event listener registration" or "ServerPlayer inventory".

    Args:
        query: Search query — class names, method names, or descriptions
        limit: Max results to return (default 8)
        package: Optional package prefix to scope results (e.g. "com.hypixel.hytale.server.core.command")
        type: Optional chunk type filter — "class_overview" or "method"
    """
    from indexer import search
    from fts import hybrid_search

    fetch = max(30, limit * 3)
    dense = search(query, API_COLLECTION, n_results=fetch, package_filter=package, type_filter=type)
    results = hybrid_search(query, API_COLLECTION, dense, n_results=fetch)
    results = _exact_identifier_boost(query, [API_COLLECTION], results)
    results = results[:limit]
    if not results:
        return "No API results. Run index_jar first to index a HytaleServer.jar."

    return _format_results(results)


@mcp.tool()
def search_hytale_guides(query: str, limit: int = 8) -> str:
    """Search only the community guides and best practices from hytalemodding.dev.

    Use this for how-to questions, tutorials, and recommended patterns like
    "best practice for custom items" or "how to set up a plugin".

    Args:
        query: Search query
        limit: Max results to return (default 8)
    """
    from indexer import search
    from fts import hybrid_search

    fetch = max(30, limit * 3)
    dense = search(query, GUIDES_COLLECTION, n_results=fetch)
    results = hybrid_search(query, GUIDES_COLLECTION, dense, n_results=fetch)
    results = _exact_identifier_boost(query, [GUIDES_COLLECTION], results)
    results = results[:limit]
    if not results:
        return "No guide results. Run scrape_guides first to index the community docs."

    return _format_results(results)


@mcp.tool()
def search_hytale_mods(query: str, limit: int = 8) -> str:
    """Search only real-world mod examples from GitHub repositories.

    Use this to find how other developers implemented a specific feature,
    see real patterns and code examples from working mods.

    Args:
        query: Search query — feature names, patterns, or class names
        limit: Max results to return (default 8)
    """
    from indexer import search
    from fts import hybrid_search

    fetch = max(30, limit * 3)
    dense = search(query, MODS_COLLECTION, n_results=fetch)
    results = hybrid_search(query, MODS_COLLECTION, dense, n_results=fetch)
    results = _exact_identifier_boost(query, [MODS_COLLECTION], results)
    results = results[:limit]
    if not results:
        return "No mod examples indexed. Run index_github_mods first."

    return _format_results(results)


def _collect_inherited_methods(collection, parent_name: str, depth: int = 0) -> list[str]:
    """Walk the superclass chain and collect public method signatures."""
    if not parent_name or depth > 5:
        return []
    parent_results = collection.get(
        where={"$and": [{"class_name": parent_name}, {"type": "class_overview"}]},
        include=["metadatas", "documents"],
    )
    if not parent_results["ids"]:
        return []

    lines = []
    parent_fqn = parent_name
    next_parent = ""
    overview_meta = parent_results["metadatas"][0]
    parent_fqn = overview_meta.get("fqn", parent_name)
    next_parent = overview_meta.get("extends", "")

    method_results = collection.get(
        where={"$and": [{"fqn": parent_fqn}, {"type": "method"}, {"visibility": {"$in": ["public", "protected"]}}]},
        include=["documents"],
    )
    for doc in method_results["documents"]:
        sig = doc.split("\n")[1] if "\n" in doc else doc
        if len(sig) > 200:
            sig = sig[:200] + "..."
        lines.append(f"  {sig.strip()};  // inherited from {parent_fqn}")

    lines.extend(_collect_inherited_methods(collection, next_parent, depth + 1))
    return lines


@mcp.tool()
def get_class_info(class_name: str) -> str:
    """Get the full overview of a specific Java class from the decompiled API.

    Returns the class declaration, fields, method signatures, package, and imports.

    Args:
        class_name: Simple class name (e.g. "BlockType") or fully qualified name
    """
    from indexer import get_collection, API_COLLECTION

    collection = get_collection(API_COLLECTION)
    if collection.count() == 0:
        return "No API indexed. Run index_jar first."

    if "." in class_name:
        results = collection.get(
            where={"fqn": class_name},
            include=["documents", "metadatas"],
        )
    else:
        results = collection.get(
            where={"$and": [{"class_name": class_name}, {"type": "class_overview"}]},
            include=["documents", "metadatas"],
        )

    if not results["ids"]:
        results = collection.get(
            where={"class_name": class_name},
            include=["documents", "metadatas"],
        )

    if not results["ids"]:
        from indexer import _get_embedding_fn
        ef = _get_embedding_fn()
        qe = ef.embed_query(class_name)
        search_results = collection.query(
            query_embeddings=[qe],
            n_results=5,
            where={"type": "class_overview"},
        )
        if search_results["ids"][0]:
            suggestions = [
                f"  - {m['fqn']}" for m in search_results["metadatas"][0]
            ]
            return f"Class '{class_name}' not found exactly. Did you mean:\n" + "\n".join(suggestions)
        return f"Class '{class_name}' not found in the index."

    parts = []
    for i, doc_id in enumerate(results["ids"]):
        meta = results["metadatas"][i]
        text = results["documents"][i]
        parts.append(f"### {meta.get('fqn', class_name)} ({meta.get('type', '')})\n{text}")

    overview_meta = next(
        (results["metadatas"][i] for i in range(len(results["ids"]))
         if results["metadatas"][i].get("type") == "class_overview"),
        None,
    )
    if overview_meta:
        inherited = _collect_inherited_methods(collection, overview_meta.get("extends", ""))
        if inherited:
            parts.append("### Inherited Methods\n" + "\n".join(inherited))

    return "\n\n---\n\n".join(parts)


@mcp.tool()
def get_class_hierarchy(class_name: str) -> str:
    """Get the inheritance chain for a class — what it extends, what it implements, and what extends it.

    Useful to understand type relationships, find parent classes with shared methods,
    or discover all subclasses of an abstract type.

    Args:
        class_name: Simple class name (e.g. "AbstractCommand") or fully qualified name
    """
    from indexer import get_collection, API_COLLECTION

    collection = get_collection(API_COLLECTION)
    if collection.count() == 0:
        return "No API indexed. Run index_jar first."

    if "." in class_name:
        results = collection.get(
            where={"$and": [{"fqn": class_name}, {"type": "class_overview"}]},
            include=["metadatas"],
        )
    else:
        results = collection.get(
            where={"$and": [{"class_name": class_name}, {"type": "class_overview"}]},
            include=["metadatas"],
        )

    if not results["ids"]:
        results = collection.get(
            where={"class_name": class_name},
            include=["metadatas"],
        )

    if not results["ids"]:
        return f"Class '{class_name}' not found in the index."

    meta = results["metadatas"][0]
    fqn = meta.get("fqn", class_name)
    extends = meta.get("extends", "")
    implements = meta.get("implements", "")

    lines = [f"# {fqn}\n"]
    if extends:
        lines.append(f"Extends: {extends}")
    if implements:
        lines.append(f"Implements: {implements}")

    if extends:
        parent_results = collection.get(
            where={"$and": [{"class_name": extends}, {"type": "class_overview"}]},
            include=["metadatas"],
        )
        if parent_results["ids"]:
            pm = parent_results["metadatas"][0]
            lines.append(f"\n## Parent: {pm.get('fqn', extends)}")
            if pm.get("extends"):
                lines.append(f"  Extends: {pm['extends']}")
            if pm.get("implements"):
                lines.append(f"  Implements: {pm['implements']}")

    children = collection.get(
        where={"$and": [{"extends": meta.get("class_name", class_name)}, {"type": "class_overview"}]},
        include=["metadatas"],
    )
    if children["ids"]:
        child_names = sorted(set(m.get("fqn", "?") for m in children["metadatas"]))
        lines.append(f"\n## Subclasses ({len(child_names)}):")
        for cn in child_names[:30]:
            lines.append(f"  - {cn}")
        if len(child_names) > 30:
            lines.append(f"  - ... and {len(child_names) - 30} more")

    implementors = collection.get(
        where={"$and": [
            {"implements": {"$ne": ""}},
            {"type": "class_overview"},
        ]},
        include=["metadatas"],
    )
    if implementors["ids"]:
        impl_names = []
        target = meta.get("class_name", class_name)
        for m in implementors["metadatas"]:
            ifaces = [s.strip() for s in m.get("implements", "").split(",")]
            if target in ifaces:
                impl_names.append(m.get("fqn", "?"))
        if impl_names:
            impl_names.sort()
            lines.append(f"\n## Implementors ({len(impl_names)}):")
            for cn in impl_names[:30]:
                lines.append(f"  - {cn}")
            if len(impl_names) > 30:
                lines.append(f"  - ... and {len(impl_names) - 30} more")

    return "\n".join(lines)


@mcp.tool()
def list_packages() -> str:
    """List all Java packages found in the indexed API.

    Useful to understand the structure of the Hytale server API.
    """
    from indexer import get_collection, API_COLLECTION

    collection = get_collection(API_COLLECTION)
    if collection.count() == 0:
        return "No API indexed. Run index_jar first."

    results = collection.get(
        where={"type": "class_overview"},
        include=["metadatas"],
    )

    packages: dict[str, list[str]] = {}
    for meta in results["metadatas"]:
        pkg = meta.get("package", "(default)")
        cls = meta.get("class_name", "?")
        packages.setdefault(pkg, []).append(cls)

    lines = []
    for pkg in sorted(packages):
        classes = sorted(packages[pkg])
        lines.append(f"\n{pkg} ({len(classes)} classes)")
        for cls in classes[:20]:
            lines.append(f"  - {cls}")
        if len(classes) > 20:
            lines.append(f"  - ... and {len(classes) - 20} more")

    return f"Found {len(packages)} packages, {sum(len(v) for v in packages.values())} classes total:\n" + "\n".join(lines)


@mcp.tool()
def get_index_status() -> str:
    """Check what's currently indexed — API version, chunk counts, last update times, and background jobs."""
    from indexer import get_status

    status = get_status()
    lines = ["# Hytale Docs RAG Status\n"]

    active_jobs = {jid: j for jid, j in _jobs.items() if j["status"] not in ("done", "failed")}
    recent_jobs = {jid: j for jid, j in _jobs.items() if j["status"] in ("done", "failed")}

    if active_jobs:
        lines.append("## Active Jobs")
        for jid, j in active_jobs.items():
            elapsed = int(time.time() - j.get("started_at", 0))
            lines.append(
                f"  - [{jid}] {j['type']}: {j['status']} "
                f"({elapsed}s elapsed, {j.get('total_chunks', 0)} chunks)"
            )
        lines.append("")

    if recent_jobs:
        lines.append("## Recent Jobs")
        for jid, j in list(recent_jobs.items())[-5:]:
            if j["status"] == "done":
                lines.append(f"  - [{jid}] {j['type']}: {j.get('result', 'done')}")
            else:
                lines.append(f"  - [{jid}] {j['type']}: FAILED — {j.get('error', '?')}")
        lines.append("")

    api = status.get("api", {})
    if api.get("indexed"):
        lines.append(f"## Java API")
        lines.append(f"  - Jar: {api.get('jar', 'unknown')}")
        lines.append(f"  - Chunks: {api.get('chunks', '?')}")
        lines.append(f"  - Indexed at: {api.get('indexed_at', '?')}")
    else:
        lines.append("## Java API: NOT INDEXED — run index_jar")

    guides = status.get("guides", {})
    if guides.get("indexed"):
        lines.append(f"\n## Community Guides")
        lines.append(f"  - Chunks: {guides.get('chunks', '?')}")
        lines.append(f"  - Indexed at: {guides.get('indexed_at', '?')}")
    else:
        lines.append("\n## Community Guides: NOT INDEXED — run scrape_guides")

    mods = status.get("mods", {})
    if mods.get("indexed"):
        lines.append(f"\n## GitHub Mods")
        lines.append(f"  - Repos: {mods.get('repo_count', '?')}")
        lines.append(f"  - Chunks: {mods.get('chunks', '?')}")
        lines.append(f"  - Indexed at: {mods.get('indexed_at', '?')}")
    else:
        lines.append("\n## GitHub Mods: NOT INDEXED — run index_github_mods")

    return "\n".join(lines)


@mcp.tool()
def get_api_changes() -> str:
    """Show what changed in the last API re-index — new classes, removed classes, modified methods/fields.

    Only available after index_jar has been run at least twice (so there's a previous version to compare against).
    """
    from diffing import get_latest_diff

    diff = get_latest_diff()
    if diff is None:
        return (
            "No API diff available yet. A diff is generated automatically when you "
            "run index_jar on a new version of HytaleServer.jar (requires a previous "
            "snapshot to compare against)."
        )

    return diff["summary"]


@mcp.tool()
def save_snapshot(label: str = "") -> str:
    """Save a backup of the entire ChromaDB database so you can restore it later.

    Copies the full database directory. Use this before re-indexing to preserve
    the current state of all collections (API, guides, mods).

    Args:
        label: Optional label for the snapshot (e.g. "before-update", "v1.2")
    """
    from snapshots import save_snapshot as do_save

    result = do_save(label)
    if "error" in result:
        return result["error"]

    return (
        f"Snapshot saved:\n"
        f"  - File: {result['file']}\n"
        f"  - Chunks: {result['count']}\n"
        f"  - Size: {result['size_mb']} MB\n"
        f"  - Created: {result['created_at']}"
    )


@mcp.tool()
def list_snapshots() -> str:
    """List all saved database snapshots."""
    from snapshots import list_snapshots as do_list

    records = do_list()
    if not records:
        return "No snapshots saved yet. Use save_snapshot to create one."

    lines = [f"# Snapshots ({len(records)})\n"]
    for r in records:
        label_str = f' "{r["label"]}"' if r.get("label") else ""
        lines.append(
            f"  - {r['file']}{label_str}\n"
            f"    {r['count']} chunks | {r.get('size_mb', '?')} MB | {r['created_at']}"
        )

    return "\n".join(lines)


@mcp.tool()
def restore_snapshot(filename: str) -> str:
    """Restore the database from a previously saved snapshot.

    This replaces ALL current collections with the snapshot's data.
    The MCP server and dashboard must be stopped first on Windows.

    Args:
        filename: The snapshot filename (from list_snapshots)
    """
    from snapshots import restore_snapshot as do_restore

    result = do_restore(filename)
    if "error" in result:
        return result["error"]

    return (
        f"Snapshot restored:\n"
        f"  - File: {result['restored']}\n"
        f"  - Chunks: {result['chunks']}\n"
        f"  - No re-embedding needed (embeddings were saved)"
    )


@mcp.tool()
def get_method_source(class_name: str, method_name: str) -> str:
    """Get the full untruncated source code of a specific method from the decompiled API.

    Reads directly from the decompiled .java file so you get the complete method body,
    not the 1500-char truncated chunk from the index.

    Args:
        class_name: Simple class name (e.g. "BlockType") or fully qualified name
        method_name: Method name to find (e.g. "register", "<init>" for constructor)
    """
    import re as _re
    from indexer import get_collection
    from config import API_COLLECTION

    collection = get_collection(API_COLLECTION)

    if "." in class_name:
        results = collection.get(
            where={"$and": [{"fqn": class_name}, {"type": "class_overview"}]},
            include=["metadatas"],
        )
    else:
        results = collection.get(
            where={"$and": [{"class_name": class_name}, {"type": "class_overview"}]},
            include=["metadatas"],
        )
    if not results["ids"]:
        results = collection.get(
            where={"class_name": class_name},
            include=["metadatas"],
        )
    if not results["ids"]:
        return f"Class '{class_name}' not found."

    file_path = results["metadatas"][0].get("file", "")
    if not file_path:
        return "No file path found for this class."

    java_file = DECOMPILED_DIR / "HytaleServer" / file_path
    if not java_file.exists():
        return f"Decompiled file not found: {file_path}"

    source = java_file.read_text(encoding="utf-8", errors="replace")

    if method_name == "<init>":
        simple_name = class_name.split(".")[-1]
        pattern = _re.compile(
            r"((?:/\*\*.*?\*/\s*)?)"
            r"((?:@\w+(?:\([^)]*\))?\s*)*)"
            r"((?:public|protected|private)\s+)"
            + _re.escape(simple_name)
            + r"\s*\([^)]*\)",
            _re.DOTALL,
        )
    else:
        pattern = _re.compile(
            r"((?:/\*\*.*?\*/\s*)?)"
            r"((?:@\w+(?:\([^)]*\))?\s*)*)"
            r"((?:public|protected|private|static|final|abstract|synchronized|native|default)\s+)"
            r"[\w<>\[\], ?&]+\s+"
            + _re.escape(method_name)
            + r"\s*\([^)]*\)(?:\s*throws\s+[\w,\s]+)?",
            _re.DOTALL,
        )

    matches = list(pattern.finditer(source))
    if not matches:
        return f"Method '{method_name}' not found in {class_name}."

    parts = []
    for m in matches:
        start = m.start()
        brace = source.find("{", m.end())
        if brace == -1 or brace - m.end() > 20:
            parts.append(source[start:m.end()])
            continue
        depth = 0
        end = brace
        for i in range(brace, len(source)):
            if source[i] == "{":
                depth += 1
            elif source[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        parts.append(source[start:end])

    fqn = results["metadatas"][0].get("fqn", class_name)
    header = f"// {fqn}.{method_name} — full source\n\n"
    return header + "\n\n// ---\n\n".join(parts)


@mcp.tool()
def find_usages(name: str, limit: int = 10) -> str:
    """Find where a class or method is used across the decompiled API and mod examples.

    Greps through the decompiled Java sources for references using word-boundary matching.

    Args:
        name: Class name, method name, or fully qualified name to search for
        limit: Max results to return (default 10)
    """
    import re as _re

    results = []
    pattern = _re.compile(r"\b" + _re.escape(name) + r"\b")

    decompiled_dir = DECOMPILED_DIR / "HytaleServer"
    if decompiled_dir.exists():
        for java_file in decompiled_dir.rglob("*.java"):
            try:
                source = java_file.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if name not in source:
                continue

            rel = java_file.relative_to(decompiled_dir)
            for i, line in enumerate(source.split("\n"), 1):
                if pattern.search(line) and not line.strip().startswith("package "):
                    results.append({
                        "file": str(rel),
                        "line": i,
                        "text": line.strip()[:200],
                        "source": "api",
                    })
                    if len(results) >= limit * 3:
                        break
            if len(results) >= limit * 3:
                break

    from indexer import search as idx_search
    mod_hits = idx_search(name, MODS_COLLECTION, n_results=limit)
    for hit in mod_hits:
        results.append({
            "file": hit["metadata"].get("file", ""),
            "line": 0,
            "text": hit["text"][:200],
            "source": f"mod:{hit['metadata'].get('repo', '?')}",
        })

    if not results:
        return f"No usages of '{name}' found."

    results = results[:limit]
    lines = [f"# Usages of '{name}' ({len(results)} results)\n"]
    for r in results:
        src = r["source"]
        loc = f"{r['file']}:{r['line']}" if r["line"] else r["file"]
        lines.append(f"[{src}] {loc}")
        lines.append(f"  {r['text']}")

    return "\n".join(lines)


def _format_results(results: list[dict]) -> str:
    parts = []
    for i, r in enumerate(results, 1):
        meta = r["metadata"]
        source_tag = f"[{meta.get('source', '?').upper()}]"

        header_parts = [f"**Result {i}** {source_tag}"]
        if meta.get("fqn"):
            header_parts.append(f"`{meta['fqn']}`")
        if meta.get("method_name"):
            header_parts.append(f"method: `{meta['method_name']}`")
        if meta.get("title"):
            header_parts.append(meta["title"])
        if meta.get("heading"):
            header_parts.append(f"> {meta['heading']}")
        if meta.get("url"):
            header_parts.append(f"({meta['url']})")
        if meta.get("repo"):
            header_parts.append(f"repo: {meta['repo']}")

        header = " | ".join(header_parts)
        rrf = r.get("rrf_score")
        dist = r.get("distance")
        if rrf is not None:
            score_str = f" (score: {rrf:.4f})"
        elif dist is not None:
            score_str = f" (similarity: {1 - dist:.2f})"
        else:
            score_str = ""

        parts.append(f"{header}{score_str}\n```\n{r['text']}\n```")

    return "\n\n---\n\n".join(parts)


if __name__ == "__main__":
    mcp.run()
