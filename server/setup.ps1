# First-run setup: downloads the Hytale server using the official downloader.
# Extracts HytaleServer.jar and Assets.zip into the server/ directory.
#
# Usage:
#   .\setup.ps1                   # interactive — prompts for patchline
#   .\setup.ps1 -Patchline release
#
# After setup, both HytaleServer.jar and Assets.zip live in server/ and the
# project is ready to build and run.
param(
    [string] $Patchline
)
$ErrorActionPreference = 'Stop'

$ServerDir = $PSScriptRoot
$ProjectDir = Split-Path $ServerDir -Parent
$Downloader = Join-Path $ServerDir 'hytale-downloader-windows-amd64.exe'

if (-not (Test-Path $Downloader)) {
    Write-Error "hytale-downloader-windows-amd64.exe not found in $ServerDir"
    exit 1
}

# Check if already set up
$ServerJar = Join-Path $ServerDir 'HytaleServer.jar'
$AssetsZip = Join-Path $ServerDir 'Assets.zip'
if ((Test-Path $ServerJar) -and (Test-Path $AssetsZip)) {
    Write-Host "[setup] Server files already present:"
    Write-Host "        HytaleServer.jar: $('{0:N1} MB' -f ((Get-Item $ServerJar).Length / 1MB))"
    Write-Host "        Assets.zip:       $('{0:N1} MB' -f ((Get-Item $AssetsZip).Length / 1MB))"
    $redownload = Read-Host "Re-download? (y/N)"
    if ($redownload -ne 'y') {
        Write-Host "[setup] Keeping existing files."
        exit 0
    }
}

# Prompt for patchline if not provided
if (-not $Patchline) {
    Write-Host ""
    Write-Host "=== Hytale Server Setup ==="
    Write-Host ""
    Write-Host "Available patchlines:"
    Write-Host "  1) release       (stable, recommended)"
    Write-Host "  2) pre-release   (latest features, may be unstable)"
    Write-Host ""
    $choice = Read-Host "Select patchline [1]"
    switch ($choice) {
        '2'     { $Patchline = 'pre-release' }
        default { $Patchline = 'release' }
    }
}

Write-Host ""
Write-Host "[setup] Patchline: $Patchline"

# Download the game package — the downloader handles OAuth2 authentication.
# On first run it prints a URL + code; open the URL in your browser to log in.
$DownloadPath = Join-Path $ServerDir 'game.zip'
Write-Host "[setup] Downloading server files (this may take a few minutes)..."
Write-Host ""
& $Downloader -patchline $Patchline -download-path $DownloadPath
if ($LASTEXITCODE -ne 0) {
    Write-Error "Download failed."
    exit 1
}

if (-not (Test-Path $DownloadPath)) {
    Write-Error "Download completed but game.zip not found at $DownloadPath"
    exit 1
}

# Extract server files
Write-Host "[setup] Extracting server files..."
$ExtractDir = Join-Path $ServerDir '_extract'
if (Test-Path $ExtractDir) { Remove-Item $ExtractDir -Recurse -Force }
Expand-Archive -Path $DownloadPath -DestinationPath $ExtractDir -Force

# Find HytaleServer.jar and Assets.zip in the extracted contents
$foundJar = Get-ChildItem $ExtractDir -Recurse -Filter 'HytaleServer.jar' -ErrorAction SilentlyContinue | Select-Object -First 1
$foundAssets = Get-ChildItem $ExtractDir -Recurse -Filter 'Assets.zip' -ErrorAction SilentlyContinue | Select-Object -First 1

if (-not $foundJar) {
    Write-Error "HytaleServer.jar not found in downloaded package."
    Write-Host "Contents of download:"
    Get-ChildItem $ExtractDir -Recurse | ForEach-Object { Write-Host "  $($_.FullName.Replace($ExtractDir, ''))" }
    exit 1
}
if (-not $foundAssets) {
    Write-Error "Assets.zip not found in downloaded package."
    exit 1
}

Copy-Item $foundJar.FullName $ServerJar -Force
Copy-Item $foundAssets.FullName $AssetsZip -Force

# Cleanup
Remove-Item $ExtractDir -Recurse -Force
Remove-Item $DownloadPath -Force

# Copy HytaleServer.jar to libs/ for compilation
$LibsDir = Join-Path $ProjectDir 'libs'
if (-not (Test-Path $LibsDir)) { New-Item -ItemType Directory -Force $LibsDir | Out-Null }
Copy-Item $ServerJar (Join-Path $LibsDir 'HytaleServer.jar') -Force

Write-Host ""
Write-Host "[setup] Done! Server files installed:"
Write-Host "        server/HytaleServer.jar: $('{0:N1} MB' -f ((Get-Item $ServerJar).Length / 1MB))"
Write-Host "        server/Assets.zip:       $('{0:N1} MB' -f ((Get-Item $AssetsZip).Length / 1MB))"
Write-Host "        libs/HytaleServer.jar:   (copy for compilation)"
Write-Host ""
Write-Host "[setup] Next steps:"
Write-Host "        1. Open the project in IntelliJ IDEA"
Write-Host "        2. Set Gradle JDK to Java 25"
Write-Host "        3. Select 'Hytale Server' run config and hit Run"
