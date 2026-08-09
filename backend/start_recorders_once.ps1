$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$data = Join-Path $root "data"
$python = (Get-Command python.exe -ErrorAction Stop).Source
$null = New-Item -ItemType Directory -Path $data -Force
$env:PYTHONPATH = "$root\backend;$root\backend\polymarket;$root"
$env:BTC_DATA_DIR = $data
$script:RecorderFailures = @()

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
    try {
        $stderrPath = Join-Path $data $stderrName
        $process = Start-Process -FilePath $python -ArgumentList $arguments `
            -WorkingDirectory $root -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $data $stdoutName) `
            -RedirectStandardError $stderrPath -PassThru
        Start-Sleep -Milliseconds 500
        $process.Refresh()
        if ($process.HasExited) {
            $detail = "exit=$($process.ExitCode)"
            if (Test-Path -LiteralPath $stderrPath) {
                $tail = (Get-Content -LiteralPath $stderrPath -Tail 2 -ErrorAction SilentlyContinue) -join " | "
                if ($tail) { $detail = "$detail $tail" }
            }
            Write-Host "[recorder] ERROR $name failed during startup ($detail)."
            $script:RecorderFailures += $name
            return
        }
        Write-Host "[recorder] $name started (PID $($process.Id))."
    } catch {
        Write-Host "[recorder] ERROR $name could not start: $($_.Exception.Message)"
        $script:RecorderFailures += $name
    }
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

# Sub-second Binance reference recorded on the SAME host clock as Polymarket L2. This is a
# separate evidence stream from the slower multi-venue archive and keeps raw envelopes off by
# default to limit storage growth (roughly 0.3 GB/day in the measured smoke run).
if ($env:BTC_SKIP_BTC_TICK_RECORDER -eq "1") {
    Write-Host "[recorder] Fast Binance BTC tick recorder skipped."
} else {
    Start-Recorder "Fast Binance BTC tick stream" "btc_tick_recorder\.py" `
        @("-u", "backend\btc_tick_recorder.py") `
        "btc_tick_recorder.stdout.log" "btc_tick_recorder.stderr.log"
}

if ($env:BTC_SKIP_MICROSTRUCTURE_RECORDER -eq "1") {
    Write-Host "[recorder] Cross-exchange microstructure recorder skipped."
} else {
    Start-Recorder "Cross-exchange microstructure" "microstructure_recorder\.py" `
        @("-u", "backend\microstructure_recorder.py", "--interval", "1.0") `
        "microstructure_recorder.stdout.log" "microstructure_recorder.stderr.log"
}

# Multi-venue event-time collector (Binance spot/perp, Bybit, Coinbase). Public read-only market
# data only - this process holds no credentials and CANNOT trade. It captures the one thing that
# cannot be reconstructed later: the event-time cross-venue picture with honest recv_ts.
#
# NOTE ON THE EVIDENCE CLOCK: BINANCE_VOLATILITY_MOMENTUM_V1 needs >= 4 CONTINUOUS weeks at full
# stream health. A laptop that sleeps will produce mostly NON-QUALIFYING episodes, and the episode
# ledger records that honestly rather than hiding it. Local collection is therefore useful for
# mechanics and monitoring; the qualifying run belongs on the always-on box (see
# docs/active/COLLECTOR_DEPLOYMENT_RUNBOOK_2026-07-26.md).
#   python backend\venues\multi_venue_recorder.py --report    (uptime vs qualifying coverage)
if ($env:BTC_SKIP_VENUE_COLLECTOR -eq "1") {
    Write-Host "[recorder] Multi-venue event-time collector skipped."
} else {
    Start-Recorder "Multi-venue event-time collector" "multi_venue_recorder\.py" `
        @("-u", "backend\venues\multi_venue_recorder.py") `
        "multi_venue_recorder.stdout.log" "multi_venue_recorder.stderr.log"
}

# Full sequenced Binance USD-M book. This is intentionally isolated from the
# main app and from the top-of-book multi-venue archive. It records raw
# snapshot+diff evidence only and has no credentials or order path.
if ($env:BTC_SKIP_BINANCE_L2_RECORDER -eq "1") {
    Write-Host "[recorder] Binance sequenced L2 recorder skipped."
} else {
    Start-Recorder "Binance sequenced L2" "binance_l2_recorder\.py" `
        @("-u", "backend\venues\binance_l2_recorder.py", "--max-db-gb", "10") `
        "binance_l2_recorder.stdout.log" "binance_l2_recorder.stderr.log"
}

# High-frequency anchor crossings. --forever implies the recorder's own bounded-backoff
# supervisor, so transient websocket failures do not permanently stop forward evidence.
if ($env:BTC_SKIP_HF_CROSSING_RECORDER -eq "1") {
    Write-Host "[recorder] High-frequency crossing recorder skipped."
} else {
    Start-Recorder "High-frequency anchor crossings" "crossing_recorder_hf\.py" `
        @("-u", "backend\crossing_recorder_hf.py", "--forever") `
        "crossing_recorder_hf.stdout.log" "crossing_recorder_hf.stderr.log"
}

# Cross-window dominance observations are evidence only. The recorder proves liveness with a
# heartbeat on every pass, including passes where no synchronized 5m/15m pair exists.
if ($env:BTC_SKIP_CROSS_WINDOW_RECORDER -eq "1") {
    Write-Host "[recorder] Cross-window recorder skipped."
} else {
    Start-Recorder "Polymarket cross-window observations" "cross_window_recorder\.py" `
        @("-u", "backend\cross_window_recorder.py", "--forever", "--interval", "5") `
        "cross_window_recorder.stdout.log" "cross_window_recorder.stderr.log"
}

# Public per-strike options surface. Optional for core decisions, but required to accumulate
# honest implied-volatility/straddle evidence instead of reconstructing it after the fact.
if ($env:BTC_SKIP_DERIBIT_CHAIN_RECORDER -eq "1") {
    Write-Host "[recorder] Deribit option-chain recorder skipped."
} else {
    Start-Recorder "Deribit BTC option chain" "deribit_option_chain_recorder\.py" `
        @("-u", "backend\venues\deribit_option_chain_recorder.py", "--interval", "30") `
        "deribit_option_chain_recorder.stdout.log" "deribit_option_chain_recorder.stderr.log"
}

if ($script:RecorderFailures.Count -gt 0) {
    Write-Host "[recorder] Startup failures: $($script:RecorderFailures -join ', ')"
    exit 1
}
Write-Host "[recorder] All enabled standalone recorders are running or were already active."
exit 0
