# Hytale Modding RAG

MCP server that gives Claude Code searchable knowledge about Hytale server modding — decompiled Java API, community guides, and open-source mod examples.

## Quick Start

```bash
pip install -e .

# Download pre-built RAG data (~2.3 GB)
hytale-rag setup

# Register as MCP server in Claude Code
claude mcp add hytale-docs --scope user -- python "C:\path\to\server.py"
```

That's it. The data is pre-indexed — no need to decompile or embed anything yourself.

## Prerequisites

- Python 3.10+
- [Ollama](https://ollama.com) with `nomic-embed-text` (for local embeddings):
  ```
  ollama pull nomic-embed-text
  ```

## CLI Commands

### For users

| Command | Description |
|---|---|
| `hytale-rag setup` | Download pre-built RAG data (first-time) |
| `hytale-rag update` | Check for and download newer data |
| `hytale-rag serve` | Start the MCP server |
| `hytale-rag dashboard` | Start the web dashboard (localhost:5111) |
| `hytale-rag create-mod <Name> [--hot-reload]` | Scaffold a new Hytale mod project |
| `hytale-rag status` | Show index status and data version |

### For maintainers (rebuild the index)

| Command | Description |
|---|---|
| `hytale-rag index-jar <path> [--force]` | Decompile and index a HytaleServer.jar |
| `hytale-rag scrape-guides` | Scrape and index hytalemodding.dev guides |
| `hytale-rag index-mods [--min-stars N] [--max-repos N]` | Index GitHub mod repos |
| `hytale-rag publish --tag v0.6.3 [--upload]` | Package data and create GitHub Release |
| `hytale-rag snapshot save/list/restore/delete` | Manage index snapshots |
| `hytale-rag eval [--pipeline]` | Run eval suite |

## Mod Scaffolding

```bash
hytale-rag create-mod MyMod --output C:\Users\Me\Desktop --author MyName --hot-reload
```

Generates a complete IntelliJ + Gradle project with:
- Build against HytaleServer.jar (`compileOnly` via shadow plugin)
- Server downloader (OAuth, auto-setup on first run)
- "Hytale Server" run config with optional hot reload (`-XX:+AllowEnhancedClassRedefinition`, requires JBR 25)
- Plugin class with `setup()`/`start()`/`shutdown()` lifecycle
- `boot-server.ps1` standalone launcher

## Architecture

Three data sources, ranked by trust:

1. **Decompiled Java API** (ground truth) — extracted from HytaleServer.jar via CFR
2. **Community Guides** (how-to) — scraped from hytalemodding.dev
3. **GitHub Mods** (examples) — cloned from public repos

Search: hybrid (dense + FTS5 keyword via RRF), exact-identifier boost, per-source slot enforcement
Embeddings: Ollama `nomic-embed-text` (768-dim, cosine similarity)
Vector store: ChromaDB (local, persistent)
Keyword index: SQLite FTS5 (Porter stemmer, CamelCase token splitting)

## Contributors

- [@SpectreWall](https://github.com/SpectreWall) — Modding skills and knowledge base
