import json
import re
from datetime import datetime, timezone
from pathlib import Path

from config import DATA_DIR, API_COLLECTION

SNAPSHOT_FILE = DATA_DIR / "api_snapshot.json"
PREV_SNAPSHOT_FILE = DATA_DIR / "api_snapshot_prev.json"
DIFF_FILE = DATA_DIR / "api_diff.json"

_PAGE = 5000


def _get_paginated(collection, where, include):
    """Fetch all matching docs in pages to avoid SQLite variable limits."""
    all_ids: list[str] = []
    all_docs: list[str] = []
    all_metas: list[dict] = []
    offset = 0
    while True:
        result = collection.get(
            where=where, include=include,
            limit=_PAGE, offset=offset,
        )
        if not result["ids"]:
            break
        all_ids.extend(result["ids"])
        if "documents" in include:
            all_docs.extend(result["documents"])
        if "metadatas" in include:
            all_metas.extend(result["metadatas"])
        if len(result["ids"]) < _PAGE:
            break
        offset += _PAGE
    return {"ids": all_ids, "documents": all_docs, "metadatas": all_metas}


def snapshot_api() -> Path:
    from indexer import get_collection

    collection = get_collection(API_COLLECTION)
    if collection.count() == 0:
        return SNAPSHOT_FILE

    overviews = _get_paginated(
        collection,
        where={"type": "class_overview"},
        include=["documents", "metadatas"],
    )

    methods_data = _get_paginated(
        collection,
        where={"type": "method"},
        include=["metadatas"],
    )

    methods_by_fqn: dict[str, list[str]] = {}
    for meta in methods_data["metadatas"]:
        fqn = meta.get("fqn", "")
        name = meta.get("method_name", "")
        if fqn and name:
            methods_by_fqn.setdefault(fqn, []).append(name)

    snapshot = {}
    for i, doc_id in enumerate(overviews["ids"]):
        meta = overviews["metadatas"][i]
        text = overviews["documents"][i]
        fqn = meta.get("fqn", "")
        if not fqn:
            continue

        fields = _extract_all_fields(text)

        snapshot[fqn] = {
            "class_name": meta.get("class_name", ""),
            "package": meta.get("package", ""),
            "extends": meta.get("extends", ""),
            "implements": meta.get("implements", ""),
            "methods": sorted(set(methods_by_fqn.get(fqn, []))),
            "fields": fields,
        }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_FILE.write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return SNAPSHOT_FILE


def _extract_all_fields(text: str) -> list[str]:
    """Extract field signatures from overview text (the // Fields: section)."""
    idx = text.find("// Fields:")
    if idx == -1:
        return []

    after = text[idx + len("// Fields:"):]
    next_section = len(after)
    for marker in ["// Methods:", "// Extends:", "// Implements:", "// Inherited methods:"]:
        pos = after.find(marker)
        if pos != -1 and pos < next_section:
            next_section = pos

    block = after[:next_section].strip()
    return [line.strip().rstrip(";") for line in block.splitlines() if line.strip()]


def diff_api(old_path: str | Path, new_path: str | Path) -> dict:
    old_path = Path(old_path)
    new_path = Path(new_path)

    if not old_path.exists() or not new_path.exists():
        return {
            "added_classes": [],
            "removed_classes": [],
            "modified_classes": [],
            "summary": "Cannot diff: one or both snapshots missing.",
        }

    old = json.loads(old_path.read_text(encoding="utf-8"))
    new = json.loads(new_path.read_text(encoding="utf-8"))

    old_fqns = set(old.keys())
    new_fqns = set(new.keys())

    added = sorted(new_fqns - old_fqns)
    removed = sorted(old_fqns - new_fqns)

    modified = []
    for fqn in sorted(old_fqns & new_fqns):
        changes = _compare_class(old[fqn], new[fqn])
        if changes:
            modified.append({"fqn": fqn, "changes": changes})

    summary_lines = []
    if added:
        summary_lines.append(f"{len(added)} new classes added")
    if removed:
        summary_lines.append(f"{len(removed)} classes removed")
    if modified:
        summary_lines.append(f"{len(modified)} classes modified")
    if not summary_lines:
        summary_lines.append("No API changes detected")

    summary = "API Diff Summary: " + ", ".join(summary_lines) + "."

    if added:
        summary += f"\n\nNew classes:\n" + "\n".join(f"  + {c}" for c in added[:20])
        if len(added) > 20:
            summary += f"\n  ... and {len(added) - 20} more"

    if removed:
        summary += f"\n\nRemoved classes:\n" + "\n".join(f"  - {c}" for c in removed[:20])
        if len(removed) > 20:
            summary += f"\n  ... and {len(removed) - 20} more"

    if modified:
        summary += f"\n\nModified classes:\n"
        for m in modified[:20]:
            summary += f"\n  ~ {m['fqn']}:\n"
            for ch in m["changes"]:
                summary += f"      {ch}\n"
        if len(modified) > 20:
            summary += f"\n  ... and {len(modified) - 20} more"

    return {
        "added_classes": added,
        "removed_classes": removed,
        "modified_classes": modified,
        "summary": summary,
        "diffed_at": datetime.now(timezone.utc).isoformat(),
    }


def _compare_class(old: dict, new: dict) -> list[str]:
    changes = []

    if old.get("extends", "") != new.get("extends", ""):
        changes.append(
            f"extends changed: {old.get('extends', '(none)')} -> {new.get('extends', '(none)')}"
        )

    if old.get("implements", "") != new.get("implements", ""):
        changes.append(
            f"implements changed: {old.get('implements', '(none)')} -> {new.get('implements', '(none)')}"
        )

    old_methods = set(old.get("methods", []))
    new_methods = set(new.get("methods", []))
    added_m = new_methods - old_methods
    removed_m = old_methods - new_methods
    if added_m:
        for m in sorted(added_m)[:5]:
            changes.append(f"+ method: {m}")
        if len(added_m) > 5:
            changes.append(f"  ... and {len(added_m) - 5} more new methods")
    if removed_m:
        for m in sorted(removed_m)[:5]:
            changes.append(f"- method: {m}")
        if len(removed_m) > 5:
            changes.append(f"  ... and {len(removed_m) - 5} more removed methods")

    old_fields = set(old.get("fields", []))
    new_fields = set(new.get("fields", []))
    added_f = new_fields - old_fields
    removed_f = old_fields - new_fields
    if added_f:
        for f in sorted(added_f)[:5]:
            changes.append(f"+ field: {f}")
        if len(added_f) > 5:
            changes.append(f"  ... and {len(added_f) - 5} more new fields")
    if removed_f:
        for f in sorted(removed_f)[:5]:
            changes.append(f"- field: {f}")
        if len(removed_f) > 5:
            changes.append(f"  ... and {len(removed_f) - 5} more removed fields")

    return changes


def save_diff(diff_result: dict) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DIFF_FILE.write_text(
        json.dumps(diff_result, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return DIFF_FILE


def get_latest_diff() -> dict | None:
    if not DIFF_FILE.exists():
        return None
    return json.loads(DIFF_FILE.read_text(encoding="utf-8"))


def rotate_snapshot():
    if SNAPSHOT_FILE.exists():
        if PREV_SNAPSHOT_FILE.exists():
            PREV_SNAPSHOT_FILE.unlink()
        SNAPSHOT_FILE.rename(PREV_SNAPSHOT_FILE)
