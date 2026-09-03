"""Tests for FTS5 keyword search and hybrid retrieval."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fts import build_fts, keyword_search, hybrid_search, _build_fts_query


class TestFTSQuery:
    def test_basic_tokens(self):
        q = _build_fts_query("register command")
        assert '"register"' in q
        assert '"command"' in q

    def test_short_tokens_filtered(self):
        q = _build_fts_query("a register b")
        assert '"register"' in q
        assert '"a"' not in q

    def test_empty_query(self):
        q = _build_fts_query("")
        assert q == ""


class TestBuildAndSearch:
    def test_round_trip(self, tmp_path):
        import sqlite3
        import fts as fts_module

        old_db = fts_module.FTS_DB
        fts_module.FTS_DB = tmp_path / "test_fts.sqlite"
        fts_module._conn = None

        try:
            chunks = [
                {
                    "id": "c1",
                    "text": "public void registerCommand(String name)",
                    "metadata": {
                        "fqn": "com.test.CommandRegistry",
                        "class_name": "CommandRegistry",
                        "method_name": "registerCommand",
                    },
                },
                {
                    "id": "c2",
                    "text": "public void spawnEntity(EntityType type)",
                    "metadata": {
                        "fqn": "com.test.EntityManager",
                        "class_name": "EntityManager",
                        "method_name": "spawnEntity",
                    },
                },
            ]
            build_fts("test_col", chunks)

            results = keyword_search("test_col", "registerCommand")
            assert len(results) >= 1
            assert results[0]["id"] == "c1"

            results2 = keyword_search("test_col", "spawnEntity")
            assert len(results2) >= 1
            assert results2[0]["id"] == "c2"
        finally:
            fts_module.FTS_DB = old_db
            fts_module._conn = None


class TestHybridSearch:
    def test_rrf_merge(self):
        dense = [
            {"id": "a", "text": "result a", "metadata": {}, "distance": 0.1},
            {"id": "b", "text": "result b", "metadata": {}, "distance": 0.2},
        ]
        result = hybrid_search("test query", "nonexistent_collection", dense, n_results=2)
        assert len(result) >= 1
        assert all("rrf_score" in r for r in result)
