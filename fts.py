import re
import sqlite3
from pathlib import Path

from config import DATA_DIR

FTS_DB = DATA_DIR / "fts.sqlite"

_conn: sqlite3.Connection | None = None

_STOPWORDS = frozenset({
    "how", "to", "a", "the", "in", "of", "for", "with",
    "and", "or", "is", "it", "do", "what", "can", "does",
    "are", "be", "by", "on", "an", "at", "from", "that",
    "this", "was", "which", "will", "not", "but", "have",
    "has", "had", "all", "been", "would", "there", "their",
})

_CAMEL_RE = re.compile(r"[A-Z][a-z]+|[A-Z]+(?=[A-Z]|$)|[a-z]+")
_IDENT_RE = re.compile(r'[A-Z]|[._]')


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is not None:
        return _conn
    FTS_DB.parent.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(str(FTS_DB), check_same_thread=False)
    _conn.execute("PRAGMA journal_mode=WAL")
    return _conn


def _ensure_table(conn: sqlite3.Connection, table: str):
    conn.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS [{table}] USING fts5("
        f"  chunk_id, fqn, class_name, method_name, body, "
        f"  tokenize='porter unicode61'"
        f")"
    )


def build_fts(collection_name: str, chunks: list[dict]):
    conn = _get_conn()
    table = f"fts_{collection_name}"
    conn.execute(f"DROP TABLE IF EXISTS [{table}]")
    _ensure_table(conn, table)

    batch = []
    for c in chunks:
        meta = c.get("metadata", {})
        batch.append((
            c["id"],
            meta.get("fqn", ""),
            meta.get("class_name", ""),
            meta.get("method_name", ""),
            c["text"],
        ))
        if len(batch) >= 500:
            conn.executemany(
                f"INSERT INTO [{table}](chunk_id, fqn, class_name, method_name, body) "
                f"VALUES (?, ?, ?, ?, ?)",
                batch,
            )
            batch.clear()
    if batch:
        conn.executemany(
            f"INSERT INTO [{table}](chunk_id, fqn, class_name, method_name, body) "
            f"VALUES (?, ?, ?, ?, ?)",
            batch,
        )
    conn.commit()


def keyword_search(collection_name: str, query: str, n_results: int = 30) -> list[dict]:
    conn = _get_conn()
    table = f"fts_{collection_name}"

    try:
        conn.execute(f"SELECT 1 FROM [{table}] LIMIT 1")
    except sqlite3.OperationalError:
        return []

    fts_query = _build_fts_query(query)
    if not fts_query:
        return []

    rows = conn.execute(
        f"SELECT chunk_id, fqn, class_name, method_name, body, rank "
        f"FROM [{table}] "
        f"WHERE [{table}] MATCH ? "
        f"ORDER BY rank "
        f"LIMIT ?",
        (fts_query, n_results),
    ).fetchall()

    results = []
    for row in rows:
        results.append({
            "id": row[0],
            "fqn": row[1],
            "class_name": row[2],
            "method_name": row[3],
            "text": row[4],
            "fts_rank": row[5],
        })
    return results


def _build_fts_query(query: str) -> str:
    tokens = query.split()
    groups: list[str] = []
    for t in tokens:
        cleaned = "".join(c for c in t if c.isalnum() or c in "._")
        if not cleaned or len(cleaned) < 2:
            continue
        if cleaned.lower() in _STOPWORDS:
            continue
        camel_parts = _CAMEL_RE.findall(cleaned)
        if len(camel_parts) > 1:
            alternatives = [f'"{cleaned}"']
            for part in camel_parts:
                if len(part) >= 2 and part.lower() not in _STOPWORDS:
                    alternatives.append(f'"{part}"')
            groups.append("(" + " OR ".join(alternatives) + ")")
        else:
            groups.append(f'"{cleaned}"')
    if not groups:
        return ""
    return " OR ".join(groups)


def hybrid_search(
    query: str,
    collection_name: str,
    dense_results: list[dict],
    n_results: int = 10,
    rrf_k: int = 60,
    kw_weight: float = 0.3,
) -> list[dict]:
    has_identifier = any(_IDENT_RE.search(t) for t in query.split())
    kw_results = keyword_search(collection_name, query, n_results=n_results * 3) if has_identifier else []

    dense_ids = {r["id"]: rank for rank, r in enumerate(dense_results)}
    kw_ids = {r["id"]: rank for rank, r in enumerate(kw_results)}
    all_ids = set(dense_ids.keys()) | set(kw_ids.keys())

    scored: list[tuple[str, float]] = []
    for doc_id in all_ids:
        score = 0.0
        if doc_id in dense_ids:
            score += 1.0 / (rrf_k + dense_ids[doc_id])
        if doc_id in kw_ids:
            score += kw_weight / (rrf_k + kw_ids[doc_id])
        scored.append((doc_id, score))

    scored.sort(key=lambda x: x[1], reverse=True)

    doc_map: dict[str, dict] = {}
    for r in dense_results:
        doc_map[r["id"]] = r

    fts_only_ids = [doc_id for doc_id, _ in scored[:n_results] if doc_id not in doc_map]
    if fts_only_ids:
        try:
            from indexer import get_collection
            collection = get_collection(collection_name)
            enriched = collection.get(
                ids=fts_only_ids,
                include=["metadatas", "documents"],
            )
            for i, eid in enumerate(enriched["ids"]):
                doc_map[eid] = {
                    "id": eid,
                    "text": enriched["documents"][i],
                    "metadata": enriched["metadatas"][i],
                    "distance": None,
                }
        except Exception:
            kw_map = {r["id"]: r for r in kw_results}
            for doc_id in fts_only_ids:
                if doc_id not in doc_map and doc_id in kw_map:
                    r = kw_map[doc_id]
                    doc_map[doc_id] = {
                        "id": doc_id,
                        "text": r["text"],
                        "metadata": {
                            "fqn": r.get("fqn", ""),
                            "class_name": r.get("class_name", ""),
                            "method_name": r.get("method_name", ""),
                        },
                        "distance": None,
                    }

    merged = []
    for doc_id, rrf_score in scored[:n_results]:
        if doc_id in doc_map:
            entry = doc_map[doc_id].copy()
            entry["rrf_score"] = rrf_score
            merged.append(entry)

    return merged
