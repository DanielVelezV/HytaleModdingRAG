---
name: hytale-scaffold
description: "Use when the user asks to create a new Hytale mod project. Covers project structure, Gradle setup, IntelliJ configuration, hot reload, and the dev server boot workflow."
metadata:
  type: reference
---

# Hytale Mod Scaffold

Use the `create_mod` MCP tool to generate a complete, ready-to-build Hytale mod project. This skill documents what gets generated, why each piece exists, and how to guide the user through first-run setup.

## When to use

- User says "create a mod", "new mod", "scaffold a project", "start a new plugin"
- User asks how to set up a Hytale mod from scratch

## The `create_mod` tool

```
create_mod(
    name="MyMod",           # PascalCase. Becomes class name, jar name, manifest Name.
    output_dir="C:/Users/X/Desktop",  # Where to create the MyMod/ folder.
    group="com.mymod",      # Java package. Default: com.<name_lowercase>
    author="PlayerName"     # For manifest.json Authors field.
)
```

The tool creates a deterministic, self-contained Gradle project. Every file is generated — no manual setup needed beyond copying HytaleServer.jar and opening in IntelliJ.

## What gets generated

```
MyMod/
  build.gradle                    # Shadow plugin, compileOnly against HytaleServer.jar
  settings.gradle                 # rootProject.name = 'mymod'
  gradle.properties               # JVM args, Java 25 instructions
  .gitignore                      # Ignores build/, .idea/*, libs/, server/, run/
  run/                            # Working directory for Hytale Server
    mods/                         # Mod jars loaded at runtime
  server/
    boot-server.ps1               # Standalone dev server launcher (no IntelliJ needed)
  src/main/
    java/com/mymod/
      MyModPlugin.java            # Extends JavaPlugin, setup/start/shutdown lifecycle
    resources/
      manifest.json               # Name, Version, Main class, LoadOrder, ServerVersion
  .idea/
    runConfigurations/
      Hytale_Server.xml           # Application run config with hot reload
      Build.xml                   # gradle build
      Clean_Build.xml             # gradle clean build
      ShadowJar.xml               # gradle shadowJar
  gradle/
    wrapper/
      gradle-wrapper.properties   # Gradle 9.2.1
      gradle-wrapper.jar          # (copied from Hythaum if available)
  gradlew                         # (copied from Hythaum if available)
  gradlew.bat
```

## Key design decisions

### build.gradle

- **`compileOnly`** for HytaleServer.jar — the server provides these classes at runtime, they must NOT be bundled in the mod jar.
- **Shadow plugin** (`com.gradleup.shadow:8.3.0`) — bundles only the mod's own dependencies (if any). Excludes `com.hypixel` packages.
- **Java 25 toolchain** — HytaleServer.jar is compiled for class file major 69 (Java 25). The mod must target the same version.
- **`copyServerJar` task** — auto-copies HytaleServer.jar from `server/` to `libs/` on first compile so the user only needs to place it in one location.
- Default `jar` task is disabled to avoid conflicts with shadowJar.

### manifest.json

```json
{
  "Group": "com.mymod",
  "Name": "MyMod",
  "Version": "1.0.0",
  "Main": "com.mymod.MyModPlugin",
  "LoadOrder": "POSTWORLD",
  "ServerVersion": "^0.6.3"
}
```

- **LoadOrder: POSTWORLD** — mod initializes after worlds are loaded, which is the safe default for most mods.
- **ServerVersion: ^0.6.3** — semver range matching the current Hytale server.
- **Name** — this is what the server uses to identify the mod. Must match the jar file name pattern.

### Plugin class

Extends `JavaPlugin` with the standard lifecycle:
- `setup()` — register components, events, systems. Called during server startup.
- `start()` — called after all plugins are set up. Safe to interact with other mods here.
- `shutdown()` — cleanup. Null out static instance to prevent leaks.

Uses `HytaleLogger.forEnclosingClass()` — the engine's logger, NOT `java.util.logging.Logger`.

### Hytale Server run config (hot reload)

The `Hytale_Server.xml` run configuration is an **Application** type that launches the Hytale server directly from IntelliJ:

- **Main class:** `com.hypixel.hytale.Main`
- **Program arguments:** `--allow-op --disable-sentry`
- **VM options:** `-XX:+AllowEnhancedClassRedefinition` — enables live class redefinition
- **Working directory:** `$PROJECT_DIR$/run`
- **Before launch:** Make (builds the project first)

This is the primary way to develop — it replaces the boot-server.ps1 workflow for IntelliJ users.

### boot-server.ps1

The standalone dev server launcher (for users without IntelliJ or for CI):
1. Probes for a Java 25+ runtime (checks `MOD_JAVA` env, `JAVA_HOME`, PATH, common install dirs)
2. Reads the mod name/version from `manifest.json` to find the correct jar in `build/libs/`
3. Copies the jar into `run/mods/` (only replaces the mod's own jar, preserves other mods)
4. Launches `com.hypixel.hytale.Main` from `%APPDATA%\Hytale\install\release\package\game\latest\Server\HytaleServer.jar`
5. Passes `--allow-op --disable-sentry --assets=<path to Assets.zip>`

**Requires Hytale installed** — the server runs from the game's installed files.

## First-run instructions (give these to the user)

1. **Copy `HytaleServer.jar`** into the project's `libs/` folder (or `server/` — the build auto-copies it)
2. **Copy `Assets.zip`** into the `run/` folder (from `%APPDATA%\Hytale\install\release\package\game\latest`)
3. **Open the project** in IntelliJ IDEA
4. **Set Gradle JDK to Java 25**: Settings > Build, Execution, Deployment > Build Tools > Gradle > Gradle JDK
5. **Reload Gradle**: click the elephant icon in the Gradle tool window
6. **Select "Hytale Server"** from the run configurations dropdown (top-right) and hit Run
7. **Connect**: launch Hytale client, connect to `localhost`

## Hot reload workflow

The "Hytale Server" run config launches with `-XX:+AllowEnhancedClassRedefinition`, which enables the JVM to hot-swap changed classes at runtime.

### How it works

1. Edit code in IntelliJ
2. Press **Ctrl+F9** (Build Project) — IntelliJ recompiles the changed classes
3. The JVM picks up the new class definitions automatically — **no server restart needed**

### What can be hot-reloaded

- Method body changes (add/modify logic inside existing methods)
- Field value changes

### What requires a restart

- Adding new classes
- Changing class hierarchy (extends/implements)
- Adding/removing fields or methods (structural changes)
- Changes to `manifest.json`

For these, stop the server and re-run the "Hytale Server" config.

### Alternative: boot-server.ps1

If not using IntelliJ:
1. Run `.\gradlew shadowJar` in terminal
2. Run `.\server\boot-server.ps1`
3. For changes, stop the server, rebuild, and relaunch (~2 seconds)

## Common issues

| Problem | Cause | Fix |
|---------|-------|-----|
| `UnsupportedClassVersionError` | Wrong Java version | Set Gradle JDK to Java 25 in IntelliJ settings |
| `no Java 25+ runtime found` | boot-server.ps1 can't find Java 25 | Set `$env:MOD_JAVA` to your java.exe path |
| `no <Name>-1.0.0.jar in build\libs` | Haven't built yet | Run `.\gradlew shadowJar` first |
| 118 MB jar file | Server classes bundled | Check shadowJar excludes `com.hypixel` — see [[hytale-plugin]] |
| Plugin not loading | Wrong Main class in manifest | Verify `Main` matches the fully qualified class name |
| `compileOnly` errors | Missing HytaleServer.jar | Copy it into `libs/` or `server/` |
| Hot reload not working | Missing VM flag | Ensure run config has `-XX:+AllowEnhancedClassRedefinition` |

## Related skills

- [[hytale-plugin]] — Plugin lifecycle, manifest deep-dive, version management
- [[hytale-ecs]] — Registering components and systems in `setup()`
- [[hytale-command]] — Adding commands
- [[hytale-assets]] — Asset injection and hot reload
- [[hytale-config]] — Plugin configuration files
