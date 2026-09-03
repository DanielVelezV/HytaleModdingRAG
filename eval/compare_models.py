"""Compare embedding models for the Hytale RAG eval set.

IMPORTANT: This script compares query-model performance against the EXISTING
index (built with the current model). A fair comparison requires building a
separate index per candidate model. This script only tests cross-model
compatibility and may understate a candidate model's true performance.

For a proper comparison:
  1. Build a temp index with the candidate model
  2. Run eval against each index separately
  3. Compare the results

Usage:
    python eval/compare_models.py [model_name]
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import API_COLLECTION, GUIDES_COLLECTION, MODS_COLLECTION, OLLAMA_EMBED_MODEL


def run_eval_with_model(model_name: str) -> dict:
    """Run eval using a specific model for query embedding only.

    NOTE: This only changes the query model. The index documents remain embedded
    with whatever model was used at index time. Cross-model results are NOT a
    valid comparison of model quality — they test cross-model compatibility only.
    """
    os.environ["OLLAMA_EMBED_MODEL"] = model_name

    import importlib
    import indexer
    importlib.reload(indexer)

    from eval.run_eval import load_questions, check_hit, recall_at_k, reciprocal_rank
    from indexer import search

    questions = load_questions()
    total_recall5 = 0
    total_recall10 = 0
    total_mrr = 0
    misses = []

    source_map = {
        "api": API_COLLECTION,
        "guides": GUIDES_COLLECTION,
        "mods": MODS_COLLECTION,
    }

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

        if r5 == 0:
            misses.append(query)

    n = len(questions)
    return {
        "model": model_name,
        "questions": n,
        "recall_at_5": round(total_recall5 / n, 4),
        "recall_at_10": round(total_recall10 / n, 4),
        "mrr": round(total_mrr / n, 4),
        "misses": misses,
    }


def main():
    import ollama

    current_model = OLLAMA_EMBED_MODEL
    compare_model = sys.argv[1] if len(sys.argv) > 1 else "mxbai-embed-large"

    available = [m.model for m in ollama.list().models]
    if not any(compare_model in m for m in available):
        print(f"Model '{compare_model}' not available. Available: {available}")
        print(f"Pull it first: ollama pull {compare_model}")
        sys.exit(1)

    print(f"Comparing embedding models: {current_model} (current) vs {compare_model}")
    print()
    print(f"WARNING: The index was built with '{current_model}'. This comparison only")
    print(f"tests cross-model query compatibility, NOT the candidate model's true quality.")
    print(f"For a fair comparison, build a separate index with each model.\n")

    print(f"Running with {current_model}...")
    baseline = run_eval_with_model(current_model)

    print(f"Running with {compare_model}...")
    candidate = run_eval_with_model(compare_model)

    os.environ["OLLAMA_EMBED_MODEL"] = current_model

    print(f"\n{'='*60}")
    print(f"{'Metric':<15} {current_model:<25} {compare_model:<25}")
    print(f"{'-'*60}")
    print(f"{'Recall@5':<15} {baseline['recall_at_5']:<25.1%} {candidate['recall_at_5']:<25.1%}")
    print(f"{'Recall@10':<15} {baseline['recall_at_10']:<25.1%} {candidate['recall_at_10']:<25.1%}")
    print(f"{'MRR':<15} {baseline['mrr']:<25.3f} {candidate['mrr']:<25.3f}")
    print(f"{'='*60}")

    if candidate["recall_at_5"] > baseline["recall_at_5"]:
        print(f"\n{compare_model} shows higher cross-model recall.")
        print(f"This suggests it may perform well — build a full index to confirm.")
    elif candidate["recall_at_5"] == baseline["recall_at_5"]:
        if candidate["mrr"] > baseline["mrr"]:
            print(f"\n{compare_model} has better cross-model MRR. Build a full index to confirm.")
        else:
            print(f"\nModels are comparable in cross-model mode. Keeping {current_model}.")
    else:
        print(f"\n{current_model} (current) performs better even in cross-model mode.")

    if baseline["misses"]:
        print(f"\nMisses with {current_model}: {baseline['misses']}")
    if candidate["misses"]:
        print(f"Misses with {compare_model}: {candidate['misses']}")

    results_path = Path(__file__).parent / "model_comparison.json"
    results_path.write_text(json.dumps({
        "baseline": baseline,
        "candidate": candidate,
        "note": "Cross-model comparison only. Index was built with the baseline model.",
    }, indent=2), encoding="utf-8")
    print(f"\nResults saved to {results_path}")


if __name__ == "__main__":
    main()
