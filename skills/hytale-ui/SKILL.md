---
name: hytale-ui
description: "Use when building or changing a native custom UI page in a Hytale server plugin — anything involving `.ui` markup, `CustomUIPage` / `InteractiveCustomUIPage`, `UICommandBuilder` / `UIEventBuilder`, `PageManager.openCustomPage`, selectors like `#Id.Property`, event bindings, widgets (fields, dropdowns, item slots, tabs, progress bars, tooltips), or `Common.ui` `$C.@` macros. Concrete triggers: 'add a page/tab/screen', 'add a row/button/checkbox to the page', 'the page won't open', 'Custom UI — Markup Error', 'the page froze on Loading…', 'the client crashed with Selected element … was not found', 'my checkbox does nothing', 'the text field loses what I type', 'the page keeps the old language after I change it', 'the UI only translates after a reconnect', 'Failed to load CustomUI documents', 'players cannot join since I edited a .ui', 'a label came out blank', styling or laying out anything a player sees on a page. SKIP for: where a value is stored or validated (that is the mod's own config/persistence layer), command trees and permissions (a command is only one way to open a page), and pushing to the client outside a page — toasts, HUD overlays, chat broadcasts."
---

# Building a native Hytale UI page

> **Engine `0.5.9`** (patchline `release`) · last checked 2026-08-20 — facts verified against
> `Server-0.5.9.jar` plus the shipped `.ui` assets in the client `Assets.zip`, with in-game
> confirmation through 2026-08. **Newer server? Re-verify before trusting anything below** —
> especially texture paths and widget property names, which a bump moves first and silently.
> Two things here *were* re-derived on engine `0.6.1`, in-game 2026-09-05, and carry that
> date inline: **step 7's rule that all text is pushed**, and the **unbound `@Text`** trap.

**Scope.** This is how a page is *built, drawn and wired*. A page renders values and
reports gestures; where those values come from and where an edit is written — a config
file, a component, a database — is the caller's business and deliberately not here. Keep
that seam clean: a view class that reads a store directly is a page you cannot reuse.

## The model: the server drives a client-side markup tree

The server does **not** describe widgets in Java. Widgets live in **`.ui` markup files
shipped to the client**; the server sends *mutations* against that tree, addressed by
CSS-like selectors. Building a page is therefore: ship the layout as `.ui`, then push
values and event bindings into named elements from Java.

Three consequences that shape every decision:

- **Layout is markup, so Java cannot change it.** No `set` reaches a `Width`, a
  `LayoutMode` or a padding. A row that needs different geometry is a **different file**.
- **The shipped `.ui` files cannot be extended, only copied.** `append` adds a child
  *inside* an element; there is no way to add a sibling into a file the game ships. A
  config-style row that needs anything beyond the shipped `#Label` + `#Input` is your own
  file with the shipped body copied verbatim.
- **A wrong path or selector fails only in-game.** Nothing in the build checks either, so
  a page is re-verified by opening it, never by a green build.

`.ui` assets live under `Common/UI/Custom/` inside the pack, and every path the server
passes is relative to that root. **Ship yours under `Pages/<ModName>/`** — the flat
`Pages/` directory is the game's own, and two mods picking one file name collide.

## The recipe

**1. Write the markup.** A shell file (overlay + frame + the containers Java fills) plus
one file per repeated row. Import the design system and build from its macros:

```
$C = "../../Common.ui";      // RELATIVE — a file one folder deeper gains a "../"
```

**An appended file's root must be a plain element, never a macro instance.** A file whose
root is `$C.@SomePanel { Label #X {…} }` appends the panel and the label as *siblings*
into the parent, silently mangling the layout. Wrap the macro in a `Group` root; style
macros declared at the top of the file are fine, it is the root element that must be plain.

**2. Subclass `InteractiveCustomUIPage<T>`** (`BasicCustomUIPage` only for read-only
pages). `T` is a plain class with a public no-arg constructor and a `BuilderCodec`, keys
PascalCase matching the `EventData` keys the bindings carry.

```java
public static final BuilderCodec<MyPageEvent> CODEC = BuilderCodec
        .builder(MyPageEvent.class, MyPageEvent::new)
        .append(new KeyedCodec<>("Tab", Codec.STRING), MyPageEvent::setTab, MyPageEvent::getTab)
        .add().build();
```

**Every field must be boxed and nullable** — the client sends only the keys bound to the
element that fired, so one class serves the whole page and each event fills a subset. A
primitive `boolean` would read every other event as `false`. A handler whose expected key
is absent must bail, not substitute a default. `T` may not be `String` (it collides with
the raw `handleDataEvent` overload).

**3. Open it.** The manager hangs off the **`Player` component**, not off `PlayerRef` —
you need both, and the store asserts its thread, so hop to the world thread first:

```java
Player    player = store.getComponent(ref, Player.getComponentType());
PlayerRef pRef   = store.getComponent(ref, PlayerRef.getComponentType());
player.getPageManager().openCustomPage(ref, store, new MyPage(pRef));
```

Page identity is `page.getClass().getName()`. `CustomPageLifetime` is
`CantClose | CanDismiss | CanDismissOrCloseThroughInteraction`.

**4. `build(ref, cmd, evt, store)`** — append the shell, then push values and register
bindings. A batch is applied **in order**, so it can edit what it just created: append the
shell and `remove("#AdminTab")` in the same builder and that subtree never reaches the
client. That is how a page gates a section by permission — removed at build time, not
shipped `Visible: false`.

**5. Handle the event** — decode, act, and answer with **at most one** `sendUpdate`.

**6. Push a partial update** with `sendUpdate(cmd, evt, false)`. The boolean is *clear*;
`false` keeps the existing tree and every binding already registered on it, so a handler
that swaps one sub-tree need not re-add the rest.

**7. Text is always a translation key, and it is always *pushed*.** Use
`set("#X.TextSpans", message)` from Java for **every** string a player reads, fixed labels
included — `.TextSpans`, never `.Text`, which is the static markup form.

**Do not write `%key` into markup.** It looks like the cheaper path and costs the page its
translations: a `%key` is resolved by the client **once**, when it first reads that file,
and nothing re-resolves it against the table it is handed afterwards. A player who changes
language mid-session then reads every markup-authored label in the language they left,
while everything the server pushes as a `Message` switches immediately — and only a
**reconnect** puts the two back in agreement. Verified in-game 2026-09-04 by direct
comparison: on one page the pushed strings switched and the 70 markup keys did not, while a
sibling mod's page switched whole. That mod carries **two** `%key`s across 45 `.ui` files;
both are a `PlaceholderText`, which has no attested `Message` form.

So the split is not "fixed vs computed". It is:

- **Java push** — everything with words in it.
- **`%key` in markup** — only a property with no `…TextSpans` counterpart (`PlaceholderText`
  is the known one). Accept that those freeze until reconnect.

A macro instance takes an `#Id` (`$C.@Subtitle #Heading { @Text = ""; }`), and the label
macros expand to a **single root element** carrying the `Text`, so the id addresses exactly
the element to push into — no macro needs replacing with a hand-styled label. Two things to
watch: **keep `@Text = "";`** rather than emptying the body (the trap below — it costs the
join, not the page), and push **before** anything `remove`s the element, since a batch
applies in order and a `set` on a removed element is the fatal "not found".

**Check it, don't remember it.** What is left in markup should be only the properties with
no `Message` form:

```bash
grep -rho "%[a-zA-Z0-9_.]*" <pack>/Common/UI/Custom/Pages/  # expect: placeholders only
```

Two failure shapes tell you which half went wrong when you convert a page: a **blank label**
is a push you did not write, a **client crash on open** is a selector that matches nothing.
Neither shows up in a build, so re-verify by opening every tab and every prompt.

## The traps that actually cost days

**A selector matching nothing takes the client down.** *"Selected element in CustomUI
command was not found"* is a crash, not a warning and not a skipped command. **Never issue
a `set` speculatively** — hiding an element a row does not declare is as fatal as
addressing a row that does not exist. This bites where one container holds rows appended
from different files: a helper ending in `set(row + "#Reset.Visible", false)` is correct
for every row file that has a `#Reset` and fatal for the one that does not.

**`locksInterface` defaults to `true`.** The `addEventBinding` overloads without the
trailing boolean lock the client: the page grays out, shows "Loading…", and refuses input
**until the server sends an update back**. Correct for an action that answers (a tab
switch); a permanent hang for a checkbox or a search field, which should answer with
nothing. **Rule: if the handler does not call `sendUpdate`, the binding passes `false`.**

**And the rule is per-*path*, not per-handler.** The nastier version of this bug is a
handler that answers on its main path and returns early on one branch — most often "the
user picked what is already selected, so there is nothing to do". Under a locking binding
that branch is a permanent freeze. Either repaint anyway (re-rendering what is on screen
is usually the honest answer to a click, and reads as a refresh), or pass `false` and
answer with nothing on every path. **Never mix the two.**

**The acknowledgement gate.** Each update increments a counter, and incoming `Data` events
are **silently dropped while it is non-zero** — so spamming updates swallows the player's
next clicks, and an unexpected ack throws. One `sendUpdate` per handled event is the safe
rhythm.

**`sendUpdate` is thread-safe but not liveness-checked.** It hops to the world thread
itself and costs nothing for a player who logged off, so it may be called from any thread.
What it does *not* check is that this page is still on screen — an update aimed at a page
the player has replaced leaves *whatever is open now* waiting to acknowledge a packet it
never received, and swallows its clicks. **Any update not answering a click of the
player's own** (an async job finishing, a broadcast) must check on the world thread:

```java
Player p = ref.getStore().getComponent(ref, Player.getComponentType());
return p != null && p.getPageManager().getCustomPage() == this;
```

**A codec type mismatch drops the whole event silently.** The client sends the control's
native JSON type; `true` will not decode through a `STRING` codec, the handler never runs,
and nothing is logged. One `@key` per value **shape** — see `reference/widgets.md`.

**`ValueChanged` fires per keystroke on text *and* number fields.** So a handler answering
an edit **must not rebuild the container that field lives in** — the repaint lands on the
first character, tears down the element being typed into, and a two-digit number cannot be
entered at all. Only a checkbox or a dropdown may be answered by redrawing its own section.
When a typed edit must still refresh something *derived* (a caption, a preview), **replay
the render into scratch builders** and hand only the calls you want on screen the real
builder — the row counter advances identically, so element *n* of the replay addresses
element *n* of the render. A replay may push a bare property, **never a binding**: one
pushed per keystroke stacks. So a control the replay reveals must be bound at the render
while still hidden; `Visible` is what withholds the action.

**Closing is the client's decision.** `onDismiss` is a notification — the page is already
off screen, there is no veto and no pre-close hook, and the server drops its reference
*right after* the call returns. So a confirm-on-exit flow cannot prevent the close, only
put something back up afterwards; and **do not open a page from inside `onDismiss`** —
defer one `World.execute` task, or the server forgets the page the client is showing and
routes its events elsewhere. Note `openCustomPage` also dismisses the page it replaces, so
a swap runs that override too.

**Two-tone text is a `Message` *child*, never a *param*.** `outer.param("n", inner.color(…))`
substitutes as flat text; `Message.empty().insert(a).insert(b.color(…))` keeps one span
each, which is what `TextSpans` renders. The failure is silent and looks like the color
was ignored.

**Markup errors take the page down at parse time**, with file, line and column in a *Custom
UI — Markup Error* overlay — so a page that opens at all has valid markup. Two rules that
trip it: **a `%lang.key` in markup may not contain an underscore** (the tokenizer ends the
identifier there), and **macro parameters (`@X = …`) must come before ordinary properties
(`Prop: …`)** in an instance body.

**A broken document blocks the *join*, not the page — and blames the wrong file.** Every
custom document is parsed when a player connects, so bad markup anywhere is a `Crash`
disconnect during `GameLoading` with *"Failed to load CustomUI documents"* on screen. Nobody
has to open your page to hit it. The parenthesized detail names the file the failing
*property* lives in, which for anything inherited is the **design system**, not yours:

```
Failed to load CustomUI documents
(Failed to parse file Common.ui (211:9) – Could not resolve expression for property Text
 to type String)
```

*(seen in-game 2026-09-05, client on engine `0.6.1`)* That one is the trap below.

**Removing text from a macro instance can leave a required parameter unbound.** An instance
spells its text two ways and they are **not** interchangeable when you take one away:
`@Text = …` supplies the *parameter*, while a plain `Text: …` property *overrides* the
macro's own `Text: @Text;` line — which is why an instance written the second way parses
without ever supplying `@Text`. Delete that property and the macro's line comes back with
nothing behind it, and if the macro declares no default the whole document fails.

So **leave `@Text = "";` behind** rather than an empty body. Which macros require it is
mechanical — body has `Text: @Text` and no `@Text = ` default — so check rather than
remember, against the **pack's** `Common.ui` (the install's smaller file is a different one):

```bash
awk '/^@[A-Za-z]+ = /{n=$1;sub(/^@/,"",n);b="";f=1} f{b=b"\n"$0} f&&/^};/{
  if (b ~ /Text: @Text/ && b !~ /@Text = /) print n; f=0}' Common.ui
```

On engine `0.6.1` that is `@TextButton`, `@SecondaryTextButton`, `@TertiaryTextButton`,
`@SmallTertiaryTextButton`, `@CancelTextButton`, `@CheckBoxWithLabel` and `@Subtitle`;
`@Title` and `@SmallSecondaryTextButton` default to `""`. Passing `@Text = "";` on every
instance you strip costs nothing and removes the question.

## The design rule: the page must look shipped with the game

Build from the design system's `$C.@` macros — containers, buttons, inputs, scrollbars,
progress bars, separators, tooltips, and the `@Color*` / label-style tokens. **Hand-writing
a color, font, size, texture, border or padding is a defect** when a macro exists, and one
nearly always does. Two narrow exceptions: an idiom the system does not export (there is no
error/red token — copy the client's own `Style: (RenderBold: true, TextColor: #bb3333)`),
and data-driven color that *is* your content.

**A macro existing in the design system is not proof its art ships.** At least one shipped
button style points at textures that no longer exist in the archive and renders as a
white-and-red missing-texture box; no shipped page instantiates it, which is why the rot is
invisible until a mod uses it. Before using a macro nothing in the client's own `.ui` files
instantiates, check its texture paths against the archive. The fix stays inside the rule:
rebuild the style by spreading a working one and overriding the state backgrounds.

## References

- **`reference/markup.md`** — read when writing or debugging a `.ui` file: the grammar
  rules, layout that measures wrong, styles and `Value.ref`, paths and shipping, i18n.
- **`reference/widgets.md`** — read when placing or wiring **one control**: reading its
  value back, the shipped rows, dropdowns, item slots and grids, tooltips, live search,
  collapsible blocks, tabs.
