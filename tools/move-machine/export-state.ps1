<#
.SYNOPSIS
Packs the mtg-forge-ai run state on this PC and sends it, with setup-target.ps1, to the always-on PC over Taildrop.

.DESCRIPTION
Run on the SOURCE machine with no Forge job running:

    powershell -ExecutionPolicy Bypass -File tools\move-machine\export-state.ps1

It refuses to pack while java, simrunner or setmodel runs, or while either repo has uncommitted or unpushed work,
because the target clones both repos from GitHub and only the git-ignored state travels in the bundle.

The bundle holds runs\ (without runs\venv and without leftover worker junctions), models\, data\, results\ and a
move-manifest.json that records the source paths and commits. setup-target.ps1 reads that manifest to rewrite the
absolute paths the run state stores.
#>
param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [string]$Target = "desktop-6dubsjt",
    [string]$OutDir = (Join-Path $env:LOCALAPPDATA "Temp\forgeai-move"),
    [switch]$NoSend
)
$ErrorActionPreference = "Stop"

function Fail([string]$msg) { Write-Host "STOP: $msg" -ForegroundColor Red; exit 1 }
function Step([string]$msg) { Write-Host "== $msg" -ForegroundColor Cyan }

$forgeRoot = Join-Path (Split-Path $ProjectRoot -Parent) "forge"
if (-not (Test-Path (Join-Path $ProjectRoot "simrunner"))) { Fail "$ProjectRoot is not the mtg-forge-ai checkout." }
if (-not (Test-Path (Join-Path $forgeRoot "forge-gui-desktop"))) { Fail "no Forge clone at $forgeRoot." }

Step "Nothing may write to runs\ while it is packed"
if (Get-Process java -ErrorAction SilentlyContinue) { Fail "a java process is running. Let the Forge job stop first." }
$busy = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'simrunner|setmodel' }
if ($busy) { Fail "simrunner or setmodel is running (pid $(@($busy.ProcessId) -join ', '))." }

Step "Both repos clean and pushed"
foreach ($repo in @($ProjectRoot, $forgeRoot)) {
    $dirty = git -C $repo status --porcelain --untracked-files=no
    if ($dirty) { Fail "$repo has uncommitted changes:`n$dirty" }
    git -C $repo fetch -q origin
    $ahead = git -C $repo rev-list --count "@{u}..HEAD"
    if ([int]$ahead -gt 0) { Fail "$repo has $ahead commit(s) that aren't pushed." }
}

Step "Manifest"
$java = Get-ChildItem "C:\Program Files\Eclipse Adoptium" -Directory -Filter "jdk-21*" -ErrorAction SilentlyContinue |
    Sort-Object Name -Descending | Select-Object -First 1
$manifest = [ordered]@{
    made                = (Get-Date).ToString("s")
    source_host         = $env:COMPUTERNAME
    source_parent       = (Split-Path $ProjectRoot -Parent)
    source_java_bin     = $(if ($java) { Join-Path $java.FullName "bin" } else { $null })
    mtg_forge_ai_commit = (git -C $ProjectRoot rev-parse HEAD)
    forge_branch        = (git -C $forgeRoot rev-parse --abbrev-ref HEAD)
    forge_commit        = (git -C $forgeRoot rev-parse HEAD)
}
$manifestPath = Join-Path $ProjectRoot "runs\move-manifest.json"
[IO.File]::WriteAllText($manifestPath, ($manifest | ConvertTo-Json), (New-Object Text.UTF8Encoding $false))
$manifest.GetEnumerator() | ForEach-Object { "  {0,-20} {1}" -f $_.Key, $_.Value }

Step "Leftover worker junctions (left out of the bundle)"
$junctions = Get-ChildItem (Join-Path $ProjectRoot "runs"), (Join-Path $ProjectRoot "models") -Recurse -Force `
        -Attributes ReparsePoint -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notlike "*\runs\venv\*" }
$excludes = @("--exclude=runs/venv")
foreach ($j in $junctions) {
    $rel = $j.FullName.Substring($ProjectRoot.Length + 1).Replace("\", "/")
    $excludes += "--exclude=$rel"
    "  $rel"
}

Step "Packing"
New-Item -ItemType Directory -Force $OutDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmm"
$bundle = Join-Path $OutDir "forgeai-state-$stamp.tgz"
$parts = @("runs", "models", "data", "results") | Where-Object { Test-Path (Join-Path $ProjectRoot $_) }
# Windows' own bsdtar, not Git's GNU tar, so drive-letter paths work
& "$env:SystemRoot\System32\tar.exe" -czf $bundle -C $ProjectRoot @excludes @parts
if ($LASTEXITCODE -ne 0) { Fail "tar exited $LASTEXITCODE." }
$hash = (Get-FileHash $bundle -Algorithm SHA256).Hash.ToLower()
"$hash  $(Split-Path $bundle -Leaf)" | Set-Content -Encoding ascii "$bundle.sha256"
$size = [math]::Round((Get-Item $bundle).Length / 1GB, 2)
"  $bundle  $size GB  sha256 $hash"

$setup = Join-Path $PSScriptRoot "setup-target.ps1"
if ($NoSend) {
    Step "Not sent (-NoSend). Copy these three files to the target's Downloads folder:"
    "  $bundle"; "  $bundle.sha256"; "  $setup"
    exit 0
}

Step "Sending to $Target over Taildrop (lands in the target user's Downloads)"
$ts = "C:\Program Files\Tailscale\tailscale.exe"
if (-not (Test-Path $ts)) { Fail "Tailscale isn't installed here. Rerun with -NoSend and copy the files by hand." }
foreach ($f in @($setup, "$bundle.sha256", $bundle)) {
    & $ts file cp $f "${Target}:"
    if ($LASTEXITCODE -ne 0) { Fail "tailscale file cp failed for $f." }
    "  sent $(Split-Path $f -Leaf)"
}
Step "Done. On $Target, accept the files if Tailscale asks, then run:"
"  powershell -ExecutionPolicy Bypass -File `$HOME\Downloads\setup-target.ps1"
