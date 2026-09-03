"""
Tree-sitter based Java parser for the Hytale modding RAG indexer.

Provides two APIs:

1. ``parse_file(source)`` -- low-level, returns a structured dict describing
   all types / methods / fields in a single Java source string.

2. ``parse_java_files(source_dir)`` -- high-level, walks a directory of
   ``.java`` files and returns RAG chunks in the *exact* same format as
   ``indexer.parse_java_files()`` (list of ``{"id", "text", "metadata"}``
   dicts).  This is the drop-in replacement for the regex parser.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import tree_sitter as ts
import tree_sitter_java as tsjava

from config import MAX_CHUNK_SIZE

# ---------------------------------------------------------------------------
# Language / parser singleton
# ---------------------------------------------------------------------------

_JAVA_LANG = ts.Language(tsjava.language())
_parser = ts.Parser(_JAVA_LANG)

_TYPE_NODES = frozenset({
    "class_declaration", "interface_declaration",
    "enum_declaration", "record_declaration",
})

# ---------------------------------------------------------------------------
# Shared low-level helpers
# ---------------------------------------------------------------------------

def _text(node) -> str:
    return node.text.decode("utf-8") if node else ""


def _get_modifiers(node) -> set[str]:
    mods: set[str] = set()
    for child in node.children:
        if child.type == "modifiers":
            for m in child.children:
                if m.type in (
                    "public", "private", "protected", "static", "final",
                    "abstract", "synchronized", "native", "default",
                    "sealed", "strictfp", "non-sealed",
                ):
                    mods.add(m.type)
    return mods


def _get_modifier_list(node) -> list[str]:
    """Return modifiers as a list (preserving order) -- used by chunk builder."""
    mods: list[str] = []
    for child in node.children:
        if child.type == "modifiers":
            for m in child.children:
                if m.type in ("marker_annotation", "annotation"):
                    continue
                txt = _text(m)
                if txt and not txt.startswith("@"):
                    mods.append(txt)
    return mods


def _get_annotations(node) -> list[str]:
    annots: list[str] = []
    for child in node.children:
        if child.type == "modifiers":
            for m in child.children:
                if m.type in ("marker_annotation", "annotation"):
                    annots.append(_text(m))
    return annots


def _visibility(mods) -> str:
    if "public" in mods:
        return "public"
    if "protected" in mods:
        return "protected"
    if "private" in mods:
        return "private"
    return "package-private"


def _get_name(node) -> str:
    for child in node.children:
        if child.type == "identifier":
            return _text(child)
    return ""


def _split_implements(raw: str) -> list[str]:
    if not raw:
        return []
    depth = 0
    current: list[str] = []
    result: list[str] = []
    for ch in raw + ",":
        if ch == "<":
            depth += 1
            current.append(ch)
        elif ch == ">":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            token = "".join(current).strip()
            if token:
                result.append(token.split("<")[0].strip())
            current = []
        else:
            current.append(ch)
    return result


def _type_keyword(node) -> str:
    for child in node.children:
        if child.type in ("class", "interface", "enum", "record"):
            return child.type
    return "class"


def _find_body(node):
    for child in node.children:
        if child.type in ("class_body", "enum_body", "interface_body",
                          "record_declaration_body"):
            return child
    body = node.child_by_field_name("body")
    return body


def _get_superclass(node) -> str:
    for child in node.children:
        if child.type == "superclass":
            for c in child.children:
                if c.type in ("type_identifier", "generic_type",
                              "scoped_type_identifier"):
                    raw = _text(c)
                    return raw.split("<")[0].strip()
    return ""


def _get_interfaces_text(node) -> str:
    for child in node.children:
        if child.type == "super_interfaces":
            for c in child.children:
                if c.type == "type_list":
                    return _text(c)
    return ""


def _get_extends_interfaces_text(node) -> str:
    """Interfaces use extends_interfaces rather than superclass."""
    for child in node.children:
        if child.type == "extends_interfaces":
            for c in child.children:
                if c.type == "type_list":
                    return _text(c)
    return ""


# ===================================================================
# 1. Low-level ``parse_file`` API  (structured dict per file)
# ===================================================================

def parse_file(source: str) -> dict | None:
    """Parse a single Java source string and return a structured dict.

    Returns ``{"package": str, "imports": [str], "types": [dict]}``
    or ``None`` if the file could not be parsed at all.
    """
    tree = _parser.parse(source.encode("utf-8"))
    root = tree.root_node
    if root.has_error:
        return None

    package = ""
    imports: list[str] = []
    types: list[dict] = []

    for child in root.children:
        if child.type == "package_declaration":
            for sub in child.children:
                if sub.type in ("scoped_identifier", "identifier"):
                    package = _text(sub)
                    break
        elif child.type == "import_declaration":
            raw = _text(child).rstrip(";").strip()
            if raw.startswith("import "):
                raw = raw[7:].strip()
            imports.append(raw)
        elif child.type in _TYPE_NODES:
            types.append(_extract_type(child, source))

    return {"package": package, "imports": imports, "types": types}


def _extract_type(node, source: str) -> dict:
    mods = _get_modifiers(node)
    kind = node.type.replace("_declaration", "")
    name = _get_name(node)

    # extends / implements
    extends_raw = ""
    superclass_node = node.child_by_field_name("superclass")
    if superclass_node:
        extends_raw = _text(superclass_node)
        if extends_raw.startswith("extends "):
            extends_raw = extends_raw[8:].strip()

    implements_raw = ""
    interfaces_node = node.child_by_field_name("interfaces")
    if interfaces_node:
        implements_raw = _text(interfaces_node)
        if implements_raw.startswith("implements "):
            implements_raw = implements_raw[11:].strip()

    # For interfaces, "extends" goes into the extends field
    if kind == "interface" and not extends_raw:
        ext_iface = _get_extends_interfaces_text(node)
        if ext_iface:
            extends_raw = ext_iface

    extends_simple = extends_raw.split("<")[0].strip() if extends_raw else ""
    implements_list = _split_implements(implements_raw)

    decl_text = _text(node)
    brace = decl_text.find("{")
    class_decl_line = decl_text[:brace].strip() if brace > 0 else ""

    fields: list[dict] = []
    methods: list[dict] = []
    constructors: list[dict] = []
    nested_types: list[dict] = []

    body = _find_body(node)
    if body:
        _extract_members(body, source, fields, methods, constructors, nested_types)

    return {
        "name": name,
        "kind": kind,
        "modifiers": mods,
        "visibility": _visibility(mods),
        "declaration": class_decl_line,
        "extends": extends_simple,
        "extends_raw": extends_raw,
        "implements": ", ".join(implements_list),
        "implements_raw": implements_raw,
        "fields": fields,
        "methods": methods,
        "constructors": constructors,
        "nested_types": nested_types,
    }


def _extract_members(body, source, fields, methods, constructors, nested_types):
    for member in body.children:
        if member.type == "field_declaration":
            fmods = _get_modifiers(member)
            ftype_node = member.child_by_field_name("type")
            ftype = _text(ftype_node) if ftype_node else ""
            for decl in member.children:
                if decl.type == "variable_declarator":
                    fname_node = decl.child_by_field_name("name")
                    fname = _text(fname_node) if fname_node else ""
                    if fname:
                        fields.append({
                            "name": fname,
                            "type": ftype,
                            "modifiers": fmods,
                            "visibility": _visibility(fmods),
                            "source": _text(member).rstrip(";").strip() + ";",
                        })

        elif member.type == "method_declaration":
            mmods = _get_modifiers(member)
            mname = _get_name(member)
            mret_node = member.child_by_field_name("type")
            mret = _text(mret_node) if mret_node else "void"
            annots = _get_annotations(member)
            src = _text(member)
            if len(src) > MAX_CHUNK_SIZE:
                src = src[:MAX_CHUNK_SIZE] + "\n// ... truncated"
            params_node = member.child_by_field_name("parameters")
            params_text = _text(params_node) if params_node else "()"
            sig = _build_method_signature(mmods, mret, mname, params_text)
            methods.append({
                "name": mname,
                "return_type": mret,
                "params": params_text,
                "signature": sig,
                "modifiers": mmods,
                "visibility": _visibility(mmods),
                "annotations": annots,
                "source": src,
            })

        elif member.type == "constructor_declaration":
            cmods = _get_modifiers(member)
            annots = _get_annotations(member)
            src = _text(member)
            if len(src) > MAX_CHUNK_SIZE:
                src = src[:MAX_CHUNK_SIZE] + "\n// ... truncated"
            constructors.append({
                "modifiers": cmods,
                "visibility": _visibility(cmods),
                "annotations": annots,
                "source": src,
            })

        elif member.type in _TYPE_NODES:
            nested_types.append(_extract_type(member, source))

        elif member.type == "enum_body_declarations":
            _extract_members(member, source, fields, methods, constructors,
                             nested_types)


def _build_method_signature(mods: set[str], ret: str, name: str, params: str) -> str:
    vis_order: list[str] = []
    for m in ("public", "protected", "private"):
        if m in mods:
            vis_order.append(m)
    for m in ("static", "final", "abstract", "synchronized", "native", "default"):
        if m in mods:
            vis_order.append(m)
    prefix = " ".join(vis_order)
    return f"{prefix} {ret} {name}{params}".strip()


# ===================================================================
# 2. High-level ``parse_java_files`` API  (RAG chunk list)
# ===================================================================

def _chunk_id(text: str, prefix: str) -> str:
    h = hashlib.sha256(text.encode()).hexdigest()[:24]
    return f"{prefix}_{h}"


def _extract_package_from_root(root, source_bytes: bytes) -> str:
    for child in root.children:
        if child.type == "package_declaration":
            for c in child.children:
                if c.type in ("scoped_identifier", "identifier"):
                    return source_bytes[c.start_byte:c.end_byte].decode(
                        "utf-8", errors="replace")
    return ""


def _extract_imports_from_root(root, source_bytes: bytes) -> list[str]:
    imports: list[str] = []
    for child in root.children:
        if child.type == "import_declaration":
            imports.append(
                source_bytes[child.start_byte:child.end_byte]
                .decode("utf-8", errors="replace").strip()
            )
    return imports


# --- chunk-level helpers ---

def _collect_fields_for_overview(body, source_bytes: bytes) -> list[str]:
    """Non-private field one-liners for the class overview."""
    fields: list[str] = []

    def _scan(container):
        for child in container.children:
            if child.type == "field_declaration":
                mods = _get_modifier_list(child)
                vis = _visibility(mods)
                if vis == "private":
                    continue
                raw = source_bytes[child.start_byte:child.end_byte].decode(
                    "utf-8", errors="replace").strip()
                eq_pos = raw.find("=")
                if eq_pos > 0:
                    raw = raw[:eq_pos].strip() + ";"
                if not raw.endswith(";"):
                    raw += ";"
                fields.append(f"  {raw}")
            elif child.type == "enum_body_declarations":
                _scan(child)

    _scan(body)
    return fields


def _collect_method_sigs_for_overview(body, source_bytes: bytes) -> list[str]:
    """Non-private method signature one-liners for the class overview."""
    sigs: list[str] = []

    def _scan(container):
        for child in container.children:
            if child.type == "method_declaration":
                mods = _get_modifier_list(child)
                vis = _visibility(mods)
                if vis == "private":
                    continue
                sig = _build_sig_from_bytes(child, source_bytes)
                if sig and len(sig) < 300:
                    sigs.append(f"  {sig};")
            elif child.type == "enum_body_declarations":
                _scan(child)

    _scan(body)
    return sigs


def _build_sig_from_bytes(method_node, source_bytes: bytes) -> str:
    """Build a clean signature (no body, no annotations) from a method node."""
    parts: list[str] = []
    for child in method_node.children:
        if child.type in ("block", "constructor_body"):
            break
        if child.type == "modifiers":
            mod_parts: list[str] = []
            for m in child.children:
                if m.type not in ("marker_annotation", "annotation"):
                    mod_parts.append(
                        source_bytes[m.start_byte:m.end_byte]
                        .decode("utf-8", errors="replace"))
            if mod_parts:
                parts.append(" ".join(mod_parts))
        elif child.type == ";":
            break
        else:
            parts.append(
                source_bytes[child.start_byte:child.end_byte]
                .decode("utf-8", errors="replace"))
    return " ".join(parts)


def _build_method_source_bytes(method_node, source_bytes: bytes) -> str:
    """Annotations + modifiers + signature + body."""
    parts: list[str] = []
    annotations = _get_annotations(method_node)
    if annotations:
        parts.extend(annotations)

    sig_parts: list[str] = []
    body_text = ""
    for child in method_node.children:
        if child.type == "block":
            body_text = source_bytes[child.start_byte:child.end_byte].decode(
                "utf-8", errors="replace")
        elif child.type == "modifiers":
            mod_parts = []
            for m in child.children:
                if m.type not in ("marker_annotation", "annotation"):
                    mod_parts.append(
                        source_bytes[m.start_byte:m.end_byte]
                        .decode("utf-8", errors="replace"))
            if mod_parts:
                sig_parts.append(" ".join(mod_parts))
        elif child.type == ";":
            pass  # abstract / interface method
        else:
            sig_parts.append(
                source_bytes[child.start_byte:child.end_byte]
                .decode("utf-8", errors="replace"))

    sig = " ".join(sig_parts)
    parts.append(sig)

    full = "\n".join(parts)
    if body_text:
        full += " " + body_text

    if len(full) > MAX_CHUNK_SIZE:
        full = full[:MAX_CHUNK_SIZE] + "\n// ... truncated"
    return full


def _build_ctor_source_bytes(ctor_node, source_bytes: bytes) -> str:
    """Annotations + modifiers + constructor signature + body."""
    parts: list[str] = []
    annotations = _get_annotations(ctor_node)
    if annotations:
        parts.extend(annotations)

    sig_parts: list[str] = []
    body_text = ""
    for child in ctor_node.children:
        if child.type == "constructor_body":
            body_text = source_bytes[child.start_byte:child.end_byte].decode(
                "utf-8", errors="replace")
        elif child.type == "modifiers":
            mod_parts = []
            for m in child.children:
                if m.type not in ("marker_annotation", "annotation"):
                    mod_parts.append(
                        source_bytes[m.start_byte:m.end_byte]
                        .decode("utf-8", errors="replace"))
            if mod_parts:
                sig_parts.append(" ".join(mod_parts))
        else:
            sig_parts.append(
                source_bytes[child.start_byte:child.end_byte]
                .decode("utf-8", errors="replace"))

    sig = " ".join(sig_parts)
    parts.append(sig)

    full = "\n".join(parts)
    if body_text:
        full += " " + body_text

    if len(full) > MAX_CHUNK_SIZE:
        full = full[:MAX_CHUNK_SIZE] + "\n// ... truncated"
    return full


def _build_class_overview_chunk(
    class_node,
    source_bytes: bytes,
    fqn: str,
    package: str,
    imports: list[str],
    inheritance: dict,
    inherited_methods: list[tuple[str, str]] | None = None,
) -> str:
    """Build a class overview string matching the regex parser's output."""
    body = _find_body(class_node)

    # Build declaration line
    type_kw = _type_keyword(class_node)
    mods = _get_modifier_list(class_node)
    name = _get_name(class_node)

    decl_parts: list[str] = []
    if mods:
        decl_parts.append(" ".join(mods))
    decl_parts.append(type_kw)
    decl_parts.append(name)

    # Type parameters
    for child in class_node.children:
        if child.type == "type_parameters":
            decl_parts[-1] += source_bytes[child.start_byte:child.end_byte].decode(
                "utf-8", errors="replace")

    # extends / implements clauses
    for child in class_node.children:
        if child.type == "superclass":
            decl_parts.append(
                source_bytes[child.start_byte:child.end_byte]
                .decode("utf-8", errors="replace"))
        elif child.type == "super_interfaces":
            decl_parts.append(
                source_bytes[child.start_byte:child.end_byte]
                .decode("utf-8", errors="replace"))
        elif child.type == "extends_interfaces":
            decl_parts.append(
                source_bytes[child.start_byte:child.end_byte]
                .decode("utf-8", errors="replace"))

    class_decl = " ".join(decl_parts) + " {"

    # Fields and method signatures
    fields: list[str] = []
    signatures: list[str] = []
    if body:
        fields = _collect_fields_for_overview(body, source_bytes)
        signatures = _collect_method_sigs_for_overview(body, source_bytes)

    overview_parts: list[str] = [f"package {package};" if package else ""]
    if imports[:10]:
        overview_parts.append("\n".join(imports[:10]))
        if len(imports) > 10:
            overview_parts.append(f"// ... {len(imports) - 10} more imports")
    overview_parts.append(class_decl)
    if inheritance["extends"]:
        overview_parts.append(f"// Extends: {inheritance['extends']}")
    if inheritance["implements"]:
        overview_parts.append(f"// Implements: {inheritance['implements']}")
    if fields:
        overview_parts.append("// Fields:")
        overview_parts.append("\n".join(fields[:30]))
    if signatures:
        overview_parts.append("// Methods:")
        overview_parts.append("\n".join(signatures[:50]))

    if inherited_methods:
        by_parent: dict[str, list[str]] = {}
        for sig, parent_fqn in inherited_methods:
            by_parent.setdefault(parent_fqn, []).append(sig)
        inherited_lines: list[str] = []
        for parent_fqn, sigs in by_parent.items():
            for sig in sigs[:20]:
                inherited_lines.append(f"  {sig};  // inherited from {parent_fqn}")
        if inherited_lines:
            overview_parts.append("// Inherited methods:")
            overview_parts.append("\n".join(inherited_lines[:40]))

    return "\n\n".join(p for p in overview_parts if p)


# --- inheritance map for two-pass parsing ---

def _collect_type_info(class_node, source_bytes: bytes, fqn: str, package: str) -> dict:
    """Extract extends + public method signatures from a type node for the inheritance map."""
    extends = _get_superclass(class_node)
    if not extends and _type_keyword(class_node) == "interface":
        ext_raw = _get_extends_interfaces_text(class_node)
        extends = ext_raw.split(",")[0].split("<")[0].strip() if ext_raw else ""

    body = _find_body(class_node)
    sigs: list[str] = []
    if body:
        sigs = _collect_method_sigs_for_overview(body, source_bytes)

    result = {"extends": extends, "method_sigs": sigs}

    if body:
        for child in body.children:
            if child.type in _TYPE_NODES:
                nested_name = _get_name(child)
                nested_fqn = f"{fqn}.{nested_name}"
                result[nested_fqn] = _collect_type_info(child, source_bytes, nested_fqn, package)

    return result


def _collect_all_class_info(source_dir: Path) -> dict[str, dict]:
    """Pass 1: Build {fqn: {extends, method_sigs}} for inherited method resolution."""
    class_map: dict[str, dict] = {}

    for java_file in sorted(source_dir.rglob("*.java")):
        try:
            raw = java_file.read_bytes()
        except Exception:
            continue

        tree = _parser.parse(raw)
        root = tree.root_node
        package = _extract_package_from_root(root, raw)

        for child in root.children:
            if child.type in _TYPE_NODES:
                name = _get_name(child)
                fqn = f"{package}.{name}" if package else name
                info = _collect_type_info(child, raw, fqn, package)
                class_map[fqn] = {"extends": info["extends"], "method_sigs": info["method_sigs"]}
                for key, val in info.items():
                    if isinstance(val, dict) and "extends" in val:
                        class_map[key] = val

    return class_map


def _get_inherited_methods(fqn: str, class_map: dict, max_depth: int = 5) -> list[tuple[str, str]]:
    """Walk the extends chain, return [(signature, parent_fqn)]."""
    inherited: list[tuple[str, str]] = []
    visited = {fqn}
    current = class_map.get(fqn, {}).get("extends", "")
    depth = 0

    while current and depth < max_depth:
        candidates = [k for k in class_map if k == current or k.endswith("." + current)]
        resolved = candidates[0] if candidates else ""
        if not resolved or resolved in visited:
            break
        visited.add(resolved)
        parent_info = class_map[resolved]
        for sig in parent_info["method_sigs"]:
            inherited.append((sig.strip().rstrip(";"), resolved))
        current = parent_info.get("extends", "")
        depth += 1

    return inherited


# --- recursive type node processor ---

def _process_type_node(
    class_node,
    source_bytes: bytes,
    fqn: str,
    package: str,
    rel_path: str,
    imports: list[str],
    nested_in: str | None,
    chunks: list[dict],
    class_map: dict | None = None,
) -> None:
    """Process a class/interface/enum/record node into RAG chunks."""
    name = _get_name(class_node)

    # Determine inheritance
    type_kw = _type_keyword(class_node)
    if type_kw == "interface":
        extends_raw = _get_extends_interfaces_text(class_node)
        extends = ", ".join(
            p.split("<")[0].strip()
            for p in extends_raw.split(",")
            if p.strip()
        ) if extends_raw else ""
        implements = ""
    else:
        extends = _get_superclass(class_node)
        iface_raw = _get_interfaces_text(class_node)
        implements = ", ".join(
            p.split("<")[0].strip()
            for p in iface_raw.split(",")
            if p.strip()
        ) if iface_raw else ""

    inheritance = {"extends": extends, "implements": implements}

    inherited = _get_inherited_methods(fqn, class_map) if class_map else []

    # Class overview chunk
    overview_text = _build_class_overview_chunk(
        class_node, source_bytes, fqn, package, imports, inheritance,
        inherited_methods=inherited or None,
    )
    class_meta: dict = {
        "source": "api",
        "type": "class_overview",
        "class_name": name,
        "fqn": fqn,
        "package": package,
        "file": rel_path,
        "extends": extends,
        "implements": implements,
    }
    if nested_in:
        class_meta["nested_in"] = nested_in

    chunks.append({
        "id": _chunk_id(f"class:{fqn}", "class"),
        "text": overview_text,
        "metadata": class_meta,
    })

    # Walk body members
    body = _find_body(class_node)
    if body:
        _process_body_members(body, source_bytes, fqn, name, package,
                              rel_path, imports, chunks, class_map=class_map)


def _process_body_members(
    body,
    source_bytes: bytes,
    fqn: str,
    class_name: str,
    package: str,
    rel_path: str,
    imports: list[str],
    chunks: list[dict],
    class_map: dict | None = None,
) -> None:
    """Walk body children and append method / constructor / nested chunks."""
    for child in body.children:
        if child.type == "method_declaration":
            mods = _get_modifier_list(child)
            vis = _visibility(mods)
            if vis == "private":
                continue
            method_name = _get_name(child)
            method_source = _build_method_source_bytes(child, source_bytes)
            method_text = f"// {fqn}\n{method_source}"
            if len(method_text) > 50:
                chunks.append({
                    "id": _chunk_id(
                        f"method:{fqn}.{method_name}:{method_source}",
                        "method",
                    ),
                    "text": method_text,
                    "metadata": {
                        "source": "api",
                        "type": "method",
                        "class_name": class_name,
                        "fqn": fqn,
                        "method_name": method_name,
                        "package": package,
                        "file": rel_path,
                        "visibility": vis,
                    },
                })

        elif child.type == "constructor_declaration":
            mods = _get_modifier_list(child)
            vis = _visibility(mods)
            if vis == "private":
                continue
            ctor_source = _build_ctor_source_bytes(child, source_bytes)
            ctor_text = f"// {fqn}\n{ctor_source}"
            if len(ctor_text) > 50:
                chunks.append({
                    "id": _chunk_id(
                        f"ctor:{fqn}:{ctor_source}", "ctor",
                    ),
                    "text": ctor_text,
                    "metadata": {
                        "source": "api",
                        "type": "constructor",
                        "class_name": class_name,
                        "fqn": fqn,
                        "method_name": "<init>",
                        "package": package,
                        "file": rel_path,
                        "visibility": vis,
                    },
                })

        elif child.type in _TYPE_NODES:
            nested_name = _get_name(child)
            nested_fqn = f"{fqn}.{nested_name}"
            _process_type_node(
                child, source_bytes, nested_fqn, package, rel_path,
                imports, nested_in=fqn, chunks=chunks, class_map=class_map,
            )

        elif child.type == "enum_body_declarations":
            _process_body_members(child, source_bytes, fqn, class_name,
                                  package, rel_path, imports, chunks,
                                  class_map=class_map)


# --- public entry point ---

def parse_java_files(source_dir: Path) -> list[dict]:
    """Parse all ``.java`` files under *source_dir* and return RAG chunks.

    Two-pass approach: first collects inheritance map, then builds chunks
    with inherited method information in class overviews.

    Return format is identical to ``indexer.parse_java_files()``:
    ``[{"id": str, "text": str, "metadata": dict}, ...]``
    """
    class_map = _collect_all_class_info(source_dir)

    chunks: list[dict] = []

    for java_file in sorted(source_dir.rglob("*.java")):
        try:
            raw = java_file.read_bytes()
        except Exception:
            continue

        rel_path = str(java_file.relative_to(source_dir))
        tree = _parser.parse(raw)
        root = tree.root_node

        package = _extract_package_from_root(root, raw)
        imports = _extract_imports_from_root(root, raw)

        for child in root.children:
            if child.type in _TYPE_NODES:
                top_name = _get_name(child)
                top_fqn = f"{package}.{top_name}" if package else top_name
                _process_type_node(
                    child, raw, top_fqn, package, rel_path,
                    imports, nested_in=None, chunks=chunks,
                    class_map=class_map,
                )

    return chunks
