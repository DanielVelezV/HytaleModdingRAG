# `.ui` markup — grammar, layout, styles, paths

> Read when: writing or debugging a `.ui` file, addressing the tree from Java, or deciding
> where a file lives. The page architecture and its Java-side traps are in `SKILL.md`.
>
> **Engine `0.5.9`** (patchline `release`) · last checked 2026-08-20 — re-verify against a
> newer client pack before trusting a path or a macro here; both fail only at open time —
> **except a document that fails to parse, which fails at *join*** (see "i18n in `.ui`").
> The i18n section and the `@Text` trap under it were re-derived on `0.6.1`, in-game
> 2026-09-05, and say so inline; the rest still carries the older date.

## Where files live, and what a path costs

- Asset root is **`Common/UI/Custom/`** in the pack. Every path the server passes is
  relative to it (`Pages/<Mod>/MyPage.ui`).
- **Namespace under your own subdirectory.** A shipping mod puts its pages flat into the
  shared `Pages/`, which is also where the *game's* pages live — two mods picking one file
  name collide. Never write outside your own folder: a pack *can* claim a core engine UI
  path, and nothing stops it.
- **Nesting is free and attested.** Engine pages nest (a page's tab bodies in a subfolder)
  and also stay flat (one shipped page keeps 25 files in one directory, disambiguated by
  name prefix). Nothing resolves by convention — a `.ui` is named by its full path, in Java
  and in another file's import alike — so a move costs exactly two things: the Java path
  constant and the **relative** `$C` import, which gains a `../` per level. **Neither fails
  the build**, so re-verify a move by opening the page.
- The design system is `Common/UI/Custom/Common.ui`, **inside the pack**. The client
  install carries a *different, smaller* file of the same name with almost none of the same
  macros, so grepping the install to check a macro answers the wrong question. Extract the
  pack's copy instead:

```bash
unzip -o "$ASSETS" "Common/UI/Custom/*" -d /tmp/gameui
```

## The design-system token inventory

| Need | Use |
|---|---|
| Page backdrop | `$C.@PageOverlay` |
| Window frame | `$C.@DecoratedContainer` (runed header) or `$C.@Container` — both give `#Title` + `#Content` |
| Inner panel | `$C.@Panel`, `$C.@SimpleContainer` |
| Title / section heading | `$C.@Title`, `$C.@Subtitle`, and the bare **`@SubtitleStyle`** |
| Body text | `$C.@DefaultLabelStyle` |
| Buttons | `$C.@TextButton`, `@SecondaryTextButton`, `@TertiaryTextButton`, `@CancelTextButton` (+ `Small*` variants) and their `*Style` forms |
| Inputs | `$C.@CheckBox`, `@CheckBoxWithLabel`, `@TextField`, `@NumberField`, `@MultilineTextField`, `@DropdownBox` |
| Typed config rows | `Pages/Fields/{Checkbox,Dropdown,Int,Number,Text,Vec3,AssetPicker}Row.ui` |
| Scrolling | `LayoutMode: TopScrolling` + `ScrollbarStyle: $C.@DefaultScrollbarStyle` |
| Progress | `$C.@ProgressBar`, `$C.@CircularProgressBar`, `$C.@DefaultSpinner` |
| Horizontal rule | `$C.@ContentSeparator { @Anchor = (Bottom: 10); }` |
| Tooltips | `$C.@DefaultTextTooltipStyle` |
| Search box | `$C.@HeaderSearch` (a `CompactTextField #SearchInput`, collapsed and expanding, with magnifier and clear button) |
| Leave the page | `$C.@BackButton` |
| Header search / result row | `$C.@HeaderSearch`, `Pages/BasicTextButton.ui` |

**Colors** come from `@Color*` tokens (default white, default-label, blue accent, gold
highlight, gray caption, caption light, button text, disabled). **Fonts: there are exactly
two** — the default and `FontName: "Secondary"` (used by the title style, with
`RenderUppercase: true`). Do not introduce a third or invent sizes; heading 15, body 16,
caption 12–13.

**⚠️ A `TexturePath` is relative to the file that writes it**, not to the file that declared
the macro it was copied from. The shared macro file sits near the top of the pack, so its
own `"Common/Foo.png"` reaches a folder beside it; write that same literal in a page file
four directories down and it resolves under *that* directory, finds nothing, and the client
draws a missing-texture patch — white with red corners, at exactly the right size, so it
reads as a broken widget rather than as a bad path. Nothing is logged. **Copy the macro's
path only together with the prefix your own file already uses to import that macro file**
(the `../../../../` in your `$C = "…/Common.ui"` line is the same climb). Verified in-game
2026-08-31.

**There is no error/red token.** The client spells its warning look out per page:
`Style: (RenderBold: true, TextColor: #bb3333)` — four shipped pages declare an `#Error`
label identically. Copy that recipe verbatim rather than picking a red.

**Idioms worth copying instead of reinventing:** a 1px `Group` with `Background: (Color: …)`
as a rule (or better, the separator macro); `Group { FlexWeight: 1; }` as a flexible
spacer; `Visible: false` labels toggled from Java for status text.

**A progress bar does not need inventing.** `ProgressBar` is a native widget with shipped
art, `Value:` a normalized `0.0..1.0` set from Java. The game's own crafting-progress panel
is the model for a framed one: a `Group` with a background frame wrapping **two stacked
`ProgressBar`s** (fill + texture overlay) plus a "tip of the bar" effect sprite.

## Grammar rules the parser enforces

A markup mistake is caught at **parse time on the client**, with file, line and column in a
*Custom UI — Markup Error* overlay — the page refuses to open rather than degrading. So a
page that opens at all has valid markup.

**1. Macro parameters before properties.** A macro instance takes parameter assignments
(`@Text = …`, using `=`) and ordinary properties (`FlexWeight: 1`, using `:`); the parser
will not accept a parameter after a property — it reads the `@` as the start of another
macro instance and fails with `Expected {, found =`.

```
$C.@SmallSecondaryTextButton #Reset {
  @Text = %server.<mod>.ui.reset;   // parameters first…
  FlexWeight: 1;                    // …then properties
}
```

**2. An appended file's root must be a plain element.** A root that is a macro instance
appends the macro and its children as *siblings* into the parent container. Wrap it in a
`Group`; style macros at the top of the file are fine.

**3. A `%lang.key` may not contain an underscore.** The tokenizer ends the identifier at
`_` and fails on the rest — a parse error that takes the whole page down. Across the
client's ~950 `%` references there is not one underscore; every key is camelCase segments
joined by dots. Only the *markup* path is affected: a key reached from Java takes
underscores fine. Rule: **no underscore anywhere in the namespace your markup draws from**,
not just in the keys currently written there, since any of them may move into markup later.

## Layout

**A property on a macro *instance* beats the macro's own.** Shipped widgets fix sizes
inside their macro (`Anchor: (...@Anchor, Width: 284, Height: 6)`), and because the spread
comes **first**, a width passed through `@Anchor` is silently overwritten. Writing the
whole `Anchor:` tuple in the instance body wins outright — restate the height from its
token when you do:

```
$C.@ProgressBar #Bar { Anchor: (Height: 6, Bottom: 6); }        // no Width -> fills the row
$C.@DropdownBox #Input { Anchor: (Width: 198, Right: 8, Height: $C.@DropdownBoxHeight); }
```

**Generally: before passing a size through a macro parameter, check where the macro writes
that property relative to its spread.** After it means the macro wins and your value is
dropped without a word. (Text and number fields write only a `Height` after their spread,
so a width passed to *them* does apply.)

**Narrowing wrapped text takes a `Width`, never a `Right` margin.** A right margin is
honored when wrapped text is *drawn* but not when it is *measured*: the label wraps to two
lines while its parent keeps a one-line height, and the second line draws over whatever
comes next. Nothing below it moves. A resolved width measures correctly — put it on the
group and leave the label inside unanchored. Full-width wrapping (no side anchors) is fine
too; this only bites once you try to narrow it.

**`Visible: false` collapses the element out of the layout** — it does not leave a hole. So
a control shown on some rows and hidden on others *moves its siblings*. **Reserve the
column with a wrapper**: a `Group` carrying the fixed width with the control inside it. The
descendant selector still reaches the control, so no Java changes.

**`FlexWeight` works on buttons too**, not just labels and groups. The strip idiom: give
every button `FlexWeight: 1` and **no** `Width`, separated by `Group { Anchor: (Width: 5); }`
rather than a right margin — the row then divides the space it has instead of running out
part-way across. Two flexed children split the row between them, so a flexed label means a
fixed width on everything right of it.

**The row shape that scales.** The shipped field rows give `#Label` a fixed 200px column,
sized for the engine's ~700px inspector panels; on a page twice that wide a two-word label
wraps to three lines while two thirds of the row sits empty. The shape that works on a wide
page:

```
[ gutter, fixed ][ label panel, flexed ][ input, fixed ][ button column, fixed ]
```

— the label wrapped in `$C.@SimpleContainer` (the subtle panel the game's own settings rows
draw behind their labels), a trailing button inside a fixed-width wrapper so hiding it
moves nothing, and a leading spacer of that same width so the form sits centered instead of
hugging the left edge. Keep **one** geometry for every option row on the page and spell it
out in one file the others copy.

**Anything interleaved with the rows takes the same gutter** — a block heading, an aside,
a list row of another shape. Only what stands above a whole section (its title, its notes,
its navigation) stays flush left; a heading left out there reads as unrelated to the block
it opens. In a `Top` container the indent is just `Anchor: (…, Left: <gutter>)`. A macro is
anchored by **wrapping it in a group**, since overriding a macro's `Anchor` drops whatever
else that anchor set.

**A list container cannot be interrupted.** Rows append in order into one group, so
anything that must appear *between* two stretches of a list is a container of its own. That
is what decides the layout around a search field and its results: field above, results
directly under it, entries below — three sibling containers, because one group could give
either ordering, never both. It also keeps every selector two levels deep; a third level
(`#A[i] #B[j] #C`) has no shipped precedent.

**A container that declares no `LayoutMode` stacks its children instead of flowing them**,
and a later sibling draws **over** an earlier one. That is how anything gets drawn on top of
a picture — a quantity, a badge, a dimming veil — and it is the shipped idiom, not a trick:
`Pages/DroppedItemSlot.ui` lays its `Label #QuantityLabel` over its `ItemSlot` that way,
both anchored inside a `Group` that names no layout. Give the overlay `Anchor: (Full: 0)`
and it covers the child beneath it exactly.

**An overlay is hit-testable by default; `HitTestVisible: false` is how you let the pointer
through.** `Pages/EntitySpawnPage.ui` stacks a decorative `Group #DropIndicator` over an
`ItemGrid` and sets that key on the overlay so an item can still be dropped into the grid
underneath — the only occurrence in the shipped pack (161 `.ui` files, engine `0.6.1`), and
the reason it has to be written at all is that the default is the opposite. So the choice is
yours to make explicitly: an overlay meant to be *seen through* (a badge, a drop icon)
declares `HitTestVisible: false`, and one meant to *intercept* (a veil that carries its own
tooltip in place of the widget's) leaves it alone. What the shipped file attests is
drag/drop hit-testing; whether hover and tooltip resolution run on the same pass is not
attested by any markup, so verify a tooltip-bearing overlay in game.

There is **no opacity or tint property on an item widget** — no `Opacity`, no `Color`; the
only opacity the design system exports is `IconOpacity` inside a *tab* style. So "draw this
item dimmed" is a stacked `Group` with a translucent `Background` (`#000000(0.6)` and the
like — a color literal the design system has no token for), not a property on the picture.
An `ItemGridSlot` also carries `setItemIncompatible` / `setItemUncraftable`, but what the
client draws for either is not attested by any shipped page or by the server jar, so a page
that needs a *predictable* dim uses the veil.

**`ItemSlot` needs no wrapper and no fixed size.** Every shipped use puts one at 64–68px
inside a bordered group, which makes it look like the container supplies the slot art — it
does not. At 44×44 with nothing around it the widget still draws its own slot background
and its quality border. And **vertical alignment inside `LayoutMode: Left` is handled for
you**: a row mixing a fixed-size slot, stretch labels and a button macro that fixes its own
height lands on one line with no child carrying a vertical anchor.

## Styles, and swapping one from Java

**`Value.ref(uiPath, macroName)`** names a style macro declared at the top of a `.ui` file;
the client resolves it, so the server never ships a style. It resolves against **any file
the page has appended**, including the page root itself — one file fewer than pointing at a
separate one-element `.ui`, which is what the engine's own pages do.

```java
private static final Value<String> TAB       = Value.ref("Pages/<Mod>/MyPage.ui", "TabStyle");
private static final Value<String> TAB_ACTIVE = Value.ref("Pages/<Mod>/MyPage.ui", "TabSelectedStyle");
cmd.set("#TabHome.Style", selected ? TAB_ACTIVE : TAB);
```

**Re-export a shipped style under a local name with the spread form** — a bare top-level
alias (`@X = $C.@Y;`) has no shipped precedent and is untested:

```
@TabStyle         = (...$C.@SecondaryTextButtonStyle);
@TabSelectedStyle = (...$C.@SmallSecondaryTextButtonStyle, Default: (…), Hovered: (…), Pressed: (…));
```

**Overriding a state key replaces that whole state**, not the keys named in it — which is
why the shipped styles restate `Background` *and* `LabelStyle` in every state. It bites
hardest on a style that carries **no background in any state** (the header text button,
whose entire hover feedback is a label `TextColor`): spread it, override `Hovered:
(LabelStyle: …)`, and the button ends up with no cue at all, because the one key that
carried the state is the key you replaced. The fix inside the design rule is a *background*
from the tertiary set — the quietest the system has, and unlike a text tint it stays
visible on a label whose color is fixed by a `TextSpans` span (a span color wins over the
style's, so any server-colored label is immune to a tint-only hover).

**"This one is on" has its own token.** An **active** background ships beside the tertiary
button's ordinary ones; no shipped `.ui` instantiates it, so there is no ready `@…ActiveStyle`
— assemble one by spreading the working style and overriding the state backgrounds. Reach
for this, **not for a red**, whenever the meaning is *state* rather than *error*: a row
wearing the error recipe reads as "something went wrong", and a page that also prints a
real warning then has two meanings on one color. On a **navigation** button keep the
active background on all three states (clicking the tab you are on does nothing, so a hover
dropping back to plain reads as the selection coming off); a **toggle** wearing it wants
the ordinary hovered/pressed backgrounds, since the click does something.

Spend **one color per claim**: one for which item of a strip you are on, another for state
inside a body. One color doing both jobs reads as swapped.

## Addressing the tree from Java

```java
cmd.append(uiPath);                       // at page root
cmd.append(selector, uiPath);             // inside an element
cmd.appendInline(selector, markup);
cmd.insertBefore(selector, uiPath);
cmd.clear(selector);                      // drop an element's children
cmd.remove(selector);
cmd.set(propertyPath, value);             // String, boolean, int, float, double, Message,
cmd.setNull(propertyPath);                //   Value<T>, T[], List<T>, Object
```

Selectors are `#ElementId`; a property path is `#ElementId.PropertyName`.

**A repeated row is `#Container[i] #ChildId.Property`** — a space is the **descendant**
combinator exactly as in CSS, and `[i]` indexes the children appended into a container.
Build the prefix once (`"#List[" + i + "] "`) and concatenate. A row file may therefore
hold as many named children as it likes; the ids repeat across copies and `[i]`
disambiguates them.

**When the appended file's root *is* the control, address the element itself**:
`#List[i].TextSpans`, `#List[i].Style`, and bind against `#List[i]`. Both forms are
correct — which one applies depends on whether the row file's root is a container or the
control.

Two properties worth knowing: `TooltipTextSpans` (the `Message` form of a tooltip) and
`#Container.ScrollChildIndexIntoView` (scroll a list to a given child).

## i18n

Static markup text is `%server.<mod>.ui.<key>`, resolving from the **mod's own**
`Server/Languages/<locale>/server.lang` — written **without** the `server.` prefix in the
lang file, referenced **with** it in markup. The client also ships its own `client.*`
namespace: reuse those keys for generic UI words (a Back button caption) so the wording
matches the game in every language for free.

The file format has its own traps — trimmed values, and a wrapped line costing the keys
below it. See the `hytale-assets` skill, "Shipping strings".

The choice between the two text paths **is** about i18n, and there is only one right answer
*(engine `0.6.1`, diagnosed in-game 2026-09-04 and the conversion confirmed 2026-09-05)*:

- **`set("#X.TextSpans", Message)` from Java** — every string with words in it, fixed labels
  as much as computed ones.
- **`%key` in markup** — only where no `…TextSpans` counterpart exists. `PlaceholderText` is
  the one known case.

**A `%key` is resolved once and never again.** The client resolves it when it first reads
that `.ui` file and holds the result for the session, so a player who changes language
mid-session keeps reading it in the language they left while every pushed `Message` follows
them at once. Only a reconnect re-reads the markup. The server side is not at fault and
cannot fix it: `GamePacketHandler.handleUpdateLanguage` is `PlayerRef.setLanguage` followed
by `I18nModule.sendTranslations`, which ships the **whole** table for the new language.

Converting a `%key` label to a push needs an `#Id`, and a **macro instance takes one**
(`$C.@Subtitle #Heading { @Text = ""; }`). `@Subtitle`, `@Title` and
`@SmallSecondaryTextButton` each expand to a single root element carrying `Text`, so the id
lands on the element to push into.

> ### ⚠️ Deleting an instance's `Text:` can break a macro that has no `@Text` default
>
> *(engine `0.6.1`, in-game 2026-09-05 — the macro list below is measured, not remembered)*
>
> ```
> Failed to load CustomUI documents
> (Failed to parse file Common.ui (211:9) – Could not resolve expression for property Text
>  to type String)
> ```
>
> **The client refuses to join** — a `Crash` disconnect during `GameLoading`, before any
> page is opened, because every custom document is parsed at join. And **the error names
> `Common.ui`, not your file**, because the unresolvable `Text: @Text;` is the *macro's*
> line: `@TextButton` (Common.ui:198) declares `@Anchor` and `@Sounds` defaults and **no
> `@Text`**.
>
> A macro instance can spell its text two ways, and they are not interchangeable when you
> remove one: `@Text = …` supplies the *parameter*, while a plain `Text: …` property
> *overrides* the macro's own line, which is why an instance written that way parses
> without ever supplying `@Text`. Delete that property and the macro's `Text: @Text;` comes
> back with nothing behind it.
>
> **So when you take the text out of a macro instance, leave `@Text = "";` behind** rather
> than an empty body. Which macros need it, measured over the pack's `Common.ui`: those
> whose body has `Text: @Text` and no `@Text = ` default — `@TextButton`,
> `@SecondaryTextButton`, `@TertiaryTextButton`, `@SmallTertiaryTextButton`,
> `@CancelTextButton`, `@CheckBoxWithLabel` and `@Subtitle`. `@Title` and
> `@SmallSecondaryTextButton` default to `""` and are safe either way; passing it anyway
> costs nothing and removes the question.

Literal strings in markup do work. They are the same rule violation as a raw reply string.
