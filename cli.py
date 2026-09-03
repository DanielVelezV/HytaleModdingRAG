"""CLI for Hytale Modding RAG — setup, serve, and manage the knowledge base."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent))

from config import DATA_DIR, GITHUB_DATA_REPO, BASE_DIR

DATA_VERSION_FILE = DATA_DIR / "version.json"


# ---------------------------------------------------------------------------
#  User-facing commands
# ---------------------------------------------------------------------------

def cmd_setup(args):
    """Download pre-built RAG data from the latest GitHub Release."""
    import httpx

    if DATA_DIR.exists() and any(DATA_DIR.iterdir()):
        if not args.force:
            print(f"Data directory already exists: {DATA_DIR}")
            print("Use --force to overwrite, or 'hytale-rag update' to check for updates.")
            return

    print("Checking latest release...")
    release = _get_latest_release()
    if not release:
        print("No releases found. You may need to build the index locally:")
        print("  hytale-rag index-jar <path/to/HytaleServer.jar>")
        return

    tag = release["tag_name"]
    print(f"Latest data version: {tag}")

    assets = [a for a in release["assets"] if a["name"].startswith("hytale-rag-data")]
    if not assets:
        print("No data assets found in release.")
        return

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for asset in assets:
        _download_asset(asset, DATA_DIR)

    _save_version(tag, release)
    print(f"\nSetup complete! Data version: {tag}")
    print("Start the MCP server with: hytale-rag serve")


def cmd_update(args):
    """Check for new data and download if available."""
    import httpx

    current = _load_version()
    current_tag = current.get("tag") if current else None

    print(f"Current data version: {current_tag or 'none'}")
    print("Checking for updates...")

    release = _get_latest_release()
    if not release:
        print("No releases found.")
        return

    latest_tag = release["tag_name"]
    if latest_tag == current_tag:
        print(f"Already up to date ({latest_tag}).")
        return

    print(f"New version available: {latest_tag}")
    assets = [a for a in release["assets"] if a["name"].startswith("hytale-rag-data")]
    if not assets:
        print("No data assets found in release.")
        return

    if DATA_DIR.exists():
        backup = DATA_DIR.parent / "data_backup"
        if backup.exists():
            shutil.rmtree(backup)
        print("Backing up current data...")
        shutil.copytree(DATA_DIR, backup)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for asset in assets:
        _download_asset(asset, DATA_DIR)

    _save_version(latest_tag, release)

    backup = DATA_DIR.parent / "data_backup"
    if backup.exists():
        shutil.rmtree(backup)

    print(f"\nUpdated to {latest_tag}!")


def cmd_serve(args):
    """Start the MCP server."""
    if not DATA_DIR.exists() or not any(DATA_DIR.iterdir()):
        print("No data found. Run 'hytale-rag setup' first.")
        return

    server_py = BASE_DIR / "server.py"
    print("Starting Hytale Modding RAG MCP server...")
    print("Press Ctrl+C to stop.\n")

    try:
        subprocess.run(
            [sys.executable, str(server_py)],
            check=True,
        )
    except KeyboardInterrupt:
        print("\nServer stopped.")


def cmd_dashboard(args):
    """Start the web dashboard."""
    dashboard_py = BASE_DIR / "dashboard.py"
    port = args.port or 5111
    print(f"Starting dashboard on http://localhost:{port}")
    print("Press Ctrl+C to stop.\n")

    try:
        env = os.environ.copy()
        env["DASHBOARD_PORT"] = str(port)
        subprocess.run(
            [sys.executable, str(dashboard_py)],
            env=env,
            check=True,
        )
    except KeyboardInterrupt:
        print("\nDashboard stopped.")


def cmd_create_mod(args):
    """Scaffold a new Hytale mod project."""
    from server import create_mod

    name = args.name
    output_dir = args.output or os.getcwd()
    group = args.group or ""
    author = args.author or ""
    hot_reload = args.hot_reload

    print(f"Creating mod '{name}' in {output_dir}...")
    result = create_mod(name, output_dir, group=group, author=author, hot_reload=hot_reload)
    print(result)


# ---------------------------------------------------------------------------
#  Admin / indexing commands
# ---------------------------------------------------------------------------

def cmd_index_jar(args):
    from indexer import parse_java_files, parse_json_configs, index_api_chunks, compute_jar_hash, check_jar_changed
    from decompiler import decompile_jar
    from diffing import snapshot_api, rotate_snapshot, diff_api, save_diff, PREV_SNAPSHOT_FILE
    from config import HYTALE_PACKAGE_PREFIX

    jar_path = args.jar_path
    jar_name = Path(jar_path).name

    if not args.force and not check_jar_changed(jar_path):
        print(f"{jar_name} has not changed since last indexing (hash matches). Use --force to re-index.")
        return

    jar_hash = compute_jar_hash(jar_path)

    print("Snapshotting current API state...")
    rotate_snapshot()
    snapshot_api()

    print(f"Decompiling {jar_name}...")
    t0 = time.time()
    output_dir = decompile_jar(jar_path)
    print(f"Decompiled in {time.time()-t0:.1f}s")

    print("Parsing Java files...")
    t1 = time.time()
    all_chunks = parse_java_files(output_dir)
    config_chunks = parse_json_configs(output_dir)
    print(f"Parsed {len(all_chunks)} total chunks in {time.time()-t1:.1f}s")

    chunks = [
        c for c in all_chunks
        if c["metadata"].get("package", "").startswith(HYTALE_PACKAGE_PREFIX)
    ]
    chunks.extend(config_chunks)
    print(f"Filtered to {len(chunks)} Hytale-specific chunks (+{len(config_chunks)} configs)")

    print(f"Indexing {len(chunks)} chunks...")
    t2 = time.time()
    index_api_chunks(chunks, jar_name=jar_name, jar_hash=jar_hash)
    print(f"Indexed in {time.time()-t2:.1f}s")

    new_snap = snapshot_api()
    if PREV_SNAPSHOT_FILE.exists():
        diff_result = diff_api(PREV_SNAPSHOT_FILE, new_snap)
        save_diff(diff_result)
        if diff_result.get("summary"):
            print(f"\nAPI changes:\n{diff_result['summary']}")

    print(f"\nDone! {len(chunks)} chunks indexed from {jar_name}")


def cmd_scrape_guides(args):
    from scraper import scrape_guides
    from indexer import chunk_guides, index_guide_chunks

    print("Scraping hytalemodding.dev...")
    t0 = time.time()
    pages = scrape_guides()
    print(f"Scraped {len(pages)} pages in {time.time()-t0:.1f}s")

    if not pages:
        print("No pages found.")
        return

    chunks = chunk_guides(pages)
    print(f"Indexing {len(chunks)} guide chunks...")
    t1 = time.time()
    index_guide_chunks(chunks)
    print(f"Indexed in {time.time()-t1:.1f}s")

    print(f"\nDone! {len(pages)} pages, {len(chunks)} chunks")


def cmd_index_mods(args):
    from github_scraper import scrape_github_mods
    from indexer import index_mod_chunks

    print(f"Searching GitHub (min_stars={args.min_stars}, max_repos={args.max_repos})...")
    t0 = time.time()
    chunks, repos = scrape_github_mods(
        min_stars=args.min_stars,
        max_repos=args.max_repos,
    )
    print(f"Found {len(repos)} repos, {len(chunks)} chunks in {time.time()-t0:.1f}s")

    if not chunks:
        print("No mod chunks found.")
        return

    print(f"Indexing {len(chunks)} mod chunks...")
    t1 = time.time()
    index_mod_chunks(chunks, repos)
    print(f"Indexed in {time.time()-t1:.1f}s")

    print(f"\nDone! {len(repos)} repos, {len(chunks)} chunks")


def cmd_snapshot(args):
    from snapshots import save_snapshot, list_snapshots, restore_snapshot, delete_snapshot

    action = args.snapshot_action

    if action == "save":
        print("Saving whole-DB snapshot...")
        result = save_snapshot(args.label or "")
        if "error" in result:
            print(f"Error: {result['error']}")
        else:
            print(f"Saved: {result['file']} ({result['count']} chunks, {result.get('size_mb', '?')} MB)")

    elif action == "list":
        records = list_snapshots()
        if not records:
            print("No snapshots found.")
            return
        print(f"Snapshots ({len(records)}):")
        for r in records:
            label = f' "{r["label"]}"' if r.get("label") else ""
            print(f"  {r['file']}{label} — {r['count']} chunks | {r.get('size_mb', '?')} MB | {r['created_at']}")

    elif action == "restore":
        if not args.filename:
            print("Error: filename required for restore")
            return
        print(f"Restoring {args.filename}...")
        result = restore_snapshot(args.filename)
        if "error" in result:
            print(f"Error: {result['error']}")
        else:
            print(f"Restored: {result['restored']} — {result['chunks']} chunks")

    elif action == "delete":
        if not args.filename:
            print("Error: filename required for delete")
            return
        result = delete_snapshot(args.filename)
        if "error" in result:
            print(f"Error: {result['error']}")
        else:
            print(f"Deleted: {result['deleted']}")


def cmd_status(args):
    from indexer import get_status

    status = get_status()
    version = _load_version()

    print("Hytale Modding RAG Status\n")

    if version:
        print(f"Data version: {version.get('tag', '?')}")
    else:
        print("Data version: local build")
    print()

    api = status.get("api", {})
    if api.get("indexed"):
        print(f"Java API: {api.get('chunks', '?')} chunks | jar: {api.get('jar', '?')} | {api.get('indexed_at', '?')}")
    else:
        print("Java API: NOT INDEXED")

    guides = status.get("guides", {})
    if guides.get("indexed"):
        print(f"Guides:   {guides.get('chunks', '?')} chunks | {guides.get('indexed_at', '?')}")
    else:
        print("Guides:   NOT INDEXED")

    mods = status.get("mods", {})
    if mods.get("indexed"):
        print(f"Mods:     {mods.get('chunks', '?')} chunks | {mods.get('repo_count', '?')} repos | {mods.get('indexed_at', '?')}")
    else:
        print("Mods:     NOT INDEXED")


def cmd_eval(args):
    eval_script = str(Path(__file__).parent / "eval" / "run_eval.py")
    cmd = [sys.executable, eval_script]
    if getattr(args, "pipeline", False):
        cmd.append("--pipeline")
    subprocess.run(cmd, check=False)


def cmd_publish(args):
    """Zip data/ and create a GitHub Release (admin only)."""
    tag = args.tag
    if not tag:
        print("Error: --tag is required (e.g. v0.6.3)")
        return

    if not DATA_DIR.exists():
        print("No data directory found.")
        return

    print(f"Packaging data for release {tag}...")
    zip_path = BASE_DIR / f"hytale-rag-data-{tag}.zip"

    exclude = {"dashboard.log", "decompilers", "snapshots", "data_backup"}
    t0 = time.time()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for root, dirs, files in os.walk(DATA_DIR):
            dirs[:] = [d for d in dirs if d not in exclude]
            for f in files:
                fp = Path(root) / f
                arc = fp.relative_to(DATA_DIR)
                print(f"  Adding {arc}...")
                zf.write(fp, f"data/{arc}")

    size_mb = zip_path.stat().st_size / (1024 * 1024)
    print(f"\nPackaged: {zip_path.name} ({size_mb:.1f} MB) in {time.time()-t0:.0f}s")

    if size_mb > 2048:
        print(f"\nWARNING: File is {size_mb:.0f} MB — exceeds GitHub's 2 GB release asset limit.")
        print("Consider splitting or increasing compression.")
        return

    print(f"\nTo create the release:")
    print(f"  gh release create {tag} {zip_path} --title \"Hytale RAG Data {tag}\" --notes \"Pre-built RAG index for Hytale Server {tag}\"")

    if args.upload:
        print(f"\nUploading release {tag}...")
        result = subprocess.run(
            ["gh", "release", "create", tag, str(zip_path),
             "--title", f"Hytale RAG Data {tag}",
             "--notes", f"Pre-built RAG index for Hytale Server {tag}"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            print(f"Release created: {result.stdout.strip()}")
        else:
            print(f"Error: {result.stderr}")


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

def _get_latest_release():
    import httpx
    url = f"https://api.github.com/repos/{GITHUB_DATA_REPO}/releases/latest"
    try:
        r = httpx.get(url, timeout=15, follow_redirects=True)
        if r.status_code == 200:
            return r.json()
        if r.status_code == 404:
            return None
        print(f"GitHub API error: {r.status_code}")
        return None
    except httpx.HTTPError as e:
        print(f"Network error: {e}")
        return None


def _download_asset(asset, dest_dir: Path):
    import httpx
    name = asset["name"]
    url = asset["browser_download_url"]
    size_mb = asset["size"] / (1024 * 1024)

    print(f"Downloading {name} ({size_mb:.1f} MB)...")
    dest = dest_dir / name

    with httpx.stream("GET", url, timeout=600, follow_redirects=True) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        downloaded = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_bytes(chunk_size=1024 * 1024):
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded * 100 // total
                    bar = "#" * (pct // 2) + "-" * (50 - pct // 2)
                    print(f"\r  [{bar}] {pct}%", end="", flush=True)
        print()

    if name.endswith(".zip"):
        print(f"Extracting {name}...")
        with zipfile.ZipFile(dest, "r") as zf:
            for member in zf.namelist():
                rel = member
                if rel.startswith("data/"):
                    rel = rel[5:]
                if not rel:
                    continue
                target = dest_dir / rel
                if member.endswith("/"):
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(member) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
        dest.unlink()


def _save_version(tag: str, release: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DATA_VERSION_FILE.write_text(json.dumps({
        "tag": tag,
        "published_at": release.get("published_at", ""),
        "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }, indent=2), encoding="utf-8")


def _load_version():
    if DATA_VERSION_FILE.exists():
        try:
            return json.loads(DATA_VERSION_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return None


# ---------------------------------------------------------------------------
#  CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        prog="hytale-rag",
        description="Hytale Modding RAG — AI-powered knowledge base for Hytale server modding",
    )
    sub = parser.add_subparsers(dest="command")

    # --- User commands ---
    p_setup = sub.add_parser("setup", help="Download pre-built RAG data (first-time setup)")
    p_setup.add_argument("--force", action="store_true", help="Overwrite existing data")
    p_setup.set_defaults(func=cmd_setup)

    p_update = sub.add_parser("update", help="Check for and download new RAG data")
    p_update.set_defaults(func=cmd_update)

    p_serve = sub.add_parser("serve", help="Start the MCP server")
    p_serve.set_defaults(func=cmd_serve)

    p_dash = sub.add_parser("dashboard", help="Start the web dashboard")
    p_dash.add_argument("--port", type=int, default=5111, help="Port (default 5111)")
    p_dash.set_defaults(func=cmd_dashboard)

    p_mod = sub.add_parser("create-mod", help="Scaffold a new Hytale mod project")
    p_mod.add_argument("name", help="Mod name in PascalCase (e.g. MyFirstMod)")
    p_mod.add_argument("--output", "-o", help="Output directory (default: current dir)")
    p_mod.add_argument("--group", help="Java package group (default: com.<name>)")
    p_mod.add_argument("--author", help="Author name for manifest.json")
    p_mod.add_argument("--hot-reload", action="store_true", help="Enable hot reload (requires JBR 25)")
    p_mod.set_defaults(func=cmd_create_mod)

    p_status = sub.add_parser("status", help="Show index status")
    p_status.set_defaults(func=cmd_status)

    # --- Admin / indexing commands ---
    p_jar = sub.add_parser("index-jar", help="Decompile and index a HytaleServer.jar")
    p_jar.add_argument("jar_path", help="Path to HytaleServer.jar")
    p_jar.add_argument("--force", action="store_true", help="Force re-index even if jar unchanged")
    p_jar.set_defaults(func=cmd_index_jar)

    p_guides = sub.add_parser("scrape-guides", help="Scrape and index hytalemodding.dev guides")
    p_guides.set_defaults(func=cmd_scrape_guides)

    p_mods = sub.add_parser("index-mods", help="Search GitHub and index Hytale mods")
    p_mods.add_argument("--min-stars", type=int, default=2, help="Minimum stars (default 2)")
    p_mods.add_argument("--max-repos", type=int, default=30, help="Max repos (default 30)")
    p_mods.set_defaults(func=cmd_index_mods)

    p_snap = sub.add_parser("snapshot", help="Save/list/restore/delete index snapshots")
    snap_sub = p_snap.add_subparsers(dest="snapshot_action")
    p_ss = snap_sub.add_parser("save", help="Save a whole-DB snapshot")
    p_ss.add_argument("--label", default="", help="Label for the snapshot")
    snap_sub.add_parser("list", help="List snapshots")
    p_sr = snap_sub.add_parser("restore", help="Restore a snapshot")
    p_sr.add_argument("filename", help="Snapshot filename")
    p_sd = snap_sub.add_parser("delete", help="Delete a snapshot")
    p_sd.add_argument("filename", help="Snapshot filename")
    p_snap.set_defaults(func=cmd_snapshot)

    p_eval = sub.add_parser("eval", help="Run the eval set against the index")
    p_eval.add_argument("--pipeline", action="store_true", help="Run hybrid+boost+slots pipeline comparison")
    p_eval.set_defaults(func=cmd_eval)

    p_pub = sub.add_parser("publish", help="Package data and create GitHub Release (admin)")
    p_pub.add_argument("--tag", required=True, help="Release tag (e.g. v0.6.3)")
    p_pub.add_argument("--upload", action="store_true", help="Upload to GitHub (requires gh CLI)")
    p_pub.set_defaults(func=cmd_publish)

    args = parser.parse_args()
    if not args.command:
        _print_banner()
        parser.print_help()
        return

    args.func(args)


def _print_banner():
    print("""
  _   _       _        _        __  __           _     _ _             ____      _    ____
 | | | |_   _| |_ __ _| | ___  |  \\/  | ___   __| | __| (_)_ __   __ |  _ \\    / \\  / ___|
 | |_| | | | | __/ _` | |/ _ \\ | |\\/| |/ _ \\ / _` |/ _` | | '_ \\ / _` | |_) |  / _ \\ | |  _
 |  _  | |_| | || (_| | |  __/ | |  | | (_) | (_| | (_| | | | | | (_| |  _ <  / ___ \\| |_| |
 |_| |_|\\__, |\\__\\__,_|_|\\___| |_|  |_|\\___/ \\__,_|\\__,_|_|_| |_|\\__, |_| \\_\\/_/   \\_\\\\____|
        |___/                                                      |___/
    """)


if __name__ == "__main__":
    main()
