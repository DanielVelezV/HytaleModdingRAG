"""CLI for Hytale Modding RAG — index, search, snapshot, and eval."""
import argparse
import json
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))


def cmd_index_jar(args):
    from indexer import parse_java_files, parse_json_configs, index_api_chunks, compute_jar_hash, check_jar_changed
    from decompiler import decompile_jar
    from diffing import snapshot_api, rotate_snapshot, diff_api, save_diff, PREV_SNAPSHOT_FILE
    from config import HYTALE_PACKAGE_PREFIX
    from pathlib import Path

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
    print("Hytale Docs RAG Status\n")

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
    import subprocess
    eval_script = str(__import__("pathlib").Path(__file__).parent / "eval" / "run_eval.py")
    cmd = [sys.executable, eval_script]
    if getattr(args, "pipeline", False):
        cmd.append("--pipeline")
    subprocess.run(cmd, check=False)


def main():
    parser = argparse.ArgumentParser(
        prog="hytale-rag",
        description="Hytale Modding RAG — CLI for indexing, snapshots, and eval",
    )
    sub = parser.add_subparsers(dest="command")

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

    p_sl = snap_sub.add_parser("list", help="List snapshots")

    p_sr = snap_sub.add_parser("restore", help="Restore a snapshot")
    p_sr.add_argument("filename", help="Snapshot filename")

    p_sd = snap_sub.add_parser("delete", help="Delete a snapshot")
    p_sd.add_argument("filename", help="Snapshot filename")

    p_snap.set_defaults(func=cmd_snapshot)

    p_status = sub.add_parser("status", help="Show index status")
    p_status.set_defaults(func=cmd_status)

    p_eval = sub.add_parser("eval", help="Run the eval set against the index")
    p_eval.add_argument("--pipeline", action="store_true", help="Also run hybrid+boost+slots pipeline comparison")
    p_eval.set_defaults(func=cmd_eval)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    args.func(args)


if __name__ == "__main__":
    main()
