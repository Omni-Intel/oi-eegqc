# Stop the hidden LAN inbox receiver started by start-local-inbox.ps1.
$ErrorActionPreference = 'Stop'
$pidFile = 'D:\EEG_Data\inbox\_state\receiver.pid'
$stopped = $false
if (Test-Path -LiteralPath $pidFile) {
    $receiverId = [int]((Get-Content -LiteralPath $pidFile -Raw).Trim())
    $proc = Get-Process -Id $receiverId -ErrorAction SilentlyContinue
    if ($proc) {
        Stop-Process -Id $receiverId -Force
        $stopped = $true
    }
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
}
Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*oi_eegqc.upload_signer.app*' } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        $stopped = $true
    }
if ($stopped) { Write-Output 'stopped' } else { Write-Output 'not-running' }
