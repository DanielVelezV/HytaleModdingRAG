---
name: hytale-ecs
description: "Use when writing or debugging an ECS system in a Hytale server plugin, or hooking anything the engine dispatches — `EntityEventSystem`, `EntityTickingSystem`, `HolderSystem`, `Store`/`Ref`/`ArchetypeChunk`/`CommandBuffer`, `registerSystem`, `registerComponent`, `registerEntityEventType`, `store.invoke`, or picking which engine event to listen to. Concrete triggers: 'react when a player crafts / drops / picks up / takes damage / joins', 'add a component to an entity', 'IllegalStateException: Store is currently processing', 'Assert not in thread', 'my event handler never runs', 'my custom event dispatches to nobody', 'read the player's inventory', 'my system doesn't fire for ground items', defining a mod event other mods can consume. SKIP for: the codec that serializes a component (`hytale-codec`), asset injection at boot (`hytale-assets`), UI pages, and command trees."
---

# ECS systems and events

> **Engine `0.5.9`** (patchline `release`) · last checked 2026-08-23 — facts verified against
> `Server-0.5.9.jar`, with in-game confirmation where noted. **Newer server? Re-verify before
> trusting a signature below**; store/system signatures and the event catalog are what a
> bump moves first.

**Which engine event to use for which job is `reference/events.md`** — the catalog, with
the hooks that look right and are not. Read it when choosing a hook; this file is how a
system is written, registered and kept legal.

## The model

Entities are ids; state lives in components; behavior lives in **systems** registered at
`setup()`. A system declares a **query** (the components an entity must have) and the
engine only ever hands it matching entities.

Three kinds, and picking the wrong one is a common dead end:

| Kind | Runs when | Use for |
|---|---|---|
| `EntityEventSystem<EntityStore, E>` | an event `E` is dispatched to a matching entity | reacting to something that happened |
| `EntityTickingSystem<EntityStore>` | every tick, over its query | polling state, deadlines |
| `HolderSystem<EntityStore>` | an entity is **added or removed** from the store | lifecycle — including removals no event reports |

Register all of them, plus your components and your own event types, in `setup()` before
stores start:

```java
getEntityStoreRegistry().registerSystem(new MySystem());
getEntityStoreRegistry().registerComponent(MyData.class, "Widget", MyData.CODEC);
getEntityStoreRegistry().registerEntityEventType(MyEvent.class);
```

## ⚠️ The two store asserts — different causes, different fixes

These are the most common runtime failures in a plugin, and they are **not** the same
problem:

**1. Thread affinity — `IllegalStateException: Assert not in thread`.** Every `Store`
method asserts it is on the owning **world thread**. A command handler, a future
completion, a storage callback and any other pool thread will throw — **even on a read**.
The fix is to hop: `World implements Executor`, so resolve the world and
`world.execute(() -> …)`, doing the read *and* whatever answers it inside the task.

**2. Structural writes mid-processing — `IllegalStateException: Store is currently
processing! Ensure you aren't calling a store method from a system`.** Anything that
changes an entity's **archetype** — attaching a component, creating or removing an entity —
is illegal while the store is processing, which it is inside any system.

- **Reads (`getComponent`) and in-place mutation of a component you already have are fine
  mid-tick.** That is the distinction to hold onto: changing an entity's *data* is safe;
  changing its *shape* is not. **Writing an item into an inventory slot is data** — it
  mutates a container component that already exists — so it is legal from inside a handler
  and takes effect immediately. Reading the rule one notch too wide, and assuming every
  inventory write must be deferred, is an expensive mistake: the deferral lands after the
  operation you were trying to influence, which pushes you into a corrective design that a
  direct write would have made unnecessary.
- To add or first-create a component from a system, **defer through the `CommandBuffer`**
  the system is handed: `commandBuffer.run(store -> { … })` runs the callback at a safe
  point outside processing. It also exposes `ensureComponent` / `addComponent` /
  `removeComponent` directly, all deferred.
- **Do not try to dodge it by moving the work to a join handler** — a join handler can also
  run during store processing. Defer there too.

> **`CommandBuffer.run` is not "run after the current operation completes".** It flushes at
> the engine's next safe point, which for some paths is *before* the operation you were
> waiting on has finished. Use it for legality, never for ordering. When you genuinely need
> "after the thing exists", record what you saw and act **on a later tick**.

## Reading state

`store.getComponent(ref, SomeComponent.getComponentType())` — the component or `null`.
`store.ensureAndGetComponent(ref, type)` is get-or-create, and is therefore a structural
write with all of the above attached.

**Prefer the pure-ECS path over legacy wrappers.** Where a convenience object exposes the
same data, it is frequently `@Deprecated(forRemoval = true)` while the component read is
not — and the deprecated path vanishes on the next engine bump. Read the components.

**Not every entity carries every "obvious" component.** The legacy entity wrapper is
opt-in per entity, so a ground item — assembled from a handful of components — never has
one. Any hook that lives on that wrapper is therefore **dead code for items**, which is
exactly the trap documented in the event catalog. When a system does not fire, check
what the target entity is actually built from before assuming the hook is wrong.

**A player who is offline still has components — on disk.** The player storage service
loads a saved player as a **detached holder** (a future, resolved on a storage thread), and
the same component types read off it exactly as off a live `Ref`. That is how you answer
"what is this UUID's name / stored state" without requiring them to be logged in. Two rules
come with it: the holder is detached, so **writes there are not a live entity's state**, and
you are on a pool thread when it resolves — hop back to the world thread before touching
the store, and before replying to anything that expects to be answered there. For a bulk
scan, batch the futures rather than loading serially; player files are not small.

## Your own events: the integration surface

A mod can define an event and dispatch it so any system — yours or a third party's — reacts,
**with no coupling to the producer**. This is how you give other mods something to hook.

1. **Define** — extend `EcsEvent` (plain notification; make it immutable) or
   `CancellableEcsEvent` (adds `isCancelled()` / `setCancelled(…)`).
2. **⚠️ Register the type at `setup()`** — `registerEntityEventType(MyEvent.class)`.
   **`store.invoke` looks the class up in the registry and, if the type was never
   registered, returns silently.** The type is not created lazily, and `registerSystem`
   does **not** register it. An unregistered event dispatches to nobody, with no error.
3. **Consume** with an ordinary `EntityEventSystem<EntityStore, MyEvent>`. Any number of
   independent systems receive the same event, each still filtered by its own query.
4. **Dispatch** with `store.invoke(ref, event)`:
   - **Synchronous, same call stack** — handlers run before `invoke` returns, so a
     cancellable event or a mutable payload can be read back immediately after.
   - **Re-entrant** — safe from inside a ticking system *and* from inside another handler.
     `invoke` is not a structural write, so it does not trip the processing assert; a
     *consumer* that needs one still defers through its own `CommandBuffer`.

Design the payload as the thing a consumer needs, not as your internals: the id, the
resulting stack, the outcome. A well-shaped mod event turns your own optional features into
consumers of it, which is the test that it is shaped right.

## Two dispatch systems, and choosing the right registration

The engine has **two** ways of announcing things, and they take different registrations:

- **ECS events** — dispatched to an entity via `store.invoke`, consumed by an
  `EntityEventSystem` registered with `registerSystem`. Entity-targeted, query-filtered.
- **The event bus** — classic listeners, frequently **keyed by world name**. Subscribe with
  `getEventRegistry().registerGlobal(SomeEvent.class, consumer)` to sidestep the per-world
  keying; a plain `register` binds to one world's key and will silently miss the others.

When a hook exists in both flavors, prefer the ECS one — in at least one documented case
the bus version is `@Deprecated(forRemoval = true)` and awkward to register globally while
the ECS version is the surviving, blessed hook.

## Registering at boot

`setup()` is the place: components, event types and systems, before any store starts. Hold
the `ComponentType` a registration returns — it is the handle every later read needs.

Anything you inject into asset stores has its own, later hook and its own rules; see
`hytale-assets`.
