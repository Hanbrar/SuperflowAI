param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$python = if (Test-Path ".\.venv\Scripts\python.exe") { ".\.venv\Scripts\python.exe" } else { "python" }

if (-not $SkipInstall) {
    & $python -m pip install --upgrade pip
    & $python -m pip install -r requirements.txt
    & $python -m pip install pyinstaller
}

& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --onefile `
    --name SuperFlow `
    --add-data "logo.png;." `
    app\main.py

Write-Host "Build complete: dist\SuperFlow.exe"
