param(
    [switch]$SkipBundle,
    [switch]$SkipRuntime,
    [switch]$SkipInstallVerification
)

$ErrorActionPreference = "Stop"
$JavisRoot = Split-Path -Parent $PSScriptRoot
$JavisApp = Join-Path $JavisRoot "app"
$JavisPython = Join-Path $JavisRoot "venv\Scripts\python.exe"
$JavisNpm = Join-Path $JavisRoot "tools\nodejs\npm.cmd"
$JavisCargoBin = Join-Path $JavisRoot "tools\rust\cargo\bin"
$JavisNodeBin = Join-Path $JavisRoot "tools\nodejs"
$JavisMinGwBin = Join-Path $JavisRoot "tools\mingw32\bin"
$JavisRustToolchain = "stable-x86_64-pc-windows-gnu"
$JavisRustTarget = "x86_64-pc-windows-gnu"
$JavisGnuLinker = Join-Path $JavisMinGwBin "x86_64-w64-mingw32-gcc.exe"
$JavisTargetRoot = Join-Path $JavisApp "src-tauri\target\$JavisRustTarget\release"
$JavisResources = Join-Path $JavisApp "src-tauri\resources"
$JavisRuntimeArchive = Join-Path $JavisResources "javis-runtime.zip"
$WasapiSource = Join-Path $JavisRoot "voice\native\WASAPILoopbackRecorder.cs"
$WasapiHelper = Join-Path $JavisRoot "voice\native\javis-wasapi-loopback.exe"
$CSharpCompiler = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
$JavisInstaller = Join-Path $JavisTargetRoot "bundle\nsis\Javis_3.0.0_x64-setup.exe"
$ArtifactDir = Join-Path $JavisRoot "artifacts\Javis-v3.0.0-test"
$DesktopZip = Join-Path ([Environment]::GetFolderPath("Desktop")) "Javis-v3.0.0-Windows-x64.zip"

foreach ($required in @($JavisPython, $JavisNpm, $JavisGnuLinker, $CSharpCompiler, $WasapiSource)) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Required local build dependency is missing: $required"
    }
}

& $CSharpCompiler /nologo /optimize+ /platform:x64 "/out:$WasapiHelper" $WasapiSource
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $WasapiHelper)) {
    throw "Native WASAPI loopback helper build failed."
}

& $JavisPython (Join-Path $JavisRoot "scripts\app_build_preflight.py") --strict
if ($LASTEXITCODE -ne 0) {
    throw "Strict App build preflight failed."
}

if (-not $SkipRuntime) {
    & $JavisPython (Join-Path $JavisRoot "scripts\stage_javis_runtime.py") `
        --root $JavisRoot `
        --output $JavisRuntimeArchive
    if ($LASTEXITCODE -ne 0) {
        throw "Packaged Python runtime staging failed."
    }
}

if (-not (Test-Path -LiteralPath $JavisRuntimeArchive)) {
    throw "Runtime archive is missing: $JavisRuntimeArchive"
}

$env:PATH = "$JavisNodeBin;$JavisCargoBin;$JavisMinGwBin;$env:PATH"
$env:CARGO_HOME = Join-Path $JavisRoot "tools\rust\cargo"
$env:RUSTUP_HOME = Join-Path $JavisRoot "tools\rust\rustup"
$env:RUSTUP_TOOLCHAIN = $JavisRustToolchain
$env:CARGO_BUILD_TARGET = $JavisRustTarget
$env:CARGO_TARGET_X86_64_PC_WINDOWS_GNU_LINKER = $JavisGnuLinker
$env:CC_x86_64_pc_windows_gnu = $JavisGnuLinker

Push-Location $JavisApp
try {
    & $JavisNpm run build
    if ($LASTEXITCODE -ne 0) {
        throw "Frontend build failed."
    }
    if (-not $SkipBundle) {
        & $JavisNpm run tauri -- build --target $JavisRustTarget
        if ($LASTEXITCODE -ne 0) {
            throw "Tauri bundle failed."
        }
    }
}
finally {
    Pop-Location
}

if ($SkipBundle) {
    Write-Host "Javis App v3.0 frontend and runtime build complete."
    exit 0
}
if (-not (Test-Path -LiteralPath $JavisInstaller)) {
    throw "Build completed without the v3 NSIS installer."
}

if (Test-Path -LiteralPath $ArtifactDir) {
    Remove-Item -LiteralPath $ArtifactDir -Recurse -Force
}
New-Item -ItemType Directory -Path $ArtifactDir -Force | Out-Null
$ArtifactInstaller = Join-Path $ArtifactDir (Split-Path -Leaf $JavisInstaller)
Copy-Item -LiteralPath $JavisInstaller -Destination $ArtifactInstaller -Force
Copy-Item -LiteralPath (Join-Path $JavisResources "javis-runtime-manifest.json") -Destination $ArtifactDir -Force
Copy-Item -LiteralPath (Join-Path $JavisRoot "app\release.manifest.json") -Destination $ArtifactDir -Force

$RuntimeReport = Join-Path $ArtifactDir "RUNTIME-TEST-REPORT.md"
& $JavisPython (Join-Path $JavisRoot "scripts\verify_javis_release.py") `
    --archive $JavisRuntimeArchive `
    --manifest (Join-Path $JavisResources "javis-runtime-manifest.json") `
    --report $RuntimeReport `
    --port 18080
if ($LASTEXITCODE -ne 0) {
    throw "Packaged runtime verification failed."
}

$InstallReport = Join-Path $ArtifactDir "INSTALL-TEST-REPORT.md"
if (-not $SkipInstallVerification) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass `
        -File (Join-Path $JavisRoot "scripts\verify_javis_v3_installer.ps1") `
        -Installer $ArtifactInstaller `
        -Report $InstallReport `
        -Manifest (Join-Path $JavisResources "javis-runtime-manifest.json")
    if ($LASTEXITCODE -ne 0) {
        throw "Installed App verification failed."
    }
}
else {
    @(
        "# Javis v3.0 Installation Test Report"
        ""
        "Overall: UNTESTED"
        ""
        "Installer verification was explicitly skipped."
    ) | Set-Content -LiteralPath $InstallReport -Encoding utf8
}

$InstallerHash = (Get-FileHash -LiteralPath $ArtifactInstaller -Algorithm SHA256).Hash.ToLowerInvariant()
$RuntimeHash = (Get-FileHash -LiteralPath $JavisRuntimeArchive -Algorithm SHA256).Hash.ToLowerInvariant()
$HashFile = Join-Path $ArtifactDir "SHA256SUMS.txt"
$DeliveryFiles = @(
    $ArtifactInstaller,
    (Join-Path $ArtifactDir "javis-runtime-manifest.json"),
    (Join-Path $ArtifactDir "release.manifest.json"),
    $RuntimeReport,
    $InstallReport
)
$HashLines = foreach ($File in $DeliveryFiles) {
    $Hash = (Get-FileHash -LiteralPath $File -Algorithm SHA256).Hash.ToLowerInvariant()
    "$Hash  $(Split-Path -Leaf $File)"
}
$HashLines += "$RuntimeHash  bundled:javis-runtime.zip"
$HashLines | Set-Content -LiteralPath $HashFile -Encoding ascii

if (Test-Path -LiteralPath $DesktopZip) {
    Remove-Item -LiteralPath $DesktopZip -Force
}
Compress-Archive -Path (Join-Path $ArtifactDir "*") -DestinationPath $DesktopZip -CompressionLevel Optimal
if (-not (Test-Path -LiteralPath $DesktopZip)) {
    throw "Desktop delivery ZIP was not created."
}

Write-Host "Javis App v3.0 build complete."
Write-Host "Installer: $ArtifactInstaller"
Write-Host "Checksums: $HashFile"
Write-Host "Desktop ZIP: $DesktopZip"
