# The hook catalog — which event answers which question

> Read when: choosing what to listen to for a given job, or a system you wrote never fires.
> How to write and register the system is `SKILL.md`.
>
> Several entries here exist to record a hook that **looks** right and is not — those are
> the expensive ones.
>
> **Engine `0.5.9`** (patchline `release`) · last checked 2026-08-23 — re-verify against a
> newer jar before trusting an entry here; a renamed hook does not fail the build, it just
> never fires.

## Reading a player's inventory

Six sections, each its own component: **Hotbar, Storage, Armor, Utility, Tool, Backpack**.

```java
store.getComponent(ref, HotbarInventoryComponent.getComponentType()).getInventory();
```

An `Inventory` exposes an `ItemContainer`, which exposes slots. This is the pure-ECS read;
prefer it over the legacy player-object accessors, which are deprecated for removal.

**Not every section is reachable by every mechanic.** A crafting bench, for one, composes
its own combined container from a *subset* of them — see the crafting section for which,
and for why that subset is load-bearing when two stacks of an item are not
interchangeable.

**Anything that scans containers misses items that never reached one** — see the craft
overflow below. A scan is a legitimate technique, but it needs the drop hook beside it.

## Crafting

The order within one craft, identical in the immediate and queued paths:

```
CraftRecipeEvent.Pre → the inputs are removed → CraftRecipeEvent.Post → giveOutput
    → PlayerCraftEvent (deprecated)
```

- **⚠️ `.Post` fires BEFORE the output exists.** It carries the recipe, not the output
  stack. To read the crafted item you must wait for a **later tick** — record what you saw
  (crafter, output id, a pre-craft snapshot of the containers) and act next tick.
- **Both `.Pre` and `.Post` are cancellable**, and they are not symmetric. `.Pre` fires
  before anything is consumed, so cancelling costs nothing and that is where a gate
  belongs. **Cancelling `.Post` skips the output and refunds nothing** — the inputs are
  already gone and the refund path belongs to job cancellation, which never reaches
  `.Post`. It is the right tool for "this craft must not complete", but anything that has
  to go back, you put back yourself.
- The removal sitting **between** the two means a `.Pre` handler sees the before-picture
  and a `.Post` handler the after-picture, in one synchronous sequence — so diffing the two
  is a way to learn what the engine took when it does not tell you.
- The event carries `getCraftedRecipe()` and `getQuantity()`; the recipe gives
  `getPrimaryOutput()`, `getInput()`, `getOutputs()`, `getTimeSeconds()`,
  `isKnowledgeRequired()`, `getBenchRequirement()`. It is dispatched to the **crafter**.
- The event-bus `PlayerCraftEvent` is deprecated for removal and world-keyed. Do not build
  on it.

### ⚠️ Which copy of an ingredient gets consumed — and how to decide it

The engine does not pick at random and cannot be asked to prefer one: **it takes the first
match by index in one flat container**, which the bench composes as the player's
`backpack → storage → hotbar` and *then* the containers around the bench, ascending slot
within each. Two things fall straight out of that: sections outside those three (a tool or
utility bar) are not craft inputs at all, and **anything the player carries beats anything
in a chest**.

This only matters when two stacks of the same item are **not interchangeable** — a
per-instance stamp, an owner, a durability. Then "does the player own one?" is the wrong
question, because they may own one and the engine may still eat somebody else's copy from
an earlier slot. A player who understands the order can aim at that on purpose.

**Arrange the craft at `.Pre`; do not correct it at `.Post`.** Because the order is fixed,
a `.Pre` handler can read the containers, work out which copy the removal will name, and
**swap the copy it wants spent into that slot before returning** — an item-slot write is an
in-place container mutation, not a structural one, so it is legal from inside a handler and
takes effect immediately (`SKILL.md` → the two store asserts). From then on the engine's own
rule picks correctly and nothing has to be undone.

The corrective shape — diff the containers at `.Post` and put things back — looks
equivalent and is strictly worse: the diff sees one copy of each gone and **cannot tell
which one the engine actually ate**, so a wrong guess hands back an item that still exists.
It is also racy in the queued path, where consumption happens when a craft *starts*: a
player can empty the container before `.Post` and leave the correction nothing to spend.

⚠️ **This order is behavior behind an unchanged signature.** A version bump that reorders
it breaks nothing at compile time and logs nothing — re-verify it by hand whenever you
bump, and keep a note saying so on whatever depends on it.

**⚠️ A full inventory means the output is DROPPED, never stored.** When nothing fits, the
engine drops the output as a **world item entity** — so a container scan cannot see it, and
anything keyed off finding the stack silently misses this case. The hook for that stack is
the **drop event**, which fires synchronously in the same call stack, *before* the entity is
spawned, and carries a **mutable** stack: rewriting it in the handler changes what lands on
the ground. Note a dropped stack is **not** split by max-stack size — a bulk craft that
overflows arrives as one quantity-N stack.

**Recipe knowledge is per player.** A recipe shipped `KnowledgeRequired: true` is not
craftable until learned, and the learn/forget API is public static on the crafting plugin
(`learnRecipe` / `forgetRecipe` / `sendKnownRecipes`, all taking the ref and a component
accessor). That makes a **hidden unlock** trivial: ship the recipe locked, track your own
conditions, and teach it when they are met. Teaching writes a player component — **defer
through a `CommandBuffer`**.

## Items: possession, drops, despawn

| Vector | Hook | Cancellable |
|---|---|---|
| Ground pickup | `InteractivelyPickupItemEvent` | ✅ — veto = that player cannot pick it up |
| Drop (manual, death, craft overflow) | `DropItemEvent.Drop` | ✅ — and the stack is mutable |
| Player-initiated drop | `DropItemEvent.PlayerRequest` | ✅ — adds the source section + slot |
| Anything landing in a container | `InventoryChangeEvent` | ❌ **post-change only** |
| Item entity removed / despawned | **a `HolderSystem`** — see below | ❌ observe-only |

- The drop event fires for **every** drop, so filter to the case you care about before
  acting.
- `InventoryChangeEvent` gives the container and the transaction — enough to see *what*
  landed in *which* slot and pull it back out, but **not where it came from**. "Return this
  item to its origin container" is therefore not implementable from it.

### ⚠️ The entity-removed event never fires for a ground item

It is dispatched from exactly one place: the legacy entity wrapper's `remove()`. Removals
that go through the ECS directly (`CommandBuffer.removeEntity` / `Store.removeEntity`) never
touch the event bus — and a ground item is assembled without that wrapper at all, so it can
never reach the dispatch. **A listener on it is dead code for items.**

**The right hook is a `HolderSystem`**, and the engine uses it for exactly this job: query
the item component, implement `onEntityRemoved(Holder, RemoveReason, Store)`, read the
component **off the `Holder`** (the removed entity's components are still readable there),
and filter:

- `RemoveReason` has exactly three values — **`REMOVE`, `UNLOAD`, `BUILDER_TOOLS_UNDO`** —
  so a real despawn separates from a chunk unload *at the hook*.
- Skip the ones flagged as removed by player pickup.

The wrapper-based event stays valid for entities that *do* carry the wrapper (players,
NPCs), subscribed globally on the bus. It carries no removal reason.

### Despawn is a deadline, not an age

The lifetime is resolved **once, at drop time**, and stored as an absolute instant on a
despawn component. Re-tuning the config afterwards does **not** move an
already-dropped item's deadline, and there is no readable age — so a "sweep things about to
expire" design is not possible; the removal hook is the only one.

Resolution order for the seconds: the **item's own entity config** → the world gameplay
config → a hardcoded fallback. The item-level value comes from the item's **quality**, not
from its JSON — the same config block that carries a drop beam also carries the lifetime.
That gives a neat testing lever: shorten the lifetime on a quality you inject and every item
wearing it despawns fast, through the real system rather than a simulation.

To force a removal yourself, issue the same call the despawn system makes
(`CommandBuffer.removeEntity(ref, RemoveReason.REMOVE)`) from a world-thread sweep — there is
no native command for it, and this way every observer fires exactly as on a real despawn.

## Damage

Damage is a normal **cancellable ECS event**, dispatched to the **victim**.

- **Mutate the hit:** `getAmount()` / `setAmount(float)` (0 nulls it), `getInitialAmount()`,
  `getCause()`, `getSource()`, plus cancel.
- **Who dealt it** is the source: an entity source exposes `getRef()` (the attacker); a
  projectile source extends it and adds the projectile while `getRef()` stays the shooter;
  command and environment sources have no attacker. From the attacker's ref you can read
  their active hotbar item — the weapon being swung.
- **⚠️ Ordering is by system group.** Put a reduction in the **filter** group (the middle of
  gather → filter → inspect), which is where the engine's own armour and wielding
  reductions run. The system that applies the damage runs after filter, so a zeroed amount
  survives — and `0 × anything` survives later multiplicative reductions too.
- **Query the victim side** with the engine's "can take damage" marker component, which is a
  conjunct in the engine's own filter systems and so matches every damageable entity.
- **Dealing damage yourself:** `store.invoke(targetRef, new Damage(source, cause, amount))`
  runs the full pipeline. **The source decides both attribution and loop safety** — use the
  engine's null source for self-inflicted or reflected damage, so handlers keyed on an
  entity source ignore the synthetic hit and cannot recurse.

## Player join

The player-ready event is **keyed by world name**, so register it **globally** or it will
miss players. It gives the player's ref, and from it the store.

Beware: a join handler may itself run during store processing, so attaching a component
there needs the same deferral as anywhere else.

## Things that are NOT hookable — worth knowing before designing around them

- **Item repair.** It is UI-page-driven with **no ECS event** at any point — the durability
  is restored inline. Nothing carries both the player and the chosen item, so per-item
  repair gating is not implementable natively. The only lever is the item's **static**
  repairable flag, which applies globally to everyone.
- **A container item's origin.** See `InventoryChangeEvent` above.
- **An item entity's remaining lifetime.** See despawn above.

When a design depends on one of these, say so early — the alternative is usually a different
feature, not a cleverer hook.
