import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from config import SNAPSHOTS_DIR, CHROMADB_DIR, META_FILE


def _ensure_dir():
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)


def save_snapshot(label: str = "") -> dict:
    _ensure_dir()

    if not CHROMADB_DIR.exists():
        return {"error": "ChromaDB directory does not exist, nothing to snapshot"}

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_label = label.replace(" ", "_").replace("/", "_")[:30] if label else ""
    dirname = f"backup_{ts}"
    if safe_label:
        dirname += f"_{safe_label}"

    snapshot_dir = SNAPSHOTS_DIR / dirname

    try:
        from indexer import get_client
        client = get_client()
        total_chunks = sum(c.count() for c in client.list_collections())
    except Exception:
        total_chunks = 0

    shutil.copytree(str(CHROMADB_DIR), str(snapshot_dir))

    meta = _load_meta()
    meta_file = snapshot_dir / "snapshot_meta.json"
    meta_file.write_text(json.dumps({
        "count": total_chunks,
        "label": label,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_meta": meta,
    }, indent=2), encoding="utf-8")

    size_mb = round(sum(
        f.stat().st_size for f in snapshot_dir.rglob("*") if f.is_file()
    ) / (1024 * 1024), 1)

    info = {
        "file": dirname,
        "count": total_chunks,
        "label": label,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "size_mb": size_mb,
    }

    _save_snapshot_record(info)
    return info


def list_snapshots() -> list[dict]:
    records = _load_snapshot_records()
    return sorted(records, key=lambda r: r.get("created_at", ""), reverse=True)


def restore_snapshot(filename: str) -> dict:
    snapshot_dir = SNAPSHOTS_DIR / filename
    if not snapshot_dir.exists():
        return {"error": f"Snapshot not found: {filename}"}

    meta_file = snapshot_dir / "snapshot_meta.json"
    if not meta_file.exists():
        return {"error": f"Snapshot metadata missing in: {filename}"}

    snap_meta = json.loads(meta_file.read_text(encoding="utf-8"))

    if sys.platform == "win32" and CHROMADB_DIR.exists():
        try:
            lock_file = CHROMADB_DIR / "chroma.sqlite3-wal"
            if lock_file.exists():
                try:
                    with open(lock_file, "r+b"):
                        pass
                except PermissionError:
                    return {
                        "error": "ChromaDB is locked by another process (MCP server or dashboard). "
                        "Stop them first, then retry."
                    }
        except Exception:
            pass

    if CHROMADB_DIR.exists():
        shutil.rmtree(str(CHROMADB_DIR))
    shutil.copytree(str(snapshot_dir), str(CHROMADB_DIR))

    restore_meta = CHROMADB_DIR / "snapshot_meta.json"
    if restore_meta.exists():
        restore_meta.unlink()

    if snap_meta.get("source_meta"):
        META_FILE.parent.mkdir(parents=True, exist_ok=True)
        META_FILE.write_text(json.dumps(snap_meta["source_meta"], indent=2), encoding="utf-8")

    return {
        "restored": filename,
        "chunks": snap_meta.get("count", 0),
    }


def delete_snapshot(filename: str) -> dict:
    snapshot_dir = SNAPSHOTS_DIR / filename
    if not snapshot_dir.exists():
        return {"error": f"Snapshot not found: {filename}"}

    shutil.rmtree(str(snapshot_dir))

    records = _load_snapshot_records()
    records = [r for r in records if r.get("file") != filename]
    _save_all_snapshot_records(records)

    return {"deleted": filename}


def _load_meta() -> dict:
    if META_FILE.exists():
        return json.loads(META_FILE.read_text(encoding="utf-8"))
    return {}


def _snapshot_index_path() -> Path:
    _ensure_dir()
    return SNAPSHOTS_DIR / "index.json"


def _load_snapshot_records() -> list[dict]:
    p = _snapshot_index_path()
    if p.exists():
        try:
            records = json.loads(p.read_text(encoding="utf-8"))
            return [r for r in records if (SNAPSHOTS_DIR / r.get("file", "")).exists()]
        except Exception:
            return []
    return []


def _save_snapshot_record(info: dict):
    records = _load_snapshot_records()
    records.append(info)
    _save_all_snapshot_records(records)


def _save_all_snapshot_records(records: list[dict]):
    p = _snapshot_index_path()
    p.write_text(json.dumps(records, indent=2), encoding="utf-8")
