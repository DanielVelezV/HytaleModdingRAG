"""
Deobfuscation tool: recovers real parameter and local variable names from
Hytale shared source and applies them to CFR-decompiled files.

CFR strips parameter names to var1, var2, ... and local variables to
type-based names like n, bl, string, object, etc. This script
cross-references the real source to restore them.

Usage:
    python deobfuscator.py <source_root> [--dry-run] [--params-only] [--locals-only]

source_root should point to the HytaleServer directory inside the shared source.
"""

import re
import sys
import json
from pathlib import Path
from config import DECOMPILED_DIR

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

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

_CFR_LOCAL_PATTERNS = re.compile(
    r"^(?:"
    r"n\d*|bl\d*|string\d*|object\d*|f\d*|d\d*|l\d*|by\d*|c\d*|s\d*|"
    r"arr\w+\d*|"
    r"[a-z]{1,2}\d+"
    r")$"
)

LOCAL_DECL_RE = re.compile(
    r"(?:final\s+)?(?:var\s+)?"
    r"((?:[\w.]+(?:<[^>]*>)?(?:\[\])*)\s+)"
    r"(\w+)"
    r"\s*(=\s*[^;]{0,120}|;)"
)

KEYWORDS = frozenset({
    "if", "while", "for", "switch", "catch", "return", "new", "throw",
    "class", "interface", "enum", "record", "package", "import",
    "extends", "implements", "throws", "try", "else", "case", "break",
    "continue", "do", "instanceof", "super", "this", "void", "null",
    "true", "false", "default", "finally", "assert", "yield",
})

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _parse_params(param_str: str) -> list[tuple[str, str]]:
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
    t = re.sub(r"@\w+(?:\([^)]*\))?\s*", "", t).strip()
    t = re.sub(r"\b(?:final|var)\b\s*", "", t).strip()
    t = re.sub(r"<.*>", "", t)
    if "." in t:
        t = t.rsplit(".", 1)[-1]
    return t.strip()


def _extract_methods(source: str) -> dict[str, list[tuple[list[str], list[str]]]]:
    methods: dict[str, list[tuple[list[str], list[str]]]] = {}
    for m in METHOD_SIG_RE.finditer(source):
        name = m.group(1)
        if name in KEYWORDS:
            continue
        params = _parse_params(m.group(2))
        types = [_simple_type(t) for t, _ in params]
        names = [n for _, n in params]
        methods.setdefault(name, []).append((types, names))
    return methods


def _build_source_index(source_root: Path) -> dict[str, Path]:
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
    try:
        rel = decompiled_file.relative_to(decompiled_root / "HytaleServer")
        return str(rel).replace("\\", "/")
    except ValueError:
        return None


def _find_method_body(text: str, method_name: str, start_from: int = 0) -> tuple[int, int, int] | None:
    """Find a method's body using simple string search + brace matching."""
    search_pat = method_name + "("
    pos = text.find(search_pat, start_from)
    while pos != -1:
        line_start = text.rfind("\n", 0, pos)
        prefix = text[line_start + 1:pos].strip() if line_start != -1 else text[:pos].strip()
        if prefix and not any(prefix.endswith(kw) for kw in ("new", "return", "=", ".", "throw")):
            paren_end = text.find(")", pos)
            if paren_end == -1:
                pos = text.find(search_pat, pos + 1)
                continue
            brace = text.find("{", paren_end)
            if brace != -1 and brace - paren_end < 80:
                depth = 0
                end = brace
                for i in range(brace, len(text)):
                    if text[i] == "{":
                        depth += 1
                    elif text[i] == "}":
                        depth -= 1
                        if depth == 0:
                            end = i + 1
                            break
                sig_start = line_start + 1 if line_start != -1 else 0
                return (sig_start, brace, end)
        pos = text.find(search_pat, pos + 1)
    return None


# ---------------------------------------------------------------------------
# Parameter renaming (pass 1)
# ---------------------------------------------------------------------------

def _rename_param_in_method(text: str, method_name: str, old_name: str, new_name: str) -> str:
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


def _run_param_pass(decompiled_text: str, source_text: str, rel: str, mapping_log: list) -> tuple[str, int]:
    """Rename var1/var2/... parameter names. Returns (new_text, rename_count)."""
    if "var1" not in decompiled_text and "var2" not in decompiled_text:
        return decompiled_text, 0

    src_methods = _extract_methods(source_text)
    dec_methods = _extract_methods(decompiled_text)

    renames = 0
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

            for i, (dec_name, src_name) in enumerate(zip(dec_names, match)):
                if dec_name == src_name:
                    continue
                if not re.match(r"var\d+$", dec_name):
                    continue

                new_text = _rename_param_in_method(new_text, method_name, dec_name, src_name)
                dec_names[i] = src_name
                renames += 1
                mapping_log.append({
                    "file": rel, "method": method_name,
                    "kind": "param", "old": dec_name, "new": src_name,
                })

    return new_text, renames


# ---------------------------------------------------------------------------
# Local variable renaming (pass 2)
# ---------------------------------------------------------------------------

def _is_cfr_local(name: str) -> bool:
    return bool(_CFR_LOCAL_PATTERNS.match(name))


def _normalize_init(init_str: str) -> str:
    """Normalize an initializer for fuzzy matching."""
    s = init_str.strip().rstrip(";").strip()
    if s.startswith("= "):
        s = s[2:]
    elif s.startswith("="):
        s = s[1:]
    return re.sub(r"\s+", "", s.strip())[:80]


def _extract_local_decls(body: str) -> list[tuple[str, str, str, int]]:
    """Extract local variable declarations from a method body.
    Returns [(simple_type, var_name, init_fingerprint, char_offset), ...].
    """
    decls = []
    for m in LOCAL_DECL_RE.finditer(body):
        raw_type = m.group(1).strip()
        name = m.group(2)
        init = m.group(3) if m.group(3) else ""
        if name in KEYWORDS:
            continue
        decls.append((_simple_type(raw_type), name, _normalize_init(init), m.start()))
    return decls


def _extract_source_local_decls(body: str) -> list[tuple[str, str, str, int]]:
    """Extract ALL local variable declarations from a source method body."""
    decls = []
    for m in LOCAL_DECL_RE.finditer(body):
        raw_type = m.group(1).strip()
        name = m.group(2)
        init = m.group(3) if m.group(3) else ""
        if name in KEYWORDS:
            continue
        decls.append((_simple_type(raw_type), name, _normalize_init(init), m.start()))
    return decls


def _match_locals(dec_decls: list[tuple[str, str, str, int]],
                  src_decls: list[tuple[str, str, str, int]]) -> list[tuple[str, str]]:
    """Match decompiled locals to source locals by initializer expression.
    Only renames when initializers match — high confidence, no false positives.
    """
    renames = []
    matched_src = set()

    for i, (dtype, dname, dinit, _) in enumerate(dec_decls):
        if not dinit or dinit == ";":
            continue
        for j, (stype, sname, sinit, _) in enumerate(src_decls):
            if j in matched_src:
                continue
            if not sinit or sinit == ";":
                continue
            if dname == sname:
                matched_src.add(j)
                break
            type_ok = (not dtype or not stype or dtype == stype)
            if type_ok and dinit == sinit and sname not in KEYWORDS:
                renames.append((dname, sname))
                matched_src.add(j)
                break

    return renames


def _rename_local_in_scope(body: str, old_name: str, new_name: str) -> str:
    """Replace a local variable name within a method body, using word boundaries."""
    return re.sub(r"\b" + re.escape(old_name) + r"\b", new_name, body)


_HAS_INITIALIZER = re.compile(r"\w+\s+\w+\s*=\s*\S")


def _run_locals_pass(text: str, source_text: str, rel: str, mapping_log: list) -> tuple[str, int]:
    """Rename CFR-style local variables. Returns (new_text, rename_count)."""
    if not _HAS_INITIALIZER.search(text):
        return text, 0

    dec_methods_list = _extract_methods(text)
    src_methods_list = _extract_methods(source_text)

    total_renames = 0
    result = text

    for method_name, dec_overloads in dec_methods_list.items():
        src_overloads = src_methods_list.get(method_name, [])
        if not src_overloads:
            continue

        for dec_types, dec_names in dec_overloads:
            src_match = None
            for src_types, src_names in src_overloads:
                if len(src_types) == len(dec_types):
                    if all(_simple_type(st) == _simple_type(dt) for st, dt in zip(src_types, dec_types)):
                        src_match = (src_types, src_names)
                        break
            if not src_match:
                for src_types, src_names in src_overloads:
                    if len(src_names) == len(dec_names):
                        src_match = (src_types, src_names)
                        break
            if not src_match:
                continue

            dec_body_info = _find_method_body(result, method_name)
            src_body_info = _find_method_body(source_text, method_name)

            if not dec_body_info or not src_body_info:
                continue

            dec_body = result[dec_body_info[1]:dec_body_info[2]]
            src_body = source_text[src_body_info[1]:src_body_info[2]]

            dec_locals = _extract_local_decls(dec_body)
            src_locals = _extract_source_local_decls(src_body)

            if not dec_locals:
                continue

            matches = _match_locals(dec_locals, src_locals)

            if not matches:
                continue

            used_new_names = set()
            all_existing = set()
            for m2 in LOCAL_DECL_RE.finditer(dec_body):
                all_existing.add(m2.group(2))
            for _, pn in zip(dec_types, dec_names):
                all_existing.add(pn)

            safe_matches = []
            for old, new in matches:
                if new in all_existing and new != old:
                    continue
                if new in used_new_names:
                    continue
                used_new_names.add(new)
                safe_matches.append((old, new))

            if not safe_matches:
                continue

            new_body = dec_body
            for old, new in safe_matches:
                new_body = _rename_local_in_scope(new_body, old, new)
                total_renames += 1
                mapping_log.append({
                    "file": rel, "method": method_name,
                    "kind": "local", "old": old, "new": new,
                })

            if new_body != dec_body:
                result = result[:dec_body_info[1]] + new_body + result[dec_body_info[2]:]

    return result, total_renames


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def deobfuscate(source_root: Path, dry_run: bool = False,
                do_params: bool = True, do_locals: bool = True) -> dict:
    """
    Cross-references source and decompiled files, replaces obfuscated names.
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
        "locals_renamed": 0,
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

        new_text = decompiled_text
        file_changes = 0

        if do_params:
            new_text, count = _run_param_pass(new_text, source_text, rel, mapping_log)
            stats["params_renamed"] += count
            file_changes += count

        if do_locals:
            new_text, count = _run_locals_pass(new_text, source_text, rel, mapping_log)
            stats["locals_renamed"] += count
            file_changes += count

        if file_changes > 0 and new_text != decompiled_text:
            stats["files_modified"] += 1
            if not dry_run:
                df.write_text(new_text, encoding="utf-8")

    mapping_path = decompiled_root / "deobfuscation_mapping.json"
    if not dry_run and mapping_log:
        mapping_path.write_text(json.dumps(mapping_log, indent=2), encoding="utf-8")

    return stats


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python deobfuscator.py <source_root> [--dry-run] [--params-only] [--locals-only]")
        print("  source_root: path to HytaleServer dir in shared source")
        sys.exit(1)

    source_root = Path(sys.argv[1])
    dry_run = "--dry-run" in sys.argv
    params_only = "--params-only" in sys.argv
    locals_only = "--locals-only" in sys.argv

    do_params = not locals_only
    do_locals = not params_only

    if not source_root.exists():
        print(f"Error: {source_root} does not exist")
        sys.exit(1)

    label = "DRY RUN — " if dry_run else ""
    mode = "params only" if params_only else ("locals only" if locals_only else "params + locals")
    print(f"{label}Deobfuscating ({mode})...")

    result = deobfuscate(source_root, dry_run=dry_run, do_params=do_params, do_locals=do_locals)

    if "error" in result:
        print(f"Error: {result['error']}")
        sys.exit(1)

    print(f"\nResults:")
    print(f"  Files matched:   {result['files_matched']}")
    print(f"  Files modified:  {result['files_modified']}")
    print(f"  Params renamed:  {result['params_renamed']}")
    print(f"  Locals renamed:  {result['locals_renamed']}")
    print(f"  Files skipped:   {result['files_skipped']}")
    print(f"  No source match: {result['files_no_match']}")
