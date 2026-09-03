---
name: hytale-plugin
description: "Use when setting up, building or booting a Hytale server plugin itself — the plugin class and its lifecycle, `manifest.json`, the Gradle wiring, or the dev server. Concrete triggers: 'start a new Hytale plugin', 'where do I register X', 'what goes in setup vs start', 'my plugin didn't load', 'Failed to setup plugin', 'the plugin is listed but does nothing', `PluginBase` / `JavaPlugin` / `PluginState`, `manifest.json` fields, `Dependencies` vs `OptionalDependencies`, `IncludesAssetPack`, `compileOnly` vs `runtimeOnly`, shadowJar, `runServer`, `HYTALE_HOME`, 'how do I test this in-game', 'give myself an item with metadata to test', 'preview a drop table', 'bump the plugin version', 'which version does the server actually read', 'how do I read my own version at runtime', `Semver`, 'my log line printed as mojibake'. SKIP for: what a registered thing then does — systems and hooks (`hytale-ecs`), commands (`hytale-command`), pages (`hytale-ui`), asset injection (`hytale-assets`), and the plugin's own data files (`hytale-config`)."
---

# The plugin itself: lifecycle, manifest, build

> **Engine `0.5.9`** (patchline `release`) · last checked 2026-08-20 — facts verified against
> `Server-0.5.9.jar` (`PluginBase`, `PluginState`, `PluginManager`, `PluginManifest`/`Semver`
> disassembled; the version conventions measured over an installed mod folder). **Newer
> server? Re-verify before trusting anything below**; lifecycle phases and manifest keys are
> what a bump moves first.

**This is the floor every other skill stands on** — where registration is legal, what the
manifest declares, and how the thing gets built and booted. What you register *does* is each
feature skill's subject.

## The lifecycle

```
NONE ──setup()──▶ SETUP ──start()──▶ START ──▶ ENABLED ──shutdown()──▶ SHUTDOWN ──▶ DISABLED
   ▲                  │                  │
   └── DISABLED ───────┘        any throw ─┴─▶ FAILED
```

Three overridable hooks — `setup()`, `start()`, `shutdown()` — each wrapped by a `final`
engine method that owns the state transition. Call `super` when you override.

**⚠️ `setup()` and `start()` are different phases, and the difference is other plugins.**
The manager runs **every** plugin's setup phase, in dependency load order, and only then
begins the start phase.

| Hook | Runs | Put here |
|---|---|---|
| **constructor** | at load, before anything | `withConfig` (it is refused later), caching the instance |
| **`setup()`** | all plugins, before any `start()` | **your own declarations**: components, event types, systems, commands, asset hooks, reading your config |
| **`start()`** | after every plugin has set up | anything that must **see another plugin's registrations** |
| **`shutdown()`** | on server stop | flushing anything you own |

If you are reaching for another mod's registry and finding it empty, you are in the wrong
phase — that is what `start()` is for. (A mod that applies its work even later, during its
own enable, is a different problem; see `hytale-assets` → depending on a helper mod.)

Note `setup0` accepts state `NONE` **or `DISABLED`**, so a disabled plugin can be set up
again — do not assume `setup()` runs exactly once per process.

**⚠️ A throw in `setup()` or `start()` does not stop the server.** The engine catches
`Throwable`, logs one `SEVERE` line — `Failed to setup plugin %s` / `Failed to start %s` —
parks the plugin in **`FAILED`**, and keeps booting. So the failure mode of a broken plugin
is *the server runs and your mod silently does nothing*. Two consequences:

- **Never swallow an exception in `setup()`** to "keep going" — you will produce exactly the
  same invisible half-initialized state, minus the one log line that would have explained it.
- When a mod "isn't doing anything", grep the boot log for `Failed to setup` before
  debugging the feature.

## What `PluginBase` gives you

Registries, all off the plugin instance — this is the map of where registration happens:

| Registry | For |
|---|---|
| `getEntityStoreRegistry()` | components, entity event types, systems (`hytale-ecs`) |
| `getChunkStoreRegistry()` | the same, for chunk-store components |
| `getCommandRegistry()` | command trees (`hytale-command`) |
| `getEventRegistry()` | event-bus listeners, incl. the asset-load hook (`hytale-assets`) |
| `getAssetRegistry()` / `getCodecRegistry(…)` | asset and codec map registration |
| `getEntityRegistry()`, `getTaskRegistry()`, `getClientFeatureRegistry()` | entities, scheduled tasks, client features |

Plus `getManifest()`, `getIdentifier()`, `getName()`, `getLogger()`, `getDataDirectory()`,
`getState()`, `isEnabled()` / `isDisabled()`, and — on the Java plugin subclass —
`getFile()` (the jar) and `getClassLoader()`.

**`getBasePermission()` is `final`** and is the root the engine mints command permission
nodes from. You do not choose it per command; see `hytale-command` for what that implies.

**`withConfig` is constructor-time only.** The engine collects the configs you declare and
loads them all in a `preLoad()` pass that returns one combined future — which is why the
call is refused once the plugin has left state `NONE`, and why a config declared later would
never be loaded.

## `manifest.json`

Ships in the jar root of resources. The fields that matter:

```json
{
  "Group": "Acme",  "Name": "Widget",  "Version": "0.1.0",
  "Main": "com.acme.widget.Widget",
  "ServerVersion": "^0.5",
  "Dependencies": {},
  "OptionalDependencies": { "Vendor:OtherMod": "*" },
  "DisabledByDefault": false,
  "IncludesAssetPack": true
}
```

- **`Group` + `Name` are the plugin's identity** — they form its identifier, its data
  directory (`mods/<Group>_<Name>`), and the key a server config uses to disable it. Renaming
  either orphans existing data.
- **`Main`** is the entry class extending the Java plugin base.
- **`ServerVersion`** is a semver *range*, not a pin.
- **`Version`** is the plugin's own, parsed as a `Semver` — and the only one the engine reads.
  See *Your own version* under **Build wiring**: the build carries a second number that names
  the jar, and nothing keeps the two in step.
- **⚠️ `Dependencies` are hard, and a missing one takes the whole server down** at asset-pack
  load — not just your mod. Declare integrations as **`OptionalDependencies`** and bind to
  them reflectively. Full symptom trail in `hytale-assets`.
- **`IncludesAssetPack`** must be true if the jar ships anything under the pack roots.

## Build wiring

- **`compileOnly` the server artifact** from the Hytale maven repo for your patchline, and
  **`runtimeOnly` the local install's server jar**. Compiling against the published artifact
  while running against the installed one is what keeps a plugin honest about the API surface.
- **Toolchain:** the game is *built* on Java 21 but *runs* on Java 25 — set the toolchain to
  what the server actually runs.
- **`shadowJar`** for anything you bundle; merge service files if you ship any.
- **Never bundle the engine** — and `compileOnly` on the published artifact is **not enough
  on its own** to prevent it. See below.

#### ⚠️ `runtimeOnly` + `shadowJar` puts the whole engine inside your mod

The two rules above compose into a trap. Declaring the installed server jar as
`runtimeOnly(files("…/HytaleServer.jar"))` puts it on **`runtimeClasspath`**, and `shadowJar`
shades `runtimeClasspath` by default — so the fat jar swallows the engine. Measured on a real
plugin (2026-08-24): **118 MB and 9022 engine classes** in a mod whose own code is 636 KB. It
runs perfectly, which is exactly why it survives a whole test campaign unnoticed.

The declaration usually buys nothing anyway: a dev-server task sets its own
`classpath = files(serverJar)`, so the engine is on the *server's* classpath because the
server is the thing being launched — not because your build declared a dependency on it. Drop
it and nothing changes but the size.

**Check it, don't assume it:**

```bash
unzip -l build/libs/<your>.jar | grep -c 'com/hypixel/hytale/'   # must be 0
```

- **Produce one mod jar, not two.** `shadowJar` with `archiveClassifier = ''` and the plain
  `jar` task disabled leaves exactly one artifact, so what you deploy to the dev server and
  what you publish cannot drift apart — and a release step cannot pick the wrong file. Ask for
  it **by name** when publishing; a glob will happily upload your `-sources` and `-javadoc`
  jars beside it and leave a user choosing between three files.

### Your own version: two numbers, and the engine reads one

A plugin carries its version **twice** — in `manifest.json` and as the build's `version`
property — and **nothing links them**. Neither is validated against the other, so they drift
silently and the drift is invisible until someone needs to know which build they are running.

- **`manifest.json`'s `Version` is the only one the engine reads.** It is parsed into a
  `Semver` (`getMajor/getMinor/getPatch`, `getPreRelease`, `compareTo`, `satisfies`,
  `fromString`) and is what another plugin's dependency range is matched against, and what
  `getManifest().getVersion()` returns at runtime.
- **The build's `version` names the jar file** (and your maven coordinates, if you publish).
  The engine never reads it.

⚠️ **The engine never prints a plugin's version.** The plugin manager logs
`Group:Name from path <jar file name>` and nothing else — so the **jar file name is the only
place a version appears in a boot log or a pasted bug report**. That is what makes the build
number load-bearing despite the engine ignoring it: name the jar without a version and no
log, anywhere, can say which build a server is running.

Measured over an installed mod folder (36 mods, 2026-08-24): the convention is
`<Name>-<version>.jar` and it is near-universal — one mod shipped with no version in the name
at all, and its line in the boot log names no build. A shadow classifier (`-all`) rides along
fine, and prerelease/calendar forms (`2026.5.27-26724`) parse as semver.

**So pick one source of truth and derive the other.** Two directions, both fine:

1. **Manifest as the source** — have the build read it, so the jar name follows the file the
   engine actually reads:
   ```gradle
   version = new groovy.json.JsonSlurper()
       .parse(file('src/main/resources/manifest.json')).Version
   ```
   The manifest stays valid JSON, there is no templating, and one edit moves both.
2. **Build as the source** — template the manifest at `processResources`:
   ```gradle
   processResources {
     filesMatching('manifest.json') { expand(version: project.version) }   // scoped!
   }
   ```

⚠️ **Never call `expand()` unscoped in `processResources`.** It runs a Groovy template engine
over *every* resource it copies, and a pack's markup and asset JSON are full of `$` — macro
references and the like. An unscoped `expand` eats them or fails the build on the first one.
Always `filesMatching` the single file you mean.

**And print your version yourself at setup**, off `getManifest().getVersion()` rather than a
constant — the engine will not, and a constant is the thing that drifts.

### Bumping the engine version

The version property picks the **compile** artifact only; the **runtime** jar is whatever the
launcher installed. The two can disagree in silence — compile against a newer API than the
installed server and the call links fine, then throws `NoSuchMethodError` in-game. Read the
installed build from that jar's `META-INF/MANIFEST.MF` (`Implementation-Version`); the install
folder is always named `latest` and tells you nothing.

Then measure what actually moved, instead of re-deriving your whole API reference:

1. **Class inventory** — `unzip -l` both jars, `comm` the sorted entry names: what was added
   or removed.
2. **The public API you import** — collect your `import` lines, `javap -cp <jar>` them under
   each version, `diff`. Empty ⇒ every signature in your notes still holds.
3. **Compile under `-Xlint:deprecation` on both** and compare the warning sets. A symbol that
   became deprecated is the next bump's removal.

An empty diff is the cheap license to move your version stamps; a non-empty one names the
files to re-verify rather than all of them. Note that two jars carrying the same version
number can still differ — the published artifact and the installed build are built separately
— so compare artifacts, never version strings.

### The dev server

The recipe that works, and each part earns its place:

- Resolve the install root from an explicit property, else `HYTALE_HOME`, else the
  per-OS default. **Make every server task conditional on that path existing**, so a checkout
  without the game installed still builds.
- **Drop the built jar into `<runDir>/mods/` rather than passing a mods flag** — the server
  scans that directory relative to its working dir. Delete only *your* stale jars from it, so
  other mods you dropped in for compat testing survive a rebuild.
- Exec the server's main class with the local server jar on the classpath, working directory
  = your run dir, and pass the install's `Assets.zip`.
- **Forward `System.in`** or the server console will not accept typed commands.
- `--allow-op` grants yourself the generated permission nodes locally. **Which is exactly why
  you must also test as a non-op** — see below.

## Verifying: a green build proves almost nothing

This is the single most important habit on this stack. The compiler checks Java and nothing
else. Every one of these fails **only at runtime, in-game**:

- an asset or `.ui` path that does not resolve — nothing in the build checks one;
- markup errors, which surface as a client-side overlay when the page is opened;
- a selector that matches no element, which **crashes the client**;
- a permission node that silently gates a command to ops — **invisible while you test with
  `--allow-op`**;
- an event type you forgot to register, which dispatches to nobody in silence.

So: open the page, run the command as a **non-op**, craft the item. And when something does
not happen, check the boot log for a `SEVERE` before assuming the logic is wrong.

### Two engine commands that save you building content to test with

*(verified against `Server-0.5.9.jar`, 2026-08-25 — `GiveCommand`, `DroplistCommand`)*

- **`/give <item> [quantity] [durability] [metadata]`** takes a **`metadata`** argument
  parsed with **`org.bson.BsonDocument.parse`**, and the parsed document goes onto the
  stack it hands over. That makes it the fastest way to produce an instance carrying
  *arbitrary per-instance metadata* — the thing you would otherwise need a craft, a drop or
  a purpose-built debug command to obtain:

  ```
  /give Some_Item --metadata={"MyMod.Tier":"GOLD"}
  ```

  `durability` and `metadata` are both `OptionalArg`s, so pass the **flag form**; relying
  on position binds your JSON to `durability`. Bad JSON answers
  `server.commands.give.invalidMetadata`, which is how you tell a parse failure from a
  key your own code did not read.

  ⚠️ The same fact is a design constraint: **per-instance metadata is not a trust
  boundary.** Anyone who can run `/give` can forge any key your mod reads, so never gate
  something that matters on metadata alone.

- **`/droplist <droplistId> [count]`** (permission **`hytale:WorldEditor`**) rolls a drop
  table `count` times, merges the results and **prints** them. It **spawns nothing and
  touches no inventory** — it is a preview, not a spawn, so it answers "did my table load
  and what does it yield" without killing anything, but it cannot show you an entry's
  `Metadata` and it will not exercise code that reacts to an item arriving somewhere.

## ⚠️ The server console is not UTF-8 — keep log and boot-failure strings ASCII

Anything the plugin prints to the console — log lines, and in particular the reason string
handed to the boot-abort path — must stay inside plain ASCII. Non-ASCII arrives as
mojibake (in-game, `0.5.9`, 2026-08-25), and it does so on the one message that has least
room to be unreadable: a boot-failure reason is the last thing an operator sees before the
server stops.

Use a comma, a colon or a period in place of a dash, straight quotes, no arrows and no
accented characters. This is a *different* rule from the one governing player-facing text,
which is translated and rendered by the client — accents and dashes are fine there, and
the console rule reaches strings that never pass through a lang file at all. Java comments
are unaffected; they are never printed.

## Two engine-specific code rules

- **Do not `== null`-guard an engine getter annotated `@Nonnull`** — it is dead code and
  tooling flags it as always-false.
- **Prefer the native replacement for any `@Deprecated(forRemoval = true)` symbol.** It
  almost always already exists in the jar, and the `forRemoval` path disappears on the next
  engine bump. Never blanket-suppress the warning.
