# Unsigned Inno Setup installer. Do not Authenticode-sign.
param([string]$IsccPath = '', [switch]$SkipDependencies)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path '.venv/Scripts/python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed' }
}
if (-not $SkipDependencies) {
    & ./.venv/Scripts/python.exe -m pip install -e '.[desktop,packaging,dev]'
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
}
$checkArgs = @('scripts/check.py', '--calibrate')
if ($env:GITHUB_REF_TYPE -eq 'tag') { $checkArgs += @('--tag', $env:GITHUB_REF_NAME) }
& ./.venv/Scripts/python.exe @checkArgs
if ($LASTEXITCODE -ne 0) { throw 'Product verification failed' }
& ./.venv/Scripts/python.exe -m PyInstaller --noconfirm eegqc.spec
if ($LASTEXITCODE -ne 0) { throw 'Packaging failed' }
& ./.venv/Scripts/python.exe scripts/smoke-desktop.py dist/OI-EEGQC/OI-EEGQC.exe
if ($LASTEXITCODE -ne 0) { throw 'Packaged application check failed' }
Copy-Item docs/desktop.md dist/OI-EEGQC/README.md -Force
Copy-Item docs/folder-upload.md dist/OI-EEGQC/folder-upload.md -Force

$iscc = @(
    $IsccPath,
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles}\Inno Setup 6\ISCC.exe"
) | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
if (-not $iscc) {
    throw 'Inno Setup 6 not found. Install it or pass -IsccPath. Installer is required.'
}
$version = & ./.venv/Scripts/python.exe -c 'from oi_eegqc import __version__; print(__version__)'
if ($LASTEXITCODE -ne 0) { throw 'Could not read package version' }
& $iscc "/DAppVersion=$version" packaging\oi-eegqc.iss
if ($LASTEXITCODE -ne 0) { throw 'Installer compilation failed' }
Write-Host 'Ready: dist/OI-EEGQC-Setup-Windows-x64.exe'
