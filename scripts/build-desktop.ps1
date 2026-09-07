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
Compress-Archive -Path dist/OI-EEGQC -DestinationPath dist/OI-EEGQC-Windows-x64.zip -Force
Write-Host 'Ready: dist/OI-EEGQC-Windows-x64.zip'
