---
name: hytale-config
description: "Use when a Hytale server plugin needs to own a file on disk — a settings/config file, an index, any state the mod writes itself — or when deciding WHERE that file goes. Concrete triggers: 'add a config option', 'where should this file live', 'the config disappeared / was not found', 'my data directory is null', `getDataDirectory()`, `withConfig`, 'move the config somewhere else', 'the setting didn't take effect', 'restore defaults', 'a server owner edited the file by hand', reading or writing JSON the mod owns. SKIP for: the engine's codec/serialization machinery itself (that is `hytale-codec`), the command or page that edits a value (`hytale-command`, `hytale-ui`), and game asset JSON shipped in the pack, which the plugin reads but does not own."

---

# A plugin's own files and settings

> **Engine `0.5.9`** (patchline `release`) · last checked 2026-08-20 — facts verified against
> `Server-0.5.9.jar` and a real install. **Newer server? Re-verify before trusting anything
> below**; the directory layout is what a bump moves first, and it moves silently.

**Scope.** Where a file the plugin owns lives, and the lifecycle of the settings inside it.
The *serialization* is a separate subject (`hytale-codec`), and so is whatever edits a value
— a command or a page is one caller among several, which is exactly why validation belongs
on the setter and not in the caller.

## The three directories, and why they are not the same one

| What | Where | Notes |
|---|---|---|
| **Server working directory** | `System.getProperty("user.dir")` | Holds the server's own `config.json`, `permissions.json`, `universe/`, `logs/`, `mods/`. For a **client-hosted world this is the save folder**; under a Gradle `runServer` it is the project's `run/`. |
| **Plugin data directory** | `<server dir>/mods/<Group>_<Name>/` | What `PluginBase.getDataDirectory()` returns. **Per world/server, not per install.** |
| **Where the jars are** | the launcher's own `Mods/` tree | A different tree entirely. |

**The consequence worth internalising: deleting a mod's jar never removes its data**, and
copying a world carries every mod's settings with it. Both follow from the jar and the data
living in unrelated trees.

### `getDataDirectory()` — how it is built, and the one trap

```java
Path dataDirectory = PluginManager.MODS_PATH.resolve(manifest.getGroup() + "_" + manifest.getName());
```

- `MODS_PATH` is `Path.of("mods")` — **relative**, so it resolves against `user.dir` at
  use. **Call `toAbsolutePath()` before `getParent()`, or you get `null`.**
- The separator is `_`: group `Acme` + name `Widget` ⇒ `mods/Acme_Widget`.
- Available from the plugin's **constructor** onwards, so `setup()` can use it.
- **Not created eagerly** — the engine only hands over the path; the folder appears when
  the plugin first writes. On a real install, 29 mods loaded and 4 folders existed.

### `withConfig` — the engine's own helper

```java
protected final <T> Config<T> withConfig(BuilderCodec<T> codec);              // name = "config"
protected final <T> Config<T> withConfig(String name, BuilderCodec<T> codec);
```

- **Constructor-time only** — it throws `IllegalStateException("Must be called before
  setup")` once the plugin has left state `NONE`.
- `Config<T>` gives `load()` → `CompletableFuture<T>`, `get()`, `save()`, and the engine
  drives the load. It writes **into the plugin's data directory**; there is no per-mod
  `config/` notion anywhere in the engine.

**Use it when** the config is a value object that maps cleanly onto a codec and is read
whole. **Hand-roll instead when** the config has live setters, per-key validation, defaults
that must survive an unknown or superseded key, and an editor UI — a codec round-trip fights
all four. Hand-rolled means parsing the JSON yourself (BSON's document parser is already on
the classpath and reads/writes plain JSON fine).

## Choosing the folder

The engine's `<Group>_<Name>` is a layout, not a requirement — sibling mods in the wild use
a hand-picked folder name in the same place, and both the game's own plugins and third-party
ones keep config *and* state in one folder. A plain mod name reads better to the server owner
than `Author_Mod`.

**But derive the location from `getDataDirectory()` even when you rename it** — take its
**parent** and resolve your own name beside it:

```java
Path dir = plugin.getDataDirectory().toAbsolutePath().getParent().resolve("Widget");
```

That keeps `mods/` an engine fact you *read* rather than a path you hardcode, so a layout
change upstream moves you with it. Keep a literal `"mods"` only as the fallback for a null
parent.

## Moving a file after you have shipped

Changing your mind about where a file lives is a **migration**, not an edit — someone has
the old one. Put it behind one seam that every file goes through:

```java
Path resolve(Path directory, String fileName, Path... legacyLocations)
```

- Target exists → use it, done.
- Otherwise, first legacy path that exists → `createDirectories` + `Files.move`, **announce
  it at a log level the owner sees** (this is their data moving), then delete the vacated
  folder **only if it is now empty**.
- **The move failed → keep reading the old location for this run** and warn. A failed
  migration must degrade to "still works", never to "config reset to defaults".
- Nothing exists → return the target and let the caller write a fresh file.

Keep the old paths listed even long after the move: the machine that has not been updated is
exactly the one that needs it.

## Loading

**Defaults first, then overlay.** Build the whole config from constants, then apply whatever
the file actually contains, key by key. That way a truncated, partial, or hand-edited file
degrades to defaults on the missing keys instead of failing to load — and a config written
by an older version simply keeps the new defaults for keys it never had.

- **A superseded key is read once, with a warning, not silently ignored** — when a setting
  splits into three, honor the old value as the default for all three and say so in the
  log. Silently ignoring it means the owner's deliberate setting evaporates on upgrade.
- **An unparseable enum or a malformed value keeps the default and warns.** Never read it as
  the "off"/zero value: an owner's typo would then silently disable a feature.
- **Clamp and refuse in the setter, not in the caller.** A config with a command *and* a UI
  *and* a load path has three callers; validation in any one of them is validation missing
  from the other two. The setter returns what it stored (or a refusal reason) and every
  caller reports that.

**Keep the shipped defaults as named constants**, not as literals inside the loader. It is
the only way to offer "restore this one setting" later, and it makes a diff of *what this
server changed* computable — which an editor UI will want.

## Every setting must answer two questions

**When does it apply?** In a Hytale plugin this is rarely "immediately", and the difference
is not cosmetic:

- **Boot-applied** — anything that feeds asset injection or registration at startup. Changing
  it does nothing until a restart.
- **Live** — read per use, so the next craft/tick/command sees it.
- **Live, but only for new things** — the value is baked into something at creation time
  (per-instance item metadata is the classic case), so a change never repaints what already
  exists.

Say which, **in the reply that confirms the edit and next to the control that made it**. A
setting that appears to do nothing is the single most common config bug report, and it is
almost always this.

**Who may change it?** A server-wide setting and a per-player preference are different
storage: the first is this file, the second belongs on the player's own persisted data. When
both exist for one feature (a server switch and a personal opt-out), decide and document
which one wins — and prefer dropping a control that currently changes nothing over showing
it with an explanation.

## Saving

Write the **whole document**, from the in-memory state, on an explicit `save()`. Batching
several edits into one write is the caller's job (an editor UI stages a draft; a command
saves per edit).

Be aware that a hand-rolled save **reformats and reorders** the owner's file, and drops any
key it does not know — so an unrecognized key is a decision: preserve it, or warn that it
will be lost. Comments do not survive JSON at all, which is an argument for keeping the
documentation in the UI and the reply text rather than in the file.
