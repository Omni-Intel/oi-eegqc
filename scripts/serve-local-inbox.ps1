# Receive EEG uploads on this machine's intranet address.
$ErrorActionPreference = 'Stop'
$root = 'D:\EEG_Data\inbox'
New-Item -ItemType Directory -Force -Path "$root\_state" | Out-Null
$env:UPLOAD_BACKEND = 'local'
$env:EEG_INBOX_ROOT = $root
$env:UPLOAD_STATE = "$root\_state\signer.sqlite3"
$env:UPLOAD_PUBLIC_URL = 'http://172.16.1.249:8443'
$env:LISTEN_HOST = '172.16.1.249'
$env:LISTEN_PORT = '8443'
$python = Join-Path $PSScriptRoot '..\.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    $python = 'C:\Users\kunpeng\AppData\Local\Programs\Python\Python313\python.exe'
}
Set-Location (Split-Path $PSScriptRoot -Parent)
& $python -m oi_eegqc.upload_signer.app
