# Super Flow — macOS Build Guide

The Mac version of Super Flow lives in `app_mac/main.py`.  It is a
self-contained adaptation of the Windows app with all Windows-specific
dependencies replaced by macOS-compatible alternatives.

---

## What changed from the Windows version

| Windows | macOS replacement | Reason |
|---|---|---|
| `keyboard` library | `pynput` (global `Listener`) | `keyboard` requires low-level OS hooks only available on Windows |
| `ctypes.windll` (DPI, monitor enum) | removed / tkinter fallback | Windows-only Win32 API |
| `iconbitmap()` (.ico file) | skipped — `iconphoto()` only | macOS does not support `.ico` window icons |
| `keyboard.send("ctrl+v")` | `pynput.Controller` sends `Cmd+V` | Paste shortcut is `⌘V` on macOS |
| Segoe UI / Consolas fonts | Helvetica Neue / Courier | Segoe UI is a Microsoft font not present on macOS |

All features are preserved: dictation, mic selection, toggle/hold modes,
large/mini/background recorder views, session transcript, PDF export, and
automatic update checks.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| macOS 12 Monterey or later | Tested target |
| Python 3.10+ | Recommended: 3.12 |
| Xcode Command Line Tools | `xcode-select --install` |
| Accessibility permission | Required for global hotkeys via pynput (see below) |

### Accessibility permission (required for hotkeys)

`pynput` needs the app (or Terminal / your Python process) to be granted
**Accessibility** access so it can listen for global key events.

1. Open **System Settings → Privacy & Security → Accessibility**
2. Click **+** and add your Terminal app (or the built `.app`)
3. Restart the app after granting access

---

## Running locally (development)

```bash
# 1. Clone / open the repo
cd SuperflowAI

# 2. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install Mac dependencies
pip install --upgrade pip
pip install pynput faster-whisper sounddevice pyperclip reportlab Pillow numpy

# 4. Run the app
python app_mac/main.py
```

---

## Building the .app bundle

```bash
# Make the script executable (first time only)
chmod +x build_mac.sh

# Run the build
./build_mac.sh
```

Output:
- `dist/SuperFlow.app` — macOS app bundle (drag to Applications)
- `dist/SuperFlow` — raw Unix executable (same build, --onefile)

### Creating a distributable zip

```bash
cd dist
zip -r SuperFlow-Mac.zip SuperFlow.app
```

Upload `SuperFlow-Mac.zip` to the GitHub Release as an asset.

---

## Hotkey

The hotkey is the same as the Windows version:

`Ctrl + Alt + Space`  (^ ⌥ Space on a Mac keyboard)

Hold to record, release to transcribe and paste.

---

## Troubleshooting

**Hotkeys don't work / app can't listen for key events**
Grant Accessibility access (see Prerequisites above).

**`portaudio` / `sounddevice` install error**
Install PortAudio first via Homebrew:
```bash
brew install portaudio
pip install sounddevice
```

**`faster-whisper` model download is slow on first run**
The `tiny.en` model (~75 MB) is downloaded once and cached in
`~/.cache/huggingface/hub/` on subsequent runs.

**App bundle is flagged by Gatekeeper**
Right-click → Open → Open anyway (first launch only), or run:
```bash
xattr -dr com.apple.quarantine dist/SuperFlow.app
```
