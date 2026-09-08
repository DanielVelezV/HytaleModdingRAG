---
name: hytale-command
description: "Use when adding or changing a command in a Hytale server plugin — a command tree, a subcommand, an argument type, a permission gate, a command reply, or tab-completion. Concrete triggers: 'add a /command', 'add a subcommand', 'make this command admin-only', 'make this command open to everyone', 'the command doesn't autocomplete', 'a player can't run my command but ops can', 'the arg won't suggest values', 'Assert not in thread' from inside a command, 'the command description shows the raw key', replying to the console vs a player, an async job that has to answer after the command returned. SKIP for: what the command's action actually does once it runs (opening a page is the `hytale-ui` skill; storing a value is the mod's own config layer), and permissions granted outside a command tree."
---

# Adding a command to a Hytale server plugin

> **Engine `0.5.9`** (patchline `release`) · last checked 2026-08-23 — facts verified against
> `Server-0.5.9.jar`, with in-game confirmation. **Newer server? Re-verify before trusting a
> signature below**; the permission and argument APIs are what a bump moves first.

**Scope.** The tree, the arguments, the gate and the reply. What the command *does* once it
runs — open a page, write a config value, mutate an entity — belongs to whatever owns that
thing; a command class that grows the logic itself is one you cannot call from anywhere
else.

## The model

A command is a class; a tree is nodes holding subcommands. You build the tree in
constructors and register the root once:

```java
getCommandRegistry().registerCommand(new MyRootCommand(deps));
```

Two things the engine then does **to** your tree, both invisible until a non-op tries it:
it **invents a permission node** for anything you left ungated, and its **autocomplete
prunes** the tree at the first node the caller cannot use. Both are below, and between them
they account for most "my command doesn't work for players" reports.

## The recipe

**One class per node.** A group node's constructor builds its children; a leaf node
declares its args and implements the action.

```java
final class NotifyCommand extends CommandBase {

    private final RequiredArg<Toggle> state;

    NotifyCommand(MyConfig config) {
        super("notify", Text.desc("notify"));            // ← a LANG KEY, not a sentence
        requirePermission(Text.PERMISSION);              // admin-gated; see below
        this.config = config;
        this.state = withRequiredArg("state", Text.arg("notify", "state"),
                Toggle.ARG);                             // your own enum, via ArgTypes.forEnum
    }

    @Override
    protected void executeSync(CommandContext ctx) {
        boolean wanted = ctx.get(this.state).asBoolean();
        …
        ctx.sendMessage(Text.ok("notify.enabled"));
    }
}
```

- `withRequiredArg(name, descriptionKey, type)` → `RequiredArg<T>`;
  `withOptionalArg(...)` → `OptionalArg<T>`, read behind `ctx.provided(arg)`.
- `addSubCommand(child)` builds the tree; `addAliases("mw")` gives the root a short form.
- Reply with `ctx.sendMessage(Message)`.

## Every player-facing string is a translation key — including the descriptions

**Both the command description and every argument description are lang keys, not text**
(jar-verified). `getUsageString` builds the header with `Message.translation(description)`,
and `matches` resolves it through the i18n module to match a command by its description
text. Vanilla confirms the convention — its own commands pass
`server.commands.op.add.desc` and `server.commands.op.add.player.desc`. A literal English
sentence there is an *unresolved key that merely happens to render as itself*, and it will
never translate.

Build the keys through **one text seam class** for the whole tree — key builders for
descriptions and arg descriptions, plus the reply factories and the colors — so a leaf
never spells a key or a color itself.

**The `.lang` file itself has two traps** — every value is trimmed, so indentation has to
be built in code, and a value wrapped onto a second physical line stops the keys *after* it
from resolving. See the `hytale-assets` skill, "Shipping strings".

**The console resolves translations too.** `Message.getAnsiMessage` falls back to the i18n
module in `en-US` and formats the params, so replacing a raw string with a key costs
nothing on the server console.

**⚠️ Lang values are trimmed.** The parser does `.trim()` on every value, so **leading
whitespace in a lang value is silently dropped** — an indented reply line cannot carry its
indent in the lang file; prepend it in code. The parser also rejects an empty key or value,
warns on a duplicate (keeping the first), and un-escapes `\n` / `\t` — so a tab *does*
survive the trim, if you ever want one.

## Permissions — the two rules that actually bite

**⚠️ Omitting `requirePermission` does NOT make a command open.** Registration walks the
whole tree and, for every node whose permission is still null **and** whose
`canGeneratePermission()` returns true (the default), **assigns a generated one**:

- plugin-owned root → `<plugin base permission>.command.<name>`
- any child → `<parent's permission>.<child name>`

It logs `Generated missing permission '<node>'.` at FINE and nothing else. So by the time a
player can type the command, its permission is never null, the real check runs, and your
"ungated" command sits behind a node **nobody holds** — only ops get through. **This is
invisible unless you test as a non-op.**

The fix is to override **`canGeneratePermission()` → `false`** (protected, on the command
base). The permission then stays null through registration and the check short-circuits to
`true` for everyone. Give that its own base class — every genuinely open command extends
it, admin commands keep calling `requirePermission`.

**Permission is checked leaf-only, but autocomplete is not.** At execution the dispatcher
delegates to a subcommand first and checks the permission only on the **terminal** node
that runs — intermediate nodes are not re-checked. But the tree sent to the client for
tab-completion `continue`s past any child the caller fails, **never descending into it**.

> **Net rule: for an open leaf to be discoverable, every ancestor must be open too.** A
> gated group erases its whole subtree from a non-op's suggestions; an open leaf under it
> still executes when typed blind, but nothing will ever offer it.

So the shape that works: **a group holding an open leaf is itself never gated** — the gate
sits on the individual leaves. A group holding nothing open stays gated as a whole and
vanishes wholesale, which is what you want.

**Branch a reply by audience without gating the command.** `CommandSender` extends the
permission holder interface, so `ctx.sender().hasPermission("<mod>.admin")` picks a fuller
usage listing for an admin and a shorter one for everyone else. `ConsoleSender.hasPermission`
is a hard-coded `true`, so the console always takes the admin branch. (Not to be confused
with `AbstractCommand.hasPermission(sender)`, which tests *that command's* node and returns
`true` when it has none.)

A reasonable gating rule to state once and apply mechanically: **a command is gated iff it
reads or writes server configuration** — including the read-only listings, which expose
config state. Anything that only tells players about themselves, or hands them their own
property, is open.

## More than one node per command

Sooner or later somebody asks for a node narrower than "admin" — one command handed to a
builder, or read-only access to the settings. Three engine facts shape the answer:

**The matcher expands the *queried* node into wildcard prefixes.** For `a.b.c` it looks in
the holder's set for `-*`, `-a.b.c`, `a.b.c`, then the negated prefixes `-a.b.c.*` /
`-a.b.*` / `-a.*` deepest-first, then `*`, then the positive `a.*` / `a.b.*` / `a.b.c.*`;
no hit means "no opinion" and the lookup continues into the holder's groups, the virtual
groups, and the group parent chain. So `<mod>.*` grants your whole namespace for free —
do **not** hand-roll a `<mod>.command.*` check, the engine already does it. And holding
the literal `<mod>.admin` does **not** grant `<mod>.admin.anything`: an exact node is not
a prefix. Nesting a finer node under your umbrella node buys nothing; make it a sibling.

**The chain is an `and`, and a gate is one node.** `hasPermission` returns true for a null
permission; otherwise it tests its own node and, on success, recurses into the parent
(short-circuiting only if the command has `permissionGroups`). So `requirePermission`
cannot say "this node **or** admin", and a leaf can never outrun its ancestors.

**So express a multi-node gate by overriding `hasPermission(CommandSender)` itself.** It is
the method the dispatcher calls on the terminal command *and* the method the tree builder
calls on every node — one override gates execution and autocomplete together. Put it in a
base class holding a node list, return true if the sender holds any of them, and set
`canGeneratePermission()` false so registration cannot invent a node the override ignores.

> **Give a group every node its leaves accept.** The pruning rule above is unforgiving: a
> group gated more narrowly than a leaf under it erases that leaf from the suggestions of
> somebody who can run it.

Always include the umbrella node in every list. Shipping finer nodes without it silently
takes commands away from every server that had already granted the umbrella.

**Advertise your nodes with `PermissionsModule.registerPermission(node)`** (the no-groups
overload) so a server owner can find them. It grants nobody anything. The *groups* overload
is a different tool: it places the node in a virtual group. Never pass a group for a node
you mean to gate on.

**You can grant and revoke at runtime too.** `PermissionsModule.addUserPermission(uuid,
Set)` / `removeUserPermission(...)` are public and are what `/perm user add|remove` call:
the write goes to the first provider, persists synchronously, and dispatches a permission
change event. Two things to get right if you build a screen on it. **Effective is not
granted**: `hasPermission` sees `*`, OP, wildcards and groups, while a revoke only lifts
what that *user* holds explicitly (`provider.getUserPermissions(uuid)`) — so a single
checkbox off `hasPermission` shows an op as ticked and then does nothing. Report the
difference, and offer a button only where one would work. And check
`areProvidersTampered()`: another mod may own permissions on that server, and your write
is then not the last word.

**There are two ways to ship a command open, and they are not equivalent.**
`setPermissionGroup(GameMode.Adventure)` puts the command's generated node in
`hytale:Adventurer`, which is the group every user with no explicit entry is in
(`DEFAULT_GROUP_LIST`), so it reaches everyone *and* leaves a real node an owner can
revoke or move. Overriding `canGeneratePermission()` to `false` leaves the permission null,
so the command is open unconditionally and there is nothing to revoke. Pick the first when
an owner should be able to take the command away, the second when nothing should ever be
able to. (`permissionGroups` also short-circuits the ancestor walk at execution — but not
the autocomplete pruning, which still calls `hasPermission` on every node.)

## Arguments

**Addressing a player who might be offline — and the trap in the obvious fix.** The
player-argument type resolves **online** players only, which silently makes the command
unable to name anyone who logged out. There is usually a UUID-yielding type beside it that
parses a raw UUID *and* falls back to an online name, so it looks like one argument can
serve both. Check what it **suggests** before swapping: parsing and suggesting are separate
methods, and a type that accepts names may still offer none — in which case the swap costs
your online case its tab-completion and nothing errors to tell you. A name is what a user
types and a UUID is what they paste; when the two disagree, that is **two commands**
sharing one implementation, not one clever argument.

And a UUID is not a name: if the command displays or stores one, you need a lookup of your
own (the saved player file is the usual place) and a decision for when there is none.
Writing the UUID where a name belongs is the answer that looks like it works and is wrong
the moment anyone reads it.

**Prefer a typed arg over `ArgTypes.STRING`, which gives no tab suggestions at all.**

- **Fixed value set** → `ArgTypes.forEnum("<lang.key>", MyEnum.class)`, a
  `SingleArgumentType<MyEnum>` that parses and suggests each constant's **lower-cased
  `name()`** (`XP_BASE` → `xp_base`). The idiom worth copying: an enum whose constants
  *carry the binding* — the getter/setter or value each one names — so the command body is
  a lookup rather than a switch. An on/off toggle is one of these, not free text.
- **Item ids** → `ArgTypes.ITEM_ASSET` resolves real ids and autocompletes.
- **A value set no enum can express** (ids registered at runtime) → subclass
  `SingleArgumentType<T>` directly. Super ctor is
  `SingleArgumentType(String nameLangKey, String usageString, String... examples)`;
  override `parse(String, ParseResult)` (call `result.fail(Message)` and return `null` to
  reject) and `suggest(CommandSender, String, int, SuggestionResult)` (for each live
  candidate whose lower-cased form `startsWith` the input, `result.suggest(candidate)`).
  Read the candidate list **live inside both**, or the suggestions freeze at registration.
- **A destructive or wide-reaching action** takes an optional boolean flag and refuses
  without it — `[--confirmation=true]`, read as `ctx.get(flag)` and compared explicitly,
  since an absent optional is `null`.
- Accepting a **literal alongside a value set** (a `default` keyword that restores the
  shipped value) means the arg is a `String` after all — that is the one case where losing
  the suggestions is the right trade, and it is worth a comment saying so.

## Threading, and answering late

**⚠️ A command runs on a `ForkJoinPool` worker, and every `Store` method asserts it is on
the owning world thread** (`Store.assertThread` → `IllegalStateException: Assert not in
thread`). Even a *read* throws — this is thread affinity, distinct from the "store is
currently processing" structural-write assert. Resolve the world and defer onto it; `World`
implements `Executor`:

```java
PlayerRef player = ctx.senderAs(PlayerRef.class);       // guard with ctx.isPlayer() first
World world = Universe.get().getWorld(player.getWorldUuid());   // null if unloaded
Ref<EntityStore> ref = player.getReference();
world.execute(() -> {                                   // now on the world thread
    var data = read(ref.getStore(), ref);
    ctx.sendMessage(...);                               // replying from the hop is fine
});
```

`CommandContext` gives you `isPlayer()`, `sender()`, `senderAs(Class)` and
`senderAsPlayerRef()`. The player sender **is** a `PlayerRef` (it implements
`CommandSender`), which is where `getReference()` and `getWorldUuid()` come from. The
console has no entity — guard first.

**For work that finishes after the command returned** (a scan, a future, anything async),
**capture `ctx.sender()`, not the context**, and reply through it — the context is done.
Reply twice: once on start, once with the result. Wrap the late send so a player who logged
off in the meantime is a log line, not an exception.
