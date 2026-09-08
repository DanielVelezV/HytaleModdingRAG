"""Read side of the index: collections, search, and tier detection.

Building the index lives elsewhere; this module only reads what was built.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import chromadb

from config import (
    DATA_DIR,
    CHROMADB_DIR,
    API_COLLECTION,
    GUIDES_COLLECTION,
    MODS_COLLECTION,
    META_FILE,
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


_LITE_MODE: bool | None = None


def lite_mode() -> bool:
    """True when search runs on keyword + boosts only (the default).

    Lite is the default tier. Full semantic search is opt-in and requires both
    a vector store on disk and a reachable Ollama; if either is missing we stay
    in lite mode rather than failing.

    Resolution order: env override -> recorded tier preference -> capability.
    Cached after the first call.
    """
    global _LITE_MODE
    if _LITE_MODE is not None:
        return _LITE_MODE

    env = os.environ.get("HYTALE_FORCE_LITE")
    if env == "1":
        _LITE_MODE = True
        return _LITE_MODE
    if env == "0":
        _LITE_MODE = False
        return _LITE_MODE

    # Explicit user preference, written at setup time.
    tier = None
    try:
        version_file = DATA_DIR / "version.json"
        if version_file.exists():
            tier = json.loads(version_file.read_text(encoding="utf-8")).get("tier")
    except Exception:
        pass

    if tier == "lite":
        _LITE_MODE = True
        return _LITE_MODE

    # Full tier requested (or unset): only use it if we actually can.
    if not (CHROMADB_DIR / "chroma.sqlite3").exists():
        _LITE_MODE = True
        return _LITE_MODE

    if tier != "full":
        # No recorded preference and vectors happen to be present: honour them.
        pass

    try:
        import ollama
        ollama.list()
        _LITE_MODE = False
    except Exception:
        _LITE_MODE = True
    return _LITE_MODE


def get_client() -> chromadb.ClientAPI:
    CHROMADB_DIR.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(CHROMADB_DIR))


def get_collection(name: str):
    if lite_mode():
        from lite_store import get_lite_collection
        return get_lite_collection(name)
    client = get_client()
    ef = _get_embedding_fn()
    return client.get_or_create_collection(
        name=name,
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )


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

    if lite_mode():
        # No vectors / no embedding backend: the keyword leg carries the search.
        return []

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


def _save_meta(key: str, data: dict):
    META_FILE.parent.mkdir(parents=True, exist_ok=True)
    meta = _load_meta()
    meta[key] = data
    META_FILE.write_text(json.dumps(meta, indent=2), encoding="utf-8")


def _load_meta() -> dict:
    if META_FILE.exists():
        return json.loads(META_FILE.read_text(encoding="utf-8"))
