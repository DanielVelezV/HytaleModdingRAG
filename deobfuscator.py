"""
Deobfuscation tool: recovers real parameter names from Hytale shared source
and applies them to CFR-decompiled files.

CFR strips parameter names to var1, var2, ... because the jar lacks debug info.
This script cross-references the real source to restore them, making the
decompiled code (and therefore every RAG chunk) far more readable.

Usage:
    python deobfuscator.py <source_root> [--dry-run]

source_root should point to the HytaleServer directory inside the shared source,
e.g. C:/Users/User/Desktop/hytale-shared-source/.../HytaleServer
"""

import re
import sys
import json
from pathlib import Path
from config import DECOMPILED_DIR

METHOD_SIG_RE = re.compile(
    r"(?:(?:public|protected|private|static|final|abstract|synchronized|native|default|strictfp)\s+)*"
    r"(?:<[^>]+>\s+)?"
    r"(?:[\w.<>\[\]?, ]+\s+)?"
    r"(\w+)"
    r"\s*\(([^)]*)\)",
)

PARAM_RE = re.compile(
    r"(?:@\w+(?:\([^)]*\))?\s+)*"
    r"([\w.<>\[\]?, ]+?)"
    r"\s+(\w+)"
    r"\s*$"
)


def _parse_params(param_str: str) -> list[tuple[str, str]]:
    """Parse a parameter list string into [(type, name), ...]."""
    if not param_str.strip():
        return []
    params = []
    depth = 0
    current = []
    for ch in param_str:
        if ch in "<(":
            depth += 1
            current.append(ch)
        elif ch in ">)":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            params.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        params.append("".join(current).strip())

    result = []
    for p in params:
        p = p.strip()
        if not p:
            continue
        m = PARAM_RE.match(p)
        if m:
            ptype = re.sub(r"@\w+(?:\([^)]*\))?\s*", "", m.group(1)).strip()
            ptype = ptype.split(".")[-1] if "." in ptype else ptype
            result.append((ptype, m.group(2)))
        else:
            parts = p.rsplit(None, 1)
            if len(parts) == 2:
                ptype = parts[0].split(".")[-1] if "." in parts[0] else parts[0]
                result.append((ptype, parts[1]))
    return result


def _simple_type(t: str) -> str:
    """Normalize a type for matching: strip annotations, generics, keep simple name."""
    t = re.sub(r"@\w+(?:\([^)]*\))?\s*", "", t).strip()
    t = re.sub(r"<.*>", "", t)
    if "." in t:
        t = t.rsplit(".", 1)[-1]
    return t.strip()


def _extract_methods(source: str) -> dict[str, list[tuple[list[str], list[str]]]]:
    """
    Extract method signatures from Java source.
    Returns {method_name: [([simple_types], [param_names]), ...]}.
    """
    methods: dict[str, list[tuple[list[str], list[str]]]] = {}

    for m in METHOD_SIG_RE.finditer(source):
        name = m.group(1)
        if name in ("if", "while", "for", "switch", "catch", "return", "new", "throw", "class", "interface", "enum", "record", "package", "import"):
            continue

        params = _parse_params(m.group(2))
        types = [_simple_type(t) for t, _ in params]
        names = [n for _, n in params]

        methods.setdefault(name, []).append((types, names))

    return methods


def _build_source_index(source_root: Path) -> dict[str, Path]:
    """Map relative path (from com/...) → source file path."""
    index: dict[str, Path] = {}
    for java_file in source_root.rglob("*.java"):
        parts = java_file.parts
        try:
            com_idx = next(i for i, p in enumerate(parts) if p == "com")
        except StopIteration:
            continue
        rel = "/".join(parts[com_idx:])
        index[rel] = java_file
    return index


def _decompiled_rel(decompiled_file: Path, decompiled_root: Path) -> str | None:
    """Get the com/... relative path for a decompiled file."""
    try:
        rel = decompiled_file.relative_to(decompiled_root / "HytaleServer")
        return str(rel).replace("\\", "/")
    except ValueError:
        return None


def deobfuscate(source_root: Path, dry_run: bool = False) -> dict:
    """
    Main entry point. Cross-references source and decompiled files,
    replaces varN parameter names with real names.

    Returns stats dict.
    """
    decompiled_root = DECOMPILED_DIR
    if not decompiled_root.exists():
        return {"error": "No decompiled directory found"}

    print(f"Building source index from {source_root}...")
    source_index = _build_source_index(source_root)
    print(f"  Found {len(source_index)} source files")

    decompiled_files = list((decompiled_root / "HytaleServer").rglob("*.java"))
    print(f"  Found {len(decompiled_files)} decompiled files")

    stats = {
        "files_matched": 0,
        "files_modified": 0,
        "params_renamed": 0,
        "files_skipped": 0,
        "files_no_match": 0,
    }

    mapping_log: list[dict] = []

    for df in decompiled_files:
        rel = _decompiled_rel(df, decompiled_root)
        if not rel:
            stats["files_skipped"] += 1
            continue

        src_path = source_index.get(rel)
        if not src_path:
            stats["files_no_match"] += 1
            continue

        stats["files_matched"] += 1

        try:
            decompiled_text = df.read_text(encoding="utf-8", errors="replace")
            source_text = src_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            stats["files_skipped"] += 1
            continue

        if "var1" not in decompiled_text and "var2" not in decompiled_text:
            continue

        src_methods = _extract_methods(source_text)
        dec_methods = _extract_methods(decompiled_text)

        file_renames = 0
        new_text = decompiled_text

        for method_name, dec_overloads in dec_methods.items():
            src_overloads = src_methods.get(method_name, [])
            if not src_overloads:
                continue

            for dec_types, dec_names in dec_overloads:
                has_var = any(re.match(r"var\d+$", n) for n in dec_names)
                if not has_var:
                    continue

                match = None
                for src_types, src_names in src_overloads:
                    if len(src_types) == len(dec_types):
                        if all(_simple_type(st) == _simple_type(dt) for st, dt in zip(src_types, dec_types)):
                            match = src_names
                            break

                if not match:
                    for src_types, src_names in src_overloads:
                        if len(src_names) == len(dec_names):
                            match = src_names
                            break

                if not match or len(match) != len(dec_names):
                    continue

                dec_param_str = ", ".join(
                    f"{dt} {dn}" for (dt, dn) in zip(
                        [_rebuild_type(decompiled_text, method_name, i, dec_names[i]) for i in range(len(dec_names))],
                        dec_names,
                    )
                )

                for i, (dec_name, src_name) in enumerate(zip(dec_names, match)):
                    if dec_name == src_name:
                        continue
                    if not re.match(r"var\d+$", dec_name):
                        continue

                    old_sig_pattern = _build_param_pattern(method_name, dec_types, dec_names)
                    new_names = list(dec_names)
                    new_names[i] = src_name

                    new_text = _rename_param_in_method(new_text, method_name, dec_names[i], src_name, dec_types, dec_names)
                    dec_names[i] = src_name
                    file_renames += 1
                    stats["params_renamed"] += 1

                    mapping_log.append({
                        "file": rel,
                        "method": method_name,
                        "old": dec_name,
                        "new": src_name,
                    })

        if file_renames > 0 and new_text != decompiled_text:
            stats["files_modified"] += 1
            if not dry_run:
                df.write_text(new_text, encoding="utf-8")

    mapping_path = decompiled_root / "param_mapping.json"
    if not dry_run and mapping_log:
        mapping_path.write_text(json.dumps(mapping_log, indent=2), encoding="utf-8")

    return stats


def _rebuild_type(text: str, method_name: str, param_idx: int, param_name: str) -> str:
    """Recover the full type string for a parameter from the decompiled text."""
    pattern = re.compile(
        re.escape(method_name) + r"\s*\(([^)]*)\)",
    )
    for m in pattern.finditer(text):
        params = _parse_params(m.group(1))
        for i, (ptype, pname) in enumerate(params):
            if pname == param_name and i == param_idx:
                return ptype
    return "?"


def _rename_param_in_method(text: str, method_name: str, old_name: str, new_name: str,
                             param_types: list[str], param_names: list[str]) -> str:
    """
    Rename a parameter within a specific method's scope.
    Finds the method declaration, then replaces old_name → new_name
    within its body (brace-delimited scope).
    """
    sig_pattern = re.compile(
        re.escape(method_name) + r"\s*\([^)]*\b" + re.escape(old_name) + r"\b[^)]*\)"
    )

    result = text
    for m in sig_pattern.finditer(text):
        sig_start = m.start()
        sig_end = m.end()

        new_sig = re.sub(r"\b" + re.escape(old_name) + r"\b", new_name, m.group(0))

        brace_pos = text.find("{", sig_end)
        if brace_pos == -1 or brace_pos - sig_end > 50:
            result = result[:sig_start] + new_sig + result[sig_end:]
            break

        depth = 0
        body_end = brace_pos
        for i in range(brace_pos, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    body_end = i + 1
                    break

        body = text[sig_end:body_end]
        new_body = re.sub(r"\b" + re.escape(old_name) + r"\b", new_name, body)

        result = result[:sig_start] + new_sig + new_body + result[body_end:]
        break

    return result


def _build_param_pattern(method_name: str, types: list[str], names: list[str]) -> str:
    """Build a regex pattern to match the method signature."""
    params = r",\s*".join(
        r"[\w.<>\[\]?, @]+\s+" + re.escape(n) for n in names
    )
    return re.escape(method_name) + r"\s*\(\s*" + params + r"\s*\)"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python deobfuscator.py <source_root> [--dry-run]")
        print("  source_root: path to HytaleServer dir in shared source")
        sys.exit(1)

    source_root = Path(sys.argv[1])
    dry_run = "--dry-run" in sys.argv

    if not source_root.exists():
        print(f"Error: {source_root} does not exist")
        sys.exit(1)

    print(f"{'DRY RUN — ' if dry_run else ''}Deobfuscating parameter names...")
    result = deobfuscate(source_root, dry_run=dry_run)

    if "error" in result:
        print(f"Error: {result['error']}")
        sys.exit(1)

    print(f"\nResults:")
    print(f"  Files matched:  {result['files_matched']}")
    print(f"  Files modified: {result['files_modified']}")
    print(f"  Params renamed: {result['params_renamed']}")
    print(f"  Files skipped:  {result['files_skipped']}")
    print(f"  No source match:{result['files_no_match']}")
