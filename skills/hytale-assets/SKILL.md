---
name: hytale-assets
description: "Use when a Hytale server plugin adds, clones or mutates game assets — items, item qualities, recipes, blocks — or ships client-facing art. Concrete triggers: 'add a new item', 'add a variant of an existing item', 'inject an asset at boot', `LoadAssetEvent`, `AssetStore`, `loadAssets`, 'clone a quality', 'mint a recipe', 'my item shows a ? icon', 'my new art doesn't appear', 'the item can't be salvaged / dismantled', 'the recipe isn't recognized at the bench', 'the whole server failed to boot with Missing default assets', pack layout under `Server/**` vs `Common/**`, 'add a translation key', 'ship a new language', 'my lang key renders as itself', 'a whole lang file stopped resolving', `server.lang`. SKIP for: reading or writing a live `ItemStack`'s per-instance metadata, durability or quality — an asset is the type, not the instance (`hytale-item-stack`); the codec machinery itself (`hytale-codec`); and ECS systems (`hytale-ecs`)."
---

# Injecting and shipping assets

> **Engine `0.5.9`** (patchline `release`) · last checked 2026-08-23 — facts verified against
> `Server-0.5.9.jar` plus a real `Assets.zip`, with in-game confirmation. **Newer server?
> Re-verify before trusting anything below**; asset keys and the boot hook are what a bump
> moves first. A **section carrying its own later version and date** was re-verified against
> that one; everything without a date still rests on the stamp above.

**The distinction that governs everything here: an asset is the *type*, not the
instance.** Injecting a variant changes what every copy of that id is; per-instance
metadata on a live stack is a different mechanism entirely (`hytale-item-stack`). If a
value has to differ between two copies of the same item, it is not an asset change.

## Two ways to add an asset, and they are not interchangeable

**Declaratively** — ship a JSON under the pack's `Server/**` and the engine loads it. This
is how you add a **new** item, recipe or quality. No code.

**By injection at boot** — encode an existing asset, patch it, decode it back under a new
id. This is for **cloning or mutating something that already exists**, including assets
shipped by the game or by another mod, and for minting a family of variants that would be
absurd to hand-write.

## The boot hook

The asset-load event fires once, after base **and** mod assets are all in the shared
stores. Register on it at a **late** priority so everything you might clone is present:

```java
getEventRegistry().register(LoadAssetEvent.PRIORITY_LOAD_LATE, LoadAssetEvent.class, e -> …);
```

- **Guard against a double fire** with an `AtomicBoolean`.
- **Injection is deterministic, so nothing needs persisting** — re-run it every boot rather
  than writing state. That is what makes the whole approach safe to change between versions.
- Consequence for config: anything that feeds this hook is **boot-applied**. A setting that
  changes a variant's stats cannot take effect until a restart, and the UI or command that
  edits it must say so.

Each asset type has its own store: `Item.getAssetStore()`, `ItemQuality.getAssetStore()`,
and so on, with `loadAssets(pack, list)` and `decode(pack, id, document)`. A successful bulk
inject logs `hasFailed=false`.

## Which of the two, decided by the load-order graph

The stores are built with an explicit `loadsAfter(Class…)` list, so **an asset you ship
declaratively may statically reference another asset you ship declaratively**, as long as
its type is upstream in that graph (an item, for instance, loads after resource types and
qualities). Read the loader before assuming it: this is the difference between one JSON
file and a boot hook.

**What is never legal is a static reference to something you inject at boot** — the boot
hook runs after every pack has loaded *and validated*, so validation sees a dangling id.
The failure is not local: a bad reference fails that asset's decode, and a whole pack's
worth of them fails the mod's asset load. The pattern for a value that must be injected is
therefore to ship the asset with a **valid stand-in** (a vanilla id) and patch it in the
hook.

## The codec round-trip — clone by encoding, never by copy constructor

```java
BsonDocument doc = ((BuilderCodec<Item>) Item.getAssetStore().getCodec())
        .encode(base, ExtraInfo.THREAD_LOCAL.get());   // fully-resolved config, no id key
patch(doc);                                             // game JSON keys, PascalCase
Item variant = Item.getAssetStore().decode(pack, newId, doc);
```

**Use the codec, not the copy constructor** — a copy constructor shares nested objects, so
mutating the "copy" reaches back into the original. The round-trip yields a fully
independent object graph, and it carries every field the engine knows about, including ones
added in a version your constructor predates.

Two properties of the encoded document worth knowing: inheritance is already **inlined**
(the parent's fields are resolved into it), and there is **no id key** — the id is the
external asset key you pass to `decode`.

**A variant that should not be craftable in its own right must have its recipe fields
removed** in the patch, or it appears as its own entry in the crafting UI.

### Inline children survive the round-trip; referenced ones do not

A map field typed `Map<String, String>` can still carry whole nested documents, because the
codec behind it treats a **string** value as an asset-id reference and a **document** value
as a *contained* asset — registered as a child under a generated key, which is prefixed
`*`. On encode, a `*` key is expanded back **inline, at full depth**; anything else is
written as the bare id string.

**So: patch what the asset inlines, and never assume an interaction referenced by id is
reachable from the parent's document.** Whether a nested value is patchable is decided by
how the original JSON was written, not by the field's Java type.

## ⚠️ A decoded field is not the JSON — defaults and post-decode normalization (`0.6.1`, verified 2026-09-04)

When you enumerate the item store to decide what to touch, you are reading **resolved**
objects: the `Parent` chain has been walked and a post-decode pass has already run. Two
consequences bite anyone writing a gate over the catalog:

- **A "not declared" field is not a neutral value.** The stackable flag is initialized to
  `-1` and then rewritten by the item's post-decode step: an undeclared one becomes **1**
  when the item carries a weapon / armor / tool / builder-tool / block-selector block, and
  **100** otherwise. So most gear never mentions it and still resolves to 1, and an item
  with nothing but an *empty* `"Tool": {}` block resolves to 1 too. Never read the raw
  sentinel as "unset" at runtime — by the time a plugin sees the item, it is gone.
- **A boolean default may be `true`, set in the constructor rather than by the codec.** The
  repairability flag is one: it is `true` unless a file says otherwise, so an explicit
  `false` is a *statement*, which makes it a usable classification lever. It is the engine's
  own marker for an item whose durability is a **consumable resource** (a charge count)
  rather than wear — the class of item that must not be treated as forgeable gear, and that
  breaks in interesting ways if you write to it (`hytale-item-stack`).

**Measure before you build a gate on either.** Both of the above were only trustworthy
after a census over the shipped item files with `Parent` chains resolved — which is cheap
to run and is the difference between a rule and a guess. Watch for **tag-only gear** while
you are there: some items declare a type in their tags and carry **no stat block at all**,
so a gate written on the blocks alone silently skips them.

## An asset is not frozen after boot — same id, live

`loadAssets` **replaces** an entry under an id the store already holds, and the store
pushes the change to connected clients **itself**. So a value that has to change
mid-session — a name, a stat, anything on the type — does not need a second id per
variation, and does not need a restart note.

```java
BsonDocument doc = codec.encode(live, extraInfo);   // ← the LIVE asset, not the shipped JSON
patchOnlyWhatMoved(doc);
store.loadAssets(pack, List.of(store.decode(pack, live.getId(), doc)),
        AssetUpdateQuery.DEFAULT_NO_REBUILD);       // ← not the 2-arg overload. see below
```

**Do not broadcast the update yourself.** The store's remove/update handler asks how many
players are online and, if any are, generates the type's update packet and broadcasts it.
Sending your own is a duplicate. (At boot nobody is online, so this path is skipped and the
assets go out as init packets — which is why boot injection never has to think about it.)

**⚠️ The 2-arg `loadAssets(pack, list)` orders every client to rebuild its caches.** It
passes the default update query, whose rebuild-cache flags are **all true** — item icons,
models, model textures, block textures, map geometry, common assets — and the generated
packet carries them. Re-minting one item to change one string with a single player online
froze that client on the loading overlay until its connection read-timed out two minutes
later, **with nothing logged on the server**, because nothing had failed. Pass
`DEFAULT_NO_REBUILD` for any runtime re-mint that does not change art; keep the default
only for boot-time injection of genuinely new ids, whose icons must be generated.

**🚫 And even then it froze again.** Same re-mint with the no-rebuild query and no
duplicate broadcast: the client died a second time, again with nothing on the server, and
the cause was never established. **Treat a runtime re-mint of a store the clients mirror as
unproven.** For a change that is only a name, override the *translation* instead (below) —
that touches no asset store. Boot injection is unaffected: nobody is online, so the store
skips the broadcast path entirely. If you must re-mint at runtime, log on both sides of the
call and get it off the thread handling player events before you believe anything.

**✅ Server-only stores are not in that danger** (jar-verified 2026-08-25). The broadcast is
per store, and there is a one-line test for which ones do it: the store's remove/update
handler opens with
`if (this.packetGenerator == null) return;`. A store registered **without** a packet
generator — a drop table, a loot list, anything the client never mirrors — sends nothing on
a runtime re-register: no rebuild flags, no packet, no broadcast. Check the registry loader
for whether the store you are about to touch has one. This does not make the whole path
proven (the unexplained second freeze was on a mirrored store, so nothing rules it out
elsewhere), but it does separate "the client cannot be affected" from "unknown", and the
first is a much smaller bet.

Whether a live edit is *seen* is a separate question with its own one-line answer: a
consumer that resolves the asset **per use** (looking it up in the asset map each time it
needs it) picks the change up immediately; one that captures a reference at boot never
does.

**Encode from the live asset.** Re-encoding the shipped original silently reverts every
earlier patch, and re-applying a multiplicative patch on top of an already-scaled value
doubles it. Patch only the field that is moving, and compare before you rebuild so a
no-op costs nothing.

**Renaming: override the string, per player.** The second lever —
`UpdateTranslations(UpdateType, Map<String,String>)` — replaces the value behind a
translation key, and touches no asset store, so it is the safe way to rename something at
runtime. Two properties to design around: the payload is plain **text**, not a message key,
so one broadcast imposes one language on every client — build the map per player against
`PlayerRef.getLanguage()`, resolve values through `I18nModule.getMessage(language, key)`
(that argument order), and write to their own `getPacketHandler()`. And the client
**rebuilds its translation table from the pack on join**, so an override must be re-sent on
every login.

## Recipes are assets too — and the two-lock trap

Recipes live in their own injectable store with the same shape as items, so the encode →
patch → decode → load cycle works on them unchanged. **Dismantling/salvage is a recipe**,
keyed by its input, not a field on the item.

If you mint item variants, you must mint their recipes too — and **two independent locks
have to be opened, whose failure modes look nothing alike**:

1. **Recognition is by id alone.** Nothing matches a variant id you invented, so the item
   cannot even be *placed* at the bench. Clone the base's recipe per variant with the input
   re-pointed at the new id.
2. **Consumption compares the whole metadata document.** With the clone in place the item
   goes in, the recipe is recognized — **and the progress bar never moves**, because the
   consumption path builds a stack from the recipe and compares metadata, which any
   per-instance stamp fills. The fix is to give the item a private resource type naming
   itself and match on **that**, the one input form that is both recognized and consumed
   metadata-blind.

> **A dead end worth not repeating:** a *tag* consumes metadata-blind, which makes it look
> like the answer — but the matcher has no tag branch at all, so the item stops being
> recognized entirely and cannot be placed. Read the matcher to the end before choosing an
> input form.

**A private resource type is also the only way a *stamped* item can be a visible, named
ingredient** — the shape a recipe shows the player. Three things follow, and each is a
separate line of work:

- **A resource type is its own asset with its own id**, declared on the item
  (`ResourceTypes: [{ Id, Quantity }]`) and named by the recipe (`ResourceTypeId`). One
  member is a perfectly ordinary use of it; the engine does not require a type to be a
  category.
- **Its label is its own translation key, not the item's.** The client is sent little more
  than the id and the icon, so it resolves the name itself. If you rename items at runtime
  by pushing translations, you must push the type's key in the same packet — otherwise the
  ingredient slot and the item in hand call the same thing two different names.
- **Ship a type for every member of the family, not only the ones your own recipes
  consume.** The type is the only handle by which *anyone else* can name a per-instance
  stamped item of yours in a recipe. One nothing asks for is inert; one added later is an
  id that appears mid-life, which a recipe already shipped against your mod cannot follow.

Note also that a bench requirement's type and id are their own vocabulary and need not match
the bench **item**'s id — read the shipped recipe rather than guessing.

## Cloning a quality/rarity tier

A quality has its own injectable store and is network-serialized to clients on join. **Clone
a shipped one and override only** the id, its numeric value, its localization key and its
label visibility. Cloning reuses the template's textures and color, so a custom tier renders
a **real border with no new art streamed** to clients, and its label resolves from your own
lang file like any translated message.

### ⚠️ Injecting a quality renumbers everyone's saved items (`0.6.0`, verified 2026-08-27)

The quality store is **index-addressed**, and from `0.6.0` an `ItemStack` carries a
per-instance quality **index** that it snapshots at construction, persists to disk and sends
to the client. Those two facts compose into a live-world hazard that nothing warns you about:

- The index is a **position** in a table assembled at boot from the base game's qualities
  plus every loaded mod's. Add, remove or disable **any** mod that ships one quality and
  every entry after it shifts, while every item already saved in the world keeps the old
  number.
- The stack's "unset" sentinel is a red herring — the base constructor overwrites it with the
  item asset's index immediately, so **every** stack carries a hard number, not a fallback.

Symptoms, in the order they confuse you: items render as the **neighboring tier** (right id,
right stats, right drop beam — only the border and label are wrong, because only those read
the stack); a fresh give of the same id looks correct, because it snapshots the current
number; and an item whose old index is now past the end of a **shortened** table crashes the
client outright with an out-of-bounds read when its container is opened.

**Do not store this index as your own state**, and if your mod already writes items into a
persistent world, correct stale ones on the way past — compare `stack.getQualityIndex()`
against `stack.getItem().getQualityIndex()` and rewrite with `withQuality` wherever you
already walk containers. Correct **everything**, not only your own items: the renumbering
moved the base game's indices too.

## Shipping art

Pack layout, and the partition that matters:

- **`Server/**`** — server-only: asset config JSON, languages, recipes.
- **`Common/**`** — shared and **client-loaded**. New art goes here: icons under
  `Common/Icons/Items/<Namespace>/`, models and their textures under
  `Common/Items/<Namespace>/`, animations under `Common/Items/Animations/`.

An asset names its art by a path **relative to `Common/`** (no leading `Common/`, keep the
extension). A plugin shipping brand-new art works end to end — verified in-game, both for
item icons and for a new block model plus texture.

**⚠️ An inventory icon is NOT auto-rendered from the model.** The game's generated icons come
from its own asset-build pipeline, which a plugin cannot trigger. An item that ships a model
(which *does* render in-world and in-hand) but no icon shows a **`?` placeholder** in the
inventory. Point the icon at a real PNG — your own, or, as a stopgap, one of the game's
already-shipping generated icons.

**⚠️ Some asset paths are validated against an enforced root.** At least one category of art
must live under a specific directory or the asset is rejected — and a reflective injection
path **bypasses that validation**, so it silently tolerates a bad path that a validating
loader would refuse. Use the valid path in both cases; a "works for me" that depends on
skipping validation breaks the moment anything revalidates the asset.

## Shipping strings — the `.lang` file, and the two ways to corrupt one

Every player-facing string is a translation key, and the strings behind them ship as a pack
resource like any other: `Server/Languages/<locale>/server.lang`, one `key = value` per
line, `#` comments. Keys are written **without** the `server.` prefix in the file and
referenced **with** it (`Message.translation("server.<key>")`, `%server.<key>` in markup).
Ship every locale you support in the same change and keep their key sets identical — a key
present in one file and missing from another renders as itself for those players, which is
the failure mode nobody sees in their own language.

Two properties of the parser are not guessable, and both were paid for in-game
(`0.5.9`, 2026-08-25):

- **Every value is trimmed.** A leading indent in the file is silently dropped, so a reply
  or tooltip that needs to be indented has to build the indent **in code** and pass it as
  its own message part. Indenting in the file looks right in a diff and arrives flush.
- **A value must stay on one physical line, and a stray newline costs the whole file — not
  its own key.** The parser reads line-by-line as `key = value`; a wrapped value makes the
  continuation an unparseable line, and the keys *after* it stop resolving too. One wrapped
  tooltip took out labels 300 lines above it, which reached the client as raw key names.
  For a paragraph break inside a value, write the **two characters** `\n` twice — the
  escape, not a real break — and keep the value on its line.

  ⚠️ **A shell heredoc is where this happens.** Backslashes do not reliably survive one, so
  a value written that way can arrive as a real newline. Read the line back out of the file
  after writing it.

Prefer whole sentences over assembled fragments: give singular/plural and on/off outcomes
their **own** keys rather than gluing an `"s"` or a trailing clause onto a shared stem —
languages inflect differently, and a fragment cannot be translated without its context.

## Depending on another mod's mechanism without depending on the mod

If a helper mod offers a cleaner way to do something (a declarative patch system, say), the
zero-dependency pattern is: **ship both paths, mutually exclusive, chosen by whether that mod
will actually run.** Three facts make it work:

1. **Discovery order.** A helper that applies patches during its own enable phase may enable
   *after* your late asset hook — so probe the **discovered** plugin set, not the enabled one,
   which is still empty at that point.
2. **Discovered ≠ enabled.** The discovered set still lists a mod the server config disabled.
   Check the enabled flag in the server config too — deferring to a disabled helper means
   **nobody** does the work. (Note the flag is nullable, with a server-wide default behind it.)
3. **A patch system may rebuild and revalidate the whole asset**, discarding anything you did
   natively first — which is why deferring, rather than doing both, is correct.

Related: a **config-disabled mod's classes are not on the classpath**, even though it is still
listed as discovered. So the registry probe is right for depending on another mod's
*behavior*, and a reflective class lookup is right for depending on its *types*.

## `Failed to validate asset!` is a banner over warnings too — read the level, not the header

That line is printed over **any** non-empty validation result, so it appears when nothing
was rejected. What decides the outcome is the level under it: a validator's `fail` throws
`CodecValidationException` and the asset does not load, while its `warn` is collected,
logged once at `WARNING` and cleared — **decoding continues and the asset is stored exactly
as you wrote it**. An engine "should not" is therefore advice you may knowingly ignore, and
a patch that trips one is still live. Two consequences worth using: don't go hunting for a
load failure that never happened, and a validator that only fires on the thing you just
patched is free confirmation your patch landed.

An item's name key is one such warning: it must start with `server.` and exist in the loaded
`en-US` map, and a key nobody shipped costs a log line and the raw key on screen rather than
the item.

## A `SEVERE` "circular dependency … collect all children" is diagnostic only

Re-registering an asset that sits in a `Parent` chain can make the store log, at `SEVERE`,
that it found a circular dependency while collecting all children — followed by the
offending subtree. It reads as a failed injection and is not.

The walk is a DFS over the parent→children index that adds each node to a set. The
"circular" flag is raised when a node is reached **twice on one walk**; it stops that
branch's recursion and is then consumed **only** to log the line and print the tree. Every
child is added to the set unconditionally, so the collected set is complete and nothing
aborts — the assets behind the line are correct.

Two consequences worth knowing:

- **Do not chase it as a build failure.** Read your injection's own result instead — a bulk
  load reports whether it failed, and per-item builds can be counted. If those are clean,
  the line is noise.
- **It is yours to provoke if your pass is the only one re-registering assets**, because the
  check runs on a re-registration. The line appearing inside your window therefore proves
  only that you ran the check, not that you created the repeated node. To find out which,
  **keep the plugin loaded and take the suspect item out of the set you inject** — removing
  the plugin entirely just stops the check from running and answers nothing.

That second point is the general shape: when a diagnostic fires only because you triggered
it, the control has to keep the trigger and vary the input.

## `Skipping pack at <folder>: missing or invalid manifest.json` is not about you

A boot logs a `WARN` per data folder found under the mods directory, and one of them names
yours. The mods directory is **two things at once**: the directory the asset module scans
for packs, and the directory the engine gives every plugin as its data directory. So each
data folder is examined as a candidate pack, has no `manifest.json` in it, and is skipped —
while the plugin's real pack loads a moment later out of the **jar**, usually the very next
line.

The proof that it is nobody's bug: on a plain boot, some of those lines name the **game's
own** core plugins' data folders. Renaming your folder changes nothing, since the trigger is
being a manifest-less directory in there, which is what a data directory is.

⚠️ **Do not silence it by putting a `manifest.json` in your data folder** — that stops the
warning by making the engine load your data directory *as an asset pack*, which is worse
than the line you were trying to remove.

## ⚠️ A missing hard dependency kills the whole server at asset-pack load

Two loaders read the same dependency block and disagree about severity. The plugin loader is
forgiving — it logs and skips the mod, and boot continues. The **asset-pack loader is not**:
it computes a load order over every *discovered* pack, including the one that was just
skipped, and a missing hard dependency throws. **No asset pack loads at all — the game's own
included.**

The symptom never names the cause. What you see is a cascade —  missing default assets for
an unrelated module, a null component type during the shutdown that follows, and every world
failing on a missing default config — with the real line buried hundreds of lines above.

**So: "missing default assets" for something you never touched means the asset store is
empty, and an empty asset store usually means one mod in the folder is missing a hard
dependency.** Search the log for the load-order failure before suspecting your own injection.

It follows that **a mod's hard dependencies are the server owner's problem, not the mod's**:
dropping a jar in without its library does not degrade to "that mod is off", it takes the
server down. That is a strong argument for declaring integrations as **optional**
dependencies only.
