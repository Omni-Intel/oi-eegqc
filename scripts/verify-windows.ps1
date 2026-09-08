param([Parameter(Mandatory=$true)][string]$BaselineInstaller)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$testDir = Join-Path $repo ('build\installer-smoke-' + [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss'))
$regPath = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{8F3C2A91-4B6D-4E17-9C5A-1D8E6F0B2A44}_is1'
if (Test-Path -LiteralPath $regPath) { throw 'An existing installation was found; leave it untouched.' }
if (Test-Path -LiteralPath $testDir) { throw 'Test directory already exists; inspect it before another run.' }
New-Item -ItemType Directory -Path $testDir | Out-Null
$appDir = Join-Path $testDir 'app'
function Run-Checked([string]$File, [string]$Arguments, [int]$Timeout = 60000) {
    $taskProcess = Start-Process -FilePath $File -ArgumentList $Arguments -WindowStyle Hidden -PassThru
    if (-not $taskProcess.WaitForExit($Timeout)) { throw "Timed out: $File" }
    if ($taskProcess.ExitCode -ne 0) { throw "Failed ($($taskProcess.ExitCode)): $File" }
}
$installArgs = '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /DIR="' + $appDir + '"'
Run-Checked (Resolve-Path $BaselineInstaller).Path ($installArgs + ' /LOG="' + $testDir + '\install.log"')
$before = Get-ItemProperty -LiteralPath $regPath
if ($before.DisplayVersion -ne '0.3.2') { throw 'Unexpected baseline version' }
Copy-Item -LiteralPath (Join-Path $repo 'LICENSE') -Destination (Join-Path $appDir 'user-data-preserved.txt')
Run-Checked (Join-Path $repo 'dist\OI-EEGQC-Setup-Windows-x64.exe') ($installArgs + ' /LOG="' + $testDir + '\upgrade.log"')
$after = Get-ItemProperty -LiteralPath $regPath
if ($after.DisplayVersion -ne '0.3.3') { throw 'Upgrade version mismatch' }
if ($after.InstallLocation.TrimEnd('\') -ne $appDir) { throw 'Upgrade changed install directory' }
if (-not (Test-Path -LiteralPath (Join-Path $appDir 'user-data-preserved.txt'))) { throw 'Upgrade removed user data' }
Run-Checked (Join-Path $appDir 'OI-EEGQC.exe') ('--startup-check "' + $testDir + '\startup.json"')
Run-Checked (Join-Path $appDir 'OI-EEGQC.exe') ('--update-check "' + $testDir + '\update.json"')
$startup = Get-Content -LiteralPath (Join-Path $testDir 'startup.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($startup.heavy_modules.Count -ne 0 -or -not $startup.icon_loaded -or $startup.title -ne 'Omni-Intelligence EEG Quality Control App') { throw 'Startup check failed' }
$update = Get-Content -LiteralPath (Join-Path $testDir 'update.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($update.status -eq 'error' -or -not $update.latest) { throw 'GitHub update check failed' }
Run-Checked (Join-Path $appDir 'unins000.exe') ('/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG="' + $testDir + '\uninstall.log"')
$deadline = [DateTime]::UtcNow.AddSeconds(30)
while ((Test-Path -LiteralPath $regPath) -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 200 }
if ((Test-Path -LiteralPath $regPath) -or (Test-Path -LiteralPath (Join-Path $appDir 'OI-EEGQC.exe'))) { throw 'Uninstall left the app registered or executable installed' }
if (-not (Test-Path -LiteralPath (Join-Path $appDir 'user-data-preserved.txt'))) { throw 'Uninstall removed user data' }
Write-Output "PASS: install 0.3.2, upgrade 0.3.3, startup, GitHub settings update ($($update.latest)), uninstall; user data retained. Logs: $testDir"
