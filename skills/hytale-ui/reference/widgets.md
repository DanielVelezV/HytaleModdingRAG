# The control cookbook

> Read when: placing or wiring **one control** and you need its recipe — how it reports its
> value back, what its event carries, what it cannot do. Consulted per widget, not read
> through. The page architecture is in `SKILL.md`; the markup around the control is in
> `markup.md`.
>
> **Engine `0.5.9`** (patchline `release`) · last checked 2026-08-23 — re-verify against a
> newer client pack before trusting a property name here; a wrong one is dropped silently.

## Reading a control's value back — the `@key` convention

An `EventData` entry whose key starts with `@` does **not** carry a literal: **its value is
a property path**, and the client resolves that path and sends what it finds. Declare the
same `@key` in the page's codec and it arrives decoded.

```java
cmd.set(row + "#Input.Value", current);                        // seed it
evt.addEventBinding(CustomUIEventBindingType.ValueChanged, row + "#Input",
        EventData.of("Param", key)                             // literal: which setting
                 .append("@ParamBool", row + "#Input.Value"),  // live: its value
        false);                                                // does not lock — see SKILL.md
```

Note the asymmetry: the **binding** is registered against the element (`… #Input`), while
the `@key` points at the **property** (`… #Input.Value`).

**⚠️ One `@key` per value *type* — the codec must match the widget.** The client sends the
control's native JSON type, and a mismatched codec **drops the entire event silently**:
`true` will not decode through a `STRING` codec, the handler never runs, and nothing is
logged. So name keys by value shape, not by field, and pair each with a plain key saying
*which* setting changed:

| key | codec | field type |
|---|---|---|
| `@ParamValue` | `Codec.STRING` | `String` |
| `@ParamBool` | `Codec.BOOLEAN` | `Boolean` |
| `@ParamNumericValue` | `Codec.DOUBLE` | `Double` |

**Box the fields** (`Boolean`/`Double`, never `boolean`/`double`) — a given event carries
only the key its control uses, so every other decodes to `null` and a primitive would read
a dropdown event as `false`. And **absent is not a value**: a handler must bail when its
expected key is missing rather than substituting a default, or a malformed event silently
rewrites a setting.

**The alternative, for a pure toggle:** bind `ValueChanged` with **no** `@key` and send
`{Item: x, Type: Toggle}` — the server already knows the state and flips it. Fewer moving
parts, but only when the server's copy cannot drift; a dropdown needs the `@key` form.

**⚠️ A `@`-prefixed key can only carry a path, never a literal.** To send a button a fixed
value (which row, which id), put it in a plain key. Spelling the id into the reported
setting path (`remove.<itemId>`) beats a row index: the re-render that follows a removal
cannot then misaddress the next click.

## Reading a field from a *button* — the submit pattern

A text field does not have to report itself. A button can carry the field's value as a live
`@key` path, and **the path is an absolute selector from the page root**, not relative to
the button — so a button can read any field on the page:

```
binding element:  #MainPage #SaveButton
live path:        @Name -> #MainPage #NameInput.Value
```

This is the pattern whenever a click has to read a field, rather than answering per
keystroke.

## The five shipped config rows — and why you copy them

All five are the same two-column `Group {LayoutMode: Left}`: a `Label #Label` (fixed
`Width: 200`, wrapped, vertically centered, carrying a tooltip style) plus one `#Input`.

| row | `#Input` | reports |
|---|---|---|
| `CheckboxRow` | `$C.@CheckBox` | JSON **boolean** |
| `NumberRow` | `$C.@NumberField` (`MaxDecimalPlaces: 2, Step: 1`) | JSON **number** |
| `IntRow` | `$C.@NumberField` (`MaxDecimalPlaces: 0`) | JSON **number** |
| `TextRow` | `$C.@TextField` (`FlexWeight: 1`) | **string** |
| `DropdownRow` | `$C.@DropdownBox` | `Entries` + `Value`, both set from Java |

They also expose the rest of what a config editor needs on `#Input` / `#Label`:
`#Input.Value`, `#Input.Entries`, `#Input.Color`, `#Label.TooltipText`.

**A `NumberField` is unbounded unless its `Format` says otherwise** (in-game 2026-08-23).
The `@NumberField` macro sets no `Min` / `Max`; bounds are `Format: (MinValue: …,
MaxValue: …)`, and a field declaring neither **accepts a negative** — the minus sign types,
the value stages, and it arrives as a negative JSON number. Nothing in the shipped assets
attests this (no shipped `.ui` uses a negative `MinValue`; the game's only negative bounds
are on `Slider`s), so it took a live check. The corollary is the part that bites: **a
setting that must not go negative is not protected by the widget** — clamp it server-side
in the setter, read the value back and report it as clamped. Putting the bound in the
markup instead makes the page refuse a digit the mod's command still accepts, and the two
paths then disagree about what is storable.

**⚠️ A shipped row cannot be extended, and cannot be re-laid-out — copy it.** `append` adds
a child *inside* an element; there is no way to add a sibling into a file the game ships,
and a `Width` is markup that no `set` can reach. So a row needing anything beyond
`#Label` + `#Input` (a per-row reset button), or a label column that suits a wide page, is
**your own file** with the shipped body copied verbatim plus the extra control. Copying
keeps the design rule intact — every value is still a macro — at the cost of re-diffing
them on an engine bump. Layout guidance for the copy is in `markup.md` → Layout.

**⚠️ `#ValidationLabel` is not on the shipped rows.** It exists on one page's own row, not
on any shared field. A mod wanting inline validation either ships its own row or **refuses
the write and repaints the section** from the stored values (so the control snaps back to
what was actually stored) with the reason in a label of the page's own.

A per-row control that is only sometimes relevant is **hidden and left unbound**
(`set(row + "#Reset.Visible", false)`, no binding) rather than declared conditionally: the
row markup is one file appended N times, so what varies per row varies in the mutations.
But see `SKILL.md` — a `set` against a row file that does not declare that element is a
client crash, so the call must be conditional on which file was appended.

## Tooltips

```java
cmd.set(sel + ".TextTooltipStyle", Value.ref("Common.ui", "DefaultTextTooltipStyle"));
cmd.set(sel + ".TooltipTextSpans", message);
```

**The style push is only needed for an element whose markup does not already declare one.**
The shipped field rows carry `TextTooltipStyle` on their `#Label` already, so a row tooltip
is the one `set(row + "#Label.TooltipTextSpans", …)` and nothing else.

`TooltipTextSpans` is the `Message` form and the one that keeps the translation-key rule;
`TooltipText` (plain string, what markup writes) is not.

## Dropdowns

```java
cmd.set(row + "#Input.Entries", List.of(
        new DropdownEntryInfo(LocalizableString.fromMessageId("server.<mod>.option.amber"), "amber"),
        …));
cmd.set(row + "#Input.Value", currentValue);          // the entry's value, a String
evt.addEventBinding(ValueChanged, row + "#Input",
        EventData.of("Param", key).append("@ParamValue", row + "#Input.Value"), false);
```

`LocalizableString` is the UI-side counterpart of `Message` — `fromString` (literal, avoid),
`fromMessageId(key)`, `fromMessageId(key, Map<String,String>)`. An entry carries a
**translated label** and a **stable value**: send an enum name or a slug as `value`, never
the display text.

**⚠️ The params overload does nothing.** The map is carried on the wire but the client does
not substitute it into a dropdown entry — the placeholder renders literally. Nothing in the
server jar calls it, so there is no working example. **Treat an entry's text as a
parameterless key**; anything computed belongs on a `Label` beside the box, where an
ordinary `Message.param(…)` resolves normally.

**⚠️ A `DropdownBox` cannot be disabled.** There is no disabled state in its style, and
pushing `HitTestVisible: false` does not stop the client opening it: it opens, a value can
be picked, and the pick is shown locally. Omitting the binding stops it reaching the server
— but then the box displays a value the server never took. **To make a picker inert, append
a row with no picker in it**: the value as a `Label` in a panel of the box's width. Pin
that stand-in to **one** active row (its panel must be the width that box actually renders
at, or ticking the switch shifts the column under the reader).

**⚠️ Nor can a `CheckBox`, and it fails worse.** Same attempt, same result (in-game
2026-08-31): `HitTestVisible: false` does not stop the client ticking and unticking a
checkbox. A picker at least *looks* like it committed something; a checkbox that toggles
while the page's Save button never wakes reads to the user as a broken mod, which is the
opposite of the message an inert row is there to send. **Same fix, one step further: draw a
picture of the control.** A checkbox is a frame plus a state fill, both of them values you
can read out of the shared macro file rather than invent — the frame is the `CheckBox`
macro's own `Background` (a 9-patch), and the fill is the color its style names for the
state you want to depict, `Disabled` included. Two nested `Group`s, the outer carrying the
frame and the macro's padding, the inner filling it. Bind nothing.

**The rule behind both.** A control that cannot be honored is not a control: this markup
has no general "disabled" flag, and every attempt to fake one leaves an input that responds
and then goes nowhere. Whatever a state *looks* like is available to you as data in the
shared macros — copy that, and let the row carry no widget at all.

When a **whole page** goes read-only — a viewer who may look at the settings but not change
them — the same rule scales up, and the pinning does not: nothing is editable, so no
stand-in has an active twin to line up with. One reading row for every control kind, at the
width of the widest control on the page, is what keeps the column intact. Render it in
place of the control, bind nothing, and drop the buttons that only act (reset, save, an
add field): a form whose controls move and then refuse is worse than a table that never
offered.

**⚠️ A dropdown's width cannot be passed in** — the macro writes its own `Width` after the
spread. Overriding the whole `Anchor` tuple in the instance body works, but that is markup:
a page wanting several dropdown widths needs **a row file per width** and Java picking
between them. The room a narrower box gives back goes to the flexed label beside it, and
the box's right edge does not move, so widths mix inside a section without breaking the
column every row ends on.

## Drawing an item

Two widgets render an item, and **both are driven by one string property, `ItemId`** —
there is no way to hand them an `ItemStack`, so what is drawn is the *asset*, never the
instance:

```
ItemIcon #Icon { Anchor: (Width: 32, Height: 32); }        // just the picture
ItemSlot #Slot {                                           // picture + slot plate + border
  Anchor: (Height: 64, Width: 64);
  ShowQualityBackground: true;                             // draws the item's quality border
  ShowQuantity: false;
}
```

```java
cmd.set(row + "#Icon.ItemId", stack.getItemId());
cmd.set(row + "#Slot.Quantity", stack.getQuantity());      // ItemSlot only
```

**Per-instance metadata does not reach either widget** — a custom display name or stamped
description is invisible to them. Push that as text beside the icon:
`ItemStack.getDisplayName()` returns a **`Message`** and honors the instance's display
metadata, so it goes straight into `#Name.TextSpans`. Durability is `getDurability()` /
`getMaxDurability()`, both `double`; the engine's own row prints the rounded percentage.

**A grid of one is how a row gets the item's *real* tooltip.** `ItemSlot` draws art and
border and nothing else — no shipped `.ui` gives it a tooltip property, and the design
system offers only *your own text*. **`ItemGrid` + `ItemGridSlot`** carries the whole
`ItemStack`, so the **client** composes the item's own tooltip — name, description, quality
— in the reader's language, with nothing pushed server-side:

```java
cmd.set(row + "#Icon.Slots", new ItemGridSlot[]{ new ItemGridSlot(stack) });
```

Declare it with `SlotsPerRow: 1` plus a `Style` fixing `SlotSize` / `SlotIconSize` /
`SlotSpacing`; `ItemGridInfoDisplayMode` defaults to `Tooltip`, which is why no shipped
page sets it. `ItemGridSlot` also has `setName` / `setDescription` (raw `String`s that
*override* that tooltip — exactly what a translated page must not do), `setBackground` /
`setOverlay` / `setIcon`, `setItemIncompatible`, `setActivatable`,
`setSkipItemQualityBackground`. A row drawing an item this way needs no text naming it.

**But the grid is pushed, never bound.** In the client's own panels the grid names no
container — the client wires it to the window's `ItemContainer` from page code that runs
only for a built-in page. A custom page cannot get a live inventory grid; it gets a
snapshot, and every move (pick up, split, swap, shift-click) would be yours to re-implement
server-side.

## Live search / autocomplete

There is no server-reachable equivalent of a command argument's client-side completion.
What there is is better: **the server filters and re-offers per keystroke** — the engine's
own idiom, not a workaround.

```java
evt.addEventBinding(ValueChanged, "#SearchInput",
        EventData.of("@SearchQuery", "#SearchInput.Value"), false);   // false = does NOT lock
```

A search binding is the archetypal non-locking one: the client must keep accepting
keystrokes while the server answers the last. `$C.@HeaderSearch` is the ready-made box and
`Pages/BasicTextButton.ui` the result-row idiom (a bare button, appended once per hit — so
its text is `#List[i].TextSpans`, addressing the appended element itself).

**⚠️ A per-keystroke handler must repaint a container the field does *not* live in.** The
obvious shape — field reports, handler re-renders the section — wipes the half-typed text
on the keystroke that asked for help spelling it. Give the results their own sibling
container and clear only that.

**Not a route to the same thing:** the shipped asset-picker row is only a `Button` styled
as a dropdown, and the browser it opens is client-owned with no server class. A server page
that wants an asset picker builds one from a search field plus result rows.

## Collapsing a block of rows

**There is no foldout/accordion widget.** The engine's own collapsible is hand-built: a
toggle button above a container declared `Visible: false`, flipped from Java.

For a *generated* list there is a better second form: the toggle reports a **block key**
and the render simply **does not emit** that block's rows. The shut blocks then cost no
packet at all, and it needs no nested indexed container. What is open lives in the page,
not in the markup, since it has to outlive the render that answers the click.

- **A heading that folds is a `TextButton`, not a `Label`** — its style can keep the label
  look exactly by spreading the header button style and restating the subtitle label style.
  A raw `TextButton` also needs `Sounds:` in its style; the button *macros* add sounds, a
  bare element gets none.
- **The open/shut marker is text, not art.** The shipped set has a down caret and no
  right-facing counterpart, so a sprite can draw one state and not the other. A `+` / `-`
  inserted as a message **child** keeps the heading's own color span intact.

## Tabs

**Native, icon-only:** `TabNavigation` + `TabButton` are shipped widgets and report
`SelectedTabChanged`. **Constraint: a `TabButton` is an `Icon` + a `TooltipText` — there is
no text label.** Two shipped strip styles: a tall one (66px) and a compact one (34px).

**For tabs with a written label the engine hand-rolls a strip of text buttons**, and that
is the pattern to copy. Two variants:

- **Buttons appended from Java** (engine's tabbed inspector): `append("#TabButtons",
  ".../TabButton.ui")` per tab, then `set("#TabButton<i>.TextSpans", …)` and an
  `Activating` binding carrying the tab id. Bodies are sibling groups toggled with
  `set("#XTab.Visible", bool)` — `Visible: false` keeps a body in the tree, so a tab can be
  built once and re-shown without a rebuild.
- **Buttons declared statically in the markup** (a shipping mod's config page), with Java
  doing `clear("#ContentArea")` + `append("#ContentArea", body)` per tab — one body swapped
  in instead of N toggled. Fewer moving parts when the tab set is fixed at authoring time.

Either way these are plain buttons reporting `Activating`, **not** `SelectedTabChanged`,
and the page tracks the selected tab itself. Mark the selection with a `Value.ref` style
swap on `.Style` (see `markup.md` → Styles).

## The full event vocabulary

`CustomUIEventBindingType` has 24 values, far more than the two most pages use:

```
Activating  RightClicking  DoubleClicking  MouseEntered  MouseExited  MouseButtonReleased
ValueChanged  FocusGained  FocusLost  KeyDown  Validating  Dismissing  ElementReordered
SelectedTabChanged
SlotClicking  SlotDoubleClicking  SlotMouseEntered  SlotMouseExited
Dropped  DragCancelled  SlotMouseDragCompleted  SlotMouseDragExited
SlotClickPressWhileDragging  SlotClickReleaseWhileDragging
```

So drag-and-drop **onto** a custom page is real — the engine binds `Dropped` on an
`ItemGrid` to accept an item dragged into it. What none of them give is the client
*performing* the move: they report the gesture, the mutation is yours to write.

**⚠️ `ValueChanged` does not fire at the same moment for every widget:**

| widget | fires |
|---|---|
| `TextField` / `CompactTextField` | **per keystroke** |
| `NumberField` | **per keystroke** — not on commit |
| checkbox / dropdown | on the change itself |

A field that is typed into fires per character, whatever its type — see `SKILL.md` for what
that forbids.
