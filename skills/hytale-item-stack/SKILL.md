---
name: hytale-item-stack
description: "Use when a Hytale server plugin reads or writes a live `ItemStack` — per-instance metadata, durability, quality, or handing an item to a player. Concrete triggers: 'stamp my mod's data onto an item', 'read another mod's key off a stack', `withMetadata`, `getMetadata`, `getFromMetadataOrNull`, `isStackableWith`, `withDurability` / `withMaxDurability` / `withQuality`, 'my write to the item didn't stick', 'the item shows the wrong rarity border', 'a chest crashes the client when opened', 'the block/bench can see my item but refuses to consume it', 'ItemToRemove does nothing', 'the interaction fails with no message', 'my items stopped stacking together', 'sweep every item in a player's inventory'. SKIP for: changing what an item *type* is — injecting or cloning an asset, editing stats for every copy (`hytale-assets`); the codec machinery itself (`hytale-codec`); and container/inventory ECS plumbing (`hytale-ecs`)."
---

# Reading and writing a live item stack

> **Engine `0.6.1`** (patchline `release`) · last checked 2026-09-04 — facts verified against
> `Server-0.6.1.jar` (`ItemStack`, `ItemContainer`, `InternalContainerUtilItemStack`,
> `ModifyInventoryInteraction`, `CraftingManager` disassembled), with in-game confirmation of
> the identity trap. **Newer server? Re-verify before trusting anything below**; the field
> set on `ItemStack` and what `isStackableWith` compares are what a bump moves first.

**An asset is the type; a stack is the instance.** Everything here is the instance. If a
value should be the same for every copy of an id, it belongs in the asset
(`hytale-assets`), not here.

## The instance is immutable — write it back

`ItemStack` is copy-on-write. Every `with*` call returns a **new** stack; the one in the
container is untouched until you put it there.

```java
ItemStack updated = stack.withMetadata("MyMod.Tier", Codec.STRING, "GOLD");
container.setItemStackForSlot(slot, updated);   // without this, nothing happened
```

Forgetting the write-back is the single most common way a change to an item silently does
nothing.

## What the instance actually carries

Six fields, and the codec persists all of them: `itemId`, `quantity`, `durability`,
`maxDurability`, `qualityIndex`, `metadata` (a BSON document).

Two of them are **snapshots taken at construction** from the asset, and never refresh:

- **`maxDurability`** — seeded from `Item.getMaxDurability()` in the constructor. Re-tune
  the asset and existing stacks keep the old ceiling forever.
- **`qualityIndex`** — a **position** in a quality table that is rebuilt every boot from
  whichever mods loaded, so installing or removing any mod that ships one renumbers it and
  saved stacks go stale. Wrong border at best, a client crash on opening the container at
  worst. Repair it where you already walk containers: compare `stack.getQualityIndex()`
  against `stack.getItem().getQualityIndex()` and rewrite with `withQuality`. Full symptom
  trail in `hytale-assets` → *Injecting a quality renumbers everyone's saved items*.

If your mod can move either number, plan a sweep that re-syncs stacks when a player touches
them: joining, an inventory change, a container being opened.

## ⚠️ The trap: adding metadata changes what the stack **is**

Item matching inside a container is `ItemStack.isStackableWith`, and it compares **five**
things:

```
durability          raw field, exact
maxDurability       raw field, exact
getQualityIndex()   the getter, so an unset sentinel resolves to the asset's index
itemId              String.equals
metadata            BsonDocument.equals -- and a null document matches only another null
```

So a stack carrying `{"MyMod.Whatever": 1}` is **not the same item** as a freshly built one
carrying nothing, though both are the same id. An **empty** document is not `null` either:
removing your last key has to leave the field `null`, or the stack stays "different".

**Two consumers ask different questions, and that is the whole bug shape:**

| path | matches on | your metadata breaks it? |
|---|---|---|
| a recipe's inputs (the crafting manager's material removal) | **item id** (or resource-type id) | no |
| an interaction's `ItemToRemove` (`removeItemStack` → `isStackableWith`) | the **whole stack**, per the list above | **yes** — the interaction just ends in a failed state, with no message |

That asymmetry is what a player reports as **"the station can see the item but won't use
it"**: the recipe list shows it as available because that reading is by id, and the
interaction that spends it refuses because that one is by whole stack. Nothing is logged.

The items this actually hits are the ones whose **durability is a consumable resource
rather than wear** — filled buckets, filled drinking vessels, watering cans, fertilizer —
because those are exactly the ones an interaction *spends* rather than wears down. An asset
author who needs such a match spells the fields out by hand, which is the shipped evidence
that the match is exact:

```json
"ItemToRemove": { "Id": "*Some_Container_State_Filled", "Durability": 1, "MaxDurability": 1 }
```

### The rule that follows

> **Decorate the instances you make. Read everybody else's.**

A sweep over a player's whole inventory may *read* any stack it reaches, but it must only
*write* onto instances your own mod made or has already claimed — an id you minted, or a
stack already carrying one of your namespaced keys. Take care that the key deciding
"already claimed" is not one your sweep writes on its own initiative, or every item you
ever touched becomes permanently yours and the mistake is unrecoverable.

Writing the **durability or quality fields** is a different case and can be legitimate on
anybody's item: a stack whose asset max has moved, or whose quality index went stale, is
*already* failing that same match before you arrive — putting it back in line is what
repairs the match rather than breaking it.

## The metadata API, and one deprecated call worth keeping

```java
// write (each returns a new stack)
stack.withMetadata(String key, Codec<T> codec, T value)
stack.withMetadata(KeyedCodec<T> codec, T value)
stack.withMetadata(String key, BsonValue value)
stack.withMetadata(BsonDocument whole)

// read one key
stack.getFromMetadataOrNull(String key, Codec<T> codec)
stack.getFromMetadataOrNull(KeyedCodec<T> codec)

// read the whole document — @Deprecated, @Nullable, returns a clone
stack.getMetadata()
```

- **`getMetadata()` is `@Deprecated` but not `forRemoval`** (the annotation carries no
  elements), and there is **no whole-document replacement**: the keyed readers each answer
  one key, and every other whole-document call is a *writer*. Use it when you genuinely need
  the document — "does this carry any key of mine", "what is left after one key leaves" —
  and a keyed read otherwise. It returns a **clone**, so you may edit the result in place;
  it returns **`null`**, not an empty document, for a stack that carries none, so null-guard
  every call.
- ⚠️ **`toPacket().metadata` is not a substitute.** That field is a `java.lang.String`
  written by `toJson()`, and `toPacket()` memoizes the whole packet on the stack. Reading
  the document through it means serializing an object you already hold and re-parsing it.
- **`withMetadata(key, null)` removes a key** — it branches on a null or `BsonNull` value
  and calls `remove` instead of `put`. ⚠️ But it **never drops the document**: removing the
  last key leaves `{}`, which the constructor stores as-is, and `{}` is not `equals` to
  `null`. To actually restore an instance's identity, read the document, drop the key, and
  hand `null` to the constructor when nothing is left.
- **Namespace your keys** (`MyMod.Thing`). Every mod writes into the same document and a
  collision is silent data loss.
- **Metadata is not a trust boundary.** Anyone who can run the give command can author any
  key you read (`hytale-plugin` → the two engine test commands), so never gate anything that
  matters on a key alone.

## Coverage: you only reach what someone touches

There is no practical way to sweep every stack in a world — chunk storage is on disk, and
reaching unloaded containers means force-loading. The honest guarantee is *eventually, if
reached*, off a join, an inventory change and a container being opened. An item lying in a
chest nobody opens keeps whatever it had; write any migration so it is safe to meet that
stack years later.
