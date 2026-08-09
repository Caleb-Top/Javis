param(
    [string]$Installer = "",
    [string]$Report = "",
    [string]$Manifest = "",
    [string]$SourceRoot = "",
    [string]$InstallRoot = "D:\Javis-v3-install-test",
    [string]$DataRoot = "D:\Javis-v3-data-test",
    [switch]$SourceRegressionOnly
)

$ErrorActionPreference = "Stop"
$RuntimeRoot = Join-Path $DataRoot "runtime"
$Canary = Join-Path $RuntimeRoot "app\workspace\v3-preservation-canary.txt"
$Checks = [System.Collections.Generic.List[object]]::new()
$PreviousDataRoot = $env:JAVIS_APP_DATA_ROOT
$ExpectedRuntimeHash = ""
$VisibleHelperDetected = $false
$VerifierProcessIds = [Collections.Generic.HashSet[int]]::new()
$ProcessCursor = $PID
while ($ProcessCursor -gt 0 -and $VerifierProcessIds.Add([int]$ProcessCursor)) {
    $CurrentProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessCursor" -ErrorAction SilentlyContinue
    if (-not $CurrentProcess) { break }
    $ProcessCursor = [int]$CurrentProcess.ParentProcessId
}

function Add-Check([string]$Name, [bool]$Passed, [string]$Detail) {
    $Checks.Add([pscustomobject]@{
        Name = $Name
        Status = if ($Passed) { "PASS" } else { "FAIL" }
        Detail = $Detail.Replace("|", "/").Replace("`r", " ").Replace("`n", " ")
    })
}

function Resolve-SourceTool([string]$Override, [string[]]$Candidates) {
    if ($Override -and (Test-Path -LiteralPath $Override -PathType Leaf)) {
        return [IO.Path]::GetFullPath($Override)
    }
    foreach ($Candidate in $Candidates) {
        if ($Candidate -and (Test-Path -LiteralPath $Candidate -PathType Leaf)) {
            return [IO.Path]::GetFullPath($Candidate)
        }
    }
    return $null
}

function Resolve-PythonTestTool([string]$Override, [string[]]$Candidates) {
    $AllCandidates = @()
    if ($Override) {
        $AllCandidates += $Override
    }
    $AllCandidates += $Candidates
    foreach ($Candidate in $AllCandidates | Select-Object -Unique) {
        if (-not $Candidate -or -not (Test-Path -LiteralPath $Candidate -PathType Leaf)) {
            continue
        }
        try {
            $null = @(& $Candidate -B -m pytest --version 2>&1)
            if ($LASTEXITCODE -eq 0) {
                return [IO.Path]::GetFullPath($Candidate)
            }
        }
        catch {
            continue
        }
    }
    return $null
}

function Invoke-SourceRegressionGate {
    if (-not $SourceRoot) {
        throw "SourceRoot is required for the source regression gate."
    }
    $ResolvedSource = [IO.Path]::GetFullPath($SourceRoot)
    if (-not (Test-Path -LiteralPath $ResolvedSource -PathType Container)) {
        throw "SourceRoot does not exist: $ResolvedSource"
    }

    $Python = Resolve-PythonTestTool $env:JAVIS_TEST_PYTHON @(
        (Join-Path $ResolvedSource "venv\Scripts\python.exe"),
        (Join-Path $ResolvedSource "tools\python-runtime-3.11\python.exe"),
        "G:\Javis\venv\Scripts\python.exe",
        "G:\Javis\tools\python-runtime-3.11\python.exe"
    )
    $Node = Resolve-SourceTool $env:JAVIS_TEST_NODE @(
        (Join-Path $ResolvedSource "tools\nodejs\node.exe"),
        "G:\Javis\tools\nodejs\node.exe"
    )

    if (-not $Python) {
        Add-Check "Source voice reconnect regression" $false "Python runtime not found"
        Write-Host "Source voice reconnect regression: FAIL"
    }
    else {
        $PreviousBytecodeSetting = $env:PYTHONDONTWRITEBYTECODE
        $env:PYTHONDONTWRITEBYTECODE = "1"
        Push-Location $ResolvedSource
        try {
            $VoiceOutput = @(& $Python -B -m pytest `
                "tests/test_voice_reconnect_integration.py" `
                -q -p no:cacheprovider 2>&1)
            $VoiceExit = $LASTEXITCODE
        }
        finally {
            Pop-Location
            $env:PYTHONDONTWRITEBYTECODE = $PreviousBytecodeSetting
        }
        $VoicePassed = $VoiceExit -eq 0
        Add-Check "Source voice reconnect regression" $VoicePassed ($VoiceOutput -join " ")
        Write-Host "Source voice reconnect regression: $(if ($VoicePassed) { 'PASS' } else { 'FAIL' })"
    }

    if (-not $Python) {
        Add-Check "Source voice observability regression" $false "Python runtime not found"
        Write-Host "Source voice observability regression: FAIL"
    }
    else {
        $PreviousBytecodeSetting = $env:PYTHONDONTWRITEBYTECODE
        $env:PYTHONDONTWRITEBYTECODE = "1"
        Push-Location $ResolvedSource
        try {
            $ObservabilityOutput = @(& $Python -B -m pytest `
                "tests/test_continuous_voice_gateway.py" `
                "tests/test_streaming_voice_pipeline.py" `
                "tests/test_conversation_stall_harness.py" `
                "tests/test_conversation_gateway.py" `
                "tests/test_voice_diagnostics_collector.py" `
                "tests/test_main_voice_observability_contract.py" `
                -q -p no:cacheprovider 2>&1)
            $ObservabilityExit = $LASTEXITCODE
        }
        finally {
            Pop-Location
            $env:PYTHONDONTWRITEBYTECODE = $PreviousBytecodeSetting
        }
        $ObservabilityPassed = $ObservabilityExit -eq 0
        Add-Check "Source voice observability regression" $ObservabilityPassed ($ObservabilityOutput -join " ")
        Write-Host "Source voice observability regression: $(if ($ObservabilityPassed) { 'PASS' } else { 'FAIL' })"
    }

    if (-not $Node) {
        Add-Check "Source frontend voice lifecycle" $false "Node.js runtime not found"
        Write-Host "Source frontend voice lifecycle: FAIL"
    }
    else {
        Push-Location $ResolvedSource
        try {
            $FrontendOutput = @(& $Node --experimental-strip-types --test `
                "app/tests/backendConversationClient.test.ts" `
                "app/tests/continuousVoiceCapture.test.ts" `
                "app/tests/backendReliabilityDiagnostics.test.ts" 2>&1)
            $FrontendExit = $LASTEXITCODE
        }
        finally {
            Pop-Location
        }
        $FrontendPassed = $FrontendExit -eq 0
        Add-Check "Source frontend voice lifecycle" $FrontendPassed ($FrontendOutput -join " ")
        Write-Host "Source frontend voice lifecycle: $(if ($FrontendPassed) { 'PASS' } else { 'FAIL' })"
    }

    if (-not $Python -or -not $Node) {
        Add-Check "Source real watchdog stall harness" $false "Python or Node.js runtime not found"
        Write-Host "Source real watchdog stall harness: FAIL"
    }
    else {
        Push-Location $ResolvedSource
        try {
            $WatchdogOutput = @(& $Python -B `
                "scripts/verify_conversation_watchdogs.py" `
                --node $Node `
                --work-dir $ResolvedSource `
                --time-scale 0.01 `
                --passes 2 2>&1)
            $WatchdogExit = $LASTEXITCODE
        }
        finally {
            Pop-Location
        }
        $WatchdogPassed = $WatchdogExit -eq 0
        Add-Check "Source real watchdog stall harness" $WatchdogPassed ($WatchdogOutput -join " ")
        Write-Host "Source real watchdog stall harness: $(if ($WatchdogPassed) { 'PASS' } else { 'FAIL' })"
    }

    return -not ($Checks | Where-Object { $_.Status -eq "FAIL" })
}

function Get-JavisTestProcesses {
    $ResolvedInstall = [IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')
    $ResolvedData = [IO.Path]::GetFullPath($DataRoot).TrimEnd('\')
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            if (-not $VerifierProcessIds.Contains([int]$_.ProcessId)) {
                $ExecutablePath = if ($_.ExecutablePath) { [IO.Path]::GetFullPath($_.ExecutablePath) } else { "" }
                $CommandLine = [string]$_.CommandLine
                ($ExecutablePath -and (
                    $ExecutablePath.StartsWith($ResolvedInstall + "\", [StringComparison]::OrdinalIgnoreCase) -or
                    $ExecutablePath.StartsWith($ResolvedData + "\", [StringComparison]::OrdinalIgnoreCase)
                )) -or
                ($CommandLine -and (
                    $CommandLine.IndexOf($ResolvedInstall, [StringComparison]::OrdinalIgnoreCase) -ge 0 -or
                    $CommandLine.IndexOf($ResolvedData, [StringComparison]::OrdinalIgnoreCase) -ge 0
                ))
            }
            else {
                $false
            }
        }
}

function Wait-JavisProcessesStopped([int]$Seconds = 15) {
    $Deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $Deadline) {
        if (-not @(Get-JavisTestProcesses).Count) {
            return $true
        }
        Start-Sleep -Milliseconds 200
    }
    return (-not @(Get-JavisTestProcesses).Count)
}

function Stop-JavisProcesses {
    $Processes = @(Get-JavisTestProcesses | Sort-Object { if ($_.Name -eq "javis-app.exe") { 0 } else { 1 } })
    foreach ($Process in $Processes) {
        Stop-Process -Id $Process.ProcessId -Force -ErrorAction SilentlyContinue
    }
    return Wait-JavisProcessesStopped 15
}

function Stop-JavisPortOwner {
    $Listeners = @(Get-NetTCPConnection -State Listen -LocalPort 8080 -ErrorAction SilentlyContinue)
    foreach ($Listener in $Listeners) {
        $Owner = Get-CimInstance Win32_Process -Filter "ProcessId=$($Listener.OwningProcess)" -ErrorAction SilentlyContinue
        if (-not $Owner) {
            continue
        }
        $ExecutablePath = [string]$Owner.ExecutablePath
        $CommandLine = [string]$Owner.CommandLine
        $IsJavis = (
            $ExecutablePath.IndexOf("local.javis.desktop", [StringComparison]::OrdinalIgnoreCase) -ge 0 -or
            $CommandLine.IndexOf("Javis", [StringComparison]::OrdinalIgnoreCase) -ge 0
        ) -and $CommandLine.IndexOf("main.py", [StringComparison]::OrdinalIgnoreCase) -ge 0
        if (-not $IsJavis) {
            throw "Port 8080 is occupied by a non-Javis process: $($Owner.Name) [$($Owner.ProcessId)]"
        }
        Stop-Process -Id $Owner.ProcessId -Force -ErrorAction Stop
    }
}

function Wait-JavisStatus([int]$Seconds) {
    $Deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $Deadline) {
        if (Test-VisibleJavisHelper) {
            $script:VisibleHelperDetected = $true
        }
        try {
            $Status = Invoke-RestMethod -Uri "http://127.0.0.1:8080/api/status" -TimeoutSec 2
            if (
                $Status.service -eq "javis" -and
                $Status.desktop_api_version -eq 2 -and
                $Status.capabilities.continuous_voice -eq $true
            ) {
                return $Status
            }
        }
        catch {
            Start-Sleep -Milliseconds 500
        }
    }
    return $null
}

function Test-VisibleJavisHelper {
    foreach ($Process in @(Get-JavisTestProcesses)) {
        if ($Process.Name -notin @("tar.exe", "certutil.exe", "powershell.exe", "python.exe", "pythonw.exe")) {
            continue
        }
        $Live = Get-Process -Id $Process.ProcessId -ErrorAction SilentlyContinue
        if ($Live -and $Live.MainWindowHandle -ne 0) {
            return $true
        }
    }
    return $false
}

function Test-JavisVoiceWebSocket {
    $Socket = [System.Net.WebSockets.ClientWebSocket]::new()
    $Timeout = [Threading.CancellationTokenSource]::new([TimeSpan]::FromSeconds(8))
    try {
        $null = $Socket.ConnectAsync([Uri]"ws://127.0.0.1:8080/ws_voice_stream", $Timeout.Token).GetAwaiter().GetResult()
        return $Socket.State -eq [System.Net.WebSockets.WebSocketState]::Open
    }
    catch {
        return $false
    }
    finally {
        $null = $Socket.Dispose()
        $null = $Timeout.Dispose()
    }
}

function Wait-BundledOllamaModel([int]$Seconds) {
    $Deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $Deadline) {
        try {
            $Catalog = Invoke-RestMethod -Uri "http://127.0.0.1:11435/api/tags" -TimeoutSec 2
            $Names = @($Catalog.models | ForEach-Object { [string]$_.name })
            if ($Names | Where-Object { $_ -eq "deepseek-r1:8b" -or $_ -like "deepseek-r1:8b*" }) {
                return $Catalog
            }
        }
        catch {
            Start-Sleep -Milliseconds 500
        }
    }
    return $null
}

function Wait-AppWindow([int]$ProcessId, [int]$Seconds) {
    $Started = Get-Date
    $Deadline = $Started.AddSeconds($Seconds)
    while ((Get-Date) -lt $Deadline) {
        $Process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
        if (-not $Process) {
            return $null
        }
        if ($Process.MainWindowHandle -ne 0 -and $Process.Responding) {
            return [pscustomobject]@{
                Handle = $Process.MainWindowHandle
                Milliseconds = [int]((Get-Date) - $Started).TotalMilliseconds
            }
        }
        Start-Sleep -Milliseconds 100
    }
    return $null
}

function Wait-InstallerRemoval([int]$Seconds) {
    $Deadline = (Get-Date).AddSeconds($Seconds)
    $RegistryPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Javis"
    while ((Get-Date) -lt $Deadline) {
        $Registered = Test-Path -LiteralPath $RegistryPath
        $Installed = Test-Path -LiteralPath (Join-Path $InstallRoot "javis-app.exe")
        if (-not $Registered -and -not $Installed) {
            return $true
        }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Remove-TestInstall {
    if (-not (Test-Path -LiteralPath $InstallRoot)) {
        return
    }
    $ResolvedTarget = [IO.Path]::GetFullPath($InstallRoot)
    if (-not $ResolvedTarget.StartsWith("D:\Javis-v3-install-test", [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove install test path outside the D-drive test root: $ResolvedTarget"
    }
    for ($Attempt = 1; $Attempt -le 5; $Attempt++) {
        Stop-JavisProcesses | Out-Null
        try {
            Remove-Item -LiteralPath $ResolvedTarget -Recurse -Force
            return
        }
        catch {
            if ($Attempt -eq 5) { throw }
            Start-Sleep -Milliseconds (250 * $Attempt)
        }
    }
}

function Remove-TestData {
    if (-not (Test-Path -LiteralPath $DataRoot)) {
        return
    }
    $ResolvedTarget = [IO.Path]::GetFullPath($DataRoot)
    if (-not $ResolvedTarget.StartsWith("D:\Javis-v3-data-test", [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove data test path outside the D-drive test root: $ResolvedTarget"
    }
    for ($Attempt = 1; $Attempt -le 5; $Attempt++) {
        Stop-JavisProcesses | Out-Null
        try {
            Remove-Item -LiteralPath $ResolvedTarget -Recurse -Force
            return
        }
        catch {
            if ($Attempt -eq 5) { throw }
            Start-Sleep -Milliseconds (250 * $Attempt)
        }
    }
}

if ($SourceRegressionOnly) {
    $SourcePassed = Invoke-SourceRegressionGate
    exit $(if ($SourcePassed) { 0 } else { 1 })
}

if (-not $Installer -or -not $Report -or -not $Manifest) {
    throw "Installer, Report and Manifest are required for installation verification."
}
$ExpectedRuntimeHash = (Get-Content -Raw -LiteralPath $Manifest | ConvertFrom-Json).archive.sha256
if ($SourceRoot) {
    Invoke-SourceRegressionGate | Out-Null
}

Stop-JavisPortOwner
Stop-JavisProcesses | Out-Null
$ResolvedInstaller = [IO.Path]::GetFullPath($Installer)
if (-not $ResolvedInstaller.StartsWith("D:\", [StringComparison]::OrdinalIgnoreCase)) {
    throw "Independent installer verification only accepts the final package from D:: $ResolvedInstaller"
}
$DDrive = Get-PSDrive -Name D -PSProvider FileSystem
$RequiredFreeBytes = 5GB
if ($DDrive.Free -lt $RequiredFreeBytes) {
    throw "D: requires at least 5 GB free for the main App installation test; available=$([math]::Round($DDrive.Free / 1GB, 2)) GB"
}
Remove-TestInstall
Remove-TestData
New-Item -ItemType Directory -Path (Split-Path -Parent $Canary) -Force | Out-Null
"JAVIS_V3_PRESERVATION_TEST" | Set-Content -LiteralPath $Canary -Encoding ascii
$env:JAVIS_APP_DATA_ROOT = $DataRoot

try {
    $Install = Start-Process -FilePath $Installer -ArgumentList @("/S", "/DATA=$DataRoot", "/D=$InstallRoot") -PassThru -Wait
    Add-Check "Main setup silent install" ($Install.ExitCode -eq 0) "exit=$($Install.ExitCode); root=$InstallRoot; data=$DataRoot"

    $BundledOllama = Join-Path $DataRoot "local-ai\ollama\ollama.exe"
    $BundledModel = Join-Path $DataRoot "local-ai\models\manifests\registry.ollama.ai\library\deepseek-r1\8b"
    Add-Check "Main setup excludes Ollama" (-not (Test-Path -LiteralPath $BundledOllama -PathType Leaf)) $BundledOllama
    Add-Check "Main setup excludes R1 weights" (-not (Test-Path -LiteralPath $BundledModel -PathType Leaf)) $BundledModel

    $AppExe = Get-ChildItem -LiteralPath $InstallRoot -Recurse -Filter "javis-app.exe" -File -ErrorAction SilentlyContinue |
        Select-Object -First 1
    Add-Check "Installed App executable" ($null -ne $AppExe) $(if ($AppExe) { $AppExe.FullName } else { "missing" })

    if ($AppExe) {
        $WebView2Loader = Join-Path $AppExe.DirectoryName "WebView2Loader.dll"
        $WebView2LoaderPresent = Test-Path -LiteralPath $WebView2Loader -PathType Leaf
        $WebView2LoaderHash = if ($WebView2LoaderPresent) {
            (Get-FileHash -LiteralPath $WebView2Loader -Algorithm SHA256).Hash
        } else {
            ""
        }
        Add-Check "Installed WebView2 loader" ($WebView2LoaderPresent -and $WebView2LoaderHash.Length -eq 64) $(if ($WebView2LoaderPresent) { "$WebView2Loader; sha256=$WebView2LoaderHash" } else { "missing beside javis-app.exe" })
        if (-not $WebView2LoaderPresent) {
            throw "Installed App is missing WebView2Loader.dll beside javis-app.exe."
        }

        $BaselineAppHash = (Get-FileHash -LiteralPath $AppExe.FullName -Algorithm SHA256).Hash
        Add-Check "Installed App binary baseline" ($BaselineAppHash.Length -eq 64) "installed=$BaselineAppHash"
        if ($SourceRoot) {
            $SourceFiles = @(
                Get-ChildItem -LiteralPath (Join-Path $SourceRoot "app\src") -Recurse -File -ErrorAction Stop
                Get-ChildItem -LiteralPath (Join-Path $SourceRoot "app\src-tauri\src") -Recurse -File -ErrorAction Stop
            )
            $LatestSource = $SourceFiles | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
            $FreshBinary = $LatestSource -and $AppExe.LastWriteTimeUtc -ge $LatestSource.LastWriteTimeUtc
            Add-Check "Installed desktop binary freshness" $FreshBinary "app=$($AppExe.LastWriteTimeUtc.ToString('o')); source=$($LatestSource.LastWriteTimeUtc.ToString('o')); file=$($LatestSource.FullName)"
        }
        $App = Start-Process -FilePath $AppExe.FullName -PassThru
        $Window = Wait-AppWindow $App.Id 15
        Add-Check "Installed App window" ($null -ne $Window) $(if ($Window) { "responsive handle=$($Window.Handle); visible_ms=$($Window.Milliseconds)" } else { "no responsive window within 15 seconds" })
        $Status = Wait-JavisStatus 180
        Add-Check "Installed backend /api/status" ($null -ne $Status) $(if ($Status) { "service=$($Status.service); desktop_api=$($Status.desktop_api_version)" } else { "offline or incompatible" })
        Add-Check "No visible helper console" (-not $VisibleHelperDetected) "tar, certutil, PowerShell and Python helpers remained hidden"
        $SelfTestReady = $false
        try {
            $SelfTest = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8080/api/diagnostics/self-test" -ContentType "application/json" -Body '{"scope":"runtime"}' -TimeoutSec 30
            $SelfTestReady = $null -ne $SelfTest
        }
        catch {
            $SelfTestReady = $false
        }
        Add-Check "Installed diagnostics contract" $SelfTestReady "/api/diagnostics/self-test"
        $VoiceSocketReady = Test-JavisVoiceWebSocket
        Add-Check "Installed continuous voice contract" $VoiceSocketReady "/ws_voice_stream"

        $Marker = Join-Path $RuntimeRoot "runtime-version.json"
        $Version = ""
        $ActivatedRuntimeHash = ""
        if (Test-Path -LiteralPath $Marker) {
            try {
                $RuntimeMarker = Get-Content -Raw -LiteralPath $Marker | ConvertFrom-Json
                $Version = $RuntimeMarker.version
                $ActivatedRuntimeHash = $RuntimeMarker.archive.sha256
            }
            catch {
                $Version = ""
                $ActivatedRuntimeHash = ""
            }
        }
        Add-Check "First-start runtime activation" ($Version -eq "3.0.0") "version=$Version; marker=$Marker"
        Add-Check "Activated runtime hash" ($ActivatedRuntimeHash -eq $ExpectedRuntimeHash) "active=$ActivatedRuntimeHash; expected=$ExpectedRuntimeHash"
        Add-Check "Upgrade preservation" (Test-Path -LiteralPath $Canary) "preservation canary=$Canary"
        Stop-JavisProcesses | Out-Null

        [IO.File]::AppendAllText($AppExe.FullName, "STALE-INSTALL-TEST")
        $StaleHash = (Get-FileHash -LiteralPath $AppExe.FullName -Algorithm SHA256).Hash
        $Reinstall = Start-Process -FilePath $Installer -ArgumentList @("/S", "/DATA=$DataRoot", "/D=$InstallRoot") -PassThru -Wait
        $ReinstalledHash = (Get-FileHash -LiteralPath $AppExe.FullName -Algorithm SHA256).Hash
        Add-Check "Same-version reinstall replaces stale App" (
            $Reinstall.ExitCode -eq 0 -and
            $StaleHash -ne $BaselineAppHash -and
            $ReinstalledHash -eq $BaselineAppHash
        ) "exit=$($Reinstall.ExitCode); stale=$StaleHash; installed=$ReinstalledHash; baseline=$BaselineAppHash"
        Add-Check "Reinstall preserves user data" (Test-Path -LiteralPath $Canary) "preservation canary=$Canary"
    }

    $Uninstaller = Get-ChildItem -LiteralPath $InstallRoot -Recurse -Filter "uninstall.exe" -File -ErrorAction SilentlyContinue |
        Select-Object -First 1
    Add-Check "Uninstaller present" ($null -ne $Uninstaller) $(if ($Uninstaller) { $Uninstaller.FullName } else { "missing" })
    if ($Uninstaller) {
        $Uninstall = Start-Process -FilePath $Uninstaller.FullName -ArgumentList "/S" -PassThru -Wait
        $Removed = Wait-InstallerRemoval 30
        Add-Check "Uninstall shell" ($Uninstall.ExitCode -eq 0) "exit=$($Uninstall.ExitCode)"
        Add-Check "Uninstall registry cleanup" $Removed "shell and HKCU uninstall registration removed"
        Add-Check "Uninstall preserves user data" (Test-Path -LiteralPath $Canary) "preservation canary retained"
    }
}
catch {
    Add-Check "Installer verification exception" $false $_.Exception.Message
}
finally {
    Stop-JavisProcesses | Out-Null
    if (Test-Path -LiteralPath $Canary) {
        Remove-Item -LiteralPath $Canary -Force
    }
    Remove-TestData
    Remove-TestInstall
    $env:JAVIS_APP_DATA_ROOT = $PreviousDataRoot
}

$Passed = -not ($Checks | Where-Object { $_.Status -eq "FAIL" })
$Lines = @(
    "# Javis v3.0 Installation Test Report"
    ""
    "Overall: $(if ($Passed) { 'PASS' } else { 'FAIL' })"
    ""
    "| Check | Status | Detail |"
    "|---|---|---|"
)
$Lines += $Checks | ForEach-Object { "| $($_.Name) | $($_.Status) | $($_.Detail) |" }
$Lines += @(
    ""
    "Audio capture is provided by the packaged native Python/PortAudio and Windows WASAPI paths. The App WebView does not request microphone, camera, screen-capture or clipboard permissions."
)
$ReportParent = Split-Path -Parent $Report
New-Item -ItemType Directory -Path $ReportParent -Force | Out-Null
$Lines | Set-Content -LiteralPath $Report -Encoding utf8
$Lines | ForEach-Object { Write-Host $_ }
exit $(if ($Passed) { 0 } else { 1 })
