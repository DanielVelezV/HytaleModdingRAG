<p align="center">
  <img src="https://img.shields.io/badge/Hytale-Modding_RAG-00C2FF?style=for-the-badge&labelColor=1a1a2e" alt="Hytale Modding RAG"/>
  <img src="https://img.shields.io/badge/MCP-Server-8B5CF6?style=for-the-badge&labelColor=1a1a2e" alt="MCP Server"/>
  <img src="https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white&labelColor=1a1a2e" alt="Python 3.10+"/>
</p>

<h1 align="center">Hytale Modding RAG</h1>

<p align="center">
  <strong>An MCP server that gives Claude deep knowledge about Hytale server modding.</strong><br>
  Decompiled Java API + community guides + open-source mods — all searchable through natural language.
</p>

<p align="center">
  <code>82,000+ indexed chunks</code> &nbsp;·&nbsp; <code>30 mod repos</code> &nbsp;·&nbsp; <code>Hybrid search (dense + keyword)</code>
</p>

---

## What is this?

Hytale's modding API has no public documentation yet. This project solves that by building a **knowledge base** from three sources and exposing it as an [MCP server](https://modelcontextprotocol.io) — so Claude can answer questions about the API, find real code examples, and even scaffold new mods for you.

Instead of digging through decompiled code yourself, you just ask Claude:

> *"How do I register a custom command in Hytale?"*

Claude searches the knowledge base, finds the relevant API classes and real mod examples, and gives you a grounded answer — not a hallucination.

---

## Prerequisites

- **Python 3.10+**
- **Claude Code** (CLI, Desktop, or IDE extension)

That's it. No Ollama, no Docker, no external services.

---

## Getting Started

### 1. Clone and install

```bash
git clone https://github.com/DanielVelezV/HytaleModdingRAG.git
cd HytaleModdingRAG
pip install -e .
```

### 2. Run the CLI

```bash
hytale-rag
```

On first run, the CLI downloads the **pre-built RAG data** (~2.3 GB) from GitHub Releases. No need to decompile or index anything yourself — it's all pre-built.

### 3. Connect to Claude

Once the data is downloaded, the CLI shows you the exact command. Just tell Claude:

> *"Connect yourself to my Hytale modding MCP server. The server script is at `C:\path\to\HytaleModdingRAG\server.py`"*

Or register it manually:

```bash
claude mcp add hytale-docs --scope user -- python "C:\path\to\HytaleModdingRAG\server.py"
```

That's it. Start a new conversation and Claude now has full access to the Hytale modding knowledge base.

---

## How the Data Works

### Three sources, ranked by trust

| Source | What it contains | Trust level | Chunks |
|---|---|---|---|
| **Decompiled Java API** | Every class, method, field, and enum from `HytaleServer.jar`, extracted via CFR decompiler | Ground truth | 68,518 |
| **Community Guides** | Tutorials and documentation from [hytalemodding.dev](https://hytalemodding.dev) | How-to | 2,635 |
| **GitHub Mods** | Real mod source code from 30 open-source repositories | Examples | 11,092 |

### Search pipeline

When Claude searches the knowledge base, it uses a **hybrid search** pipeline:

```
Query → Dense embeddings (ChromaDB) + Keyword search (SQLite FTS5)
      → Reciprocal Rank Fusion (RRF) merging
      → Exact identifier boosting (class/method name matches rank higher)
      → Per-source slot enforcement (API + Guides + Mods all represented)
      → Deduplication per class
      → Top results returned to Claude
```

### Data distribution

The pre-built index is hosted as **GitHub Release assets** (split into 3 files to fit under the 2 GB limit). When you run `hytale-rag` or `hytale-rag setup`, it downloads and extracts them automatically. To check for updates:

```bash
hytale-rag update
```

---

## Example Prompts

Once connected, try asking Claude things like:

| Prompt | What Claude does |
|---|---|
| *"How do I create a custom command in Hytale?"* | Searches the API for command registration classes and finds real examples from mod repos |
| *"Show me the full source of the EventBus class"* | Returns the decompiled source code with all methods and fields |
| *"What events can I listen to for player actions?"* | Lists event classes in the API and shows how mods use them |
| *"Create me a mod called ChatFilter that censors bad words"* | Scaffolds a complete Gradle + IntelliJ project with build config, run config, and plugin lifecycle |
| *"What's the class hierarchy for ServerEntity?"* | Walks the inheritance tree showing parent classes, interfaces, and child classes |
| *"Find mods that implement a web server"* | Searches GitHub mod repos for HTTP/web server implementations |
| *"How does the permission system work?"* | Combines API docs, guide explanations, and real mod examples for a complete answer |
| *"List all packages under com.hypixel.hytale.server"* | Enumerates the API package structure |

---

## Dashboard

Browse the indexed data visually:

```bash
hytale-rag dashboard
```

Opens a web UI at `localhost:5111` where you can search and explore the knowledge base directly.

---

## Contributors

- [@SpectreWall](https://github.com/SpectreWall) — Modding skills and knowledge base

---

<p align="center">
  <sub>Built for the Hytale modding community</sub>
</p>
