"""Eval harness for Hytale Modding RAG search quality."""
import argparse
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import API_COLLECTION, GUIDES_COLLECTION, MODS_COLLECTION
from indexer import search


def load_questions() -> list[dict]:
    qfile = Path(__file__).parent / "questions.jsonl"
    questions = []
    for line in qfile.read_text(encoding="utf-8").strip().split("\n"):
        if line.strip():
            questions.append(json.loads(line))
    return questions


def check_hit(result: dict, expected_fqns: list[str]) -> bool:
    """Match ONLY against metadata.fqn — exact FQN or simple class name as last segment."""
    if not expected_fqns:
        return True
    meta = result.get("metadata", {})
    fqn = meta.get("fqn", "")
    class_name = meta.get("class_name", "")
    method_name = meta.get("method_name", "")
    for exp in expected_fqns:
        exp_lower = exp.lower()
        if exp_lower == fqn.lower():
            return True
        if exp_lower == class_name.lower():
            return True
        if "." in exp and fqn.lower().endswith("." + exp_lower.split(".")[-1]):
            if exp_lower in fqn.lower():
                return True
        if fqn.lower().endswith("." + exp_lower):
            return True
        if method_name and exp_lower == method_name.lower():
            return True
    return False


def reciprocal_rank(results: list[dict], expected_fqns: list[str]) -> float:
    for i, r in enumerate(results):
        if check_hit(r, expected_fqns):
            return 1.0 / (i + 1)
    return 0.0


def recall_at_k(results: list[dict], expected_fqns: list[str], k: int) -> float:
    for r in results[:k]:
        if check_hit(r, expected_fqns):
            return 1.0
    return 0.0


def _pipeline_search(query: str, source: str, n_results: int = 10) -> list[dict]:
    """Run the full hybrid + boost + slots pipeline matching the MCP tools."""
    from fts import hybrid_search
    from server import _exact_identifier_boost, _enforce_source_slots, _deduplicate_per_class

    source_map = {
        "api": API_COLLECTION,
        "guides": GUIDES_COLLECTION,
        "mods": MODS_COLLECTION,
    }

    if source == "any":
        fetch = max(30, n_results * 3)
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
        combined = _deduplicate_per_class(combined)
        return _enforce_source_slots(combined, n_results)

    col = source_map.get(source, API_COLLECTION)
    fetch = max(30, n_results * 3)
    dense = search(query, col, n_results=fetch)
    results = hybrid_search(query, col, dense, n_results=fetch)
    results = _exact_identifier_boost(query, [col], results)
    results = _deduplicate_per_class(results)
    return results[:n_results]


def _eval_mode(questions: list[dict], search_fn, mode_name: str) -> dict:
    """Run one eval pass with a given search function. Returns summary dict."""
    results_log = []
    total_recall5 = 0
    total_recall10 = 0
    total_mrr = 0
    per_source = {}

    source_map = {
        "api": API_COLLECTION,
        "guides": GUIDES_COLLECTION,
        "mods": MODS_COLLECTION,
    }

    for i, q in enumerate(questions):
        query = q["question"]
        expected = q.get("expected_fqns", [])
        source = q.get("source", "any")

        all_hits = search_fn(query, source)

        r5 = recall_at_k(all_hits, expected, 5)
        r10 = recall_at_k(all_hits, expected, 10)
        rr = reciprocal_rank(all_hits, expected)

        total_recall5 += r5
        total_recall10 += r10
        total_mrr += rr

        per_source.setdefault(source, {"r5": 0, "r10": 0, "mrr": 0, "n": 0})
        per_source[source]["r5"] += r5
        per_source[source]["r10"] += r10
        per_source[source]["mrr"] += rr
        per_source[source]["n"] += 1

        status = "HIT" if r5 > 0 else ("hit@10" if r10 > 0 else "MISS")
        top_fqns = [h["metadata"].get("fqn", "?") for h in all_hits[:3]]
        print(f"  [{status:6s}] Q{i+1}: {query}")
        print(f"           Top 3: {top_fqns}")
        if status == "MISS" and expected:
            print(f"           Expected: {expected}")

        results_log.append({
            "question": query,
            "expected": expected,
            "source": source,
            "recall@5": r5,
            "recall@10": r10,
            "mrr": rr,
            "top_results": [
                {
                    "fqn": h["metadata"].get("fqn", ""),
                    "type": h["metadata"].get("type", ""),
                    "distance": h.get("distance"),
                    "rrf_score": h.get("rrf_score"),
                }
                for h in all_hits[:5]
            ],
        })

    n = len(questions)
    summary = {
        "mode": mode_name,
        "questions": n,
        "recall_at_5": round(total_recall5 / n, 4),
        "recall_at_10": round(total_recall10 / n, 4),
        "mrr": round(total_mrr / n, 4),
    }

    print(f"\n{'='*50}")
    print(f"Results — {mode_name} ({n} questions):")
    print(f"  Recall@5:  {summary['recall_at_5']:.1%}")
    print(f"  Recall@10: {summary['recall_at_10']:.1%}")
    print(f"  MRR:       {summary['mrr']:.3f}")

    if per_source:
        print(f"\nPer-source breakdown:")
        for src, stats in sorted(per_source.items()):
            sn = stats["n"]
            print(f"  {src:8s}: R@5={stats['r5']/sn:.1%}  R@10={stats['r10']/sn:.1%}  MRR={stats['mrr']/sn:.3f}  ({sn} questions)")

    print(f"{'='*50}")

    return {
        "summary": summary,
        "per_source": {
            src: {k: round(v / stats["n"], 4) if k != "n" else v for k, v in stats.items()}
            for src, stats in per_source.items()
        },
        "details": results_log,
    }


def _dense_search(query: str, source: str) -> list[dict]:
    """Dense-only search (baseline)."""
    source_map = {
        "api": API_COLLECTION,
        "guides": GUIDES_COLLECTION,
        "mods": MODS_COLLECTION,
    }
    if source == "any":
        collections = [API_COLLECTION, GUIDES_COLLECTION, MODS_COLLECTION]
    elif source in source_map:
        collections = [source_map[source]]
    else:
        collections = [API_COLLECTION]

    all_hits = []
    for col in collections:
        hits = search(query, col, n_results=10)
        all_hits.extend(hits)
    all_hits.sort(key=lambda r: r.get("distance") if r.get("distance") is not None else 999)
    return all_hits


def run_eval(pipeline: bool = False):
    questions = load_questions()
    print(f"Running eval on {len(questions)} questions...\n")

    if pipeline:
        print("--- Dense-only (baseline) ---\n")
        dense_result = _eval_mode(questions, _dense_search, "dense-only")

        print("\n--- Pipeline (hybrid + boost + slots) ---\n")
        pipeline_result = _eval_mode(
            questions,
            lambda q, s: _pipeline_search(q, s, n_results=10),
            "pipeline",
        )

        ds = dense_result["summary"]
        ps = pipeline_result["summary"]
        print(f"\n{'='*60}")
        print(f"{'Metric':<12} {'Dense-only':<15} {'Pipeline':<15} {'Delta':<10}")
        print(f"{'-'*60}")
        for metric, key in [("Recall@5", "recall_at_5"), ("Recall@10", "recall_at_10"), ("MRR", "mrr")]:
            d = ds[key]
            p = ps[key]
            delta = p - d
            sign = "+" if delta > 0 else ""
            print(f"{metric:<12} {d:<15.1%} {p:<15.1%} {sign}{delta:.1%}")
        print(f"{'='*60}")

        out = {"dense": dense_result, "pipeline": pipeline_result}
    else:
        out = _eval_mode(questions, _dense_search, "dense-only")

    out_path = Path(__file__).parent / "results.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nDetailed results saved to {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Eval harness for Hytale Modding RAG")
    parser.add_argument(
        "--pipeline", action="store_true",
        help="Also eval the full pipeline (hybrid + boost + slots) and compare side by side",
    )
    args = parser.parse_args()
    run_eval(pipeline=args.pipeline)
