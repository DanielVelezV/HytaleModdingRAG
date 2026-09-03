"""Eval harness for Hytale Modding RAG search quality."""
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


def run_eval(use_rerank: bool = False):
    questions = load_questions()
    print(f"Running eval on {len(questions)} questions...")
    if use_rerank:
        print("  (with reranker enabled)")
    print()

    source_map = {
        "api": API_COLLECTION,
        "guides": GUIDES_COLLECTION,
        "mods": MODS_COLLECTION,
    }

    results_log = []
    total_recall5 = 0
    total_recall10 = 0
    total_mrr = 0
    evaluated = 0
    per_source = {}

    for i, q in enumerate(questions):
        query = q["question"]
        expected = q.get("expected_fqns", [])
        source = q.get("source", "any")

        if source == "any":
            collections = [API_COLLECTION, GUIDES_COLLECTION, MODS_COLLECTION]
        elif source in source_map:
            collections = [source_map[source]]
        else:
            collections = [API_COLLECTION]

        if use_rerank:
            from fts import hybrid_search
            from reranker import rerank
            all_hits = []
            for col in collections:
                dense = search(query, col, n_results=10)
                fused = hybrid_search(query, col, dense, n_results=20)
                all_hits.extend(fused)
            all_hits = rerank(query, all_hits, top_k=10)
        else:
            all_hits = []
            for col in collections:
                hits = search(query, col, n_results=10)
                all_hits.extend(hits)
            all_hits.sort(key=lambda r: r.get("distance") if r.get("distance") is not None else 999)

        r5 = recall_at_k(all_hits, expected, 5)
        r10 = recall_at_k(all_hits, expected, 10)
        rr = reciprocal_rank(all_hits, expected)

        total_recall5 += r5
        total_recall10 += r10
        total_mrr += rr
        evaluated += 1

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
                }
                for h in all_hits[:5]
            ],
        })

    print(f"\n{'='*50}")
    print(f"Results ({evaluated} questions):")
    print(f"  Recall@5:  {total_recall5/evaluated:.1%}")
    print(f"  Recall@10: {total_recall10/evaluated:.1%}")
    print(f"  MRR:       {total_mrr/evaluated:.3f}")

    if per_source:
        print(f"\nPer-source breakdown:")
        for src, stats in sorted(per_source.items()):
            n = stats["n"]
            print(f"  {src:8s}: R@5={stats['r5']/n:.1%}  R@10={stats['r10']/n:.1%}  MRR={stats['mrr']/n:.3f}  ({n} questions)")

    print(f"{'='*50}")

    out_path = Path(__file__).parent / "results.json"
    out_path.write_text(json.dumps({
        "summary": {
            "questions": evaluated,
            "recall_at_5": round(total_recall5 / evaluated, 4),
            "recall_at_10": round(total_recall10 / evaluated, 4),
            "mrr": round(total_mrr / evaluated, 4),
        },
        "per_source": {src: {k: round(v / stats["n"], 4) if k != "n" else v for k, v in stats.items()} for src, stats in per_source.items()},
        "details": results_log,
    }, indent=2), encoding="utf-8")
    print(f"\nDetailed results saved to {out_path}")


if __name__ == "__main__":
    use_rerank = "--rerank" in sys.argv
    run_eval(use_rerank=use_rerank)
