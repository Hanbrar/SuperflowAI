# Super Flow

<p align="center">
  <img src="website/logo_web.png" alt="Super Flow" width="420">
</p>

<p align="center">
  Super Flow is a free, open-source Windows voice dictation app with a lightweight website.
</p>

## Hotkey (Locked For Now)

The product hotkey is currently fixed as:

`Ctrl + Alt + Space`

Hold to record, release to transcribe and paste.

## What It Does

- Fast dictation and auto-paste at cursor.
- Microphone selection.
- Toggle / hold activation modes.
- Recorder view modes (Large / Minimized / Background).
- Built-in update check that opens the latest download page when a newer tagged release exists.
- Session transcript with copy actions.
- Export session transcript to PDF.

## Run Locally (Windows)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python app\main.py
```

## Build EXE

```powershell
.\build_windows.ps1
```

Build output:
- `dist\SuperFlow.exe`

Release/version notes:
- Public releases are built from Git tags that match `v*`.
- The app version is defined in `app/main.py` as `APP_VERSION`.
- The desktop app checks GitHub Releases and prompts users to open the latest download page when a newer version exists.

## Website

- Source: `website/`
- Main file: `website/index.html`
- Vercel root directory: `website`
- Download button target:
  - `https://github.com/Hanbrar/SuperflowAI/releases/latest/download/SuperFlow.exe`

## Stack

- Python + Tkinter UI
- `faster-whisper`
- `sounddevice`
- `keyboard` + `pyperclip`
- `reportlab`
