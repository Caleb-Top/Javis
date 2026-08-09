param(
    [switch]$SkipBundle,
    [switch]$SkipRuntime,
    [switch]$SkipDesktopBuild,
    [switch]$SkipAddonBuild,
    [switch]$SkipInstallVerification
)

$ErrorActionPreference = "Stop"
$JavisRoot = Split-Path -Parent $PSScriptRoot
$JavisApp = Join-Path $JavisRoot "app"
$JavisPython = Join-Path $JavisRoot "tools\python-runtime-3.11\python.exe"
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
$InnerInstaller = Join-Path $JavisTargetRoot "bundle\nsis\Javis_3.0.0_x64-setup.exe"
$OllamaRuntime = Join-Path $JavisRoot "tools\ollama-runtime"
$PythonRuntimeRoot = Join-Path $JavisRoot "tools\python-runtime-3.11"
$SitePackagesRoot = Join-Path $JavisRoot "venv\Lib\site-packages"
$SttModelRoot = Join-Path $JavisRoot "models\faster-whisper-base"
$ModelRoot = Join-Path $JavisRoot "ollama_models\models"

$Layout = (& $JavisPython (Join-Path $JavisRoot "scripts\javis_release_layout.py") `
    --root $JavisRoot `
    --test-drive "D:") | ConvertFrom-Json
$BuildTemp = $Layout.build_temp
$ArtifactDir = $Layout.artifact_dir
$MainInstaller = $Layout.package_output
$MainInstallerName = "Javis-v3.0.0-Setup.exe"
if ((Split-Path -Leaf $MainInstaller) -ne $MainInstallerName) {
    throw "Unexpected main installer name: $MainInstaller"
}
$DeliveryInstaller = $Layout.delivery_installer
$DeliveryDir = $Layout.delivery_dir
$InstallTestRoot = $Layout.install_test_root
$InstallTestData = $Layout.install_test_data
$PayloadWork = Join-Path $BuildTemp "payloads"

if ([IO.Path]::GetFullPath($JavisRoot).Substring(0, 2).ToUpperInvariant() -ne "G:") {
    throw "Javis development and build root must stay on G:."
}

if ($SkipBundle -or $SkipRuntime -or $SkipDesktopBuild -or $SkipAddonBuild -or $SkipInstallVerification) {
    throw "Release mode does not permit skip switches; use a separate development command."
}

$ReleaseGatePython = Join-Path $JavisRoot "venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $ReleaseGatePython -PathType Leaf)) {
    throw "Release gate Python with pytest is missing: $ReleaseGatePython"
}
$ReleaseGateReport = Join-Path $JavisRoot "tmp\release-gate-report.json"
& $ReleaseGatePython -B (Join-Path $JavisRoot "scripts\javis_release_gate.py") `
    --root $JavisRoot `
    --report $ReleaseGateReport
if ($LASTEXITCODE -ne 0) {
    throw "Required release gate failed; packaging is forbidden. Report: $ReleaseGateReport"
}

foreach ($path in @($BuildTemp, $ArtifactDir, $MainInstaller, $PayloadWork)) {
    if ([IO.Path]::GetFullPath($path).Substring(0, 2).ToUpperInvariant() -ne "G:") {
        throw "Build path escaped G:: $path"
    }
}

New-Item -ItemType Directory -Path $BuildTemp -Force | Out-Null
$env:TEMP = Join-Path $BuildTemp "temp"
$env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = Join-Path $JavisRoot ".cache\pip"
$env:PNPM_HOME = Join-Path $JavisRoot ".pnpm-store"
$env:CARGO_HOME = Join-Path $JavisRoot "tools\rust\cargo"
$env:RUSTUP_HOME = Join-Path $JavisRoot "tools\rust\rustup"
$env:CARGO_TARGET_DIR = Join-Path $JavisApp "src-tauri\target"
$env:PYTHONPATH = $SitePackagesRoot
New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null

foreach ($required in @(
    $JavisPython,
    $JavisNpm,
    $JavisCargo,
    $JavisRustc,
    $JavisGnuLinker,
    $CSharpCompiler,
    $WasapiSource,
    $JavisWebView2LoaderSource,
    (Join-Path $OllamaRuntime "ollama.exe"),
    (Join-Path $PythonRuntimeRoot "python.exe"),
    (Join-Path $SttModelRoot "model.bin")
)) {
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

& $JavisPython (Join-Path $JavisRoot "scripts\javis_full_installer.py") `
    --model-root $ModelRoot `
    --model "deepseek-r1:8b" | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Bundled deepseek-r1:8b validation failed."
}

if (-not $SkipRuntime) {
    & $JavisPython (Join-Path $JavisRoot "scripts\stage_javis_runtime.py") `
        --root $JavisRoot `
        --python-root $PythonRuntimeRoot `
        --site-packages $SitePackagesRoot `
        --stt-model-root $SttModelRoot `
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
$env:CARGO = $JavisCargo
$env:RUSTC = $JavisRustc
$env:CARGO_TARGET_X86_64_PC_WINDOWS_GNU_LINKER = $JavisGnuLinker
$env:CC_x86_64_pc_windows_gnu = $JavisGnuLinker

if (-not $SkipDesktopBuild) {
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
}

if ($SkipBundle) {
    Write-Host "Javis App frontend and Python runtime build complete on G:."
    exit 0
}
if (-not (Test-Path -LiteralPath $InnerInstaller)) {
    throw "Build completed without the inner v3 NSIS installer."
}

$ResolvedArtifact = [IO.Path]::GetFullPath($ArtifactDir)
$ResolvedRoot = [IO.Path]::GetFullPath($JavisRoot).TrimEnd('\')
if (-not $ResolvedArtifact.StartsWith($ResolvedRoot + "\", [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to recreate artifact directory outside G:\Javis: $ResolvedArtifact"
}
if ((Test-Path -LiteralPath $ArtifactDir) -and -not $SkipAddonBuild) {
    Remove-Item -LiteralPath $ArtifactDir -Recurse -Force
}
New-Item -ItemType Directory -Path $ArtifactDir -Force | Out-Null
New-Item -ItemType Directory -Path $PayloadWork -Force | Out-Null

Copy-Item -LiteralPath $InnerInstaller -Destination $MainInstaller -Force
if ((Get-Item -LiteralPath $MainInstaller).Length -ge 4GB) {
    throw "Main installer exceeds the Windows executable size boundary."
}
if (-not ("Javis.NativeBinary" -as [type])) {
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
namespace Javis {
    public static class NativeBinary {
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        public static extern bool GetBinaryType(string path, out uint binaryType);
    }
}
"@
}
$BinaryType = [uint32]0
if (-not [Javis.NativeBinary]::GetBinaryType($MainInstaller, [ref]$BinaryType) -or $BinaryType -notin @(0, 6)) {
    throw "Windows does not recognize the NSIS launcher as a loadable executable."
}

$RuntimeReport = Join-Path $ArtifactDir "RUNTIME-TEST-REPORT.md"
& $JavisPython (Join-Path $JavisRoot "scripts\verify_javis_release.py") `
    --archive $JavisRuntimeArchive `
    --manifest (Join-Path $JavisResources "javis-runtime-manifest.json") `
    --report $RuntimeReport `
    --port 18080
if ($LASTEXITCODE -ne 0) {
    throw "Packaged runtime verification failed."
}

if (-not $SkipAddonBuild) {
    & $JavisPython (Join-Path $JavisRoot "scripts\javis_full_installer.py") `
        --build-addon `
        --model-root $ModelRoot `
        --model "deepseek-r1:8b" `
        --ollama-root $OllamaRuntime `
        --output-dir $ArtifactDir `
        --work-dir $PayloadWork | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Optional R1 add-on assembly failed."
    }
}

$AddonDir = Join-Path $ArtifactDir "Javis-R1-8B-Addon"
$AddonManifest = Join-Path $AddonDir "Javis-R1-8B-Addon.manifest.json"
$AddonReport = Join-Path $ArtifactDir "Javis-R1-8B-Addon.report.json"
foreach ($required in @($MainInstaller, $AddonManifest, $AddonReport)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Release artifact is missing: $required"
    }
}
$InstallReport = Join-Path $ArtifactDir "INSTALL-TEST-REPORT.md"
if (-not $SkipInstallVerification) {
    $ResolvedDelivery = [IO.Path]::GetFullPath($DeliveryDir).TrimEnd('\')
    if (-not $ResolvedDelivery.Equals("D:\Javis-v3.0.0-User-Test", [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to recreate unexpected D-drive delivery directory: $ResolvedDelivery"
    }
    if (Test-Path -LiteralPath $ResolvedDelivery) {
        Remove-Item -LiteralPath $ResolvedDelivery -Recurse -Force
    }
    New-Item -ItemType Directory -Path $ResolvedDelivery -Force | Out-Null
    Copy-Item -LiteralPath $MainInstaller -Destination $DeliveryInstaller -Force
    & powershell.exe -NoProfile -ExecutionPolicy Bypass `
        -File (Join-Path $JavisRoot "scripts\verify_javis_v3_installer.ps1") `
        -Installer $DeliveryInstaller `
        -Report $InstallReport `
        -Manifest (Join-Path $JavisResources "javis-runtime-manifest.json") `
        -SourceRoot $JavisRoot `
        -InstallRoot $InstallTestRoot `
        -DataRoot $InstallTestData
    if ($LASTEXITCODE -ne 0) {
        throw "Independent D-drive installation verification failed."
    }
}
else {
    @(
        "# Javis v3.0 Installation Test Report"
        ""
        "Overall: UNTESTED"
        ""
        "D-drive independent installation verification was explicitly skipped."
    ) | Set-Content -LiteralPath $InstallReport -Encoding utf8
}

$HashFile = Join-Path $ArtifactDir "SHA256SUMS.txt"
& $JavisPython (Join-Path $JavisRoot "scripts\finalize_javis_release.py") `
    --artifact-dir $ArtifactDir `
    --runtime-manifest (Join-Path $JavisResources "javis-runtime-manifest.json") `
    --release-manifest (Join-Path $JavisRoot "app\release.manifest.json") `
    --runtime-archive $JavisRuntimeArchive `
    --gate-report $ReleaseGateReport `
    --root $JavisRoot | Out-Null
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $HashFile)) {
    throw "Final release hash and manifest validation failed."
}

Write-Host "Javis v3.0 main installer and optional R1 add-on build complete."
Write-Host "G-drive main installer: $MainInstaller"
Write-Host "Optional R1 add-on: $AddonDir"
Write-Host "Checksums: $HashFile"
if (-not $SkipInstallVerification) {
    Write-Host "D-drive verified installer: $DeliveryInstaller"
}
