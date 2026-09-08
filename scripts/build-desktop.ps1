# Unsigned zip plus optional Inno Setup installer. Do not Authenticode-sign.
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path '.venv/Scripts/python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed' }
}
& ./.venv/Scripts/python.exe -m pip install -e '.[desktop,packaging]'
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
& ./.venv/Scripts/python.exe -m PyInstaller --noconfirm eegqc.spec
if ($LASTEXITCODE -ne 0) { throw 'Packaging failed' }
Copy-Item docs/desktop.md dist/OI-EEGQC/README.md -Force
if (Test-Path dist/OI-EEGQC-Windows-x64.zip) {
    Remove-Item dist/OI-EEGQC-Windows-x64.zip -Force
}
Compress-Archive -Path dist/OI-EEGQC -DestinationPath dist/OI-EEGQC-Windows-x64.zip -Force
Write-Host 'Ready: dist/OI-EEGQC-Windows-x64.zip'

$iscc = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles}\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
    Write-Host 'Inno Setup 6 not found; zip only. Install it to also build dist/OI-EEGQC-Setup-Windows-x64.exe'
    return
}
$version = & ./.venv/Scripts/python.exe -c 'from oi_eegqc import __version__; print(__version__)'
if ($LASTEXITCODE -ne 0) { throw 'Could not read package version' }
& $iscc "/DAppVersion=$version" packaging\oi-eegqc.iss
if ($LASTEXITCODE -ne 0) { throw 'Installer compilation failed' }
Write-Host 'Ready: dist/OI-EEGQC-Setup-Windows-x64.exe'
