# Hytale Modding RAG

MCP server that gives Claude Code searchable knowledge about Hytale server modding — decompiled Java API, community guides, and open-source mod examples.

## Quick Start

```bash
pip install -e .
hytale-rag
```

The CLI downloads the pre-built RAG data (~2.3 GB) on first run and shows you the MCP command to register:

```bash
claude mcp add hytale-docs --scope user -- python "C:\path\to\server.py"
```

That's it. Start asking Claude about Hytale modding.

## Prerequisites

- Python 3.10+

## CLI Commands

| Command | Description |
|---|---|
| `hytale-rag` | Setup (first run) + show MCP usage |
| `hytale-rag setup --force` | Re-download RAG data |
| `hytale-rag update` | Check for and download newer data |
| `hytale-rag dashboard` | Browse the index at localhost:5111 |

## Architecture

Three data sources, ranked by trust:

1. **Decompiled Java API** (ground truth) — extracted from HytaleServer.jar via CFR
2. **Community Guides** (how-to) — scraped from hytalemodding.dev
3. **GitHub Mods** (examples) — cloned from public repos

Search: hybrid (dense + FTS5 keyword via RRF), exact-identifier boost, per-source slot enforcement
Vector store: ChromaDB (local, persistent)
Keyword index: SQLite FTS5 (Porter stemmer, CamelCase token splitting)

## Contributors

- [@SpectreWall](https://github.com/SpectreWall) — Modding skills and knowledge base
