import json
import logging
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from threading import Lock

sys.path.insert(0, str(Path(__file__).parent))

from flask import Flask, jsonify, request, send_from_directory

from config import (
    DATA_DIR, CHROMADB_DIR, META_FILE, DECOMPILED_DIR,
    API_COLLECTION, GUIDES_COLLECTION, MODS_COLLECTION,
    OLLAMA_BASE_URL,
)

app = Flask(__name__, static_folder="dashboard_static")

LOG_BUFFER_SIZE = 500
_log_buffer: deque[dict] = deque(maxlen=LOG_BUFFER_SIZE)
_log_lock = Lock()


class DashboardLogHandler(logging.Handler):
    def emit(self, record):
        entry = {
            "time": datetime.fromtimestamp(record.created).strftime("%H:%M:%S"),
            "level": record.levelname,
            "module": record.module,
            "message": self.format(record),
        }
        with _log_lock:
            _log_buffer.append(entry)


def setup_logging():
    handler = DashboardLogHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    log_file = DATA_DIR / "dashboard.log"
    fh = logging.FileHandler(str(log_file), encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(module)s: %(message)s"))
    root.addHandler(fh)


def _load_meta() -> dict:
    if META_FILE.exists():
        return json.loads(META_FILE.read_text(encoding="utf-8"))
    return {}


def _dir_size_mb(path: Path) -> float:
    if not path.exists():
        return 0
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return round(total / (1024 * 1024), 1)


def _check_ollama() -> dict:
    try:
        import httpx
        resp = httpx.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=3)
        if resp.status_code == 200:
            models = resp.json().get("models", [])
            model_names = [m["name"] for m in models]
            return {"status": "running", "models": model_names}
    except Exception:
        pass
    return {"status": "offline", "models": []}


@app.route("/")
def index():
    return send_from_directory("dashboard_static", "index.html")


@app.route("/api/status")
def api_status():
    meta = _load_meta()

    api_info = meta.get("api", {})
    guides_info = meta.get("guides", {})
    mods_info = meta.get("mods", {})

    try:
        from indexer import get_client
        client = get_client()
        collections = {c.name: c.count() for c in client.list_collections()}
    except Exception:
        collections = {}

    ollama = _check_ollama()

    diff_file = DATA_DIR / "api_diff.json"
    has_diff = diff_file.exists()
    last_diff = None
    if has_diff:
        try:
            diff_data = json.loads(diff_file.read_text(encoding="utf-8"))
            last_diff = diff_data.get("summary", "")
        except Exception:
            pass

    return jsonify({
        "api": {
            "indexed": bool(api_info),
            "jar": api_info.get("jar", ""),
            "jar_hash": api_info.get("jar_hash", "")[:16] + "..." if api_info.get("jar_hash") else "",
            "chunks": collections.get(API_COLLECTION, 0),
            "indexed_at": api_info.get("indexed_at", ""),
        },
        "guides": {
            "indexed": bool(guides_info),
            "chunks": collections.get(GUIDES_COLLECTION, 0),
            "indexed_at": guides_info.get("indexed_at", ""),
        },
        "mods": {
            "indexed": bool(mods_info),
            "repo_count": mods_info.get("repo_count", 0),
            "repos": mods_info.get("repos", []),
            "chunks": collections.get(MODS_COLLECTION, 0),
            "indexed_at": mods_info.get("indexed_at", ""),
        },
        "storage": {
            "chromadb_mb": _dir_size_mb(CHROMADB_DIR),
            "decompiled_mb": _dir_size_mb(DECOMPILED_DIR),
            "total_mb": _dir_size_mb(DATA_DIR),
        },
        "ollama": ollama,
        "has_diff": has_diff,
        "last_diff_summary": last_diff,
    })


@app.route("/api/search")
def api_search():
    query = request.args.get("q", "")
    source = request.args.get("source", "all")
    limit = int(request.args.get("limit", "8"))
    package = request.args.get("package", "")

    if not query:
        return jsonify({"error": "Missing query parameter 'q'"}), 400

    from indexer import search
    from fts import hybrid_search
    from server import _exact_identifier_boost, _enforce_source_slots, _deduplicate_per_class

    fetch = max(30, limit * 3)
    collections = []
    if source in ("all", "api"):
        collections.append(API_COLLECTION)
    if source in ("all", "guides"):
        collections.append(GUIDES_COLLECTION)
    if source in ("all", "mods"):
        collections.append(MODS_COLLECTION)

    results = []
    for col in collections:
        dense = search(query, col, n_results=fetch)
        results.extend(hybrid_search(query, col, dense, n_results=fetch // max(1, len(collections))))

    results = _exact_identifier_boost(query, collections, results)
    results.sort(key=lambda r: r.get("rrf_score", 0), reverse=True)
    results = _deduplicate_per_class(results)

    if package:
        results = [r for r in results if package in r.get("metadata", {}).get("package", "")]

    if source == "all":
        results = _enforce_source_slots(results, limit)
    else:
        results = results[:limit]

    formatted = []
    for r in results:
        meta = r["metadata"]
        formatted.append({
            "source": meta.get("source", "?"),
            "type": meta.get("type", ""),
            "fqn": meta.get("fqn", ""),
            "class_name": meta.get("class_name", ""),
            "method_name": meta.get("method_name", ""),
            "package": meta.get("package", ""),
            "title": meta.get("title", ""),
            "heading": meta.get("heading", ""),
            "url": meta.get("url", ""),
            "repo": meta.get("repo", ""),
            "extends": meta.get("extends", ""),
            "implements": meta.get("implements", ""),
            "similarity": round(1 - r["distance"], 3) if r.get("distance") is not None else None,
            "score": round(r["rrf_score"], 4) if r.get("rrf_score") is not None else None,
            "text": r["text"][:500],
        })

    return jsonify({"query": query, "source": source, "count": len(formatted), "results": formatted})


@app.route("/api/logs")
def api_logs():
    with _log_lock:
        return jsonify(list(_log_buffer))


@app.route("/api/diff")
def api_diff():
    diff_file = DATA_DIR / "api_diff.json"
    if not diff_file.exists():
        return jsonify({"error": "No API diff available. Re-index with a new jar to generate one."}), 404
    try:
        return jsonify(json.loads(diff_file.read_text(encoding="utf-8")))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


SKILLS_DIR = Path(__file__).parent / "skills"


@app.route("/api/skills")
def api_skills_list():
    if not SKILLS_DIR.exists():
        return jsonify([])
    skills = []
    for d in sorted(SKILLS_DIR.iterdir()):
        if not d.is_dir():
            continue
        skill_file = d / "SKILL.md"
        files = [str(f.relative_to(d)).replace("\\", "/") for f in d.rglob("*.md")]
        skills.append({
            "name": d.name,
            "files": files,
            "has_skill": skill_file.exists(),
        })
    return jsonify(skills)


@app.route("/api/skills/<name>")
def api_skill_get(name):
    skill_dir = SKILLS_DIR / name
    if not skill_dir.exists() or not skill_dir.is_dir():
        return jsonify({"error": "Skill not found"}), 404
    files = {}
    for f in skill_dir.rglob("*.md"):
        rel = str(f.relative_to(skill_dir)).replace("\\", "/")
        files[rel] = f.read_text(encoding="utf-8")
    return jsonify({"name": name, "files": files})


@app.route("/api/skills", methods=["POST"])
def api_skill_create():
    data = request.get_json()
    name = data.get("name", "").strip()
    content = data.get("content", "").strip()
    if not name:
        return jsonify({"error": "Missing skill name"}), 400
    if not content:
        return jsonify({"error": "Missing skill content"}), 400
    safe = "".join(c for c in name if c.isalnum() or c in "-_").lower()
    if not safe:
        return jsonify({"error": "Invalid skill name"}), 400
    skill_dir = SKILLS_DIR / safe
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    return jsonify({"name": safe, "created": True})


@app.route("/api/skills/<name>", methods=["DELETE"])
def api_skill_delete(name):
    import shutil
    skill_dir = SKILLS_DIR / name
    if not skill_dir.exists():
        return jsonify({"error": "Skill not found"}), 404
    shutil.rmtree(skill_dir)
    return jsonify({"name": name, "deleted": True})


@app.route("/api/collections")
def api_collections():
    try:
        from indexer import get_client
        client = get_client()
        cols = []
        for c in client.list_collections():
            cols.append({"name": c.name, "count": c.count()})
        return jsonify(cols)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    setup_logging()
    logging.info("Dashboard starting...")
    print("Hytale Modding RAG Dashboard")
    print("Open http://localhost:5111 in your browser")
    app.run(host="127.0.0.1", port=5111, debug=False)
