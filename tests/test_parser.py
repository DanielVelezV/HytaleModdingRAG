"""Tests for Java parsing (tree-sitter + regex fallback)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from java_parser import parse_file, parse_java_files
from indexer import (
    _extract_methods,
    _extract_constructors,
    _extract_inheritance,
    _extract_nested_types,
    _split_text,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"

SAMPLE_CLASS = """\
package com.hypixel.hytale.server.core.command;

import java.util.List;
import java.util.Map;

public abstract class AbstractCommand implements ICommand, IRegisterable {

    private final String name;
    protected final List<String> aliases;

    public AbstractCommand(String name) {
        this.name = name;
    }

    protected AbstractCommand(String name, List<String> aliases) {
        this.name = name;
        this.aliases = aliases;
    }

    public abstract void execute(CommandContext ctx);

    public String getName() {
        return this.name;
    }

    protected List<String> getAliases() {
        return this.aliases;
    }

    private void internalSetup() {
        // private - should be filtered
    }

    public static class SubCommand extends AbstractCommand {
        public SubCommand(String name) {
            super(name);
        }

        @Override
        public void execute(CommandContext ctx) {
            // nested class method
        }
    }
}
"""

WILDCARD_CLASS = """\
package com.hypixel.hytale.asset;

public class AssetRegistry<T extends JsonAssetWithMap> {
    private final Map<Class<? extends JsonAssetWithMap>, AssetStore<?, ?, ?>> storeMap;

    public <K extends Comparable<? super K>> AssetStore<?, K, ?> getStore(Class<? extends JsonAssetWithMap> clazz) {
        return storeMap.get(clazz);
    }
}
"""

MULTI_MODIFIER_CLASS = """\
package com.hypixel.hytale.server.entity;

public static final class EntityConfig implements Serializable {
    public final int maxHealth;
    public static final EntityConfig DEFAULT = new EntityConfig(100);

    public EntityConfig(int maxHealth) {
        this.maxHealth = maxHealth;
    }
}
"""


class TestTreeSitterParser:
    def test_parse_basic_class(self):
        result = parse_file(SAMPLE_CLASS)
        assert result is not None
        assert result["package"] == "com.hypixel.hytale.server.core.command"
        assert len(result["types"]) >= 1

    def test_methods_extracted(self):
        result = parse_file(SAMPLE_CLASS)
        td = result["types"][0]
        method_names = [m["name"] for m in td["methods"]]
        assert "execute" in method_names
        assert "getName" in method_names
        assert "getAliases" in method_names
        assert "internalSetup" in method_names

    def test_method_visibility(self):
        result = parse_file(SAMPLE_CLASS)
        td = result["types"][0]
        by_name = {m["name"]: m for m in td["methods"]}
        assert by_name["execute"]["visibility"] == "public"
        assert by_name["getName"]["visibility"] == "public"
        assert by_name["getAliases"]["visibility"] == "protected"
        assert by_name["internalSetup"]["visibility"] == "private"

    def test_constructors_extracted(self):
        result = parse_file(SAMPLE_CLASS)
        td = result["types"][0]
        assert len(td["constructors"]) == 2
        visibilities = {c["visibility"] for c in td["constructors"]}
        assert "public" in visibilities
        assert "protected" in visibilities

    def test_inheritance(self):
        result = parse_file(SAMPLE_CLASS)
        td = result["types"][0]
        assert td["extends"] == "ICommand" or td["extends"] == ""
        assert "ICommand" in td.get("implements", "") or "IRegisterable" in td.get("implements", "")

    def test_nested_class(self):
        result = parse_file(SAMPLE_CLASS)
        td = result["types"][0]
        nested = td.get("nested_types", td.get("nested", []))
        assert len(nested) >= 1
        assert nested[0]["name"] == "SubCommand"

    def test_wildcard_generics(self):
        result = parse_file(WILDCARD_CLASS)
        assert result is not None
        td = result["types"][0]
        method_names = [m["name"] for m in td["methods"]]
        assert "getStore" in method_names

    def test_multi_modifier_class(self):
        result = parse_file(MULTI_MODIFIER_CLASS)
        assert result is not None
        td = result["types"][0]
        assert td["name"] == "EntityConfig"


class TestRegexFallback:
    def test_method_extraction_returns_tuples(self):
        methods = _extract_methods(SAMPLE_CLASS)
        assert len(methods) >= 3
        for name, source, vis in methods:
            assert isinstance(name, str)
            assert isinstance(source, str)
            assert vis in ("public", "protected", "private", "package-private")

    def test_constructor_extraction(self):
        ctors = _extract_constructors(SAMPLE_CLASS, "AbstractCommand")
        assert len(ctors) >= 1
        for source, vis in ctors:
            assert "AbstractCommand" in source

    def test_inheritance_extraction(self):
        inh = _extract_inheritance(SAMPLE_CLASS)
        assert inh["implements"]
        assert "ICommand" in inh["implements"] or "IRegisterable" in inh["implements"]

    def test_nested_type_extraction(self):
        nested = _extract_nested_types(
            SAMPLE_CLASS,
            "com.hypixel.hytale.server.core.command.AbstractCommand",
            "com.hypixel.hytale.server.core.command",
        )
        assert len(nested) >= 1
        assert nested[0]["name"] == "SubCommand"

    def test_wildcard_methods_not_dropped(self):
        methods = _extract_methods(WILDCARD_CLASS)
        method_names = [m[0] for m in methods]
        assert "getStore" in method_names


class TestSplitText:
    def test_basic_split(self):
        text = "a" * 3000
        parts = _split_text(text, 1500, 200)
        assert len(parts) >= 2
        assert all(len(p) <= 1500 for p in parts)

    def test_no_split_needed(self):
        text = "short text"
        parts = _split_text(text, 1500, 200)
        assert len(parts) == 1
        assert parts[0] == "short text"

    def test_overlap_present(self):
        text = "\n".join(f"line {i}" for i in range(200))
        parts = _split_text(text, 500, 100)
        assert len(parts) >= 2
        end_of_first = parts[0][-50:]
        assert end_of_first in parts[1]


# ===================================================================
# parse_java_files integration tests using real CFR-decompiled fixtures
# ===================================================================

def _chunks_by_fqn(chunks, fqn, chunk_type=None):
    return [
        c for c in chunks
        if c["metadata"]["fqn"] == fqn
        and (chunk_type is None or c["metadata"]["type"] == chunk_type)
    ]


class TestParseJavaFilesRegistry:
    """Registry.java -- abstract generic class with nested interface."""

    def setup_method(self):
        self.chunks = parse_java_files(FIXTURES_DIR)
        self.fqn = "com.hypixel.hytale.registry.Registry"

    def test_class_overview_exists(self):
        overviews = _chunks_by_fqn(self.chunks, self.fqn, "class_overview")
        assert len(overviews) == 1
        meta = overviews[0]["metadata"]
        assert meta["class_name"] == "Registry"
        assert meta["package"] == "com.hypixel.hytale.registry"
        assert meta["extends"] == ""  # no explicit superclass in source
        assert meta["source"] == "api"

    def test_constructor_detected(self):
        ctors = _chunks_by_fqn(self.chunks, self.fqn, "constructor")
        assert len(ctors) >= 1
        assert ctors[0]["metadata"]["method_name"] == "<init>"
        assert ctors[0]["metadata"]["visibility"] == "protected"

    def test_public_methods(self):
        methods = _chunks_by_fqn(self.chunks, self.fqn, "method")
        names = [m["metadata"]["method_name"] for m in methods]
        assert "isEnabled" in names
        assert "register" in names
        assert "toString" in names
        # protected method should appear
        assert "checkPrecondition" in names

    def test_no_private_methods(self):
        methods = _chunks_by_fqn(self.chunks, self.fqn, "method")
        for m in methods:
            assert m["metadata"]["visibility"] != "private"

    def test_nested_interface(self):
        nested_fqn = f"{self.fqn}.RegistrationWrapFunction"
        overviews = _chunks_by_fqn(self.chunks, nested_fqn, "class_overview")
        assert len(overviews) == 1
        meta = overviews[0]["metadata"]
        assert meta["nested_in"] == self.fqn
        assert meta["class_name"] == "RegistrationWrapFunction"


class TestParseJavaFilesEnum:
    """EventPriority.java -- enum with private constructor."""

    def setup_method(self):
        self.chunks = parse_java_files(FIXTURES_DIR)
        self.fqn = "com.hypixel.hytale.event.EventPriority"

    def test_class_overview(self):
        overviews = _chunks_by_fqn(self.chunks, self.fqn, "class_overview")
        assert len(overviews) == 1
        text = overviews[0]["text"]
        assert "enum" in text.lower()

    def test_public_method(self):
        methods = _chunks_by_fqn(self.chunks, self.fqn, "method")
        names = [m["metadata"]["method_name"] for m in methods]
        assert "getValue" in names
        for m in methods:
            assert m["metadata"]["visibility"] == "public"

    def test_private_constructor_skipped(self):
        ctors = _chunks_by_fqn(self.chunks, self.fqn, "constructor")
        # Enum constructor is private -- should be skipped
        assert len(ctors) == 0


class TestParseJavaFilesNestedTypes:
    """PluginManifest.java -- nested enum + nested static class."""

    def setup_method(self):
        self.chunks = parse_java_files(FIXTURES_DIR)
        self.fqn = "com.hypixel.hytale.common.plugin.PluginManifest"

    def test_nested_enum(self):
        nested_fqn = f"{self.fqn}.ServerVersionCheck"
        overviews = _chunks_by_fqn(self.chunks, nested_fqn, "class_overview")
        assert len(overviews) == 1
        assert overviews[0]["metadata"]["nested_in"] == self.fqn

    def test_nested_static_class(self):
        nested_fqn = f"{self.fqn}.CoreBuilder"
        overviews = _chunks_by_fqn(self.chunks, nested_fqn, "class_overview")
        assert len(overviews) == 1
        assert overviews[0]["metadata"]["nested_in"] == self.fqn

    def test_nested_class_methods(self):
        nested_fqn = f"{self.fqn}.CoreBuilder"
        methods = _chunks_by_fqn(self.chunks, nested_fqn, "method")
        names = [m["metadata"]["method_name"] for m in methods]
        assert "build" in names
        assert "description" in names

    def test_public_constructors(self):
        ctors = _chunks_by_fqn(self.chunks, self.fqn, "constructor")
        assert len(ctors) >= 1
        for c in ctors:
            assert c["metadata"]["visibility"] == "public"
            assert c["metadata"]["method_name"] == "<init>"

    def test_many_public_methods(self):
        methods = _chunks_by_fqn(self.chunks, self.fqn, "method")
        names = [m["metadata"]["method_name"] for m in methods]
        for expected in ("getName", "getGroup", "getDependencies", "inherit"):
            assert expected in names, f"Missing method: {expected}"


class TestParseJavaFilesInterface:
    """IEventBus.java -- interface with default methods and complex generics."""

    def setup_method(self):
        self.chunks = parse_java_files(FIXTURES_DIR)
        self.fqn = "com.hypixel.hytale.event.IEventBus"

    def test_interface_overview(self):
        overviews = _chunks_by_fqn(self.chunks, self.fqn, "class_overview")
        assert len(overviews) == 1
        meta = overviews[0]["metadata"]
        assert meta["class_name"] == "IEventBus"
        # IEventBus extends IEventRegistry
        assert "IEventRegistry" in meta["extends"]

    def test_default_methods(self):
        methods = _chunks_by_fqn(self.chunks, self.fqn, "method")
        names = [m["metadata"]["method_name"] for m in methods]
        assert "dispatch" in names
        assert "dispatchAsync" in names

    def test_all_public(self):
        methods = _chunks_by_fqn(self.chunks, self.fqn, "method")
        for m in methods:
            assert m["metadata"]["visibility"] == "public"


class TestChunkSchema:
    """Verify every chunk has the correct schema."""

    def setup_method(self):
        self.chunks = parse_java_files(FIXTURES_DIR)

    def test_all_chunks_have_required_keys(self):
        for c in self.chunks:
            assert "id" in c, f"Missing 'id' key"
            assert "text" in c, f"Missing 'text' key"
            assert "metadata" in c, f"Missing 'metadata' key"
            assert isinstance(c["id"], str)
            assert isinstance(c["text"], str)
            assert isinstance(c["metadata"], dict)

    def test_metadata_keys_by_type(self):
        required_common = {"source", "type", "class_name", "fqn", "package", "file"}
        for c in self.chunks:
            meta = c["metadata"]
            assert required_common.issubset(meta.keys()), (
                f"Missing keys in {meta['fqn']}: "
                f"{required_common - meta.keys()}"
            )
            assert meta["source"] == "api"

            if meta["type"] == "class_overview":
                assert "extends" in meta
                assert "implements" in meta
            elif meta["type"] == "method":
                assert "method_name" in meta
                assert "visibility" in meta
            elif meta["type"] == "constructor":
                assert meta["method_name"] == "<init>"
                assert "visibility" in meta

    def test_id_format(self):
        for c in self.chunks:
            assert "_" in c["id"]
            prefix = c["id"].split("_")[0]
            assert prefix in ("class", "method", "ctor")
