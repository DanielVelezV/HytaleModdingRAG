---
name: hytale-codec
description: "Use when defining or changing anything that goes through the engine's codec machinery in a Hytale server plugin — `BuilderCodec`, `KeyedCodec`, `Codec.*`, a map/collection/array codec, a nested sub-document, or codec versioning. Concrete triggers: 'persist per-player data', 'add a field to my component', 'register a custom component', 'define the codec for X', 'UnsupportedOperationException' after loading saved data, 'my saved field comes back null / empty', 'rename a persisted field without breaking saves', a codec for a custom UI page's event payload, an asset decoded or round-tripped through a codec. SKIP for: the ECS store/tick rules around a component (when a write is legal, deferring through a command buffer), the plugin's own hand-written JSON config file (`hytale-config`), and the page-side contract for an event payload (`hytale-ui`)."
---

# Codecs: the engine's serialization

> **Engine `0.5.9`** (patchline `release`) · last checked 2026-08-20 — facts verified against
> `Server-0.5.9.jar`, with in-game confirmation where noted. **Newer server? Re-verify before
> trusting a signature below**; codec factory signatures are what a bump moves first.

**Why this is its own subject:** one mechanism serves at least three unrelated jobs — a
**persisted component** (state the engine saves with an entity), a **custom UI page's event
payload** (decoded before it reaches your handler), and an **asset round-trip** (decoding a
patched document back into a game object). Learn it once here; each consumer's own rules
live with that consumer.

## The builder shape

```java
public static final BuilderCodec<MyData> CODEC =
    BuilderCodec.builder(MyData.class, MyData::new)
        .append(new KeyedCodec<>("Level", Codec.INTEGER), MyData::setLevel, MyData::getLevel).add()
        .append(new KeyedCodec<>("Prefs", Prefs.CODEC),   MyData::setPrefs, MyData::getPrefs).add()
        .build();
```

- `BuilderCodec.builder(Class<T>, Supplier<T>)` is **static** — the `Builder` constructor is
  protected. The supplier is the no-arg factory the decoder uses.
- `.append(KeyedCodec<F>, BiConsumer<T,F> setter, Function<T,F> getter)` returns a
  `FieldBuilder`; `.add()` returns you to the builder; repeat; `.build()`.
- Field codecs: `Codec.INTEGER`, `DOUBLE`, `STRING`, `BOOLEAN`, `LONG`, `UUID_BINARY`,
  arrays, and the map/collection codecs below.
- The target type needs a **public no-arg constructor** and ordinary setters/getters — the
  codec is field-by-field, not reflective over the whole object.

## ⚠️ A collection codec decodes into an UNMODIFIABLE collection

Confirmed in-game. A map codec hands your setter a `Collections$UnmodifiableMap`. A field
that is **mutated after loading** must therefore be **copied on the way in**:

```java
.append(new KeyedCodec<>("Tally", new EnumMapCodec<>(MyEnum.class, Codec.INTEGER)),
        (c, v) -> c.tally = new EnumMap<>(v),        // ✅ copy
        c -> c.tally).add()
```

Assigning it straight through works right up until the first mutation, which then throws
`UnsupportedOperationException` **on the world thread and takes the world down with it**
("The world you were on has crashed"). It is a delayed fault: the load succeeds, the crash
lands whenever that field is next written, which may be a different session.

**Assume the same of every collection codec** (set, array, object-map, primitive-map) and
copy in any setter whose field is later mutated. The trap has a signature worth recognizing
in review: a refactor that moves existing fields into a new nested block, where the new
setters are written fresh and the old ones' copy is silently dropped.

Collection/map codecs live in the codec library's `map` package — an enum-keyed map codec
serializes as a JSON object keyed by the enum **name**, which stays readable in the saved
file and survives reordering the enum.

## Nesting: a `BuilderCodec` *is* a `Codec`

Jar-verified and in-game confirmed. `KeyedCodec(String, Codec<T>)` takes any codec, so
passing another `BuilderCodec` nests a **sub-document** under that key:

```java
.append(new KeyedCodec<>("Prefs", Prefs.CODEC), Data::setPrefs, Data::getPrefs).add()
```

That is how one persisted object holds several **named blocks** instead of a flat wall of
keys. Group by **who owns the data and when it stops meaning anything**, not by type — a
block that goes inert when a feature is switched off should be one block, so the answer to
"is any of this still meaningful?" is one lookup.

**Keep the blocks as storage, not as API.** Expose flat accessors over them; then re-cutting
the shape later touches no caller.

## Schema evolution

The builder has `.versioned()` / `.legacyVersioned()` / `.codecVersion(int[, int])`, and each
`FieldBuilder` has `.setVersionRange(int min, int max)` — so a field can be declared as
living only up to version N and its replacement from N+1. That is the native way to rename
or re-cut a persisted shape without hand-migrating files.

> **Signatures verified; the mechanism is not exercised in this repo.** Treat the exact
> semantics as unconfirmed until you have run it, and prefer verifying on a copy of a real
> save. The pragmatic alternative for a tiny player base is a one-off migration script — but
> that stops being viable the moment other people run the mod.

Two rules that hold either way:

- **A key you have shipped is a compatibility surface.** Renaming it silently loses the
  field, because a decoder that does not find a key leaves the default.
- **Adding a field is safe**; the decoder leaves it at the supplier's default for documents
  written before it existed. So new state does not need a migration, only a sensible default.
- **A `null` reference field is omitted on encode, not written as `null`** — the key is
  simply absent from the document (observed in shipped player files, 2026-08-24). Which
  closes the loop with the rule above: encode-skip plus decode-leave-the-default means a
  nullable field round-trips as `null` and, more usefully, that **`null` is a readable
  state** — "nobody ever set this" and "written before the field existed" are the same
  value, and neither costs a byte on disk.

## The consumers, and what each additionally requires

**A persisted component** — register it once at `setup()` on the plugin's entity-store
registry, holding the returned `ComponentType`:

```java
this.dataType = getEntityStoreRegistry().registerComponent(MyData.class, "Widget", MyData.CODEC);
```

- The overload **with** a codec persists (it lands in the entity's JSON); the overload with
  only a supplier is a **transient marker** that is never written.
- **Namespace the id string.** It shares one registry with every other mod's components and
  is the literal key written into the save file, so a generic id risks a collision — use the
  mod's own name, which is also what an owner reading the file will recognize.
- The engine persists the live component instance; there is no explicit "put". Everything
  about *when* you may attach or mutate one belongs to the ECS rules, not here.

**A page event payload** — every field must be **boxed and nullable**, and the codec's type
must match the widget's native JSON type or the event is dropped in silence. That contract
belongs to the page; see `hytale-ui`.

**An asset round-trip** — decoding a patched document back into a game object. Prefer the
codec round-trip over a copy constructor: it carries every field the engine knows about,
including ones added in a later version that your constructor would silently drop.

## Reviewing a codec change

- [ ] Every new field has a default that is correct for documents written before it existed.
- [ ] Any collection field that is mutated later is **copied** in its setter.
- [ ] No shipped key was renamed without a version range (or a deliberate, stated loss).
- [ ] A nested block's accessors are flat, so the grouping stays re-cuttable.
- [ ] The component id is namespaced to the mod.
