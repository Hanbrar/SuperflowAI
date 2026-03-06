from __future__ import annotations

import ctypes
import logging
import math
import os
import random
import shutil
import tempfile
import textwrap
import threading
import time
import webbrowser
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

import keyboard
import numpy as np
import pyperclip
import sounddevice as sd
import tkinter as tk
from faster_whisper import WhisperModel
from huggingface_hub.utils import logging as hf_logging
from PIL import Image, ImageTk
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from tkinter import filedialog, messagebox, ttk

APP_TITLE = "Super Flow"
DEFAULT_MODEL = "tiny.en"
SAMPLE_RATE = 16000
CHANNELS = 1
TRANSCRIBE_BEAM_SIZE = 1
TRANSCRIBE_VAD_FILTER = False

warnings.filterwarnings("ignore", message="`huggingface_hub` cache-system uses symlinks.*")
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
hf_logging.set_verbosity_error()


def enable_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


class SessionPDFManager:
    def __init__(self) -> None:
        self.temp_dir = Path(tempfile.gettempdir()) / "SuperFlow"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.temp_pdf = self.temp_dir / "session.pdf"

    def update(self, entries: list[dict[str, str]]) -> None:
        self._write_pdf(self.temp_pdf, entries)

    def export_to(self, output_path: Path) -> None:
        if not self.temp_pdf.exists():
            raise FileNotFoundError("No session PDF has been generated yet.")
        shutil.copy2(self.temp_pdf, output_path)

    def cleanup(self) -> None:
        if self.temp_pdf.exists():
            self.temp_pdf.unlink(missing_ok=True)

    def _write_pdf(self, output_path: Path, entries: list[dict[str, str]]) -> None:
        pdf = canvas.Canvas(str(output_path), pagesize=letter)
        _page_width, page_height = letter
        margin = 40
        y = page_height - margin

        pdf.setTitle("Super Flow Session")
        pdf.setFont("Helvetica-Bold", 16)
        pdf.drawString(margin, y, "Super Flow Session Transcript")
        y -= 24

        pdf.setFont("Helvetica", 10)
        pdf.drawString(margin, y, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        y -= 20

        if not entries:
            pdf.setFont("Helvetica-Oblique", 11)
            pdf.drawString(margin, y, "No transcription entries yet.")
            pdf.save()
            return

        for entry in entries:
            timestamp = entry["timestamp"]
            text = entry["text"]

            if y < margin + 80:
                pdf.showPage()
                y = page_height - margin

            pdf.setFont("Helvetica-Bold", 11)
            pdf.drawString(margin, y, f"[{timestamp}]")
            y -= 16

            pdf.setFont("Helvetica", 11)
            for line in textwrap.wrap(text, width=95) or [""]:
                if y < margin + 40:
                    pdf.showPage()
                    y = page_height - margin
                    pdf.setFont("Helvetica", 11)
                pdf.drawString(margin, y, line)
                y -= 14

            y -= 10

        pdf.save()


class SuperFlowApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("860x960")
        self.root.minsize(760, 860)
        self.root.configure(bg="#f5ede3")

        self.status_var = tk.StringVar(
            value="Ready. Hold Ctrl+Space, speak, release, and paste instantly."
        )
        self.mode_var = tk.StringVar(value="control")
        self.recorder_view_var = tk.StringVar(value="show")
        self.mic_var = tk.StringVar(value="")

        self.model: WhisperModel | None = None
        self.model_lock = threading.Lock()
        self.state_lock = threading.Lock()

        self.is_recording = False
        self.is_transcribing = False
        self.combo_is_pressed = False
        self.last_transcript = ""
        self.audio_frames: list[np.ndarray[Any, Any]] = []
        self.current_sample_rate = SAMPLE_RATE
        self.stream: sd.InputStream | None = None
        self.hotkey_handles: list[int] = []
        self.hook_handle: Any = None

        self.microphones: list[tuple[str, int]] = []
        self.session_entries: list[dict[str, str]] = []
        self.pdf_manager = SessionPDFManager()

        self.logo_photo: ImageTk.PhotoImage | None = None
        self.transcript_box: tk.Text | None = None
        self.mic_combo: ttk.Combobox | None = None

        self.recording_popup: tk.Toplevel | None = None
        self.wave_canvas: tk.Canvas | None = None
        self.wave_rects: list[int] = []
        self.wave_timer: str | None = None
        self.audio_level = 0.0
        self._entry_count: int = 0

        self._build_ui()
        self._refresh_microphones()
        self._register_hotkeys()
        threading.Thread(target=self._preload_model, daemon=True).start()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background="#f5ede3")
        style.configure("TLabel", background="#f5ede3", foreground="#132640", font=("Segoe UI", 10))
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 28), foreground="#132640")
        style.configure("Subtitle.TLabel", font=("Segoe UI", 11), foreground="#375273")
        style.configure("TButton", padding=9, font=("Segoe UI Semibold", 10))

        outer = ttk.Frame(self.root, padding=20)
        outer.pack(fill="both", expand=True)

        self._build_header(outer)

        ttk.Label(outer, text="Super Flow", style="Title.TLabel", anchor="center").pack(pady=(10, 2))
        ttk.Label(
            outer,
            text="Simple voice dictation for free.",
            style="Subtitle.TLabel",
            anchor="center",
        ).pack(pady=(0, 10))

        control_card = tk.Frame(
            outer,
            bg="#ffffff",
            highlightthickness=1,
            highlightbackground="#e0d5c8",
            padx=14,
            pady=14,
        )
        control_card.pack(fill="x", pady=(0, 10))

        mic_row = tk.Frame(control_card, bg="#ffffff")
        mic_row.pack(fill="x", pady=(0, 10))
        tk.Label(mic_row, text="Microphone", bg="#ffffff", fg="#132640", font=("Segoe UI Semibold", 10)).pack(side="left")
        self.mic_combo = ttk.Combobox(mic_row, textvariable=self.mic_var, state="readonly", width=52)
        self.mic_combo.pack(side="left", padx=8, fill="x", expand=True)
        tk.Button(
            mic_row, text="Refresh", command=self._refresh_microphones,
            bg="#f5ede3", fg="#132640", activebackground="#e8ddd0", activeforeground="#132640",
            relief="flat", highlightthickness=1, highlightbackground="#c8bfb5",
            font=("Segoe UI Semibold", 10), padx=10, pady=5, cursor="hand2",
        ).pack(side="left")

        mode_row = tk.Frame(control_card, bg="#ffffff")
        mode_row.pack(fill="x", pady=(0, 6))
        tk.Label(mode_row, text="Activation mode", bg="#ffffff", fg="#132640", font=("Segoe UI Semibold", 10)).pack(side="left")
        ttk.Radiobutton(mode_row, text="Toggle", value="toggle", variable=self.mode_var, command=self._on_mode_changed).pack(
            side="left", padx=(12, 0)
        )
        ttk.Radiobutton(
            mode_row,
            text="Control (hold Ctrl+Space)",
            value="control",
            variable=self.mode_var,
            command=self._on_mode_changed,
        ).pack(side="left", padx=10)

        view_row = tk.Frame(control_card, bg="#ffffff")
        view_row.pack(fill="x", pady=(0, 6))
        tk.Label(view_row, text="Recorder view", bg="#ffffff", fg="#132640", font=("Segoe UI Semibold", 10)).pack(side="left")
        ttk.Radiobutton(
            view_row,
            text="Show SuperFlow Recorder",
            value="show",
            variable=self.recorder_view_var,
            command=self._on_view_changed,
        ).pack(side="left", padx=(12, 0))
        ttk.Radiobutton(
            view_row,
            text="Background only",
            value="hidden",
            variable=self.recorder_view_var,
            command=self._on_view_changed,
        ).pack(side="left", padx=10)

        ttk.Label(
            control_card,
            text="Hold Ctrl+Space and talk. Release to paste where your cursor is.",
            wraplength=580,
            style="Subtitle.TLabel",
        ).pack(fill="x", pady=(2, 10))

        action_row = tk.Frame(control_card, bg="#ffffff")
        action_row.pack(fill="x")
        tk.Button(
            action_row, text="Export Session PDF", command=self._export_session_pdf,
            bg="#ff6d37", fg="#ffffff", activebackground="#e85d28", activeforeground="#ffffff",
            relief="flat", borderwidth=0, font=("Segoe UI Semibold", 10), padx=14, pady=7, cursor="hand2",
        ).pack(side="left")
        tk.Button(
            action_row, text="Copy Last Transcript", command=self._copy_last_transcript,
            bg="#ffffff", fg="#132640", activebackground="#f5ede3", activeforeground="#132640",
            relief="flat", highlightthickness=1, highlightbackground="#c8bfb5",
            font=("Segoe UI Semibold", 10), padx=14, pady=7, cursor="hand2",
        ).pack(side="left", padx=8)
        tk.Button(
            action_row, text="Clear Session", command=self._clear_session,
            bg="#ffffff", fg="#c0392b", activebackground="#fef2f2", activeforeground="#c0392b",
            relief="flat", highlightthickness=1, highlightbackground="#c8bfb5",
            font=("Segoe UI Semibold", 10), padx=14, pady=7, cursor="hand2",
        ).pack(side="left")

        status_card = tk.Frame(
            outer,
            bg="#ffffff",
            highlightthickness=1,
            highlightbackground="#e0d5c8",
            padx=14,
            pady=14,
        )
        status_card.pack(fill="x", pady=(0, 10))
        tk.Label(
            status_card,
            textvariable=self.status_var,
            wraplength=640,
            bg="#ffffff",
            fg="#375273",
            font=("Segoe UI", 10),
            anchor="w",
            justify="left",
        ).pack(anchor="w")

        transcript_card = tk.Frame(
            outer,
            bg="#ffffff",
            highlightthickness=1,
            highlightbackground="#e0d5c8",
            padx=14,
            pady=14,
        )
        transcript_card.pack(fill="both", expand=True)
        tk.Label(
            transcript_card,
            text="Session Transcript",
            bg="#ffffff",
            fg="#132640",
            font=("Segoe UI Semibold", 11),
        ).pack(anchor="w", pady=(0, 8))

        transcript_inner = tk.Frame(transcript_card, bg="#ffffff")
        transcript_inner.pack(fill="both", expand=True)
        self.transcript_box = tk.Text(
            transcript_inner,
            height=14,
            wrap="word",
            state="disabled",
            bg="#fdf8f4",
            fg="#132640",
            font=("Segoe UI", 10),
            relief="flat",
            padx=10,
            pady=10,
        )
        self.transcript_box.tag_configure("time", foreground="#a07f5a", font=("Consolas", 9, "bold"))
        self.transcript_box.tag_configure("msg", foreground="#132640", font=("Segoe UI", 10))
        scroll = ttk.Scrollbar(transcript_inner, orient="vertical", command=self.transcript_box.yview)
        self.transcript_box.configure(yscrollcommand=scroll.set)
        self.transcript_box.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        footer = ttk.Frame(outer)
        footer.pack(fill="x", pady=(12, 0))

        ttk.Label(footer, text="made by Hanryck Brar").pack(side="left")
        github_link = ttk.Label(footer, text="GitHub", foreground="#0f5ea8", cursor="hand2")
        github_link.pack(side="left", padx=10)
        github_link.bind("<Button-1>", lambda _: webbrowser.open("https://github.com/Hanbrar/SuperflowAI"))
        twitter_link = ttk.Label(footer, text="Twitter", foreground="#0f5ea8", cursor="hand2")
        twitter_link.pack(side="left")
        twitter_link.bind("<Button-1>", lambda _: webbrowser.open("https://x.com/ItsHB17"))

    def _build_header(self, parent: ttk.Frame) -> None:
        canvas_h = 180
        header = tk.Canvas(parent, height=canvas_h, bd=0, highlightthickness=0)
        header.pack(fill="x")

        # Warm gradient: orange glow at top fading to cream
        for y in range(canvas_h):
            t = y / canvas_h
            glow = max(0.0, 0.30 * (1.0 - t * 1.7))
            r_v = min(255, int(247 + (255 - 247) * glow + (245 - 247) * t * 0.4))
            g_v = min(255, int(239 - int(39 * glow * 0.4) + (237 - 239) * t * 0.4))
            b_v = min(255, int(228 - int(228 * glow * 0.38) + (227 - 228) * t * 0.4))
            header.create_line(0, y, 4000, y, fill=f"#{r_v:02x}{g_v:02x}{b_v:02x}")

        logo_path = Path(__file__).resolve().parent.parent / "logo.png"
        if not logo_path.exists():
            return
        try:
            image = Image.open(logo_path).convert("RGBA")
            # Strip white/near-white background
            data = np.array(image, dtype=np.uint8)
            white = (data[:, :, 0] > 238) & (data[:, :, 1] > 238) & (data[:, :, 2] > 238) & (data[:, :, 3] > 10)
            data[white, 3] = 0
            image = Image.fromarray(data, "RGBA")
            bbox = image.getchannel("A").getbbox()
            if bbox:
                image = image.crop(bbox)
            image.thumbnail((340, 160), Image.Resampling.LANCZOS)
            self.logo_photo = ImageTk.PhotoImage(image)
            logo_id = header.create_image(400, canvas_h // 2, image=self.logo_photo, anchor="center")
            header.bind(
                "<Configure>",
                lambda e, lid=logo_id, ch=canvas_h: header.coords(lid, e.width // 2, ch // 2),
            )
        except Exception:
            pass

    def _refresh_microphones(self) -> None:
        self.microphones.clear()
        try:
            devices = sd.query_devices()
        except Exception as exc:
            self._set_status(f"Failed to read microphones: {exc}")
            return

        for index, device in enumerate(devices):
            if int(device.get("max_input_channels", 0)) > 0:
                self.microphones.append((f"[{index}] {device['name']}", index))

        if self.mic_combo is None:
            return
        self.mic_combo["values"] = [label for label, _ in self.microphones]
        if self.microphones:
            if self.mic_var.get() not in [label for label, _ in self.microphones]:
                self.mic_var.set(self.microphones[0][0])
            self._set_status("Microphone list updated. Ready.")
        else:
            self.mic_var.set("")
            self._set_status("No microphone found. Connect a mic and refresh.")

    def _selected_device_index(self) -> int | None:
        label = self.mic_var.get()
        for mic_label, mic_index in self.microphones:
            if mic_label == label:
                return mic_index
        return None

    def _register_hotkeys(self) -> None:
        try:
            self.hotkey_handles.append(keyboard.add_hotkey("ctrl+space", self._on_space_hotkey, suppress=False))
            self.hotkey_handles.append(keyboard.add_hotkey("esc", self._on_escape_hotkey, suppress=False))
            self.hook_handle = keyboard.hook(self._on_key_event)
        except Exception as exc:
            messagebox.showerror("Hotkey error", f"Failed to register hotkeys.\n\n{exc}")
            self._set_status("Hotkey registration failed. Try running the app as Administrator.")

    def _on_mode_changed(self) -> None:
        self.combo_is_pressed = False
        if self.mode_var.get() == "toggle":
            self._set_status("Toggle mode active. Press Ctrl+Space to start and press again to stop.")
        else:
            self._set_status("Control mode active. Hold Ctrl+Space, release to transcribe and paste.")

    def _on_view_changed(self) -> None:
        if self.recorder_view_var.get() == "show":
            self._set_status("Recorder popup enabled.")
            if self.is_recording and (self.recording_popup is None or not self.recording_popup.winfo_exists()):
                self._show_recording_popup()
        else:
            self._set_status("Background-only mode enabled. No popup while recording.")
            if self.recording_popup is not None and self.recording_popup.winfo_exists():
                self._hide_recording_popup()

    def _on_space_hotkey(self) -> None:
        # Control mode is handled by key-down/key-up events so release reliably stops.
        if self.mode_var.get() != "toggle":
            return
        if self.is_recording:
            self._stop_recording_and_transcribe()
        else:
            self._start_recording("space")

    def _on_escape_hotkey(self) -> None:
        if self.is_recording:
            self._stop_recording_and_transcribe()

    def _on_key_event(self, event: keyboard.KeyboardEvent) -> None:
        if self.mode_var.get() != "control":
            return
        if event.name not in {"ctrl", "left ctrl", "right ctrl", "space"}:
            return

        combo_down = keyboard.is_pressed("ctrl") and keyboard.is_pressed("space")
        if combo_down and not self.combo_is_pressed:
            self.combo_is_pressed = True
            self._start_recording("space")
        elif not combo_down and self.combo_is_pressed:
            self.combo_is_pressed = False
            self._stop_recording_and_transcribe()

    def _start_recording(self, source: str) -> None:
        with self.state_lock:
            if self.is_recording:
                return
            if self.is_transcribing:
                self._set_status("Still transcribing the previous capture.")
                return
            device_index = self._selected_device_index()
            if device_index is None:
                self._set_status("Select a microphone before dictating.")
                return
            self.audio_frames = []
            self.audio_level = 0.0
            try:
                device_info = sd.query_devices(device_index)
                device_rate = int(round(float(device_info.get("default_samplerate", SAMPLE_RATE))))
                self.current_sample_rate = device_rate if device_rate > 0 else SAMPLE_RATE
                self.stream = sd.InputStream(
                    samplerate=self.current_sample_rate,
                    channels=CHANNELS,
                    dtype="float32",
                    device=device_index,
                    callback=self._audio_callback,
                )
                self.stream.start()
            except Exception as exc:
                self.stream = None
                self._set_status(f"Failed to start recording: {exc}")
                return
            self.is_recording = True

        if self.recorder_view_var.get() == "show":
            self._show_recording_popup()

        if self.mode_var.get() == "toggle":
            self._set_status("Listening. Press Ctrl+Space again to stop.")
        else:
            self._set_status("Listening. Release Ctrl+Space to stop.")

    def _audio_callback(self, indata: np.ndarray[Any, Any], frames: int, time_info: Any, status: Any) -> None:
        del frames, time_info
        if status:
            return
        self.audio_frames.append(indata.copy())
        try:
            rms = float(np.sqrt(np.mean(np.square(indata))))
            self.audio_level = max(0.0, min(1.0, rms * 12.0))
        except Exception:
            pass

    def _stop_recording_and_transcribe(self) -> None:
        with self.state_lock:
            if not self.is_recording:
                return
            self.is_recording = False
            stream = self.stream
            self.stream = None

        self._hide_recording_popup()
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

        if not self.audio_frames:
            self._set_status("Nothing recorded. Try again.")
            return

        audio = np.concatenate(self.audio_frames, axis=0).flatten()
        self.audio_frames = []
        audio = self._resample_to_whisper_rate(audio, self.current_sample_rate)
        if audio.size < SAMPLE_RATE * 0.2:
            self._set_status("Recording too short. Hold hotkey a bit longer.")
            return

        self.is_transcribing = True
        self._set_status("Transcribing...")
        threading.Thread(target=self._transcribe_worker, args=(audio,), daemon=True).start()

    def _cancel_recording(self) -> None:
        with self.state_lock:
            if not self.is_recording:
                return
            self.is_recording = False
            stream = self.stream
            self.stream = None

        self._hide_recording_popup()
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        self.audio_frames = []
        self._set_status("Recording canceled.")

    def _transcribe_worker(self, audio: np.ndarray[Any, Any]) -> None:
        text = ""
        error = ""
        try:
            model = self._load_model()
            segments, _ = model.transcribe(
                audio,
                language="en",
                beam_size=TRANSCRIBE_BEAM_SIZE,
                vad_filter=TRANSCRIBE_VAD_FILTER,
                condition_on_previous_text=False,
                without_timestamps=True,
            )
            segments = list(segments)
            text = " ".join(segment.text.strip() for segment in segments).strip()
        except Exception as exc:
            error = str(exc)
        self.root.after(0, lambda: self._on_transcription_complete(text, error))

    def _preload_model(self) -> None:
        try:
            model = self._load_model()
            # Run a tiny warm-up inference to reduce first real transcription latency.
            warm_audio = np.zeros(SAMPLE_RATE // 6, dtype=np.float32)
            warm_segments, _ = model.transcribe(
                warm_audio,
                language="en",
                beam_size=TRANSCRIBE_BEAM_SIZE,
                vad_filter=False,
                condition_on_previous_text=False,
                without_timestamps=True,
            )
            list(warm_segments)
            self._set_status("Ready. Hold Ctrl+Space, then release to transcribe and paste at cursor.")
        except Exception:
            # Keep startup resilient even if model warm-up fails.
            pass

    def _resample_to_whisper_rate(self, audio: np.ndarray[Any, Any], source_rate: int) -> np.ndarray[Any, Any]:
        if source_rate == SAMPLE_RATE or audio.size == 0:
            return audio.astype(np.float32, copy=False)
        target_len = int(audio.size * SAMPLE_RATE / source_rate)
        if target_len <= 0:
            return np.array([], dtype=np.float32)
        old_axis = np.linspace(0.0, 1.0, num=audio.size, endpoint=False)
        new_axis = np.linspace(0.0, 1.0, num=target_len, endpoint=False)
        resampled = np.interp(new_axis, old_axis, audio)
        return resampled.astype(np.float32)

    def _load_model(self) -> WhisperModel:
        with self.model_lock:
            if self.model is not None:
                return self.model
            self._set_status("Loading low-latency speech model locally (free mode)...")
            # faster-whisper on Windows requires CUDA libs only for GPU.
            # Force CPU first so missing cublas/cudnn DLLs do not break startup.
            for compute_type in ("int8", "float32"):
                try:
                    self.model = WhisperModel(DEFAULT_MODEL, device="cpu", compute_type=compute_type)
                    return self.model
                except Exception:
                    continue
        raise RuntimeError("Could not initialize local speech model.")

    def _on_transcription_complete(self, text: str, error: str) -> None:
        self.is_transcribing = False
        timestamp = datetime.now().strftime("%H:%M:%S")
        if error:
            self._append_transcript_entry(timestamp, f"[ERROR] {error}")
            self._set_status(f"Transcription failed: {error}")
            return
        if not text:
            self._append_transcript_entry(timestamp, "[No speech detected]")
            self._set_status("No speech detected. Try speaking closer to the microphone.")
            return

        self.last_transcript = text
        self.session_entries.append({"timestamp": timestamp, "text": text})
        self.pdf_manager.update(self.session_entries)
        self._append_transcript_entry(timestamp, text)
        self._paste_text(text)
        self._set_status("Transcribed and pasted at cursor.")

    def _append_transcript_entry(self, timestamp: str, text: str) -> None:
        if self.transcript_box is None:
            return
        entry_tag = f"entry_{self._entry_count}"
        self._entry_count += 1
        self.transcript_box.configure(state="normal")
        self.transcript_box.insert("end", f"{timestamp}  ", "time")
        self.transcript_box.insert("end", f"{text}  ", ("msg", entry_tag))
        self.transcript_box.tag_bind(entry_tag, "<Button-1>", lambda _e, t=text: self._copy_entry_text(t))
        self.transcript_box.tag_bind(entry_tag, "<Enter>", lambda _e: self.transcript_box.configure(cursor="hand2"))
        self.transcript_box.tag_bind(entry_tag, "<Leave>", lambda _e: self.transcript_box.configure(cursor=""))
        copy_btn = tk.Button(
            self.transcript_box,
            text="⧉",
            fg="#c8a878",
            bg="#fdf8f4",
            activeforeground="#ff6d37",
            activebackground="#fdf8f4",
            relief="flat",
            borderwidth=0,
            padx=3,
            pady=0,
            font=("Segoe UI", 10),
            cursor="hand2",
            command=lambda t=text: self._copy_entry_text(t),
        )
        self.transcript_box.window_create("end", window=copy_btn)
        self.transcript_box.insert("end", "\n\n", "msg")
        self.transcript_box.see("end")
        self.transcript_box.configure(state="disabled")

    def _paste_text(self, text: str) -> None:
        previous: str | None
        try:
            previous = pyperclip.paste()
        except Exception:
            previous = None
        try:
            pyperclip.copy(text)
            time.sleep(0.05)
            keyboard.send("ctrl+v")
            time.sleep(0.05)
        except Exception as exc:
            self._set_status(f"Transcribed but auto-paste failed: {exc}")
        finally:
            if previous is not None:
                try:
                    pyperclip.copy(previous)
                except Exception:
                    pass

    def _copy_last_transcript(self) -> None:
        if not self.last_transcript:
            self._set_status("No transcript yet.")
            return
        try:
            pyperclip.copy(self.last_transcript)
            self._set_status("Last transcript copied.")
        except Exception as exc:
            self._set_status(f"Copy failed: {exc}")

    def _copy_entry_text(self, text: str) -> None:
        try:
            pyperclip.copy(text)
            self._set_status("Copied to clipboard.")
        except Exception as exc:
            self._set_status(f"Copy failed: {exc}")

    def _clear_session(self) -> None:
        self.session_entries.clear()
        self.last_transcript = ""
        if self.transcript_box is not None:
            self.transcript_box.configure(state="normal")
            self.transcript_box.delete("1.0", "end")
            self.transcript_box.configure(state="disabled")
        self.pdf_manager.update(self.session_entries)
        self._set_status("Session cleared.")

    def _export_session_pdf(self) -> bool:
        if not self.session_entries:
            messagebox.showinfo("No session data", "No transcript data to export yet.")
            return False
        target = filedialog.asksaveasfilename(
            title="Export Session PDF",
            defaultextension=".pdf",
            filetypes=[("PDF files", "*.pdf")],
            initialfile=f"superflow-session-{datetime.now().strftime('%Y%m%d-%H%M%S')}.pdf",
        )
        if not target:
            return False
        try:
            self.pdf_manager.export_to(Path(target))
        except Exception as exc:
            messagebox.showerror("Export failed", str(exc))
            self._set_status(f"Session export failed: {exc}")
            return False
        self._set_status(f"Session PDF exported: {target}")
        return True

    def _show_recording_popup(self) -> None:
        if self.recording_popup is not None and self.recording_popup.winfo_exists():
            return

        popup = tk.Toplevel(self.root)
        popup.title("Super Flow Recorder")
        popup.geometry("980x244")
        popup.minsize(980, 244)
        popup.configure(bg="#0a0a0a")
        popup.attributes("-topmost", True)
        popup.resizable(False, False)
        popup.protocol("WM_DELETE_WINDOW", self._cancel_recording)

        box = tk.Frame(popup, bg="#111111", highlightthickness=1, highlightbackground="#222222")
        box.pack(fill="both", expand=True, padx=14, pady=14)

        wave_wrap = tk.Frame(box, bg="#111111")
        wave_wrap.pack(fill="x", padx=26, pady=(16, 8))
        self.wave_canvas = tk.Canvas(wave_wrap, height=108, bg="#0d0d0d", highlightthickness=0)
        self.wave_canvas.pack(fill="x")

        # Subtle warm gradient background on wave canvas
        for y in range(108):
            center_dist = abs(y - 54) / 54
            warmth = max(0.0, 0.09 * (1.0 - center_dist))
            r_bg = min(255, int(13 + 50 * warmth))
            g_bg = min(255, int(13 + 15 * warmth))
            b_bg = 13
            self.wave_canvas.create_line(0, y, 4000, y, fill=f"#{r_bg:02x}{g_bg:02x}{b_bg:02x}")

        self.wave_rects.clear()
        bar_count = 86
        bar_w = 5
        gap = 5
        total = (bar_count * bar_w) + ((bar_count - 1) * gap)
        start_x = max(10, int((920 - total) / 2))
        for i in range(bar_count):
            x1 = start_x + (i * (bar_w + gap))
            x2 = x1 + bar_w
            center_dist = abs((bar_count / 2) - i) / (bar_count / 2)
            r = max(0, min(255, int(255 - center_dist * 55)))
            g = max(0, min(255, int(130 - center_dist * 65)))
            b = max(0, min(255, int(55 - center_dist * 30)))
            color = f"#{r:02x}{g:02x}{b:02x}"
            rect = self.wave_canvas.create_rectangle(x1, 54, x2, 54, fill=color, width=0)
            self.wave_rects.append(rect)

        divider = tk.Frame(box, bg="#1e1e1e", height=1)
        divider.pack(fill="x", padx=0, pady=(8, 0))

        footer = tk.Frame(box, bg="#0a0a0a")
        footer.pack(fill="x", padx=0, pady=(0, 0))

        left = tk.Frame(footer, bg="#0a0a0a")
        left.pack(side="left", padx=20, pady=14)
        dot = tk.Canvas(left, width=16, height=16, bg="#0a0a0a", highlightthickness=0)
        dot.pack(side="left", padx=(0, 10))
        dot.create_oval(3, 3, 13, 13, fill="#ff4b43", outline="")

        tk.Label(
            left,
            text="Recording",
            fg="#ffffff",
            bg="#0a0a0a",
            font=("Segoe UI Semibold", 15),
        ).pack(side="left")

        right = tk.Frame(footer, bg="#0a0a0a")
        right.pack(side="right", padx=20, pady=12)
        if self.mode_var.get() == "control":
            stop_hint = "Release"
        else:
            stop_hint = "Ctrl+Space"
        tk.Label(
            right,
            text="Stop",
            fg="#888888",
            bg="#0a0a0a",
            font=("Segoe UI", 16),
        ).pack(side="left", padx=(0, 10))
        tk.Button(
            right,
            text=stop_hint,
            command=self._stop_recording_and_transcribe,
            fg="#ffffff",
            bg="#ff6d37",
            activebackground="#e85d28",
            activeforeground="#ffffff",
            relief="flat",
            borderwidth=0,
            font=("Segoe UI Semibold", 18),
            padx=14,
            pady=4,
        ).pack(side="left")
        tk.Label(right, text="|", fg="#333333", bg="#0a0a0a", font=("Segoe UI", 18)).pack(side="left", padx=18)
        tk.Label(
            right,
            text="Exit",
            fg="#888888",
            bg="#0a0a0a",
            font=("Segoe UI", 16),
        ).pack(side="left", padx=(0, 10))
        tk.Button(
            right,
            text="Esc",
            command=self._stop_recording_and_transcribe,
            fg="#ffffff",
            bg="#333333",
            activebackground="#444444",
            activeforeground="#ffffff",
            relief="flat",
            borderwidth=0,
            font=("Segoe UI Semibold", 18),
            padx=14,
            pady=4,
        ).pack(side="left")

        popup.bind("<space>", lambda _e: self._stop_recording_and_transcribe())
        popup.bind("<Escape>", lambda _e: self._stop_recording_and_transcribe())
        self.wave_canvas.bind("<Button-1>", lambda _e: self._stop_recording_and_transcribe())

        self.recording_popup = popup
        self._tick_waveform()

    def _hide_recording_popup(self) -> None:
        if self.wave_timer is not None:
            self.root.after_cancel(self.wave_timer)
            self.wave_timer = None
        if self.recording_popup is not None and self.recording_popup.winfo_exists():
            self.recording_popup.destroy()
        self.recording_popup = None
        self.wave_canvas = None
        self.wave_rects = []

    def _tick_waveform(self) -> None:
        if self.recording_popup is None or not self.recording_popup.winfo_exists() or self.wave_canvas is None:
            return
        base = 7
        amp = max(5.0, min(44.0, self.audio_level * 54.0))
        count = len(self.wave_rects)
        for i, rect in enumerate(self.wave_rects):
            center_falloff = 1.0 - (abs((count / 2) - i) / (count / 2))
            wobble = math.sin((time.time() * 8.5) + (i * 0.34)) * 0.5 + 0.5
            jitter = random.uniform(-1.4, 1.4)
            h = base + (amp * center_falloff * wobble) + jitter
            x1, _y1, x2, _y2 = self.wave_canvas.coords(rect)
            mid = 54
            top = mid - h
            bot = mid + h
            self.wave_canvas.coords(rect, x1, top, x2, bot)
        self.wave_timer = self.root.after(45, self._tick_waveform)

    def _set_status(self, message: str) -> None:
        self.root.after(0, lambda: self.status_var.set(message))

    def _on_close(self) -> None:
        if self.session_entries:
            choice = messagebox.askyesnocancel(
                "Close Super Flow",
                "Session PDF is temporary and can be lost after closing.\n\nExport session PDF before exit?",
            )
            if choice is None:
                return
            if choice and not self._export_session_pdf():
                return

        self._cancel_recording()
        try:
            for handle in self.hotkey_handles:
                keyboard.remove_hotkey(handle)
            self.hotkey_handles.clear()
            if self.hook_handle is not None:
                keyboard.unhook(self.hook_handle)
        except Exception:
            pass

        self.pdf_manager.cleanup()
        self.root.destroy()


def main() -> None:
    enable_dpi_awareness()
    root = tk.Tk()
    SuperFlowApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
