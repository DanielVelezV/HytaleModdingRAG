import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import chromadb

from config import (
    CHROMADB_DIR,
    API_COLLECTION,
    GUIDES_COLLECTION,
    MODS_COLLECTION,
    MAX_CHUNK_SIZE,
    CHUNK_OVERLAP,
    META_FILE,
    OLLAMA_BASE_URL,
    OLLAMA_EMBED_MODEL,
)


class _OllamaEmbedding(chromadb.EmbeddingFunction):
    def __call__(self, input: list[str]) -> list[list[float]]:
        import ollama
        prefixed = [f"search_document: {t}" for t in input]
        result = ollama.embed(model=OLLAMA_EMBED_MODEL, input=prefixed)
        return result.embeddings

    def embed_query(self, query: str) -> list[float]:
        import ollama
        result = ollama.embed(model=OLLAMA_EMBED_MODEL, input=[f"search_query: {query}"])
        return result.embeddings[0]


def _get_embedding_fn():
    return _OllamaEmbedding()


def get_client() -> chromadb.ClientAPI:
    CHROMADB_DIR.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(CHROMADB_DIR))


def get_collection(name: str):
    client = get_client()
    ef = _get_embedding_fn()
    return client.get_or_create_collection(
        name=name,
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )


def _chunk_id(text: str, prefix: str) -> str:
    h = hashlib.sha256(text.encode()).hexdigest()[:24]
    return f"{prefix}_{h}"


# --- Java source parsing ---

from java_parser import parse_java_files  # noqa: F401


_MODIFIER_PATTERN = r"(?:(?:public|protected|private|abstract|final|static|sealed|non-sealed|strictfp)\s+)*"


def _extract_package(source: str) -> str:
    m = re.search(r"^\s*package\s+([\w.]+)\s*;", source, re.MULTILINE)
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# Regex extraction functions (used by github_scraper fallback)
# ---------------------------------------------------------------------------

def _extract_inheritance(source: str) -> dict:
    """Returns dict with 'extends' and 'implements' string keys."""
    m = re.search(
        _MODIFIER_PATTERN
        + r"(?:class|interface|enum|record)\s+\w+(?:<[^>]*>)?"
        r"(?:\s+extends\s+([\w.<>, ?&]+?))?"
        r"(?:\s+implements\s+([\w.<>, ?&]+?))?"
        r"\s*\{",
        source,
    )
    if not m:
        return {"extends": "", "implements": ""}

    extends_raw = m.group(1) or ""
    implements_raw = m.group(2) or ""

    parent = extends_raw.split("<")[0].strip() if extends_raw else ""
    interfaces = [
        i.split("<")[0].strip()
        for i in implements_raw.split(",")
        if i.strip()
    ] if implements_raw else []

    return {
        "extends": parent,
        "implements": ", ".join(interfaces),
    }


def _extract_nested_types(source: str, outer_fqn: str, package: str) -> list[dict]:
    """Returns list of dicts with 'name' and 'source' keys."""
    nested = []
    pattern = re.compile(
        r"(?:^|\n)([ \t]+)" + _MODIFIER_PATTERN
        + r"(class|interface|enum|record)\s+(\w+)"
        + r"(?:<[^>]*>)?"
        + r"(?:\s+extends\s+[\w.<>, ?&]+?)?"
        + r"(?:\s+implements\s+[\w.<>, ?&]+?)?"
        + r"\s*\{",
        re.MULTILINE,
    )
    for m in pattern.finditer(source):
        indent = m.group(1) if m.group(1) else ""
        if len(indent) == 0:
            continue
        type_name = m.group(3)
        start = m.start()
        brace_pos = source.find("{", m.end() - 1)
        if brace_pos == -1:
            continue
        body = _extract_brace_block(source, brace_pos)
        if body:
            full_source = source[start:brace_pos] + body
            nested.append({"name": type_name, "source": full_source})
    return nested


def _extract_brace_block(source: str, start: int) -> str:
    if start >= len(source) or source[start] != "{":
        return ""
    depth = 0
    for i in range(start, min(len(source), start + MAX_CHUNK_SIZE * 4)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start:i + 1]
    return source[start:start + MAX_CHUNK_SIZE * 2]


def _build_class_overview(
    source: str,
    fqn: str,
    package: str,
    inheritance: dict | None = None,
    inherited_methods: list[tuple[str, str]] | None = None,
) -> str:
    """Build a class overview string for embedding."""
    lines = source.split("\n")
    imports = [l.strip() for l in lines if l.strip().startswith("import ")]

    class_decl = ""
    for line in lines:
        if re.match(
            r"\s*" + _MODIFIER_PATTERN + r"(class|interface|enum|record)\s+",
            line,
        ):
            class_decl = line.strip()
            break

    signatures = []
    for m in re.finditer(
        r"((?:public|protected|static|final|abstract|synchronized|native|default)\s+)"
        r"(?!class|interface|enum|record)"
        r"[\w<>\[\], ?&]+\s+(\w+)\s*\([^)]*\)",
        source,
    ):
        sig = m.group(0).strip()
        if len(sig) < 300:
            signatures.append(f"  {sig};")

    fields = []
    for m in re.finditer(
        r"^\s+((?:public|protected|static|final)\s+)"
        r"([\w<>\[\], ?&]+)\s+(\w+)\s*[;=]",
        source,
        re.MULTILINE,
    ):
        fields.append(f"  {m.group(0).strip().rstrip('=').strip()};")

    overview_parts = [f"package {package};" if package else ""]
    if imports[:10]:
        overview_parts.append("\n".join(imports[:10]))
        if len(imports) > 10:
            overview_parts.append(f"// ... {len(imports) - 10} more imports")
    if class_decl:
        overview_parts.append(class_decl)
    if inheritance:
        if inheritance["extends"]:
            overview_parts.append(f"// Extends: {inheritance['extends']}")
        if inheritance["implements"]:
            overview_parts.append(f"// Implements: {inheritance['implements']}")
    if fields:
        overview_parts.append("// Fields:")
        overview_parts.append("\n".join(fields[:30]))
    if signatures:
        overview_parts.append("// Methods:")
        overview_parts.append("\n".join(signatures[:50]))

    if inherited_methods:
        by_parent: dict[str, list[str]] = {}
        for sig, parent_fqn in inherited_methods:
            by_parent.setdefault(parent_fqn, []).append(sig)
        inherited_lines = []
        for parent_fqn, sigs in by_parent.items():
            for sig in sigs[:20]:
                inherited_lines.append(f"  {sig}; // inherited from {parent_fqn}")
        if inherited_lines:
            overview_parts.append("// Inherited methods:")
            overview_parts.append("\n".join(inherited_lines[:40]))

    return "\n\n".join(p for p in overview_parts if p)


def _extract_methods(source: str) -> list[tuple[str, str, str]]:
    """Returns list of (method_name, method_source, visibility)."""
    methods = []
    pattern = re.compile(
        r"((?:/\*\*.*?\*/\s*)?)"
        r"((?:@\w+(?:\([^)]*\))?\s*)*)"
        r"((?:public|protected|private|static|final|abstract|synchronized|native|default)\s+)"
        r"(?!class|interface|enum|record)"
        r"([\w<>\[\], ?&]+\s+(\w+)\s*\([^)]*\)(?:\s*throws\s+[\w,\s]+)?)",
        re.DOTALL,
    )
    for m in pattern.finditer(source):
        javadoc = m.group(1).strip()
        annotations = m.group(2).strip()
        modifiers = m.group(3).strip()
        full_sig = m.group(3) + m.group(4)
        method_name = m.group(5)

        visibility = "package-private"
        if "public" in modifiers:
            visibility = "public"
        elif "protected" in modifiers:
            visibility = "protected"
        elif "private" in modifiers:
            visibility = "private"

        start = m.end()
        body = _extract_body(source, start)
        full_method = "\n".join(filter(None, [javadoc, annotations, full_sig.strip()]))
        if body:
            full_method += " " + body
        if len(full_method) > MAX_CHUNK_SIZE:
            full_method = full_method[:MAX_CHUNK_SIZE] + "\n// ... truncated"
        methods.append((method_name, full_method, visibility))
    return methods


def _extract_constructors(source: str, class_name: str) -> list[tuple[str, str]]:
    """Returns list of (constructor_source, visibility)."""
    constructors = []
    pattern = re.compile(
        r"((?:/\*\*.*?\*/\s*)?)"
        r"((?:@\w+(?:\([^)]*\))?\s*)*)"
        r"((?:public|protected|private)\s+)"
        + re.escape(class_name)
        + r"\s*\([^)]*\)(?:\s*throws\s+[\w,\s]+)?",
        re.DOTALL,
    )
    for m in pattern.finditer(source):
        javadoc = m.group(1).strip()
        annotations = m.group(2).strip()
        modifiers = m.group(3).strip()

        visibility = "package-private"
        if "public" in modifiers:
            visibility = "public"
        elif "protected" in modifiers:
            visibility = "protected"
        elif "private" in modifiers:
            visibility = "private"

        start = m.end()
        body = _extract_body(source, start)
        full_ctor = "\n".join(filter(None, [javadoc, annotations, m.group(0).strip()]))
        if body:
            full_ctor += " " + body
        if len(full_ctor) > MAX_CHUNK_SIZE:
            full_ctor = full_ctor[:MAX_CHUNK_SIZE] + "\n// ... truncated"
        constructors.append((full_ctor, visibility))
    return constructors


def _extract_body(source: str, start: int) -> str:
    idx = source.find("{", start)
    if idx == -1 or idx - start > 20:
        return ""
    depth = 0
    end = idx
    for i in range(idx, min(len(source), idx + MAX_CHUNK_SIZE)):
        c = source[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    else:
        return source[idx:idx + MAX_CHUNK_SIZE] + "\n// ... truncated"
    return source[idx:end]


# --- JSON config parsing ---

def parse_json_configs(source_dir: Path) -> list[dict]:
    resources_dir = source_dir / "_resources"
    if not resources_dir.exists():
        return []

    chunks = []
    for json_file in sorted(resources_dir.rglob("*.json")):
        try:
            raw = json_file.read_text(encoding="utf-8", errors="replace")
            data = json.loads(raw)
        except Exception:
            continue

        rel_path = json_file.relative_to(resources_dir)
        config_type = _infer_config_type(str(rel_path))

        if isinstance(data, list):
            for i, item in enumerate(data):
                item_text = json.dumps(item, indent=2)
                name = item.get("Name", item.get("name", f"item_{i}"))
                if len(item_text) > MAX_CHUNK_SIZE:
                    item_text = item_text[:MAX_CHUNK_SIZE] + "\n// ... truncated"
                chunks.append({
                    "id": _chunk_id(f"config:{rel_path}:{i}", "config"),
                    "text": f"// Config: {rel_path} [{name}]\n{item_text}",
                    "metadata": {
                        "source": "api",
                        "type": "config",
                        "config_type": config_type,
                        "config_name": str(name),
                        "file": str(rel_path),
                    },
                })
        elif isinstance(data, dict):
            text = json.dumps(data, indent=2)
            if len(text) > MAX_CHUNK_SIZE:
                for sub in _split_text(text, MAX_CHUNK_SIZE, CHUNK_OVERLAP):
                    chunks.append({
                        "id": _chunk_id(f"config:{rel_path}:{sub[:80]}", "config"),
                        "text": f"// Config: {rel_path}\n{sub}",
                        "metadata": {
                            "source": "api",
                            "type": "config",
                            "config_type": config_type,
                            "config_name": json_file.stem,
                            "file": str(rel_path),
                        },
                    })
            else:
                chunks.append({
                    "id": _chunk_id(f"config:{rel_path}", "config"),
                    "text": f"// Config: {rel_path}\n{text}",
                    "metadata": {
                        "source": "api",
                        "type": "config",
                        "config_type": config_type,
                        "config_name": json_file.stem,
                        "file": str(rel_path),
                    },
                })
    return chunks


def _infer_config_type(rel_path: str) -> str:
    if "manifest" in rel_path.lower():
        return "plugin_manifest"
    if "migration" in rel_path.lower():
        return "block_migration"
    return "config"


# --- Guide chunking ---

def chunk_guides(pages: list[dict]) -> list[dict]:
    chunks = []
    for page in pages:
        for section in page.get("sections", []):
            text = f"# {page['title']}\n## {section['heading']}\n{section['text']}"
            if len(text) > MAX_CHUNK_SIZE:
                for sub_chunk in _split_text(text, MAX_CHUNK_SIZE, CHUNK_OVERLAP):
                    chunks.append({
                        "id": _chunk_id(f"guide:{page['title']}:{sub_chunk[:80]}", "guide"),
                        "text": sub_chunk,
                        "metadata": {
                            "source": "guide",
                            "type": "guide_section",
                            "title": page["title"],
                            "heading": section["heading"],
                            "url": page.get("url", ""),
                        },
                    })
            else:
                chunks.append({
                    "id": _chunk_id(f"guide:{page['title']}:{section['heading']}", "guide"),
                    "text": text,
                    "metadata": {
                        "source": "guide",
                        "type": "guide_section",
                        "title": page["title"],
                        "heading": section["heading"],
                        "url": page.get("url", ""),
                    },
                })
    return chunks


def _split_text(text: str, max_size: int, overlap: int) -> list[str]:
    parts = []
    start = 0
    while start < len(text):
        end = start + max_size
        if end < len(text):
            split_at = text.rfind("\n", start + max_size // 2, end)
            if split_at > start:
                end = split_at
        parts.append(text[start:end].strip())
        next_start = end - overlap
        if next_start <= start:
            next_start = end
        start = next_start
    return [p for p in parts if p]


# --- Index operations ---

def _deduplicate_chunks(chunks: list[dict]) -> list[dict]:
    seen = set()
    unique = []
    for c in chunks:
        if c["id"] not in seen:
            seen.add(c["id"])
            unique.append(c)
    return unique


def index_api_chunks(chunks: list[dict], jar_name: str = "", jar_hash: str = ""):
    chunks = _deduplicate_chunks(chunks)
    client = get_client()
    ef = _get_embedding_fn()

    temp_name = f"{API_COLLECTION}__building"
    try:
        client.delete_collection(temp_name)
    except Exception:
        pass

    temp_col = client.get_or_create_collection(
        name=temp_name,
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )

    batch_size = 64
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        temp_col.add(
            ids=[c["id"] for c in batch],
            documents=[c["text"] for c in batch],
            metadatas=[c["metadata"] for c in batch],
        )

    # Swap: delete old, rename temp
    try:
        client.delete_collection(API_COLLECTION)
    except Exception:
        pass
    temp_col.modify(name=API_COLLECTION)

    # C4: Build FTS AFTER the collection swap so the live table matches the live collection
    from fts import build_fts
    build_fts(API_COLLECTION, chunks)

    _save_meta("api", {
        "jar": jar_name,
        "jar_hash": jar_hash,
        "chunk_count": len(chunks),
        "indexed_at": datetime.now(timezone.utc).isoformat(),
        "embed_model": OLLAMA_EMBED_MODEL,
    })


def index_guide_chunks(chunks: list[dict]):
    chunks = _deduplicate_chunks(chunks)
    client = get_client()
    ef = _get_embedding_fn()

    temp_name = f"{GUIDES_COLLECTION}__building"
    try:
        client.delete_collection(temp_name)
    except Exception:
        pass

    temp_col = client.get_or_create_collection(
        name=temp_name,
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )

    batch_size = 64
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        temp_col.add(
            ids=[c["id"] for c in batch],
            documents=[c["text"] for c in batch],
            metadatas=[c["metadata"] for c in batch],
        )

    try:
        client.delete_collection(GUIDES_COLLECTION)
    except Exception:
        pass
    temp_col.modify(name=GUIDES_COLLECTION)

    from fts import build_fts
    build_fts(GUIDES_COLLECTION, chunks)

    _save_meta("guides", {
        "chunk_count": len(chunks),
        "indexed_at": datetime.now(timezone.utc).isoformat(),
        "embed_model": OLLAMA_EMBED_MODEL,
    })


def index_mod_chunks(chunks: list[dict], indexed_repos: list[str] | None = None):
    chunks = _deduplicate_chunks(chunks)
    client = get_client()
    ef = _get_embedding_fn()

    temp_name = f"{MODS_COLLECTION}__building"
    try:
        client.delete_collection(temp_name)
    except Exception:
        pass

    temp_col = client.get_or_create_collection(
        name=temp_name,
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )

    batch_size = 64
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        temp_col.add(
            ids=[c["id"] for c in batch],
            documents=[c["text"] for c in batch],
            metadatas=[c["metadata"] for c in batch],
        )

    try:
        client.delete_collection(MODS_COLLECTION)
    except Exception:
        pass
    temp_col.modify(name=MODS_COLLECTION)

    from fts import build_fts
    build_fts(MODS_COLLECTION, chunks)

    _save_meta("mods", {
        "chunk_count": len(chunks),
        "repo_count": len(indexed_repos) if indexed_repos else 0,
        "repos": indexed_repos or [],
        "indexed_at": datetime.now(timezone.utc).isoformat(),
        "embed_model": OLLAMA_EMBED_MODEL,
    })


def _check_model_match(collection_name: str) -> str | None:
    meta = _load_meta()
    col_key = {API_COLLECTION: "api", GUIDES_COLLECTION: "guides", MODS_COLLECTION: "mods"}.get(collection_name)
    if not col_key:
        return None
    indexed_model = meta.get(col_key, {}).get("embed_model", "")
    if indexed_model and indexed_model != OLLAMA_EMBED_MODEL:
        return (
            f"WARNING: {collection_name} was indexed with '{indexed_model}' "
            f"but current model is '{OLLAMA_EMBED_MODEL}'. "
            f"Results may be inaccurate. Re-index to fix."
        )
    return None


def search(
    query: str,
    collection_name: str,
    n_results: int = 10,
    package_filter: str = "",
    type_filter: str = "",
) -> list[dict]:
    """Pure dense search. Hybrid fusion is applied by the server tools, not here."""
    try:
        collection = get_collection(collection_name)
    except Exception:
        return []

    if collection.count() == 0:
        return []

    warning = _check_model_match(collection_name)
    if warning:
        import logging
        logging.warning(warning)

    where_clauses: list[dict] = []
    if package_filter:
        where_clauses.append({"package": {"$gte": package_filter}})
        upper = package_filter[:-1] + chr(ord(package_filter[-1]) + 1)
        where_clauses.append({"package": {"$lt": upper}})
    if type_filter:
        where_clauses.append({"type": type_filter})

    where = None
    if len(where_clauses) == 1:
        where = where_clauses[0]
    elif len(where_clauses) > 1:
        where = {"$and": where_clauses}

    ef = _get_embedding_fn()
    query_embedding = ef.embed_query(query)

    query_kwargs: dict = {
        "query_embeddings": [query_embedding],
        "n_results": min(n_results, collection.count()),
    }
    if where:
        query_kwargs["where"] = where

    results = collection.query(**query_kwargs)

    items = []
    for i in range(len(results["ids"][0])):
        items.append({
            "id": results["ids"][0][i],
            "text": results["documents"][0][i],
            "metadata": results["metadatas"][0][i],
            "distance": results["distances"][0][i] if results.get("distances") else None,
        })

    return items


def get_status() -> dict:
    meta = _load_meta()
    client = get_client()

    status = {"api": None, "guides": None, "mods": None}

    try:
        api_col = client.get_collection(API_COLLECTION)
        status["api"] = {
            "indexed": True,
            "chunks": api_col.count(),
            **(meta.get("api", {})),
        }
    except Exception:
        status["api"] = {"indexed": False}

    try:
        guides_col = client.get_collection(GUIDES_COLLECTION)
        status["guides"] = {
            "indexed": True,
            "chunks": guides_col.count(),
            **(meta.get("guides", {})),
        }
    except Exception:
        status["guides"] = {"indexed": False}

    try:
        mods_col = client.get_collection(MODS_COLLECTION)
        status["mods"] = {
            "indexed": True,
            "chunks": mods_col.count(),
            **(meta.get("mods", {})),
        }
    except Exception:
        status["mods"] = {"indexed": False}

    return status


def compute_jar_hash(jar_path: str) -> str:
    h = hashlib.sha256()
    with open(jar_path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


def check_jar_changed(jar_path: str) -> bool:
    meta = _load_meta()
    old_hash = meta.get("api", {}).get("jar_hash", "")
    if not old_hash:
        return True
    return compute_jar_hash(jar_path) != old_hash


def _save_meta(key: str, data: dict):
    META_FILE.parent.mkdir(parents=True, exist_ok=True)
    meta = _load_meta()
    meta[key] = data
    META_FILE.write_text(json.dumps(meta, indent=2), encoding="utf-8")


def _load_meta() -> dict:
    if META_FILE.exists():
        return json.loads(META_FILE.read_text(encoding="utf-8"))
    return {}
