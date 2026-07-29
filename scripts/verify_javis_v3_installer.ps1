param(
    [Parameter(Mandatory = $true)]
    [string]$Installer,
    [Parameter(Mandatory = $true)]
    [string]$Report,
    [Parameter(Mandatory = $true)]
    [string]$Manifest
)

$ErrorActionPreference = "Stop"
$InstallRoot = Join-Path $env:TEMP "Javis-v3-install-test"
$DataRoot = Join-Path $env:TEMP "Javis-v3-data-test"
$RuntimeRoot = Join-Path $DataRoot "runtime"
$Canary = Join-Path $RuntimeRoot "app\workspace\v3-preservation-canary.txt"
$Checks = [System.Collections.Generic.List[object]]::new()
$PreviousDataRoot = $env:JAVIS_APP_DATA_ROOT
$ExpectedRuntimeHash = (Get-Content -Raw -LiteralPath $Manifest | ConvertFrom-Json).archive.sha256

function Add-Check([string]$Name, [bool]$Passed, [string]$Detail) {
    $Checks.Add([pscustomobject]@{
        Name = $Name
        Status = if ($Passed) { "PASS" } else { "FAIL" }
        Detail = $Detail.Replace("|", "/").Replace("`r", " ").Replace("`n", " ")
    })
}

function Get-JavisTestProcesses {
    $ResolvedInstall = [IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')
    $ResolvedData = [IO.Path]::GetFullPath($DataRoot).TrimEnd('\')
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
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
        try {
            $Status = Invoke-RestMethod -Uri "http://127.0.0.1:8080/api/status" -TimeoutSec 2
            if ($Status.service -eq "javis") {
                return $Status
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
    $ResolvedTemp = [IO.Path]::GetFullPath($env:TEMP).TrimEnd('\')
    $ResolvedTarget = [IO.Path]::GetFullPath($InstallRoot)
    if (-not $ResolvedTarget.StartsWith($ResolvedTemp + "\", [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove install test path outside TEMP: $ResolvedTarget"
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
    $ResolvedTemp = [IO.Path]::GetFullPath($env:TEMP).TrimEnd('\')
    $ResolvedTarget = [IO.Path]::GetFullPath($DataRoot)
    if (-not $ResolvedTarget.StartsWith($ResolvedTemp + "\", [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove data test path outside TEMP: $ResolvedTarget"
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

Stop-JavisPortOwner
Stop-JavisProcesses | Out-Null
Remove-TestInstall
Remove-TestData
New-Item -ItemType Directory -Path (Split-Path -Parent $Canary) -Force | Out-Null
"JAVIS_V3_PRESERVATION_TEST" | Set-Content -LiteralPath $Canary -Encoding ascii
$env:JAVIS_APP_DATA_ROOT = $DataRoot

try {
    $Install = Start-Process -FilePath $Installer -ArgumentList @("/S", "/D=$InstallRoot") -PassThru -Wait
    Add-Check "NSIS silent install" ($Install.ExitCode -eq 0) "exit=$($Install.ExitCode); root=$InstallRoot"

    $AppExe = Get-ChildItem -LiteralPath $InstallRoot -Recurse -Filter "javis-app.exe" -File -ErrorAction SilentlyContinue |
        Select-Object -First 1
    Add-Check "Installed App executable" ($null -ne $AppExe) $(if ($AppExe) { $AppExe.FullName } else { "missing" })

    if ($AppExe) {
        $BaselineAppHash = (Get-FileHash -LiteralPath $AppExe.FullName -Algorithm SHA256).Hash
        Add-Check "Installed App binary baseline" ($BaselineAppHash.Length -eq 64) "installed=$BaselineAppHash"
        $App = Start-Process -FilePath $AppExe.FullName -PassThru
        $Window = Wait-AppWindow $App.Id 15
        Add-Check "Installed App window" ($null -ne $Window) $(if ($Window) { "responsive handle=$($Window.Handle); visible_ms=$($Window.Milliseconds)" } else { "no responsive window within 15 seconds" })
        $Status = Wait-JavisStatus 180
        Add-Check "Installed backend /api/status" ($null -ne $Status) $(if ($Status) { "service=$($Status.service)" } else { "offline" })

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
        $Reinstall = Start-Process -FilePath $Installer -ArgumentList @("/S", "/D=$InstallRoot") -PassThru -Wait
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
