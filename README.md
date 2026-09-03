# Hytale Modding RAG

MCP server that gives Claude Code searchable knowledge about Hytale server modding — decompiled Java API, community guides, and open-source mod examples.

## Prerequisites

- Python 3.10+
- Java JDK/JRE (for CFR decompiler)
- [Ollama](https://ollama.com) with `nomic-embed-text`:
  ```
  ollama pull nomic-embed-text
  ```
- `gh` CLI (optional, for GitHub mod scraping):
  ```
  winget install GitHub.cli
  ```

## Setup

```bash
pip install -e .
```

## Indexing

```bash
# Index decompiled Java API (source of truth)
hytale-rag index-jar "C:\path\to\HytaleServer.jar"

# Scrape community guides from hytalemodding.dev
hytale-rag scrape-guides

# Index open-source mods from GitHub
hytale-rag index-mods --min-stars 2 --max-repos 30
```

## Register as MCP Server

```bash
claude mcp add hytale-docs --scope user -- python "C:\path\to\server.py"
```

## Dashboard

```bash
python dashboard.py
# Open http://localhost:5111
```

## CLI Commands

| Command | Description |
|---|---|
| `hytale-rag index-jar <path> [--force]` | Decompile and index a HytaleServer.jar |
| `hytale-rag scrape-guides` | Scrape and index hytalemodding.dev guides |
| `hytale-rag index-mods [--min-stars N] [--max-repos N]` | Index GitHub mod repos |
| `hytale-rag snapshot save [--source api\|guides\|mods] [--label TEXT]` | Save index snapshot |
| `hytale-rag snapshot list [--source SOURCE]` | List snapshots |
| `hytale-rag snapshot restore <filename>` | Restore from snapshot |
| `hytale-rag snapshot delete <filename>` | Delete a snapshot |
| `hytale-rag status` | Show index status |
| `hytale-rag eval` | Run the eval set against the current index |

## Architecture

Three data sources, ranked by trust:

1. **Decompiled Java API** (ground truth) — extracted from HytaleServer.jar via CFR
2. **Community Guides** (how-to) — scraped from hytalemodding.dev
3. **GitHub Mods** (examples) — cloned from public repos

Embeddings: Ollama `nomic-embed-text` (768-dim, cosine similarity)
Vector store: ChromaDB (local, persistent)
