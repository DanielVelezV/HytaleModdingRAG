"""ChromaDB-free backing store for lite mode.

Serves the metadata/document lookups the boost functions need from
data/meta.sqlite (metadata) joined with data/fts.sqlite (chunk body),
exposing the subset of the Chroma collection API that server.py uses.
"""
import sqlite3
from pathlib import Path

from config import DATA_DIR

META_DB = DATA_DIR / "meta.sqlite"
FTS_DB = DATA_DIR / "fts.sqlite"

_META_FIELDS = ["type", "fqn", "class_name", "method_name", "package",
                "source", "extends", "extends_fqn", "implements", "file"]

_COLLECTION_TO_TABLE = {
    "hytale_api": "fts_hytale_api",
    "hytale_guides": "fts_hytale_guides",
    "hytale_mods": "fts_hytale_mods",
}

# metadata 'source' values are singular; collection names are plural
_COLLECTION_TO_SOURCE = {
    "hytale_api": "api",
    "hytale_guides": "guide",
    "hytale_mods": "mod",
}


def available() -> bool:
    return META_DB.exists() and FTS_DB.exists()


def _flatten_where(where: dict) -> dict:
    """Turn Chroma's {'$and': [{'a': 1}, {'b': 2}]} into {'a': 1, 'b': 2}."""
    if not where:
        return {}
    if "$and" in where:
        out = {}
        for clause in where["$and"]:
            out.update(_flatten_where(clause))
        return out
    return dict(where)


class LiteCollection:
    def __init__(self, name: str):
        self.name = name
        self._meta = sqlite3.connect(f"file:{META_DB}?mode=ro", uri=True)
        self._fts = sqlite3.connect(f"file:{FTS_DB}?mode=ro", uri=True)
        self._table = _COLLECTION_TO_TABLE.get(name)

    def _bodies(self, chunk_ids: list[str]) -> dict[str, str]:
        if not self._table or not chunk_ids:
            return {}
        out: dict[str, str] = {}
        for i in range(0, len(chunk_ids), 500):
            batch = chunk_ids[i:i + 500]
            q = ",".join("?" * len(batch))
            rows = self._fts.execute(
                f"SELECT chunk_id, body FROM [{self._table}] WHERE chunk_id IN ({q})",
                batch,
            ).fetchall()
            out.update({r[0]: r[1] for r in rows})
        return out

    def get(self, ids=None, where=None, include=None, limit=None, **_):
        conds, params = [], []

        if ids:
            conds.append(f"chunk_id IN ({','.join('?' * len(ids))})")
            params.extend(ids)

        flat = _flatten_where(where)
        for k, v in flat.items():
            if k in _META_FIELDS:
                conds.append(f"{k} = ?")
                params.append(v)

        # scope to this collection's source
        source = _COLLECTION_TO_SOURCE.get(self.name)
        if source and "source" not in flat:
            conds.append("source = ?")
            params.append(source)

        sql = f"SELECT chunk_id, {','.join(_META_FIELDS)} FROM chunk_meta"
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        if limit:
            sql += f" LIMIT {int(limit)}"

        rows = self._meta.execute(sql, params).fetchall()
        out_ids = [r[0] for r in rows]
        metadatas = [
            {f: r[i + 1] for i, f in enumerate(_META_FIELDS) if r[i + 1]}
            for r in rows
        ]

        result = {"ids": out_ids, "metadatas": metadatas}
        inc = include or []
        if "documents" in inc:
            bodies = self._bodies(out_ids)
            result["documents"] = [bodies.get(cid, "") for cid in out_ids]
        return result

    def count(self) -> int:
        source = _COLLECTION_TO_SOURCE.get(self.name)
        return self._meta.execute(
            "SELECT COUNT(*) FROM chunk_meta WHERE source = ?", (source,)
        ).fetchone()[0]


_cache: dict[str, LiteCollection] = {}


def get_lite_collection(name: str) -> LiteCollection:
    if name not in _cache:
        _cache[name] = LiteCollection(name)
    return _cache[name]
