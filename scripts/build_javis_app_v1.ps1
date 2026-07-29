param(
    [switch]$SkipBundle
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
$JavisAppExe = Join-Path $JavisTargetRoot "javis-app.exe"

if (-not (Test-Path -LiteralPath $JavisPython)) {
    throw "Javis Python environment is missing: $JavisPython"
}
if (-not (Test-Path -LiteralPath $JavisNpm)) {
    throw "Bundled npm is missing: $JavisNpm"
}
if (-not (Test-Path -LiteralPath (Join-Path $JavisApp "node_modules"))) {
    throw "Frontend dependencies are missing. Run the approved dependency setup first."
}

& $JavisPython (Join-Path $JavisRoot "scripts\app_build_preflight.py")
if ($LASTEXITCODE -ne 0) {
    throw "App build preflight failed."
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
        # tauri build
        & $JavisNpm run tauri -- build --target $JavisRustTarget
        if ($LASTEXITCODE -ne 0) {
            throw "Tauri bundle failed."
        }
    }
}
finally {
    Pop-Location
}

if (-not $SkipBundle -and -not (Test-Path -LiteralPath $JavisAppExe)) {
    throw "Build completed without javis-app.exe."
}

Write-Host "Javis App v1.0 build complete."
Write-Host "Executable: $JavisAppExe"
Write-Host "Installer directory: $(Join-Path $JavisTargetRoot 'bundle\nsis')"
