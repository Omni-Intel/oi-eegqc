param([Parameter(Mandatory=$true)][string]$ConfigPath)
$ErrorActionPreference = 'Stop'
$taskConfig = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
$taskRoot = [IO.Path]::GetFullPath($taskConfig.root)
$taskIncoming = [IO.Path]::GetFullPath($taskConfig.incoming)
$taskBackup = [IO.Path]::GetFullPath($taskConfig.backup)
$taskProcess = Get-Process -Id $taskConfig.pid -ErrorAction SilentlyContinue
if ($taskProcess -and -not $taskProcess.WaitForExit(60000)) { exit 1 }
New-Item -ItemType Directory -Path $taskBackup -Force | Out-Null
$taskMoved = @()
try {
    foreach ($taskItem in Get-ChildItem -LiteralPath $taskIncoming) {
        $taskTarget = Join-Path $taskRoot $taskItem.Name
        if (Test-Path -LiteralPath $taskTarget) {
            Move-Item -LiteralPath $taskTarget -Destination (Join-Path $taskBackup $taskItem.Name)
        }
        $taskMoved += $taskItem.Name
        Move-Item -LiteralPath $taskItem.FullName -Destination $taskTarget
    }
} catch {
    $_ | Out-String | Set-Content -LiteralPath (Join-Path (Split-Path $ConfigPath) 'error.txt') -Encoding UTF8
    foreach ($taskName in $taskMoved) {
        $taskTarget = Join-Path $taskRoot $taskName
        if (Test-Path -LiteralPath $taskTarget) {
            $taskFailed = Join-Path (Split-Path $ConfigPath) ('failed-' + $taskName)
            Move-Item -LiteralPath $taskTarget -Destination $taskFailed
        }
        $taskPrevious = Join-Path $taskBackup $taskName
        if (Test-Path -LiteralPath $taskPrevious) { Move-Item -LiteralPath $taskPrevious -Destination $taskTarget }
    }
}
Start-Process -FilePath (Join-Path $taskRoot 'OI-EEGQC.exe') -WorkingDirectory $taskRoot
