# Silent install/upgrade from GitHub Releases. Per-user, no administrator.
param(
    [string]$Repo = 'Omni-Intel/oi-eegqc',
    [switch]$Start
)
$ErrorActionPreference = 'Stop'
$headers = @{
    Accept = 'application/vnd.github+json'
    'User-Agent' = 'oi-eegqc-installer'
}
$meta = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/latest" -Headers $headers -TimeoutSec 30
$asset = @($meta.assets) | Where-Object { $_.name -eq 'OI-EEGQC-Setup-Windows-x64.exe' } | Select-Object -First 1
if (-not $asset) { throw 'GitHub latest release has no Windows installer' }
$dir = Join-Path $env:TEMP 'oi-eegqc-install'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$exe = Join-Path $dir $asset.name
Write-Host "Downloading $($meta.tag_name) ..."
Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $exe -TimeoutSec 600
$expected = ([string]$asset.digest) -replace '^sha256:', ''
if ($expected -match '^[0-9a-fA-F]{64}$') {
    $actual = (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash
    if ($actual.ToLowerInvariant() -ne $expected.ToLowerInvariant()) {
        throw "installer hash mismatch: got $actual"
    }
}
Write-Host "Installing $($meta.tag_name) ..."
$process = Start-Process -FilePath $exe -ArgumentList '/VERYSILENT', '/NORESTART', '/CURRENTUSER' -Wait -PassThru
if ($process.ExitCode -ne 0) { throw "installer exited $($process.ExitCode)" }
$app = Join-Path $env:LOCALAPPDATA 'Omni-Intelligence\EEGQC\OI-EEGQC.exe'
if ($Start -and (Test-Path -LiteralPath $app)) {
    Start-Process -FilePath $app
}
Write-Host "Ready: $app"
