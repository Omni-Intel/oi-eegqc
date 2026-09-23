# Start the LAN inbox receiver hidden. Safe to double-click; no window.
$ErrorActionPreference = 'Stop'
$inbox = 'D:\EEG_Data\inbox'
$hostAddress = '172.16.1.249'
$port = 8443
$health = "http://${hostAddress}:${port}/health"
$repo = Split-Path $PSScriptRoot -Parent
$state = Join-Path $inbox '_state'
New-Item -ItemType Directory -Force -Path $state | Out-Null
$pidFile = Join-Path $state 'receiver.pid'
$outLog = Join-Path $state 'receiver.out.log'
$errLog = Join-Path $state 'receiver.err.log'

function Test-Receiver {
    try {
        $response = Invoke-WebRequest -Uri $health -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

if (Test-Receiver) {
    Write-Output "already-running $health"
    exit 0
}

$python = Join-Path $repo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    $python = 'C:\Users\kunpeng\AppData\Local\Programs\Python\Python313\python.exe'
}
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Python not found. Create D:\oi-eegqc\.venv first.'
}

$env:UPLOAD_BACKEND = 'local'
$env:EEG_INBOX_ROOT = $inbox
$env:UPLOAD_STATE = Join-Path $state 'signer.sqlite3'
$env:UPLOAD_PUBLIC_URL = "http://${hostAddress}:${port}"
$env:LISTEN_HOST = $hostAddress
$env:LISTEN_PORT = "$port"
Remove-Item -LiteralPath $outLog, $errLog -ErrorAction SilentlyContinue
$proc = Start-Process -FilePath $python -ArgumentList '-u', '-m', 'oi_eegqc.upload_signer.app' `
    -WorkingDirectory $repo -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $outLog -RedirectStandardError $errLog
$proc.Id | Set-Content -LiteralPath $pidFile -Encoding ascii
$ok = $false
foreach ($i in 1..20) {
    Start-Sleep -Milliseconds 250
    if (Test-Receiver) { $ok = $true; break }
    if ($proc.HasExited) { break }
}
if (-not $ok) {
    $detail = ''
    if (Test-Path -LiteralPath $errLog) { $detail = Get-Content -LiteralPath $errLog -Raw -ErrorAction SilentlyContinue }
    throw "inbox receiver failed to start. $detail"
}
Write-Output "started pid=$($proc.Id) $health"
