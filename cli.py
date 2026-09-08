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
            "The standard index is a ~35 MB download and needs nothing else.\n"
            "Semantic search is available as an optional upgrade.",
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
        from store import get_status
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
    tier: str = typer.Option(None, "--tier", "-t", help="Data tier: 'lite' (default, keyword) or 'full' (adds semantic search)"),
):
    """Download pre-built RAG data from GitHub Releases."""
    if DATA_DIR.exists() and any(DATA_DIR.iterdir()) and not force:
        rprint("[yellow]Data already exists.[/yellow] Use [bold]--force[/bold] to overwrite, or [bold]hytale-rag update[/bold] to check for updates.")
        return

    _do_setup(tier=tier)
    rprint()
    _show_mcp_usage()


def _detect_ollama() -> tuple[bool, str]:
    """Return (usable, detail). Checks the daemon is up and the embed model is pulled."""
    from config import OLLAMA_EMBED_MODEL
    try:
        import ollama
    except ImportError:
        return False, "python package not installed"
    try:
        tags = ollama.list()
        models = [m.get("model", "") or m.get("name", "") for m in tags.get("models", [])]
        base = OLLAMA_EMBED_MODEL.split(":")[0]
        if any(base in m for m in models):
            return True, f"running, '{OLLAMA_EMBED_MODEL}' available"
        return False, f"running, but '{OLLAMA_EMBED_MODEL}' not pulled"
    except Exception:
        return False, "not running"


def _tier_assets(assets: list[dict], tier: str) -> list[dict]:
    """Split release assets by tier.

    Both tiers get sources + the sqlite pack (fts.sqlite + meta.sqlite); the
    semantic tier adds the vector store on top. Shipping meta.sqlite to both
    means a semantic install still works if Ollama later goes away.
    """
    if tier == "lite":
        return [a for a in assets if "-vectors-" not in a["name"]]
    return assets


def _choose_tier(assets: list[dict]) -> str:
    """Show the two tiers with real download sizes and recall, and let the user pick."""
    full = _tier_assets(assets, "full")
    lite = _tier_assets(assets, "lite")

    if not lite:
        return "full"

    full_mb = sum(a["size"] for a in full) / 1024 / 1024
    lite_mb = sum(a["size"] for a in lite) / 1024 / 1024

    ok, detail = _detect_ollama()

    table = Table(box=None, padding=(0, 2), show_header=True, header_style="bold")
    table.add_column("")
    table.add_column("Standard", justify="right", style="green")
    table.add_column("+ Semantic", justify="right")
    table.add_row("Download", f"[bold green]{lite_mb:,.0f} MB[/bold green]", f"[bold]{full_mb:,.0f} MB[/bold]")
    table.add_row("Search quality (R@5)", "[bold]91.5%[/bold]", "[bold]97.2%[/bold]")
    table.add_row("Guides / cross-source", "100%", "100%")
    table.add_row("Java API", "88.5%", "96.2%")
    table.add_row("Method", "keyword + boosts", "embeddings + keyword")
    table.add_row("Requires Ollama", "[green]no[/green]", "[yellow]yes[/yellow]")

    rprint(Panel(
        table,
        title="[bold cyan]Choose your data tier[/bold cyan]",
        border_style="cyan",
    ))

    rprint("  [dim]Standard is recommended — it covers guides and cross-source[/dim]")
    rprint("  [dim]searches perfectly, and misses ~1 Java API lookup in 9.[/dim]")
    rprint()
    if ok:
        rprint(f"  [green]Ollama detected[/green] — {detail}")
        rprint(f"  [dim]You can run the semantic tier for +5.7% recall if you want it.[/dim]")
    else:
        rprint(f"  [dim]Ollama not available — {detail}. Semantic tier needs it.[/dim]")
    rprint()

    choice = typer.prompt(
        "Which tier? [standard/semantic]",
        default="standard",
    ).strip().lower()
    return "full" if choice.startswith(("se", "f")) else "lite"


def _do_setup(tier: str = None):
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

    if tier not in ("full", "lite"):
        tier = _choose_tier(assets)

    assets = _tier_assets(assets, tier)
    total_size = sum(a["size"] for a in assets)

    rprint()
    rprint(Panel(
        f"[bold]Version:[/bold] {tag}\n"
        f"[bold]Tier:[/bold]    {tier}\n"
        f"[bold]Assets:[/bold]  {len(assets)} files ({total_size / 1024 / 1024:,.0f} MB)",
        title="[bold cyan]Hytale RAG Data[/bold cyan]",
        border_style="cyan",
    ))
    rprint()

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    for asset in assets:
        _download_asset_rich(asset, DATA_DIR)

    _save_version(tag, release, tier=tier)

    rprint()
    label = "standard" if tier == "lite" else "semantic"
    rprint(f"[bold green]Data version {tag} ({label}) installed successfully![/bold green]")
    if tier == "lite":
        rprint("[dim]Keyword search + boosts. No Ollama needed.[/dim]")
        rprint("[dim]Add semantic search later: [bold]hytale-rag setup --tier full --force[/bold][/dim]")


# ---------------------------------------------------------------------------
#  update
# ---------------------------------------------------------------------------

@app.command()
def update():
    """Check for and download newer RAG data."""
    current = _load_version()
    current_tag = current.get("tag") if current else None
    current_tier = (current.get("tier") if current else None) or "full"

    rprint(f"[bold]Current version:[/bold] {current_tag or '[dim]none[/dim]'} [dim]({current_tier} tier)[/dim]")

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
    assets = _tier_assets(assets, current_tier)
    total_size = sum(a["size"] for a in assets)

    rprint(f"[bold green]New version available: {latest_tag}[/bold green] "
           f"({total_size / 1024 / 1024:,.0f} MB, {current_tier} tier)")

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

    _save_version(latest_tag, release, tier=current_tier)

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


def _save_version(tag: str, release: dict, tier: str = "full"):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DATA_VERSION_FILE.write_text(json.dumps({
        "tag": tag,
        "tier": tier,
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
