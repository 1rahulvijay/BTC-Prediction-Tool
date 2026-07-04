$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$data = Join-Path $root "data"
$python = (Get-Command python.exe -ErrorAction Stop).Source
$env:PYTHONPATH = "$root\backend;$root\backend\polymarket;$root"
$env:BTC_DATA_DIR = $data

function Test-RecorderProcess([string]$pattern) {
    foreach ($process in Get-CimInstance Win32_Process -Filter "Name='python.exe'") {
        if ($process.CommandLine -and $process.CommandLine -match $pattern) {
            return $true
        }
    }
    return $false
}

function Start-Recorder(
    [string]$name,
    [string]$pattern,
    [string[]]$arguments,
    [string]$stdoutName,
    [string]$stderrName
) {
    if (Test-RecorderProcess $pattern) {
        Write-Host "[recorder] $name already running; duplicate skipped."
        return
    }
    $process = Start-Process -FilePath $python -ArgumentList $arguments `
        -WorkingDirectory $root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $data $stdoutName) `
        -RedirectStandardError (Join-Path $data $stderrName) -PassThru
    Write-Host "[recorder] $name started (PID $($process.Id))."
}

if ($env:BTC_SKIP_PM_RECORDER -eq "1") {
    Write-Host "[recorder] Polymarket quote/settlement recorder skipped."
} else {
    Start-Recorder "Polymarket quote + settlement" "live_btc_updown_recorder\.py" `
        @("-u", "backend\polymarket\live_btc_updown_recorder.py", "--settle-batch", "100") `
        "pm_live_recorder.stdout.log" "pm_live_recorder.stderr.log"
}

if ($env:BTC_SKIP_PM_L2_RECORDER -eq "1") {
    Write-Host "[recorder] Exact Polymarket L2 recorder skipped."
} else {
    Start-Recorder "Polymarket exact L2 + VWAP" "polymarket\\l2_recorder\.py" `
        @("-u", "backend\polymarket\l2_recorder.py", "--max-db-gb", "10") `
        "pm_l2_recorder.stdout.log" "pm_l2_recorder.stderr.log"
}

if ($env:BTC_SKIP_MICROSTRUCTURE_RECORDER -eq "1") {
    Write-Host "[recorder] Cross-exchange microstructure recorder skipped."
} else {
    Start-Recorder "Cross-exchange microstructure" "microstructure_recorder\.py" `
        @("-u", "backend\microstructure_recorder.py", "--interval", "1.0") `
        "microstructure_recorder.stdout.log" "microstructure_recorder.stderr.log"
}
