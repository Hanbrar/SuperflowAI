#!/usr/bin/env bash
# build_mac.sh — Build SuperFlow.app for macOS using PyInstaller
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "==> Activating virtual environment (if present)..."
if [ -d ".venv" ]; then
    source .venv/bin/activate
else
    echo "    No .venv found — using system Python. Run:"
    echo "    python3 -m venv .venv && source .venv/bin/activate"
    echo "    ...first if you want an isolated build."
fi

echo "==> Installing/upgrading Mac dependencies..."
pip install --upgrade pip
pip install \
    pyinstaller \
    "pynput>=1.7" \
    "faster-whisper>=1.0" \
    "sounddevice>=0.4" \
    pyperclip \
    reportlab \
    Pillow \
    numpy

echo "==> Running PyInstaller..."
pyinstaller \
    --name "SuperFlow" \
    --windowed \
    --onefile \
    --noconfirm \
    --add-data "logo.png:." \
    --add-data "faviconupdated.png:." \
    --hidden-import "pynput.keyboard._darwin" \
    --hidden-import "pynput.mouse._darwin" \
    --hidden-import "faster_whisper" \
    --hidden-import "sounddevice" \
    --hidden-import "pyperclip" \
    --collect-all "faster_whisper" \
    app_mac/main.py

echo ""
echo "==> Build complete!"
echo "    App bundle : dist/SuperFlow.app"
echo "    Single exe : dist/SuperFlow  (same --onefile build)"
echo ""
echo "To create a distributable zip:"
echo "    cd dist && zip -r SuperFlow-Mac.zip SuperFlow.app"
