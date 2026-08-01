[CmdletBinding()]
param()

$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$cacheRoot = Join-Path $root ".cache"
$tempRoot = Join-Path $root "tmp\dev"

$directories = @(
    $cacheRoot,
    (Join-Path $cacheRoot "pip"),
    (Join-Path $cacheRoot "pnpm"),
    (Join-Path $cacheRoot "cargo"),
    $tempRoot,
    (Join-Path $root "app\src-tauri\target")
)
foreach ($directory in $directories) {
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
}

$env:JAVIS_ROOT = $root
$env:PIP_CACHE_DIR = Join-Path $cacheRoot "pip"
$env:PNPM_HOME = Join-Path $cacheRoot "pnpm"
$env:PNPM_STORE_DIR = Join-Path $root ".pnpm-store"
$env:CARGO_TARGET_DIR = Join-Path $root "app\src-tauri\target"
$env:TEMP = $tempRoot
$env:TMP = $tempRoot

$runtimeRoot = $root
if (-not (Test-Path -LiteralPath (Join-Path $runtimeRoot "tools\nodejs\node.exe"))) {
    $commonGitDir = (& git -C $root rev-parse --git-common-dir 2>$null)
    if ($LASTEXITCODE -eq 0 -and $commonGitDir) {
        $resolvedCommonGitDir = (Resolve-Path -LiteralPath $commonGitDir).Path
        $candidateRoot = Split-Path -Parent $resolvedCommonGitDir
        if (Test-Path -LiteralPath (Join-Path $candidateRoot "tools\nodejs\node.exe")) {
            $runtimeRoot = $candidateRoot
        }
    }
}

$rustRoot = Join-Path $runtimeRoot "tools\rust"
if (Test-Path -LiteralPath (Join-Path $rustRoot "rustup\settings.toml")) {
    $env:CARGO_HOME = Join-Path $rustRoot "cargo"
    $env:RUSTUP_HOME = Join-Path $rustRoot "rustup"
} else {
    $env:CARGO_HOME = Join-Path $cacheRoot "cargo"
    $env:RUSTUP_HOME = Join-Path $cacheRoot "rustup"
    New-Item -ItemType Directory -Force -Path $env:CARGO_HOME, $env:RUSTUP_HOME | Out-Null
}

$rustToolchainBin = Join-Path $env:RUSTUP_HOME "toolchains\stable-x86_64-pc-windows-gnu\bin"
$rustSelfContainedBin = Join-Path $rustToolchainBin "..\lib\rustlib\x86_64-pc-windows-gnu\bin\self-contained"
$toolPaths = @(
    (Join-Path $runtimeRoot "tools\nodejs"),
    $rustToolchainBin,
    (Join-Path $runtimeRoot "tools\mingw32\bin"),
    $rustSelfContainedBin,
    (Join-Path $env:CARGO_HOME "bin")
) | Where-Object { Test-Path -LiteralPath $_ }
if ($toolPaths) {
    $env:PATH = (($toolPaths -join [IO.Path]::PathSeparator) + [IO.Path]::PathSeparator + $env:PATH)
}

Write-Output "Javis development environment is rooted at $root"
