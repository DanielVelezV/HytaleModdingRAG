"""Tests for server.py helper functions."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from server import _enforce_source_slots, _BOOST_SKIP


def _make_result(source: str, doc_id: str, rrf_score: float = 0.01) -> dict:
    return {
        "id": doc_id,
        "text": f"chunk from {source}",
        "metadata": {"source": source},
        "rrf_score": rrf_score,
    }


class TestEnforceSourceSlots:
    def test_guide_key_matches_metadata(self):
        """E2 regression: slot key must be 'guide', not 'guides'."""
        results = [
            _make_result("api", "a1", 0.05),
            _make_result("api", "a2", 0.04),
            _make_result("api", "a3", 0.03),
            _make_result("api", "a4", 0.02),
            _make_result("api", "a5", 0.01),
            _make_result("guide", "g1", 0.009),
            _make_result("guide", "g2", 0.008),
            _make_result("mod", "m1", 0.007),
        ]
        selected = _enforce_source_slots(results, limit=5)
        sources = [r["metadata"]["source"] for r in selected]
        assert sources.count("guide") >= 2, f"Expected >=2 guide slots, got {sources}"
        assert sources.count("mod") >= 1, f"Expected >=1 mod slot, got {sources}"

    def test_respects_limit(self):
        results = [
            _make_result("api", f"a{i}", 0.05 - i * 0.01) for i in range(10)
        ]
        selected = _enforce_source_slots(results, limit=3)
        assert len(selected) == 3

    def test_missing_source_still_works(self):
        results = [
            _make_result("api", "a1", 0.05),
            _make_result("api", "a2", 0.04),
        ]
        selected = _enforce_source_slots(results, limit=5)
        assert len(selected) == 2

    def test_no_duplicates(self):
        results = [
            _make_result("api", "a1", 0.05),
            _make_result("guide", "g1", 0.04),
            _make_result("mod", "m1", 0.03),
            _make_result("api", "a2", 0.02),
            _make_result("guide", "g2", 0.01),
        ]
        selected = _enforce_source_slots(results, limit=5)
        ids = [r["id"] for r in selected]
        assert len(ids) == len(set(ids)), f"Duplicate IDs in selected: {ids}"


class TestBoostSkip:
    def test_common_words_filtered(self):
        assert "how" in _BOOST_SKIP
        assert "the" in _BOOST_SKIP
        assert "use" in _BOOST_SKIP
        assert "get" in _BOOST_SKIP
        assert "from" in _BOOST_SKIP
        assert "what" in _BOOST_SKIP
