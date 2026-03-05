param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"

if (-not $SkipInstall) {
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt
    python -m pip install pyinstaller
}

pyinstaller `
    --noconfirm `
    --clean `
    --windowed `
    --onefile `
    --name SuperFlow `
    --add-data "logo.png;." `
    app\main.py

Write-Host "Build complete: dist\SuperFlow.exe"

