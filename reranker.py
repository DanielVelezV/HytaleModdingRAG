import os

_model = None
_available = None


def _is_available() -> bool:
    global _available
    if _available is not None:
        return _available
    if os.environ.get("DISABLE_RERANKER", "") == "1":
        _available = False
        return False
    try:
        from sentence_transformers import CrossEncoder  # noqa: F401
        _available = True
    except ImportError:
        _available = False
    return _available


def _get_model():
    global _model
    if _model is not None:
        return _model
    from sentence_transformers import CrossEncoder
    _model = CrossEncoder("BAAI/bge-reranker-base")
    return _model


def rerank(query: str, results: list[dict], top_k: int = 8) -> list[dict]:
    if not results or not _is_available() or len(results) <= top_k:
        return results[:top_k]

    model = _get_model()
    pairs = [(query, r["text"]) for r in results]
    scores = model.predict(pairs)

    for i, r in enumerate(results):
        r["rerank_score"] = float(scores[i])

    results.sort(key=lambda r: r["rerank_score"], reverse=True)
    return results[:top_k]
