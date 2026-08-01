[CmdletBinding()]
param(
    [string]$PythonPath
)

$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
if (-not $PythonPath) {
    $candidates = @(
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
