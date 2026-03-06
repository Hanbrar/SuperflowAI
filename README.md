# Super Flow

Super Flow is a free Windows dictation app plus a lightweight download landing page.

Core product goals implemented in this first version:
- `Ctrl+Space` default hold-to-talk dictation flow with auto-paste at cursor on release.
- Optional recorder popup visibility toggle (show popup or run in background only).
- Toggle mode and control mode (hold-to-talk).
- Local microphone selection.
- Temporary session PDF generation with close warning and export option.
- No permanent transcript storage by default.

## Project Structure

- `app/main.py`: Windows desktop app (Tkinter + local speech transcription).
- `website/index.html`: landing page.
- `website/styles.css`: landing page styling.
- `build_windows.ps1`: one-command Windows build script for executable packaging.
- `logo.png`: project logo used by app and website.

## Free Stack Used

- Speech recognition: `faster-whisper` (open-source local model).
- Audio capture: `sounddevice`.
- Global hotkey + paste: `keyboard` + `pyperclip`.
- Session PDF export: `reportlab`.
- UI: built-in `tkinter`.

## Windows Setup

1. Install Python 3.11+.
2. Open PowerShell in the repo folder.
3. Run:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python app\main.py
```

## Usage

1. Open Super Flow.
2. Select a microphone.
3. Hold `Ctrl+Space` to start dictation.
4. Release `Ctrl+Space` to stop and paste text.
5. Optional: choose recorder visibility mode (`Show SuperFlow Recorder` or `Background only`).
6. Use `Export Session PDF` anytime to save the current transcript.

When closing the app, a warning appears so you can export the temporary session PDF before it is removed.

## Build Windows EXE

```powershell
.\build_windows.ps1
```

Output:
- `dist\SuperFlow.exe`

## Landing Page

Open `website/index.html` in a browser, or host the `website` folder on static hosting.
Set the download button to your release artifact URL.
