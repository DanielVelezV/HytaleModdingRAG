"""Hytale Modding RAG — interactive CLI."""
import json
import os
import shlex
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
from rich.text import Text
from rich.prompt import Prompt, Confirm

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

SHELL_COMMANDS = {
    "setup": "Download pre-built RAG data",
    "update": "Check for newer data",
    "serve": "Start MCP server",
    "dashboard": "Start web dashboard",
    "create-mod": "Scaffold a new mod project",
    "status": "Show index status",
    "admin": "Admin commands (index-jar, publish, ...)",
    "help": "Show available commands",
    "clear": "Clear the screen",
    "exit": "Exit the shell",
}


@app.callback()
def main(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        _interactive_shell()


def _print_status_summary():
    version = _load_version()
    has_data = DATA_DIR.exists() and any(DATA_DIR.iterdir())

    if not has_data:
        rprint(Panel(
            "[yellow]No data found.[/yellow] Run [bold green]hytale-rag setup[/bold green] to download the pre-built index.",
            title="[bold]Status[/bold]",
            border_style="yellow",
        ))
        return

    lines = []
    tag = version.get("tag", "local build") if version else "local build"
    lines.append(f"[bold]Data version:[/bold] {tag}")

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
            title=f"[bold]Index: {tag}[/bold]",
            border_style="green",
        ))
    except Exception:
        rprint(Panel(f"Data version: {tag}", title="[bold]Status[/bold]", border_style="green"))


# ---------------------------------------------------------------------------
#  setup
# ---------------------------------------------------------------------------

@app.command()
def setup(
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing data"),
):
    """Download pre-built RAG data from GitHub Releases."""
    rprint(BANNER)

    if DATA_DIR.exists() and any(DATA_DIR.iterdir()) and not force:
        rprint("[yellow]Data already exists.[/yellow] Use [bold]--force[/bold] to overwrite, or [bold]hytale-rag update[/bold] to check for updates.")
        return

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
        f"[bold]Assets:[/bold]  {len(assets)} files ({total_size / 1024 / 1024:.0f} MB)\n",
        title="[bold cyan]Hytale RAG Data[/bold cyan]",
        border_style="cyan",
    ))

    if not Confirm.ask(f"Download {total_size / 1024 / 1024:.0f} MB of data?", default=True):
        return

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    for asset in assets:
        _download_asset_rich(asset, DATA_DIR)

    _save_version(tag, release)

    rprint()
    rprint(Panel(
        f"[green]Data version {tag} installed successfully![/green]\n\n"
        f"[bold]Next steps:[/bold]\n"
        f"  [dim]1.[/dim] Register the MCP server:\n"
        f"     [cyan]claude mcp add hytale-docs --scope user -- python \"{BASE_DIR / 'server.py'}\"[/cyan]\n"
        f"  [dim]2.[/dim] Start asking Claude about Hytale modding!",
        title="[bold green]Setup Complete[/bold green]",
        border_style="green",
    ))


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
#  serve
# ---------------------------------------------------------------------------

@app.command()
def serve():
    """Start the MCP server."""
    if not DATA_DIR.exists() or not any(DATA_DIR.iterdir()):
        rprint("[red]No data found.[/red] Run [bold]hytale-rag setup[/bold] first.")
        raise typer.Exit(1)

    rprint(Panel(
        "[bold]MCP server starting...[/bold]\n"
        "[dim]Press Ctrl+C to stop[/dim]",
        title="[bold cyan]Hytale Modding RAG[/bold cyan]",
        border_style="cyan",
    ))

    server_py = BASE_DIR / "server.py"
    try:
        subprocess.run([sys.executable, str(server_py)], check=True)
    except KeyboardInterrupt:
        rprint("\n[dim]Server stopped.[/dim]")


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
#  create-mod
# ---------------------------------------------------------------------------

@app.command("create-mod")
def create_mod_cmd(
    name: str = typer.Argument(None, help="Mod name in PascalCase"),
    output: str = typer.Option(None, "--output", "-o", help="Output directory"),
    group: str = typer.Option("", "--group", "-g", help="Java package group"),
    author: str = typer.Option("", "--author", "-a", help="Author name"),
    hot_reload: bool = typer.Option(False, "--hot-reload", help="Enable hot reload (requires JBR 25)"),
):
    """Scaffold a new Hytale mod project."""
    rprint()

    if not name:
        name = Prompt.ask("[bold]Mod name[/bold] [dim](PascalCase)[/dim]")
    if not output:
        output = Prompt.ask("[bold]Output directory[/bold]", default=os.getcwd())
    if not author:
        author = Prompt.ask("[bold]Author name[/bold]", default="")
    if not hot_reload:
        hot_reload = Confirm.ask("[bold]Enable hot reload?[/bold] [dim](requires JBR 25)[/dim]", default=False)

    with console.status(f"[bold cyan]Creating {name}...[/bold cyan]"):
        from server import create_mod
        result = create_mod(name, output, group=group, author=author, hot_reload=hot_reload)

    if result.startswith("Error"):
        rprint(f"[red]{result}[/red]")
        raise typer.Exit(1)

    out_path = Path(output) / name

    jdk_note = "[yellow]JetBrains Runtime (JBR) 25[/yellow]" if hot_reload else "Java 25"
    hot_note = "\n  [dim]4.[/dim] Edit code, [bold]Ctrl+F9[/bold] to hot reload" if hot_reload else ""

    rprint(Panel(
        f"[bold green]Mod \"{name}\" created![/bold green]\n\n"
        f"[bold]Location:[/bold] {out_path}\n"
        f"[bold]Package:[/bold]  {group or f'com.{name.lower()}'}\n"
        f"[bold]JDK:[/bold]      {jdk_note}\n\n"
        f"[bold]Quick start:[/bold]\n"
        f"  [dim]1.[/dim] Run [cyan].\\server\\setup.ps1[/cyan] to download the Hytale server\n"
        f"  [dim]2.[/dim] Open in IntelliJ, set Gradle JDK to {jdk_note}\n"
        f"  [dim]3.[/dim] Select [bold]\"Hytale Server\"[/bold] run config and hit Run"
        f"{hot_note}",
        title=f"[bold green]{name}[/bold green]",
        border_style="green",
    ))


# ---------------------------------------------------------------------------
#  status
# ---------------------------------------------------------------------------

@app.command()
def status():
    """Show index status and data version."""
    version = _load_version()
    has_data = DATA_DIR.exists() and any(DATA_DIR.iterdir())

    if not has_data:
        rprint("[yellow]No data found.[/yellow] Run [bold]hytale-rag setup[/bold] first.")
        return

    tag = version.get("tag", "local build") if version else "local build"

    try:
        from indexer import get_status
        st = get_status()
    except Exception:
        rprint(f"[bold]Data version:[/bold] {tag}")
        return

    table = Table(title=f"Index Status — {tag}", border_style="cyan")
    table.add_column("Source", style="bold")
    table.add_column("Chunks", justify="right")
    table.add_column("Details")
    table.add_column("Indexed At", style="dim")

    api = st.get("api", {})
    if api.get("indexed"):
        table.add_row("Java API", f"{api.get('chunks', 0):,}", f"jar: {api.get('jar', '?')}", api.get("indexed_at", "?")[:19])

    guides = st.get("guides", {})
    if guides.get("indexed"):
        table.add_row("Guides", f"{guides.get('chunks', 0):,}", "hytalemodding.dev", guides.get("indexed_at", "?")[:19])

    mods = st.get("mods", {})
    if mods.get("indexed"):
        table.add_row("Mods", f"{mods.get('chunks', 0):,}", f"{mods.get('repo_count', '?')} repos", mods.get("indexed_at", "?")[:19])

    rprint()
    rprint(table)


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


def _clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def _interactive_shell():
    _clear_screen()
    rprint(BANNER)
    _print_status_summary()
    rprint()

    prompt_fn = None
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.completion import WordCompleter
        from prompt_toolkit.history import InMemoryHistory
        from prompt_toolkit.formatted_text import HTML

        commands = list(SHELL_COMMANDS.keys())
        admin_commands = ["index-jar", "scrape-guides", "index-mods", "publish", "snapshot", "eval"]
        all_completions = commands + [f"admin {c}" for c in admin_commands]
        completer = WordCompleter(all_completions, ignore_case=True)

        session = PromptSession(
            history=InMemoryHistory(),
            completer=completer,
        )
        prompt_fn = lambda: session.prompt(
            HTML("<ansibrightcyan><b>hytale-rag</b></ansibrightcyan> <ansigray>></ansigray> "),
        )
    except Exception:
        prompt_fn = lambda: input("hytale-rag > ")

    while True:
        try:
            user_input = prompt_fn().strip()
        except (KeyboardInterrupt, EOFError):
            rprint("\n[dim]Goodbye![/dim]")
            break

        if not user_input:
            continue

        if user_input in ("exit", "quit", "q"):
            rprint("[dim]Goodbye![/dim]")
            break

        if user_input == "clear":
            _clear_screen()
            rprint(BANNER)
            continue

        if user_input == "help":
            table = Table(title="Commands", border_style="cyan", show_edge=False)
            table.add_column("Command", style="bold cyan")
            table.add_column("Description")
            for cmd, desc in SHELL_COMMANDS.items():
                table.add_row(cmd, desc)
            rprint()
            rprint(table)
            rprint()
            continue

        try:
            parts = shlex.split(user_input)
        except ValueError:
            parts = user_input.split()

        try:
            app(parts, standalone_mode=False)
        except SystemExit:
            pass
        except Exception as e:
            rprint(f"[red]Error: {e}[/red]")

        rprint()


if __name__ == "__main__":
    app()
