from pathlib import Path
import os

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
CHROMADB_DIR = DATA_DIR / "chromadb"
DECOMPILED_DIR = DATA_DIR / "decompiled"
SCRAPED_DIR = DATA_DIR / "scraped"
DECOMPILER_DIR = DATA_DIR / "decompilers"
META_FILE = DATA_DIR / "meta.json"

CFR_VERSION = "0.152"
CFR_URL = f"https://github.com/leibnitz27/cfr/releases/download/{CFR_VERSION}/cfr-{CFR_VERSION}.jar"
CFR_JAR = DECOMPILER_DIR / f"cfr-{CFR_VERSION}.jar"

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_EMBED_MODEL = os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")

GUIDES_BASE_URL = "https://hytalemodding.dev"
GUIDES_DOCS_URL = f"{GUIDES_BASE_URL}/en/docs"

API_COLLECTION = "hytale_api"
GUIDES_COLLECTION = "hytale_guides"
MODS_COLLECTION = "hytale_mods"

GITHUB_MODS_DIR = DATA_DIR / "github_mods"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"

HYTALE_PACKAGE_PREFIX = "com.hypixel.hytale"

MAX_CHUNK_SIZE = 1500
CHUNK_OVERLAP = 200
