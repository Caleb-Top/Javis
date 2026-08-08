[CmdletBinding()]
param(
    [string]$PythonPath
)

$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
if (-not $PythonPath) {
    $candidates = @(
        (Join-Path $root "tools\python-runtime-3.11\python.exe"),
        (Join-Path $root "venv\Scripts\python.exe")
    )
    $commonGitDir = (& git -C $root rev-parse --git-common-dir 2>$null)
    if ($LASTEXITCODE -eq 0 -and $commonGitDir) {
        $mainRoot = Split-Path -Parent (Resolve-Path -LiteralPath $commonGitDir).Path
        $candidates += Join-Path $mainRoot "venv\Scripts\python.exe"
    }
    $PythonPath = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $PythonPath) {
    throw "No Javis Python runtime was found. Pass -PythonPath explicitly."
}

$sitePackages = Join-Path $root "venv\Lib\site-packages"
$pythonPaths = @(
    $sitePackages,
    (Join-Path $sitePackages "win32"),
    (Join-Path $sitePackages "win32\lib"),
    (Join-Path $sitePackages "Pythonwin")
)
$env:PYTHONPATH = $pythonPaths -join ";"
$env:PATH = "$(Join-Path $sitePackages 'pywin32_system32');$env:PATH"

$suites = @(
    "tests",
    "agent_distill\tests",
    "ClaudeAgent_Distill\agent_distill\tests"
)
foreach ($suite in $suites) {
    Write-Output "Running Python test suite: $suite"
    & $PythonPath -m pytest $suite -q
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}
