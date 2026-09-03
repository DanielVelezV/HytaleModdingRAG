# Dev helper: boot a local Hytale server against an ALREADY-BUILT plugin jar,
# with NO Gradle / NO recompile. Copies the mod jar into run\mods and launches
# com.hypixel.hytale.Main. Only your own jar is refreshed - any other mods you
# place in run\mods are left in place.
#
#   First time:                              .\setup.ps1
#   Build once (only when code changed):     .\gradlew shadowJar
#   Boot (repeat freely, no recompile):      .\boot-server.ps1
#   Stop (graceful, saves inventory):        type `stop` in the server console
#
# Paths are auto-detected; override with env vars if your setup differs:
#   MOD_JAVA      (full path to a java.exe; skips the probe below)
#   MOD_MANIFEST  (path to manifest.json; default: src\main\resources\manifest.json)
$ErrorActionPreference = 'Stop'

$ServerDir = $PSScriptRoot
$Proj = Split-Path $ServerDir -Parent

# --- Check server files exist, run setup if not ---
$ServerJar = Join-Path $ServerDir 'HytaleServer.jar'
$AssetsZip = Join-Path $ServerDir 'Assets.zip'
if (-not (Test-Path $ServerJar) -or -not (Test-Path $AssetsZip)) {
    Write-Host "[boot] Server files not found. Running first-time setup..."
    & (Join-Path $ServerDir 'setup.ps1')
    if (-not (Test-Path $ServerJar) -or -not (Test-Path $AssetsZip)) {
        Write-Error "Setup did not produce server files. Cannot continue."
        exit 1
    }
}

$MinJavaMajor = 25

function Get-JavaMajor([string] $exe) {
    if (-not $exe) { return 0 }

    $jdkHome = Split-Path (Split-Path $exe -Parent) -Parent
    $release = Join-Path $jdkHome 'release'
    if (Test-Path $release) {
        $hit = Select-String -Path $release -Pattern '^JAVA_VERSION="?(\d+)' -ErrorAction SilentlyContinue |
               Select-Object -First 1
        if ($hit) { return [int] $hit.Matches[0].Groups[1].Value }
    }

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

$Run  = Join-Path $Proj 'run'
$Mods = Join-Path $Run 'mods'
New-Item -ItemType Directory -Force $Mods | Out-Null
Get-ChildItem "$Mods\$Name-*.jar" -ErrorAction SilentlyContinue | Remove-Item -Force
Copy-Item $Jar.FullName $Mods
$Others = @(Get-ChildItem "$Mods\*.jar","$Mods\*.zip" -ErrorAction SilentlyContinue |
           Where-Object { $_.Name -notlike "$Name-*" })
Write-Host "[boot] java: $Java"
Write-Host "[boot] mod loaded: $($Jar.Name)  ($($Jar.LastWriteTime.ToString('HH:mm:ss')))"
Write-Host "[boot] other mods kept in run\mods: $($Others.Count)"
Write-Host "[boot] type 'stop' in the console to shut down gracefully"

Push-Location $Run
try {
    & $Java -cp $ServerJar com.hypixel.hytale.Main `
        --allow-op --disable-sentry "--assets=$AssetsZip"
}
finally {
    Pop-Location
}
