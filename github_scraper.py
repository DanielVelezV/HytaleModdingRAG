import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

from config import GITHUB_MODS_DIR, META_FILE


def _rm_readonly(func, path, _exc_info):
    os.chmod(path, stat.S_IWRITE)
    func(path)


def search_repos(
    queries: list[str] | None = None,
    min_stars: int = 0,
    max_repos: int = 30,
) -> list[dict]:
    if queries is None:
        queries = ["hytale mod", "hytale plugin", "hytale server mod"]

    seen = set()
    repos = []

    for q in queries:
        proc = subprocess.run(
            ["gh", "search", "repos", q,
             "--language=Java", "--sort=stars", "--limit=30",
             "--json", "fullName,stargazersCount,updatedAt,description"],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            continue

        for repo in json.loads(proc.stdout):
            name = repo["fullName"]
            if name in seen:
                continue
            seen.add(name)
            if repo["stargazersCount"] >= min_stars:
                repos.append(repo)

    repos.sort(key=lambda r: r["stargazersCount"], reverse=True)
    return repos[:max_repos]


def clone_repo(full_name: str) -> Path | None:
    GITHUB_MODS_DIR.mkdir(parents=True, exist_ok=True)
    repo_dir = GITHUB_MODS_DIR / full_name.replace("/", "__")

    if repo_dir.exists():
        shutil.rmtree(repo_dir, onerror=_rm_readonly)

    proc = subprocess.run(
        ["gh", "repo", "clone", full_name, str(repo_dir), "--", "--depth=1"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return None

    git_dir = repo_dir / ".git"
    if git_dir.exists():
        shutil.rmtree(git_dir, onerror=_rm_readonly)

    return repo_dir


def _extract_hytale_version(repo_dir: Path) -> str:
    import re
    for name in ("build.gradle", "build.gradle.kts"):
        gradle = repo_dir / name
        if gradle.exists():
            try:
                text = gradle.read_text(encoding="utf-8", errors="replace")
                m = re.search(r"hytale[sS]erver.*?['\"]([0-9][^'\"]+)['\"]", text)
                if m:
                    return m.group(1)
            except Exception:
                pass
    pom = repo_dir / "pom.xml"
    if pom.exists():
        try:
            import re
            text = pom.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"<artifactId>hytale.*?</artifactId>\s*<version>([^<]+)</version>", text, re.DOTALL | re.IGNORECASE)
            if m:
                return m.group(1)
        except Exception:
            pass
    return ""


def parse_mod_files(repo_dir: Path, repo_name: str, updated_at: str = "") -> list[dict]:
    from indexer import _chunk_id, _extract_package, _extract_methods, _build_class_overview, _extract_inheritance
    from java_parser import parse_file
    from config import MAX_CHUNK_SIZE

    hytale_version = _extract_hytale_version(repo_dir)
    base_meta = {
        "source": "mod",
        "repo": repo_name,
    }
    if hytale_version:
        base_meta["hytale_version"] = hytale_version
    if updated_at:
        base_meta["updated_at"] = updated_at

    chunks = []

    for readme_name in ("README.md", "readme.md", "README.MD", "README"):
        readme = repo_dir / readme_name
        if readme.exists():
            try:
                text = readme.read_text(encoding="utf-8", errors="replace")
                if len(text) > 50:
                    if len(text) > MAX_CHUNK_SIZE:
                        text = text[:MAX_CHUNK_SIZE] + "\n... truncated"
                    chunks.append({
                        "id": _chunk_id(f"{repo_name}:README", "mod"),
                        "text": f"# {repo_name} README\n\n{text}",
                        "metadata": {**base_meta, "type": "mod_readme", "file": readme_name},
                    })
            except Exception:
                pass
            break

    java_files = sorted(repo_dir.rglob("*.java"))

    for java_file in java_files:
        try:
            source = java_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        if len(source) < 30:
            continue

        rel_path = java_file.relative_to(repo_dir)
        package = _extract_package(source)
        class_name = java_file.stem
        fqn = f"{package}.{class_name}" if package else class_name

        parsed = parse_file(source)
        if parsed and parsed["types"]:
            for td in parsed["types"]:
                overview = _build_class_overview(source, fqn, package,
                    {"extends": td.get("extends", ""), "implements": td.get("implements", "")})
                chunks.append({
                    "id": _chunk_id(f"{repo_name}:{fqn}", "mod"),
                    "text": f"// {repo_name}\n{overview}",
                    "metadata": {
                        **base_meta, "type": "mod_class",
                        "class_name": class_name, "fqn": fqn,
                        "package": package or "", "file": str(rel_path),
                    },
                })

                for method in td.get("methods", []):
                    method_text = f"// {repo_name} — {fqn}\n{method['source']}"
                    if len(method_text) > 50:
                        chunks.append({
                            "id": _chunk_id(f"{repo_name}:{fqn}.{method['name']}:{method['source']}", "modmethod"),
                            "text": method_text,
                            "metadata": {
                                **base_meta, "type": "mod_method",
                                "class_name": class_name, "fqn": fqn,
                                "method_name": method["name"],
                                "package": package or "", "file": str(rel_path),
                            },
                        })

                for ctor in td.get("constructors", []):
                    ctor_text = f"// {repo_name} — {fqn}\n{ctor['source']}"
                    if len(ctor_text) > 50:
                        chunks.append({
                            "id": _chunk_id(f"{repo_name}:{fqn}.<init>:{ctor['source']}", "modmethod"),
                            "text": ctor_text,
                            "metadata": {
                                **base_meta, "type": "mod_method",
                                "class_name": class_name, "fqn": fqn,
                                "method_name": "<init>",
                                "package": package or "", "file": str(rel_path),
                            },
                        })
        else:
            inheritance = _extract_inheritance(source)
            overview = _build_class_overview(source, fqn, package, inheritance)
            chunks.append({
                "id": _chunk_id(f"{repo_name}:{fqn}", "mod"),
                "text": f"// {repo_name}\n{overview}",
                "metadata": {
                    **base_meta, "type": "mod_class",
                    "class_name": class_name, "fqn": fqn,
                    "package": package or "", "file": str(rel_path),
                },
            })

            methods = _extract_methods(source)
            for method_name, method_source, _visibility in methods:
                method_text = f"// {repo_name} — {fqn}\n{method_source}"
                if len(method_text) > 50:
                    chunks.append({
                        "id": _chunk_id(f"{repo_name}:{fqn}.{method_name}:{method_source}", "modmethod"),
                        "text": method_text,
                        "metadata": {
                            **base_meta, "type": "mod_method",
                            "class_name": class_name, "fqn": fqn,
                            "method_name": method_name,
                            "package": package or "", "file": str(rel_path),
                        },
                    })

    return chunks


def scrape_github_mods(
    queries: list[str] | None = None,
    min_stars: int = 2,
    max_repos: int = 30,
) -> tuple[list[dict], list[str]]:
    repos = search_repos(queries, min_stars, max_repos)
    if not repos:
        return [], []

    all_chunks = []
    indexed_repos = []

    for repo in repos:
        name = repo["fullName"]
        repo_dir = clone_repo(name)
        if repo_dir is None:
            continue

        chunks = parse_mod_files(repo_dir, name, updated_at=repo.get("updatedAt", ""))
        if chunks:
            all_chunks.extend(chunks)
            indexed_repos.append(f"{name} ({repo['stargazersCount']} stars, {len(chunks)} chunks)")

        shutil.rmtree(repo_dir, ignore_errors=True)

    return all_chunks, indexed_repos
