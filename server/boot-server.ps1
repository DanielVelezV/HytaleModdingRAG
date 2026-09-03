# Dev helper: boot a local Hytale server against an ALREADY-BUILT plugin jar,
# with NO Gradle / NO recompile. Copies the mod jar into run\mods and launches
# com.hypixel.hytale.Main. Only your own jar is refreshed - any other mods you
# place in run\mods are left in place.
#
#   Build once (only when code changed):   .\gradlew shadowJar
#   Boot (repeat freely, no recompile):    .\boot-server.ps1
#   Stop (graceful, saves inventory):      type `stop` in the server console
#
# Paths are auto-detected; override with env vars if your setup differs:
#   HYTALE_HOME   (default: %APPDATA%\Hytale)
#   MOD_JAVA      (full path to a java.exe; skips the probe below)
#   MOD_MANIFEST  (path to manifest.json; default: src\main\resources\manifest.json)
#
# The first run creates run\ and run\mods\ automatically (New-Item -Force
# below) - you don't need to create them by hand.
$ErrorActionPreference = 'Stop'

$Proj = $PSScriptRoot
$HytaleHome = if ($env:HYTALE_HOME) { $env:HYTALE_HOME } else { Join-Path $env:APPDATA 'Hytale' }
$HH   = Join-Path $HytaleHome 'install\release\package\game\latest'

# HytaleServer.jar is compiled for Java 25 (class file major 69). Neither `java`
# on PATH nor JAVA_HOME can be trusted to point at one: JAVA_HOME is commonly set
# by some other tool (an IDE's bundled JBR, for one) and, being checked first,
# silently wins and dies at launch with UnsupportedClassVersionError. So probe
# for a runtime that can actually run the server instead of assuming.
$MinJavaMajor = 25

function Get-JavaMajor([string] $exe) {
    if (-not $exe) { return 0 }

    # Read the JDK's own `release` manifest rather than running `java -version`.
    # That prints to STDERR, and under the $ErrorActionPreference = 'Stop' set
    # above, a `2>&1` redirect of a native command turns every captured line into
    # a terminating error - so the obvious implementation silently reports 0 for
    # every candidate and the probe finds nothing.
    $jdkHome = Split-Path (Split-Path $exe -Parent) -Parent
    $release = Join-Path $jdkHome 'release'
    if (Test-Path $release) {
        $hit = Select-String -Path $release -Pattern '^JAVA_VERSION="?(\d+)' -ErrorAction SilentlyContinue |
               Select-Object -First 1
        if ($hit) { return [int] $hit.Matches[0].Groups[1].Value }
    }

    # Fallback for a layout with no `release` file: ask the runtime, capturing
    # stderr through the process API instead of the shell redirect.
    try {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName               = $exe
        $psi.Arguments              = '-version'
        $psi.RedirectStandardError  = $true
        $psi.UseShellExecute        = $false
        $psi.CreateNoWindow         = $true
        $proc = [System.Diagnostics.Process]::Start($psi)
        $out  = $proc.StandardError.ReadToEnd()
        $proc.WaitForExit()
        # Both "1.8.0_x" and "25.0.2" shapes; we only ever care about >= 9.
        if ($out -match 'version "(\d+)') { return [int] $Matches[1] }
    } catch { }

    return 0
}

$Candidates = @()
if ($env:MOD_JAVA)   { $Candidates += $env:MOD_JAVA }
if ($env:JAVA_HOME)  { $Candidates += (Join-Path $env:JAVA_HOME 'bin\java.exe') }
$OnPath = (Get-Command java -ErrorAction SilentlyContinue).Source
if ($OnPath)         { $Candidates += $OnPath }
foreach ($root in @("$env:ProgramFiles\Eclipse Adoptium", "$env:ProgramFiles\Java",
                    "$env:ProgramFiles\Microsoft", "$env:USERPROFILE\.jdks")) {
    if (Test-Path $root) {
        # Newest-named first, so a 25 is found before a 21 sitting beside it.
        Get-ChildItem $root -Directory -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending |
            ForEach-Object { $Candidates += (Join-Path $_.FullName 'bin\java.exe') }
    }
}

$Java = $null
foreach ($c in ($Candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -Unique)) {
    if ((Get-JavaMajor $c) -ge $MinJavaMajor) { $Java = $c; break }
}
if (-not $Java) {
    Write-Error ("no Java $MinJavaMajor+ runtime found (the server needs it; class file major 69). " +
                 "Install one, or point MOD_JAVA at its java.exe.")
    exit 1
}

# The jar is asked for BY NAME, built from manifest.json's Name + Version - the
# one place your plugin's version is written by hand, and (by the usual
# shadowJar convention) what the build names the jar after. A glob would have
# to exclude any -sources/-javadoc jars sitting beside it, and would happily
# boot a stale jar left over from before a version bump; this way a missing
# file says "rebuild" instead, which is the truth.
$ManifestPath = if ($env:MOD_MANIFEST) { $env:MOD_MANIFEST } else { Join-Path $Proj 'src\main\resources\manifest.json' }
$Manifest = Get-Content $ManifestPath -Raw | ConvertFrom-Json
$Name    = $Manifest.Name
$Version = $Manifest.Version
$JarPath = Join-Path $Proj "build\libs\$Name-$Version.jar"
if (-not (Test-Path $JarPath)) {
    Write-Error "no $Name-$Version.jar in build\libs - run: .\gradlew shadowJar"
    exit 1
}
$Jar = Get-Item $JarPath

$Run  = "$Proj\run"
$Mods = "$Run\mods"
# Replace ONLY your own jar, leaving any other mods you dropped in run\mods
# intact (so the dev server can test cross-mod behavior).
New-Item -ItemType Directory -Force $Mods | Out-Null
Get-ChildItem "$Mods\$Name-*.jar" -ErrorAction SilentlyContinue | Remove-Item -Force
Copy-Item $Jar.FullName $Mods
$Others = @(Get-ChildItem "$Mods\*.jar","$Mods\*.zip" -ErrorAction SilentlyContinue |
           Where-Object { $_.Name -notlike "$Name-*" })
Write-Host "[boot] java: $Java"
Write-Host "[boot] mod loaded: $($Jar.Name)  ($($Jar.LastWriteTime.ToString('HH:mm:ss')))"
Write-Host "[boot] other mods kept in run\mods: $($Others.Count)"
Write-Host "[boot] type 'stop' in the console to shut down gracefully"

# The server needs run/ as its working dir, but we must not leave the caller's
# shell parked there after `stop`. Push in, run, and always pop back - even on
# Ctrl+C - via finally.
Push-Location $Run
try {
    & $Java -cp "$HH\Server\HytaleServer.jar" com.hypixel.hytale.Main `
        --allow-op --disable-sentry "--assets=$HH\Assets.zip"
}
finally {
    Pop-Location
}