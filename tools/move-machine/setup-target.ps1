<#
.SYNOPSIS
Sets up the mtg-forge-ai run on a fresh Windows PC from the bundle export-state.ps1 made, and checks it plays the
same games as the source machine.

.DESCRIPTION
Run on the TARGET machine, from a normal (not admin) PowerShell; winget asks for elevation itself:

    powershell -ExecutionPolicy Bypass -File $HOME\Downloads\setup-target.ps1

Steps, each skipped when already done, so a rerun after a failure picks up where it stopped:
  1. Preflight: disk, RAM, cores, GPU, Windows Update active hours (read only).
  2. Tools: Git, Temurin JDK 21, Python 3.13 by winget; Maven 3.9.9 from archive.apache.org into LOCALAPPDATA.
  3. Clones: the forge fork (branch from the manifest) and mtg-forge-ai, both at or past the manifest's commits.
  4. Forge build plus its own sim profile (never %APPDATA%\Forge).
  5. runs\venv from requirements-dev.txt (pinned CUDA 12.6 PyTorch, the last with a Pascal wheel).
  6. Unpack the bundle, then rewrite the source machine's absolute paths and JDK path in the run state.
  7. Replays two seeded batches whose record digests are known: stock heuristic (8467fad7cb41) and full sim
     with onnx:gen0 (17b0cb30a266). Both must match, or the new machine plays different games.
#>
param(
    [string]$Root = (Join-Path $env:USERPROFILE "Projects"),
    [string]$Bundle,
    [switch]$SkipCheck
)
$ErrorActionPreference = "Stop"
$utf8 = New-Object Text.UTF8Encoding $false

function Fail([string]$msg) { Write-Host "STOP: $msg" -ForegroundColor Red; exit 1 }
function Warn([string]$msg) { Write-Host "WARN: $msg" -ForegroundColor Yellow }
function Step([string]$msg) { Write-Host "== $msg" -ForegroundColor Cyan }
function Refresh-Path {
    $env:PATH = [Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" + [Environment]::GetEnvironmentVariable("PATH", "User")
}
function Winget-Install([string]$id, [string]$version) {
    $wargs = @("install", "--id", $id, "--exact", "--accept-package-agreements", "--accept-source-agreements", "--silent")
    if ($version) { $wargs += @("--version", $version) }
    & winget @wargs | Out-Host
    return $LASTEXITCODE
}

# ---------------------------------------------------------------------------------------------------------------
Step "1. Preflight"
if (-not $Bundle) {
    $found = Get-ChildItem (Join-Path $env:USERPROFILE "Downloads") -Filter "forgeai-state-*.zip" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $found) { Fail "no forgeai-state-*.zip in Downloads. Pass -Bundle <path>." }
    $Bundle = $found.FullName
}
"  bundle   $Bundle"
New-Item -ItemType Directory -Force $Root | Out-Null
$drive = Get-PSDrive ((Resolve-Path $Root).Path.Substring(0, 1))
$freeGb = [math]::Round($drive.Free / 1GB, 1)
$os = Get-CimInstance Win32_OperatingSystem
$ramGb = [math]::Round($os.TotalVisibleMemorySize / 1MB, 1)
$cores = [Environment]::ProcessorCount
"  root     $Root ($freeGb GB free)"
"  memory   $ramGb GB, $cores logical cores"
if ($freeGb -lt 15) { Fail "needs about 15 GB free (bundle, unpacked state, venv, Forge clone and Maven cache)." }
# The ladder's finished batches were played at 4 workers and reuse depends on it; each JVM peaks near 1.1 GB.
if ($cores -lt 6) { Warn "$cores logical cores. The ladder runs at 4 workers; fewer than 6 cores leaves little for the OS." }
if ($ramGb -lt 12) { Warn "$ramGb GB RAM. 4 Forge JVMs plus training want about 12 GB." }
$gpu = $null
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    $gpu = cmd /c "nvidia-smi --query-gpu=name --format=csv,noheader 2>nul" | Select-Object -First 1
}
if ($gpu) { "  gpu      $gpu" } else { Warn "no NVIDIA GPU found. Training falls back to the CPU: slower, but the data is small." }
if ($gpu -and $gpu -match "RTX 50|B100|B200") {
    Warn "torch 2.14.1+cu126 is pinned for the 1080 Ti and may not run on $gpu. See docs/training.md before training."
}
$au = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings" -ErrorAction SilentlyContinue
if ($au -and $null -ne $au.ActiveHoursStart) {
    "  updates  active hours $($au.ActiveHoursStart):00 to $($au.ActiveHoursEnd):00"
}
Warn "Windows Update restarted the source PC mid-run on 2026-11-11. On an always-on box, pause updates or set active hours to cover the run (Settings > Windows Update)."

# ---------------------------------------------------------------------------------------------------------------
Step "2. Tools"
if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { Fail "winget is missing. Install 'App Installer' from the Microsoft Store." }
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Winget-Install "Git.Git" | Out-Null; Refresh-Path }
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Fail "git still not on PATH. Open a new PowerShell and rerun." }

# Temurin 21: ONNX Runtime 1.31 needs the msvcp140.dll the JDK ships to be 14.40 or newer. 21.0.13+9 ships 14.40;
# Oracle JDK 21.0.2 ships 14.31 and crashes the evaluator.
function Find-Temurin {
    Get-ChildItem "C:\Program Files\Eclipse Adoptium" -Directory -Filter "jdk-21*" -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending | Select-Object -First 1
}
$jdk = Find-Temurin
if (-not $jdk) {
    if ((Winget-Install "EclipseAdoptium.Temurin.21.JDK" "21.0.13.9") -ne 0) { Winget-Install "EclipseAdoptium.Temurin.21.JDK" | Out-Null }
    $jdk = Find-Temurin
}
if (-not $jdk) { Fail "Temurin 21 didn't install." }
$javaBin = Join-Path $jdk.FullName "bin"
$msvcp = Get-Item (Join-Path $javaBin "msvcp140.dll") -ErrorAction SilentlyContinue
if ($msvcp -and [version]$msvcp.VersionInfo.FileVersion.Split(" ")[0] -lt [version]"14.40") {
    Fail "$javaBin\msvcp140.dll is $($msvcp.VersionInfo.FileVersion); ONNX Runtime needs 14.40+. Install Temurin 21.0.13 or newer."
}
"  java     $javaBin"

$mvnHome = Join-Path $env:LOCALAPPDATA "Programs\apache-maven-3.9.9"
if (-not (Test-Path (Join-Path $mvnHome "bin\mvn.cmd"))) {
    $zip = Join-Path $env:TEMP "apache-maven-3.9.9-bin.zip"
    $base = "https://archive.apache.org/dist/maven/maven-3/3.9.9/binaries/apache-maven-3.9.9-bin.zip"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest $base -OutFile $zip -UseBasicParsing
    $want = ((Invoke-WebRequest "$base.sha512" -UseBasicParsing).Content -split "\s+")[0].Trim().ToLower()
    $got = (Get-FileHash $zip -Algorithm SHA512).Hash.ToLower()
    if ($want -ne $got) { Fail "Maven download checksum mismatch." }
    Expand-Archive $zip -DestinationPath (Join-Path $env:LOCALAPPDATA "Programs") -Force
}
"  maven    $mvnHome"

function Has-Python313 {
    if (-not (Get-Command py -ErrorAction SilentlyContinue)) { return $false }
    cmd /c "py -3.13 --version >nul 2>&1"
    return ($LASTEXITCODE -eq 0)
}
if (-not (Has-Python313)) { Winget-Install "Python.Python.3.13" | Out-Null; Refresh-Path }
if (-not (Has-Python313)) { Fail "Python 3.13 isn't reachable as 'py -3.13'. Open a new PowerShell and rerun." }
"  python   $(cmd /c 'py -3.13 --version')"

# ---------------------------------------------------------------------------------------------------------------
Step "3. Clones"
$tmp = Join-Path $env:TEMP "forgeai-manifest"
New-Item -ItemType Directory -Force $tmp | Out-Null
& "$env:SystemRoot\System32\tar.exe" -xf $Bundle -C $tmp "runs/move-manifest.json"
if ($LASTEXITCODE -ne 0) { Fail "the bundle has no runs/move-manifest.json; was it made by export-state.ps1?" }
$m = Get-Content (Join-Path $tmp "runs\move-manifest.json") -Raw | ConvertFrom-Json
"  source   $($m.source_host), made $($m.made)"

$forge = Join-Path $Root "forge"
$mfa = Join-Path $Root "mtg-forge-ai"
if (-not (Test-Path (Join-Path $forge ".git"))) {
    git clone -c core.longpaths=true -c core.autocrlf=false --branch $m.forge_branch https://github.com/jwilliamson13-tech/forge.git $forge
    git -C $forge remote add upstream https://github.com/Card-Forge/forge.git
    git -C $forge remote set-url --push upstream DISABLED
}
if (-not (Test-Path (Join-Path $mfa ".git"))) {
    git clone https://github.com/jwilliamson13-tech/mtg-forge-ai.git $mfa
}
foreach ($pair in @(@($forge, $m.forge_commit), @($mfa, $m.mtg_forge_ai_commit))) {
    git -C $pair[0] fetch -q origin
    git -C $pair[0] merge-base --is-ancestor $pair[1] HEAD
    if ($LASTEXITCODE -ne 0) { Fail "$($pair[0]) HEAD doesn't contain the source's commit $($pair[1])." }
    $head = git -C $pair[0] rev-parse HEAD
    if ($head -ne $pair[1]) { Warn "$($pair[0]) is past the source ($head vs $($pair[1])). The digest check below says whether play changed." }
}

# ---------------------------------------------------------------------------------------------------------------
Step "4. Forge build"
$env:JAVA_HOME = $jdk.FullName
$env:PATH = "$javaBin;$(Join-Path $mvnHome 'bin');$env:PATH"
$jar = Join-Path $forge "forge-gui-desktop\target\forge-gui-desktop-2.0.16-SNAPSHOT-jar-with-dependencies.jar"
$forgeHead = git -C $forge rev-parse --short HEAD
$buildLog = Join-Path $Root "forge-build-$forgeHead.log"
if (-not (Test-Path $buildLog) -or -not (Test-Path $jar)) {
    Push-Location $forge
    # through cmd, so Maven's stderr lines don't become terminating errors under ErrorActionPreference Stop
    cmd /c "mvn -B -DskipTests -pl forge-gui-desktop -am package > `"$buildLog`" 2>&1"
    $code = $LASTEXITCODE
    Pop-Location
    if ($code -ne 0) { Fail "Maven build failed; see $buildLog." }
}
$profile = Join-Path $forge "forge-gui\forge.profile.properties"
if (-not (Test-Path $profile)) {
    "userDir=target/sim-profile/user/", "cacheDir=target/sim-profile/cache/" | Set-Content -Encoding ascii $profile
}
"  jar      $jar"

# ---------------------------------------------------------------------------------------------------------------
Step "5. Python venv"
$venvPy = Join-Path $mfa "runs\venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    New-Item -ItemType Directory -Force (Join-Path $mfa "runs") | Out-Null
    & py -3.13 -m venv (Join-Path $mfa "runs\venv")
    & $venvPy -m pip install --upgrade pip
    & $venvPy -m pip install -r (Join-Path $mfa "requirements-dev.txt")
    if ($LASTEXITCODE -ne 0) { Fail "pip install failed." }
}
& $venvPy -c "import torch, onnxruntime; print('  torch', torch.__version__, 'cuda', torch.cuda.is_available()); print('  onnxruntime', onnxruntime.__version__)"

# ---------------------------------------------------------------------------------------------------------------
Step "6. Run state"
$marker = Join-Path $mfa "runs\move-manifest.json"
if (-not (Test-Path $marker)) {
    $sha = "$Bundle.sha256"
    if (Test-Path $sha) {
        $want = ((Get-Content $sha -Raw) -split "\s+")[0].ToLower()
        $got = (Get-FileHash $Bundle -Algorithm SHA256).Hash.ToLower()
        if ($want -ne $got) { Fail "bundle checksum mismatch: the transfer is incomplete or damaged." }
        "  checksum ok"
    } else { Warn "no .sha256 beside the bundle; not verified." }
    # Windows' tar.exe, not Expand-Archive: PowerShell 5.1's Expand-Archive is slow and unreliable on a 2 GB zip
    & "$env:SystemRoot\System32\tar.exe" -xf $Bundle -C $mfa
    if ($LASTEXITCODE -ne 0) { Fail "unpacking failed." }
}

# The run state stores absolute paths (plan.json bundles, step records, launchers). A ladder or pool folder refuses
# to resume when its stored settings differ, so rewrite the source's root and JDK path to this machine's.
$oldRoot = $m.source_parent
$newRoot = (Resolve-Path $Root).Path
$pairs = @(
    @(($oldRoot -replace "\\", "\\"), ($newRoot -replace "\\", "\\")),   # JSON-escaped
    @($oldRoot, $newRoot)
)
if ($m.source_java_bin) {
    $pairs += , @(($m.source_java_bin -replace "\\", "\\"), ($javaBin -replace "\\", "\\"))
    $pairs += , @($m.source_java_bin, $javaBin)
}
$files = @()
$files += Get-ChildItem (Join-Path $mfa "runs"), (Join-Path $mfa "models") -Recurse -File -Include *.json -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notlike "*\runs\venv\*" }
$files += Get-ChildItem (Join-Path $mfa "runs") -File -Filter *.cmd -ErrorAction SilentlyContinue
$changed = 0
foreach ($f in $files) {
    $text = [IO.File]::ReadAllText($f.FullName)
    $new = $text
    foreach ($p in $pairs) {
        if ($p[0] -ne $p[1]) { $new = $new -ireplace [regex]::Escape($p[0]), $p[1].Replace('$', '$$') }
    }
    if ($new -ne $text) { [IO.File]::WriteAllText($f.FullName, $new, $utf8); $changed++ }
}
"  rewrote paths in $changed of $($files.Count) files ($oldRoot -> $newRoot)"
$left = @()
if ($files.Count -gt 0) {
    $left = @(Select-String -Path ($files | ForEach-Object FullName) -Pattern ([regex]::Escape(($oldRoot -replace "\\", "\\"))) -List)
}
if ($left.Count -gt 0) { Warn "$(@($left).Count) file(s) still name the old root, e.g. $(@($left)[0].Path)" }

# ---------------------------------------------------------------------------------------------------------------
if ($SkipCheck) { Step "7. Digest check skipped (-SkipCheck)"; exit 0 }
Step "7. Digest check (about 4 minutes, two JVMs)"
$check = Join-Path $mfa "runs\move-check"
$checks = @(
    @{ name = "stock"; want = "8467fad7cb41"; args = @("ruby-storm-3", "dragon-stompy-2", "--seed", "1", "--sim-mode", "heuristic", "heuristic") },
    @{ name = "pair01"; want = "17b0cb30a266"; args = @("initiative-stompy-1", "oops-all-spells-3", "--seed", "267533734585463",
                                                        "--sim-mode", "full", "full", "--evaluator", "forge", "onnx:gen0") }
)
New-Item -ItemType Directory -Force $check | Out-Null
function Quote([string]$a) { if ($a -match '\s') { '"' + $a + '"' } else { $a } }
Push-Location $mfa
$procs = @()
foreach ($c in $checks) {
    $dir = Join-Path $check $c.name
    if (Test-Path $dir) { Remove-Item -Recurse -Force $dir }
    $a = @("-m", "simrunner", "batch") + $c.args + @("--games", "10", "--workers", "1", "--first-player", "random", "--records",
                                                      "--forge", $forge, "--run-dir", $dir)
    $procs += Start-Process $venvPy -ArgumentList ($a | ForEach-Object { Quote $_ }) -NoNewWindow -PassThru -RedirectStandardOutput "$dir.console.log" -RedirectStandardError "$dir.err.log"
}
$procs | Wait-Process
Pop-Location
$ok = $true
foreach ($c in $checks) {
    $line = Select-String -Path "$check\$($c.name).console.log" -Pattern "record digest\s+(\w+)" | Select-Object -First 1
    $got = if ($line) { $line.Matches[0].Groups[1].Value } else { "(none)" }
    $same = $got -eq $c.want
    if (-not $same) { $ok = $false }
    "  {0,-7} {1}  want {2}  {3}" -f $c.name, $got, $c.want, $(if ($same) { "SAME" } else { "DIFFERENT" })
}
if (-not $ok) { Fail "digests differ: this machine plays different games. Compare JDK and commits with the source before running." }

Step "Ready. Resume the gen-1 ladder (4 workers, below normal priority) with a start and stop time:"
"  cmd /c start `"gen1`" /b /wait /belownormal `"$mfa\runs\gen1-generation.cmd`" HH:MM --stop HH:MM"
"  Progress: $mfa\runs\generations\gen1\loop.log and logs\ladder.log"
