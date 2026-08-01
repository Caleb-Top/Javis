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
$JavisRustToolchainBin = Join-Path $JavisRoot "tools\rust\rustup\toolchains\stable-x86_64-pc-windows-gnu\bin"
$JavisCargo = Join-Path $JavisRustToolchainBin "cargo.exe"
$JavisRustc = Join-Path $JavisRustToolchainBin "rustc.exe"
$JavisGnuLinker = Join-Path $JavisMinGwBin "x86_64-w64-mingw32-gcc.exe"
$JavisTargetRoot = Join-Path $JavisApp "src-tauri\target\release"
$JavisResources = Join-Path $JavisApp "src-tauri\resources"
$JavisRuntimeArchive = Join-Path $JavisResources "javis-runtime.zip"
$JavisWebView2LoaderResource = Join-Path $JavisResources "WebView2Loader.dll"
$JavisWebView2Crate = Get-ChildItem `
    -Path (Join-Path $JavisRoot "tools\rust\cargo\registry\src\*\webview2-com-sys-*") `
    -Directory `
    -ErrorAction SilentlyContinue |
    Sort-Object Name -Descending |
    Select-Object -First 1
$JavisWebView2LoaderSource = if ($JavisWebView2Crate) {
    Join-Path $JavisWebView2Crate.FullName "x64\WebView2Loader.dll"
} else {
    ""
}
$WasapiSource = Join-Path $JavisRoot "voice\native\WASAPILoopbackRecorder.cs"
$WasapiHelper = Join-Path $JavisRoot "voice\native\javis-wasapi-loopback.exe"
$CSharpCompiler = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
$JavisInstaller = Join-Path $JavisTargetRoot "bundle\nsis\Javis_3.0.0_x64-setup.exe"
$ArtifactDir = Join-Path $JavisRoot "artifacts\Javis-v3.0.0-test"
$DesktopDelivery = Join-Path ([Environment]::GetFolderPath("Desktop")) "Javis-v3.0.0-Verified"
$DesktopZip = Join-Path $DesktopDelivery "Javis-v3.0.0-Windows-x64.zip"
$DesktopZipHash = Join-Path $DesktopDelivery "ZIP-SHA256.txt"

foreach ($required in @($JavisPython, $JavisNpm, $JavisCargo, $JavisRustc, $JavisGnuLinker, $CSharpCompiler, $WasapiSource, $JavisWebView2LoaderSource)) {
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

Copy-Item -LiteralPath $JavisWebView2LoaderSource -Destination $JavisWebView2LoaderResource -Force
$WebView2LoaderSourceHash = (Get-FileHash -LiteralPath $JavisWebView2LoaderSource -Algorithm SHA256).Hash
$WebView2LoaderResourceHash = (Get-FileHash -LiteralPath $JavisWebView2LoaderResource -Algorithm SHA256).Hash
if ($WebView2LoaderSourceHash -ne $WebView2LoaderResourceHash) {
    throw "WebView2Loader.dll resource verification failed."
}

$env:PATH = "$JavisNodeBin;$JavisRustToolchainBin;$JavisMinGwBin;$JavisCargoBin;$env:PATH"
$env:CARGO_HOME = Join-Path $JavisRoot "tools\rust\cargo"
$env:RUSTUP_HOME = Join-Path $JavisRoot "tools\rust\rustup"
$env:CARGO = $JavisCargo
$env:RUSTC = $JavisRustc
$env:CARGO_TARGET_X86_64_PC_WINDOWS_GNU_LINKER = $JavisGnuLinker
$env:CC_x86_64_pc_windows_gnu = $JavisGnuLinker

Push-Location $JavisApp
try {
    & $JavisNpm run build
    if ($LASTEXITCODE -ne 0) {
        throw "Frontend build failed."
    }
    if (-not $SkipBundle) {
        & $JavisNpm run tauri -- build
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

New-Item -ItemType Directory -Path $DesktopDelivery -Force | Out-Null
if (Test-Path -LiteralPath $DesktopZip) {
    Remove-Item -LiteralPath $DesktopZip -Force
}
Compress-Archive -Path (Join-Path $ArtifactDir "*") -DestinationPath $DesktopZip -CompressionLevel Optimal
if (-not (Test-Path -LiteralPath $DesktopZip)) {
    throw "Desktop delivery ZIP was not created."
}
$DesktopZipSha256 = (Get-FileHash -LiteralPath $DesktopZip -Algorithm SHA256).Hash.ToLowerInvariant()
"$DesktopZipSha256  $(Split-Path -Leaf $DesktopZip)" | Set-Content -LiteralPath $DesktopZipHash -Encoding ascii
foreach ($DeliveryReport in @("INSTALL-TEST-REPORT.md", "RUNTIME-TEST-REPORT.md", "SHA256SUMS.txt")) {
    Copy-Item -LiteralPath (Join-Path $ArtifactDir $DeliveryReport) -Destination $DesktopDelivery -Force
}

Write-Host "Javis App v3.0 build complete."
Write-Host "Installer: $ArtifactInstaller"
Write-Host "Checksums: $HashFile"
Write-Host "Desktop ZIP: $DesktopZip"
Write-Host "Desktop ZIP checksum: $DesktopZipHash"
