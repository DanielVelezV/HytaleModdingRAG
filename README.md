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
  <code>82,000+ indexed chunks</code> &nbsp;·&nbsp; <code>30 mod repos</code> &nbsp;·&nbsp; <code>35 MB download</code> &nbsp;·&nbsp; <code>no external services</code>
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

*(Optional: install [Ollama](https://ollama.com) to add semantic search — see [Data tiers](#data-tiers) below.)*

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

On first run, the CLI downloads the **pre-built RAG data** (~35 MB) from GitHub Releases and asks which tier you want. No need to decompile or index anything yourself — it's all pre-built.

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
| **Decompiled Java API** | Every class, method, field, and enum from `HytaleServer.jar`, extracted via CFR decompiler. Parameter names are recovered from Hytale's shared source using a deobfuscation mapper | Ground truth | 68,518 |
| **Community Guides** | Tutorials and documentation from [hytalemodding.dev](https://hytalemodding.dev) | How-to | 2,635 |
| **GitHub Mods** | Real mod source code from 30 open-source repositories | Examples | 11,092 |

### Data tiers

Search runs on a keyword index by default. Semantic embeddings are an optional upgrade:

| | **Standard** (default) | **+ Semantic** |
|---|---|---|
| Download | **35 MB** | 543 MB |
| Search quality (R@5) | **91.5%** | **97.2%** |
| Guides / cross-source | 100% | 100% |
| Java API | 88.5% | 96.2% |
| Build your own index | **~5 min** | ~30 min |
| Method | keyword + boosts | embeddings + keyword |
| Requires Ollama | **no** | yes |

Standard answers guide and cross-source questions perfectly and misses roughly one Java API lookup in nine. The 5.7-point gap is entirely conceptual queries with no shared vocabulary — *"how does the ECS component system work"* finds nothing by keyword, because the answer classes don't contain those words.

Embedding 82,000 chunks takes ~25 minutes and is 83% of total index time; the keyword index builds in **2.6 seconds**. That's why standard is the default, and why you can index your own jar without waiting for a release:

```bash
hytale-rag setup --tier full --force   # add semantic search
hytale-rag setup --tier lite --force   # go back to keyword-only
```

### Search pipeline

When Claude searches the knowledge base, it uses a **hybrid search** pipeline:

```
Query → Keyword search (SQLite FTS5) [+ Dense embeddings, semantic tier only]
      → Reciprocal Rank Fusion (RRF) merging
      → Exact identifier boosting (class/method name matches rank higher)
      → Guide bridge boosting (extracts API classes mentioned in guide headings)
      → Classname token boosting (CamelCase-split matching with stemming)
      → Per-source slot enforcement (API + Guides + Mods all represented)
      → Deduplication per class
      → Top results returned to Claude
```

### Data distribution

The pre-built index is hosted as **GitHub Release assets**. The standard tier pulls two small files; the semantic tier adds the vector store. When you run `hytale-rag` or `hytale-rag setup`, it downloads and extracts them automatically, and remembers your tier on update. To check for updates:

```bash
hytale-rag update
```

---

## MCP Tools

Once connected, Claude has access to these tools:

### Search

| Tool | Description |
|---|---|
| `search_hytale_docs` | Search across **all three sources** at once — the default for general questions |
| `search_hytale_api` | Search only the decompiled Java API (classes, methods, fields). Accepts optional `package` and `type` filters |
| `search_hytale_guides` | Search only the community guides and tutorials from hytalemodding.dev |
| `search_hytale_mods` | Search only real-world mod examples from GitHub repositories |

### API Inspection

| Tool | Description |
|---|---|
| `get_class_info` | Get the full overview of a class — declaration, fields, method signatures, package, imports |
| `get_class_hierarchy` | Get the inheritance chain — what a class extends, implements, and what extends it |
| `get_method_source` | Get the full untruncated source code of a specific method (not the 1500-char chunk) |
| `find_usages` | Find where a class or method is used across the decompiled API and mod examples |
| `list_packages` | List all Java packages in the indexed API |

### Mod Scaffolding

| Tool | Description |
|---|---|
| `create_mod` | Scaffold a complete Hytale mod project with Gradle, IntelliJ run configs, manifest, and plugin class. Supports hot reload with JetBrains Runtime |

### Index Status

| Tool | Description |
|---|---|
| `get_index_status` | Check what's indexed — chunk counts, last update times |
| `get_api_changes` | Show what changed in the last re-index — new, removed, and modified classes |

The index itself is **pre-built and distributed through GitHub Releases** — building it requires `HytaleServer.jar`, which can't be redistributed. Use `hytale-rag update` to pull a newer index when a Hytale version ships.

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
