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
DEFAULT_MODEL = "small.en"
SAMPLE_RATE = 16000
CHANNELS = 1
INITIAL_PROMPT = (
    "The user is dictating software code, professional emails, and social posts. "
    "Preserve punctuation, symbols, and capitalization accurately."
)

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
        self.root.geometry("640x800")
        self.root.minsize(600, 740)
        self.root.configure(bg="#f7f3ea")

        self.status_var = tk.StringVar(
            value="Ready. Hold Ctrl+Space, then release to transcribe and paste at cursor."
        )
        self.mode_var = tk.StringVar(value="control")
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

        self._build_ui()
        self._refresh_microphones()
        self._register_hotkeys()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background="#f7f3ea")
        style.configure("TLabel", background="#f7f3ea", foreground="#15233c", font=("Segoe UI", 10))
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 24), foreground="#0f274d")
        style.configure("Subtitle.TLabel", font=("Segoe UI", 11), foreground="#2e466b")
        style.configure("TButton", padding=8, font=("Segoe UI Semibold", 10))

        outer = ttk.Frame(self.root, padding=20)
        outer.pack(fill="both", expand=True)

        self._build_logo(outer)

        ttk.Label(outer, text="Super Flow", style="Title.TLabel", anchor="center").pack(pady=(10, 2))
        ttk.Label(
            outer,
            text="Free dictation for Windows. Built for coding, email, and posting.",
            style="Subtitle.TLabel",
            anchor="center",
        ).pack(pady=(0, 16))

        control_card = ttk.Frame(outer, padding=14)
        control_card.pack(fill="x", pady=(0, 10))

        mic_row = ttk.Frame(control_card)
        mic_row.pack(fill="x", pady=(0, 10))
        ttk.Label(mic_row, text="Microphone").pack(side="left")
        self.mic_combo = ttk.Combobox(mic_row, textvariable=self.mic_var, state="readonly", width=46)
        self.mic_combo.pack(side="left", padx=8, fill="x", expand=True)
        ttk.Button(mic_row, text="Refresh", command=self._refresh_microphones).pack(side="left")

        mode_row = ttk.Frame(control_card)
        mode_row.pack(fill="x", pady=(0, 6))
        ttk.Label(mode_row, text="Activation mode").pack(side="left")
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

        ttk.Label(
            control_card,
            text=(
                "Default hotkey: Ctrl+Space in Control mode. Hold to speak, release to paste. "
                "Ctrl+H still opens the popup flow."
            ),
            wraplength=580,
            style="Subtitle.TLabel",
        ).pack(fill="x", pady=(2, 10))

        action_row = ttk.Frame(control_card)
        action_row.pack(fill="x")
        ttk.Button(action_row, text="Export Session PDF", command=self._export_session_pdf).pack(side="left")
        ttk.Button(action_row, text="Copy Last Transcript", command=self._copy_last_transcript).pack(side="left", padx=8)
        ttk.Button(action_row, text="Clear Session", command=self._clear_session).pack(side="left")

        status_card = ttk.Frame(outer, padding=14)
        status_card.pack(fill="x", pady=(0, 10))
        ttk.Label(status_card, textvariable=self.status_var, wraplength=580).pack(anchor="w")

        transcript_card = ttk.Frame(outer, padding=14)
        transcript_card.pack(fill="both", expand=True)
        ttk.Label(transcript_card, text="Session Transcript (temporary)").pack(anchor="w", pady=(0, 8))

        self.transcript_box = tk.Text(
            transcript_card,
            height=14,
            wrap="word",
            state="disabled",
            bg="#fffdf8",
            fg="#1a2940",
            font=("Consolas", 10),
            relief="flat",
        )
        self.transcript_box.pack(fill="both", expand=True)

        footer = ttk.Frame(outer)
        footer.pack(fill="x", pady=(12, 0))

        ttk.Label(footer, text="made by Hanryck Brar").pack(side="left")
        github_link = ttk.Label(footer, text="GitHub", foreground="#0f5ea8", cursor="hand2")
        github_link.pack(side="left", padx=10)
        github_link.bind("<Button-1>", lambda _: webbrowser.open("https://github.com/Hanbrar/SuperflowAI"))
        twitter_link = ttk.Label(footer, text="Twitter", foreground="#0f5ea8", cursor="hand2")
        twitter_link.pack(side="left")
        twitter_link.bind("<Button-1>", lambda _: webbrowser.open("https://x.com/ItsHB17"))

    def _build_logo(self, parent: ttk.Frame) -> None:
        logo_path = Path(__file__).resolve().parent.parent / "logo.png"
        if not logo_path.exists():
            return
        try:
            image = Image.open(logo_path).convert("RGBA")
            alpha = image.getchannel("A")
            bbox = alpha.getbbox()
            if bbox is not None:
                image = image.crop(bbox)
            image.thumbnail((360, 170), Image.Resampling.LANCZOS)
            self.logo_photo = ImageTk.PhotoImage(image)
            holder = tk.Label(parent, image=self.logo_photo, bg="#f7f3ea", bd=0, highlightthickness=0)
            holder.pack()
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
            self.hotkey_handles.append(keyboard.add_hotkey("ctrl+h", self._on_h_hotkey, suppress=False))
            self.hotkey_handles.append(keyboard.add_hotkey("esc", self._on_escape_hotkey, suppress=False))
            self.hook_handle = keyboard.hook(self._on_key_event)
        except Exception as exc:
            messagebox.showerror("Hotkey error", f"Failed to register hotkeys.\n\n{exc}")
            self._set_status("Hotkey registration failed. Try running the app as Administrator.")

    def _on_mode_changed(self) -> None:
        if self.mode_var.get() == "toggle":
            self._set_status("Toggle mode active. Press Ctrl+Space or Ctrl+H to start and stop.")
        else:
            self._set_status("Control mode active. Hold Ctrl+Space, release to transcribe and paste.")

    def _on_space_hotkey(self) -> None:
        if self.mode_var.get() == "control":
            if not self.is_recording:
                self._start_recording("space")
            return
        if self.is_recording:
            self._stop_recording_and_transcribe()
        else:
            self._start_recording("space")

    def _on_h_hotkey(self) -> None:
        if self.is_recording:
            self._stop_recording_and_transcribe()
        else:
            self._start_recording("h")

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

        self._show_recording_popup()
        if source == "h":
            self._set_status("Recording from Ctrl+H popup. Press Space, Esc, or Exit to transcribe.")
        elif self.mode_var.get() == "toggle":
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
                beam_size=5,
                vad_filter=True,
                condition_on_previous_text=False,
                initial_prompt=INITIAL_PROMPT,
            )
            segments = list(segments)
            text = " ".join(segment.text.strip() for segment in segments).strip()
        except Exception as exc:
            error = str(exc)
        self.root.after(0, lambda: self._on_transcription_complete(text, error))

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
            self._set_status("Loading speech model locally on CPU (free mode). First run may take 1-2 minutes.")
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
        self.transcript_box.configure(state="normal")
        self.transcript_box.insert("end", f"[{timestamp}] {text}\n\n")
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
        popup.geometry("980x250")
        popup.minsize(980, 250)
        popup.configure(bg="#f5f5f5")
        popup.attributes("-topmost", True)
        popup.resizable(False, False)
        popup.protocol("WM_DELETE_WINDOW", self._cancel_recording)

        box = tk.Frame(popup, bg="#efefef", highlightthickness=1, highlightbackground="#d9d9d9")
        box.pack(fill="both", expand=True, padx=14, pady=14)

        self.wave_canvas = tk.Canvas(box, height=118, bg="#efefef", highlightthickness=0)
        self.wave_canvas.pack(fill="x", padx=36, pady=(20, 10))
        self.wave_rects.clear()
        bar_count = 80
        bar_w = 6
        gap = 5
        total = (bar_count * bar_w) + ((bar_count - 1) * gap)
        start_x = max(10, int((900 - total) / 2))
        for i in range(bar_count):
            x1 = start_x + (i * (bar_w + gap))
            x2 = x1 + bar_w
            center_dist = abs((bar_count / 2) - i) / (bar_count / 2)
            shade = int(65 + (center_dist * 120))
            color = f"#{shade:02x}{shade:02x}{shade:02x}"
            rect = self.wave_canvas.create_rectangle(x1, 68, x2, 68, fill=color, width=0)
            self.wave_rects.append(rect)

        divider = tk.Frame(box, bg="#e1e1e1", height=2)
        divider.pack(fill="x", padx=0, pady=(8, 0))

        footer = tk.Frame(box, bg="#efefef")
        footer.pack(fill="x", padx=24, pady=(12, 12))

        left = tk.Frame(footer, bg="#efefef")
        left.pack(side="left")
        dot = tk.Canvas(left, width=18, height=18, bg="#efefef", highlightthickness=0)
        dot.pack(side="left", padx=(0, 10))
        dot.create_oval(2, 2, 16, 16, fill="#ff2f2f", outline="")

        tk.Label(
            left,
            text="Recording",
            fg="#4a4a4a",
            bg="#efefef",
            font=("Segoe UI Semibold", 16),
        ).pack(side="left")

        right = tk.Frame(footer, bg="#efefef")
        right.pack(side="right")
        tk.Button(
            right,
            text="Exit",
            command=self._stop_recording_and_transcribe,
            fg="#666666",
            bg="#efefef",
            activebackground="#e6e6e6",
            activeforeground="#3d3d3d",
            relief="flat",
            borderwidth=0,
            font=("Segoe UI", 18),
            padx=0,
            pady=0,
        ).pack(side="left", padx=(0, 10))
        tk.Button(
            right,
            text="Space",
            command=self._stop_recording_and_transcribe,
            fg="#4f4f4f",
            bg="#d9d9d9",
            activebackground="#cdcdcd",
            activeforeground="#343434",
            relief="flat",
            borderwidth=0,
            font=("Segoe UI Semibold", 24),
            padx=16,
            pady=2,
        ).pack(side="left")
        tk.Label(right, text="|", fg="#c4c4c4", bg="#efefef", font=("Segoe UI", 20)).pack(side="left", padx=20)
        tk.Button(
            right,
            text="Exit",
            command=self._stop_recording_and_transcribe,
            fg="#666666",
            bg="#efefef",
            activebackground="#e6e6e6",
            activeforeground="#3d3d3d",
            relief="flat",
            borderwidth=0,
            font=("Segoe UI", 18),
            padx=0,
            pady=0,
        ).pack(side="left", padx=(0, 10))
        tk.Button(
            right,
            text="Esc",
            command=self._stop_recording_and_transcribe,
            fg="#4f4f4f",
            bg="#d9d9d9",
            activebackground="#cdcdcd",
            activeforeground="#343434",
            relief="flat",
            borderwidth=0,
            font=("Segoe UI Semibold", 20),
            padx=14,
            pady=2,
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
        base = 8
        amp = max(6.0, min(52.0, self.audio_level * 60.0))
        count = len(self.wave_rects)
        for i, rect in enumerate(self.wave_rects):
            center_falloff = 1.0 - (abs((count / 2) - i) / (count / 2))
            wobble = math.sin((time.time() * 8.5) + (i * 0.34)) * 0.5 + 0.5
            jitter = random.uniform(-1.4, 1.4)
            h = base + (amp * center_falloff * wobble) + jitter
            x1, _y1, x2, _y2 = self.wave_canvas.coords(rect)
            mid = 62
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
