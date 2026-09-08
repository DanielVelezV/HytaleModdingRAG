"""Hand-written class descriptions, loaded from JSON.

Search needs this mapping at query time; the indexing pipeline needs it to
enrich chunks at build time. Keeping the loader here means the search path
doesn't import the parser.
"""
import json
from pathlib import Path

_PATH = Path(__file__).parent / "class_descriptions.json"

CLASS_DESCRIPTIONS: dict[str, str] = {}
if _PATH.exists():
    CLASS_DESCRIPTIONS = json.loads(_PATH.read_text(encoding="utf-8"))
