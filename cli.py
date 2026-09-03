"""Hytale Modding RAG — CLI."""
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import typer
from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, DownloadColumn, TransferSpeedColumn
from rich.table import Table
from rich.prompt import Confirm

from config import DATA_DIR, GITHUB_DATA_REPO, BASE_DIR

console = Console()
app = typer.Typer(
    name="hytale-rag",
    help="AI-powered knowledge base for Hytale server modding",
    no_args_is_help=False,
    invoke_without_command=True,
    rich_markup_mode="rich",
)

DATA_VERSION_FILE = DATA_DIR / "version.json"

BANNER = r"""[bold cyan]
  _   _       _        _        __  __           _     _ _
 | | | |_   _| |_ __ _| | ___  |  \/  | ___   __| | __| (_)_ __   __ _
 | |_| | | | | __/ _` | |/ _ \ | |\/| |/ _ \ / _` |/ _` | | '_ \ / _` |
 |  _  | |_| | || (_| | |  __/ | |  | | (_) | (_| | (_| | | | | | (_| |
 |_| |_|\__, |\__\__,_|_|\___| |_|  |_|\___/ \__,_|\__,_|_|_| |_|\__, |
        |___/                                                      |___/[/bold cyan]
[dim]  RAG — AI-powered knowledge base for Hytale server modding[/dim]
"""

MCP_COMMAND = f'claude mcp add hytale-docs --scope user -- python "{BASE_DIR / "server.py"}"'


@app.callback()
def main(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        _run_main()


def _run_main():
    os.system("cls" if os.name == "nt" else "clear")
    rprint(BANNER)

    has_data = DATA_DIR.exists() and any(DATA_DIR.iterdir())

    if not has_data:
        rprint(Panel(
            "[yellow]No RAG data found.[/yellow]\n"
            "The pre-built index (~2.3 GB) needs to be downloaded.",
            title="[bold]First-time Setup[/bold]",
            border_style="yellow",
        ))
        rprint()

        if Confirm.ask("Download the RAG data now?", default=True):
            _do_setup()
        else:
            rprint("\n[dim]Run [bold]hytale-rag setup[/bold] when you're ready.[/dim]")
            return
    else:
        _print_index_summary()

    rprint()
    _show_mcp_usage()


def _print_index_summary():
    version = _load_version()
    tag = version.get("tag", "local build") if version else "local build"

    try:
        from indexer import get_status
        status = get_status()
        api = status.get("api", {})
        guides = status.get("guides", {})
        mods = status.get("mods", {})

        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column(style="bold")
        table.add_column()
        if api.get("indexed"):
            table.add_row("Java API", f"{api.get('chunks', '?'):,} chunks")
        if guides.get("indexed"):
            table.add_row("Guides", f"{guides.get('chunks', '?'):,} chunks")
        if mods.get("indexed"):
            table.add_row("Mods", f"{mods.get('chunks', '?'):,} chunks from {mods.get('repo_count', '?')} repos")

        rprint(Panel.fit(
            table,
            title=f"[bold green]Index: {tag}[/bold green]",
            border_style="green",
        ))
    except Exception:
        rprint(Panel(f"[green]Data installed[/green] — version {tag}", border_style="green"))


def _show_mcp_usage():
    rprint(Panel(
        f"[bold]Register as MCP server in Claude Code:[/bold]\n\n"
        f"  [cyan]{MCP_COMMAND}[/cyan]\n\n"
        f"Then ask Claude anything about Hytale server modding!\n\n"
        f"[dim]Other commands:[/dim]\n"
        f"  [bold]hytale-rag update[/bold]      Check for newer data\n"
        f"  [bold]hytale-rag dashboard[/bold]    Browse the index in your browser",
        title="[bold cyan]Usage[/bold cyan]",
        border_style="cyan",
    ))


# ---------------------------------------------------------------------------
#  setup
# ---------------------------------------------------------------------------

@app.command()
def setup(
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing data"),
):
    """Download pre-built RAG data from GitHub Releases."""
    if DATA_DIR.exists() and any(DATA_DIR.iterdir()) and not force:
        rprint("[yellow]Data already exists.[/yellow] Use [bold]--force[/bold] to overwrite, or [bold]hytale-rag update[/bold] to check for updates.")
        return

    _do_setup()
    rprint()
    _show_mcp_usage()


def _do_setup():
    with console.status("[bold cyan]Checking latest release...[/bold cyan]"):
        release = _get_latest_release()

    if not release:
        rprint("[red]No releases found.[/red] You may need to build the index locally.")
        return

    tag = release["tag_name"]
    assets = [a for a in release["assets"] if a["name"].startswith("hytale-rag-data")]

    if not assets:
        rprint("[red]No data assets found in release.[/red]")
        return

    total_size = sum(a["size"] for a in assets)
    rprint(Panel(
        f"[bold]Version:[/bold] {tag}\n"
        f"[bold]Assets:[/bold]  {len(assets)} files ({total_size / 1024 / 1024:.0f} MB)",
        title="[bold cyan]Hytale RAG Data[/bold cyan]",
        border_style="cyan",
    ))
    rprint()

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    for asset in assets:
        _download_asset_rich(asset, DATA_DIR)

    _save_version(tag, release)

    rprint()
    rprint(f"[bold green]Data version {tag} installed successfully![/bold green]")


# ---------------------------------------------------------------------------
#  update
# ---------------------------------------------------------------------------

@app.command()
def update():
    """Check for and download newer RAG data."""
    current = _load_version()
    current_tag = current.get("tag") if current else None

    rprint(f"[bold]Current version:[/bold] {current_tag or '[dim]none[/dim]'}")

    with console.status("[bold cyan]Checking for updates...[/bold cyan]"):
        release = _get_latest_release()

    if not release:
        rprint("[yellow]No releases found.[/yellow]")
        return

    latest_tag = release["tag_name"]
    if latest_tag == current_tag:
        rprint(f"[green]Already up to date ({latest_tag}).[/green]")
        return

    assets = [a for a in release["assets"] if a["name"].startswith("hytale-rag-data")]
    total_size = sum(a["size"] for a in assets)

    rprint(f"[bold green]New version available: {latest_tag}[/bold green] ({total_size / 1024 / 1024:.0f} MB)")

    if not Confirm.ask("Download update?", default=True):
        return

    if DATA_DIR.exists():
        backup = DATA_DIR.parent / "data_backup"
        if backup.exists():
            shutil.rmtree(backup)
        with console.status("[dim]Backing up current data...[/dim]"):
            shutil.copytree(DATA_DIR, backup)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for asset in assets:
        _download_asset_rich(asset, DATA_DIR)

    _save_version(latest_tag, release)

    backup = DATA_DIR.parent / "data_backup"
    if backup.exists():
        shutil.rmtree(backup)

    rprint(f"\n[bold green]Updated to {latest_tag}![/bold green]")


# ---------------------------------------------------------------------------
#  dashboard
# ---------------------------------------------------------------------------

@app.command()
def dashboard(
    port: int = typer.Option(5111, "--port", "-p", help="Port number"),
):
    """Start the web dashboard."""
    rprint(Panel(
        f"[bold]Dashboard:[/bold] [link=http://localhost:{port}]http://localhost:{port}[/link]\n"
        "[dim]Press Ctrl+C to stop[/dim]",
        title="[bold cyan]Hytale RAG Dashboard[/bold cyan]",
        border_style="cyan",
    ))

    env = os.environ.copy()
    env["DASHBOARD_PORT"] = str(port)
    try:
        subprocess.run([sys.executable, str(BASE_DIR / "dashboard.py")], env=env, check=True)
    except KeyboardInterrupt:
        rprint("\n[dim]Dashboard stopped.[/dim]")


# ---------------------------------------------------------------------------
#  Admin commands
# ---------------------------------------------------------------------------

admin_app = typer.Typer(help="Admin commands for building and publishing the index", rich_markup_mode="rich")
app.add_typer(admin_app, name="admin")


@admin_app.command("index-jar")
def index_jar(
    jar_path: str = typer.Argument(..., help="Path to HytaleServer.jar"),
    force: bool = typer.Option(False, "--force", "-f", help="Force re-index"),
):
    """Decompile and index a HytaleServer.jar."""
    from indexer import parse_java_files, parse_json_configs, index_api_chunks, compute_jar_hash, check_jar_changed
    from decompiler import decompile_jar
    from diffing import snapshot_api, rotate_snapshot, diff_api, save_diff, PREV_SNAPSHOT_FILE
    from config import HYTALE_PACKAGE_PREFIX

    jar_name = Path(jar_path).name

    if not force and not check_jar_changed(jar_path):
        rprint(f"[yellow]{jar_name} unchanged (hash matches). Use --force to re-index.[/yellow]")
        return

    jar_hash = compute_jar_hash(jar_path)

    with console.status("[bold]Snapshotting current API...[/bold]"):
        rotate_snapshot()
        snapshot_api()

    rprint(f"[bold]Decompiling {jar_name}...[/bold]")
    t0 = time.time()
    output_dir = decompile_jar(jar_path)
    rprint(f"[green]Decompiled in {time.time()-t0:.1f}s[/green]")

    rprint("[bold]Parsing Java files...[/bold]")
    t1 = time.time()
    all_chunks = parse_java_files(output_dir)
    config_chunks = parse_json_configs(output_dir)

    chunks = [c for c in all_chunks if c["metadata"].get("package", "").startswith(HYTALE_PACKAGE_PREFIX)]
    chunks.extend(config_chunks)
    rprint(f"[green]Parsed {len(chunks):,} chunks in {time.time()-t1:.1f}s[/green]")

    rprint(f"[bold]Indexing {len(chunks):,} chunks...[/bold]")
    t2 = time.time()
    index_api_chunks(chunks, jar_name=jar_name, jar_hash=jar_hash)
    rprint(f"[green]Indexed in {time.time()-t2:.1f}s[/green]")

    new_snap = snapshot_api()
    if PREV_SNAPSHOT_FILE.exists():
        diff_result = diff_api(PREV_SNAPSHOT_FILE, new_snap)
        save_diff(diff_result)
        if diff_result.get("summary"):
            rprint(f"\n[bold]API changes:[/bold]\n{diff_result['summary']}")

    rprint(Panel(f"[green]{len(chunks):,} chunks indexed from {jar_name}[/green]", title="[bold green]Done[/bold green]", border_style="green"))


@admin_app.command("scrape-guides")
def scrape_guides():
    """Scrape and index hytalemodding.dev guides."""
    from scraper import scrape_guides as _scrape
    from indexer import chunk_guides, index_guide_chunks

    rprint("[bold]Scraping hytalemodding.dev...[/bold]")
    t0 = time.time()
    pages = _scrape()
    rprint(f"[green]Scraped {len(pages)} pages in {time.time()-t0:.1f}s[/green]")

    if not pages:
        rprint("[yellow]No pages found.[/yellow]")
        return

    chunks = chunk_guides(pages)
    rprint(f"[bold]Indexing {len(chunks):,} guide chunks...[/bold]")
    t1 = time.time()
    index_guide_chunks(chunks)

    rprint(Panel(f"[green]{len(pages)} pages, {len(chunks):,} chunks[/green]", title="[bold green]Done[/bold green]", border_style="green"))


@admin_app.command("index-mods")
def index_mods(
    min_stars: int = typer.Option(2, "--min-stars", help="Minimum stars"),
    max_repos: int = typer.Option(30, "--max-repos", help="Max repos"),
):
    """Search GitHub and index Hytale mods."""
    from github_scraper import scrape_github_mods
    from indexer import index_mod_chunks

    rprint(f"[bold]Searching GitHub (min_stars={min_stars}, max_repos={max_repos})...[/bold]")
    t0 = time.time()
    chunks, repos = scrape_github_mods(min_stars=min_stars, max_repos=max_repos)
    rprint(f"[green]Found {len(repos)} repos, {len(chunks):,} chunks in {time.time()-t0:.1f}s[/green]")

    if not chunks:
        rprint("[yellow]No mod chunks found.[/yellow]")
        return

    rprint(f"[bold]Indexing {len(chunks):,} mod chunks...[/bold]")
    t1 = time.time()
    index_mod_chunks(chunks, repos)

    rprint(Panel(f"[green]{len(repos)} repos, {len(chunks):,} chunks[/green]", title="[bold green]Done[/bold green]", border_style="green"))


@admin_app.command("publish")
def publish(
    tag: str = typer.Option(..., "--tag", "-t", help="Release tag (e.g. v0.6.3)"),
    upload: bool = typer.Option(False, "--upload", help="Upload to GitHub (requires gh CLI)"),
):
    """Package data and create a GitHub Release."""
    if not DATA_DIR.exists():
        rprint("[red]No data directory found.[/red]")
        raise typer.Exit(1)

    exclude_dirs = {"decompilers", "snapshots", "data_backup"}
    exclude_files = {"dashboard.log", "fts.sqlite-shm", "fts.sqlite-wal", "version.json"}

    chromadb_dir = DATA_DIR / "chromadb"
    prefix = "hytale-rag-data"
    zip_sqlite = BASE_DIR / f"{prefix}-sqlite-{tag}.zip"
    zip_vectors = BASE_DIR / f"{prefix}-vectors-{tag}.zip"
    zip_src = BASE_DIR / f"{prefix}-sources-{tag}.zip"

    t0 = time.time()

    rprint(f"\n[bold]Packaging data for release {tag}...[/bold]\n")

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        task = progress.add_task("[bold]Compressing chroma.sqlite3...[/bold]")
        with zipfile.ZipFile(zip_sqlite, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            sqlite_path = chromadb_dir / "chroma.sqlite3"
            if sqlite_path.exists():
                zf.write(sqlite_path, "data/chromadb/chroma.sqlite3")
        progress.update(task, completed=True)

        task2 = progress.add_task("[bold]Compressing HNSW vectors...[/bold]")
        with zipfile.ZipFile(zip_vectors, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for item in sorted(chromadb_dir.iterdir()):
                if item.is_dir():
                    for f in sorted(item.iterdir()):
                        arc = f.relative_to(DATA_DIR)
                        zf.write(f, f"data/{arc}")
        progress.update(task2, completed=True)

        task3 = progress.add_task("[bold]Compressing sources + FTS...[/bold]")
        with zipfile.ZipFile(zip_src, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for root, dirs, files in os.walk(DATA_DIR):
                dirs[:] = [d for d in dirs if d not in exclude_dirs and d != "chromadb"]
                for f in files:
                    if f in exclude_files:
                        continue
                    fp = Path(root) / f
                    arc = fp.relative_to(DATA_DIR)
                    zf.write(fp, f"data/{arc}")
        progress.update(task3, completed=True)

    table = Table(title=f"Release {tag}", border_style="cyan")
    table.add_column("Asset", style="bold")
    table.add_column("Size", justify="right")

    zips = [(zip_sqlite, "sqlite"), (zip_vectors, "vectors"), (zip_src, "sources")]
    total = 0
    over_limit = []
    for zp, label in zips:
        mb = zp.stat().st_size / (1024 * 1024)
        total += mb
        style = "red" if mb > 2048 else ""
        table.add_row(zp.name, f"{mb:.1f} MB", style=style)
        if mb > 2048:
            over_limit.append(zp.name)
    table.add_row("[bold]Total[/bold]", f"[bold]{total:.1f} MB[/bold]")

    rprint()
    rprint(table)
    rprint(f"[dim]Packaged in {time.time()-t0:.0f}s[/dim]")

    if over_limit:
        rprint(f"\n[red]WARNING: {', '.join(over_limit)} exceeds GitHub's 2 GB limit.[/red]")
        raise typer.Exit(1)

    all_zips = [str(zp) for zp, _ in zips]

    if upload:
        if Confirm.ask(f"\nUpload release {tag} to GitHub?", default=True):
            rprint(f"\n[bold]Creating release {tag}...[/bold]")
            result = subprocess.run(
                ["gh", "release", "create", tag, *all_zips,
                 "--title", f"Hytale RAG Data {tag}",
                 "--notes", f"Pre-built RAG index for Hytale Server {tag}"],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                rprint(f"[bold green]Release created:[/bold green] {result.stdout.strip()}")
            else:
                rprint(f"[red]Error: {result.stderr}[/red]")
    else:
        rprint(f"\n[dim]To upload:[/dim]")
        rprint(f"  [cyan]gh release create {tag} {' '.join(all_zips)} --title \"Hytale RAG Data {tag}\"[/cyan]")


@admin_app.command("snapshot")
def snapshot(
    action: str = typer.Argument(..., help="save, list, restore, or delete"),
    filename: str = typer.Argument(None, help="Snapshot filename (for restore/delete)"),
    label: str = typer.Option("", "--label", help="Label (for save)"),
):
    """Manage index snapshots."""
    from snapshots import save_snapshot, list_snapshots, restore_snapshot, delete_snapshot

    if action == "save":
        with console.status("[bold]Saving snapshot...[/bold]"):
            result = save_snapshot(label)
        if "error" in result:
            rprint(f"[red]{result['error']}[/red]")
        else:
            rprint(f"[green]Saved: {result['file']} ({result['count']:,} chunks, {result.get('size_mb', '?')} MB)[/green]")

    elif action == "list":
        records = list_snapshots()
        if not records:
            rprint("[yellow]No snapshots found.[/yellow]")
            return
        table = Table(title="Snapshots", border_style="cyan")
        table.add_column("File", style="bold")
        table.add_column("Label")
        table.add_column("Chunks", justify="right")
        table.add_column("Size", justify="right")
        table.add_column("Created", style="dim")
        for r in records:
            table.add_row(r["file"], r.get("label", ""), str(r["count"]), f"{r.get('size_mb', '?')} MB", r["created_at"][:19])
        rprint(table)

    elif action == "restore":
        if not filename:
            rprint("[red]Filename required for restore.[/red]")
            return
        with console.status(f"[bold]Restoring {filename}...[/bold]"):
            result = restore_snapshot(filename)
        if "error" in result:
            rprint(f"[red]{result['error']}[/red]")
        else:
            rprint(f"[green]Restored: {result['restored']} — {result['chunks']:,} chunks[/green]")

    elif action == "delete":
        if not filename:
            rprint("[red]Filename required for delete.[/red]")
            return
        result = delete_snapshot(filename)
        if "error" in result:
            rprint(f"[red]{result['error']}[/red]")
        else:
            rprint(f"[green]Deleted: {result['deleted']}[/green]")


@admin_app.command("eval")
def run_eval(
    pipeline: bool = typer.Option(False, "--pipeline", help="Run hybrid+boost+slots comparison"),
):
    """Run the eval suite."""
    eval_script = str(Path(__file__).parent / "eval" / "run_eval.py")
    cmd = [sys.executable, eval_script]
    if pipeline:
        cmd.append("--pipeline")
    subprocess.run(cmd, check=False)


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
        return None
    except Exception:
        return None


def _download_asset_rich(asset, dest_dir: Path):
    import httpx
    name = asset["name"]
    url = asset["browser_download_url"]
    total = asset["size"]
    dest = dest_dir / name

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold]{task.description}"),
        BarColumn(bar_width=40),
        DownloadColumn(),
        TransferSpeedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(name, total=total)
        with httpx.stream("GET", url, timeout=600, follow_redirects=True) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_bytes(chunk_size=1024 * 1024):
                    f.write(chunk)
                    progress.update(task, advance=len(chunk))

    if name.endswith(".zip"):
        with console.status(f"[dim]Extracting {name}...[/dim]"):
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


if __name__ == "__main__":
    app()
