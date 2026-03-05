from __future__ import annotations

import shutil
import tempfile
import textwrap
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any

import keyboard
import numpy as np
import pyperclip
import sounddevice as sd
import tkinter as tk
from faster_whisper import WhisperModel
from PIL import Image, ImageTk
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from tkinter import filedialog, messagebox, ttk

APP_TITLE = "Super Flow"
DEFAULT_MODEL = "medium.en"
SAMPLE_RATE = 16000
CHANNELS = 1
INITIAL_PROMPT = (
    "The user is dictating software code, professional emails, and social posts. "
    "Preserve punctuation, symbols, and capitalization accurately."
)


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
        page_width, page_height = letter
        margin = 40
        y = page_height - margin

        pdf.setTitle("Super Flow Session")
        pdf.setFont("Helvetica-Bold", 16)
        pdf.drawString(margin, y, "Super Flow Session Transcript")
        y -= 24

        pdf.setFont("Helvetica", 10)
        generated = f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        pdf.drawString(margin, y, generated)
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
            wrapped_lines = textwrap.wrap(text, width=95) or [""]
            for line in wrapped_lines:
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
        self.root.geometry("620x780")
        self.root.minsize(560, 700)
        self.root.configure(bg="#f6f2e9")

        self.status_var = tk.StringVar(value="Ready. Press Ctrl+Space to start dictation.")
        self.mode_var = tk.StringVar(value="toggle")
        self.mic_var = tk.StringVar(value="")

        self.model: WhisperModel | None = None
        self.model_lock = threading.Lock()
        self.state_lock = threading.Lock()

        self.is_recording = False
        self.is_transcribing = False
        self.combo_is_pressed = False
        self.last_transcript = ""
        self.audio_frames: list[np.ndarray[Any, Any]] = []
        self.stream: sd.InputStream | None = None
        self.hotkey_handle: int | None = None
        self.hook_handle: Any = None

        self.microphones: list[tuple[str, int]] = []
        self.session_entries: list[dict[str, str]] = []
        self.pdf_manager = SessionPDFManager()

        self.logo_photo: ImageTk.PhotoImage | None = None
        self.transcript_box: tk.Text | None = None
        self.mic_combo: ttk.Combobox | None = None

        self._build_ui()
        self._refresh_microphones()
        self._register_hotkeys()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background="#f6f2e9")
        style.configure("TLabel", background="#f6f2e9", foreground="#15233c", font=("Segoe UI", 10))
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 22), foreground="#0f274d")
        style.configure("Subtitle.TLabel", font=("Segoe UI", 11), foreground="#2e466b")
        style.configure("TButton", padding=7, font=("Segoe UI Semibold", 10))

        outer = ttk.Frame(self.root, padding=20)
        outer.pack(fill="both", expand=True)

        self._build_logo(outer)

        ttk.Label(
            outer,
            text="Super Flow",
            style="Title.TLabel",
            anchor="center",
        ).pack(pady=(8, 2))

        ttk.Label(
            outer,
            text="Free dictation for Windows. Built for coding, email, and posting.",
            style="Subtitle.TLabel",
            anchor="center",
        ).pack(pady=(0, 18))

        control_card = ttk.Frame(outer, padding=14)
        control_card.pack(fill="x", pady=(0, 10))

        mic_row = ttk.Frame(control_card)
        mic_row.pack(fill="x", pady=(0, 10))
        ttk.Label(mic_row, text="Microphone").pack(side="left")
        self.mic_combo = ttk.Combobox(mic_row, textvariable=self.mic_var, state="readonly", width=48)
        self.mic_combo.pack(side="left", padx=8, fill="x", expand=True)
        ttk.Button(mic_row, text="Refresh", command=self._refresh_microphones).pack(side="left")

        mode_row = ttk.Frame(control_card)
        mode_row.pack(fill="x", pady=(0, 6))
        ttk.Label(mode_row, text="Activation mode").pack(side="left")
        ttk.Radiobutton(
            mode_row,
            text="Toggle",
            value="toggle",
            variable=self.mode_var,
            command=self._on_mode_changed,
        ).pack(side="left", padx=(12, 0))
        ttk.Radiobutton(
            mode_row,
            text="Control (hold Ctrl+Space)",
            value="control",
            variable=self.mode_var,
            command=self._on_mode_changed,
        ).pack(side="left", padx=10)

        ttk.Label(
            control_card,
            text="Hotkey: Ctrl+Space to start. In toggle mode press again to stop. In control mode release to stop.",
            wraplength=560,
            style="Subtitle.TLabel",
        ).pack(fill="x", pady=(2, 10))

        action_row = ttk.Frame(control_card)
        action_row.pack(fill="x")
        ttk.Button(action_row, text="Export Session PDF", command=self._export_session_pdf).pack(side="left")
        ttk.Button(action_row, text="Copy Last Transcript", command=self._copy_last_transcript).pack(side="left", padx=8)
        ttk.Button(action_row, text="Clear Session", command=self._clear_session).pack(side="left")

        status_card = ttk.Frame(outer, padding=14)
        status_card.pack(fill="x", pady=(0, 10))
        ttk.Label(status_card, textvariable=self.status_var, wraplength=560).pack(anchor="w")

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
            image = Image.open(logo_path)
            image.thumbnail((180, 180))
            self.logo_photo = ImageTk.PhotoImage(image)
            ttk.Label(parent, image=self.logo_photo).pack()
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
                label = f"[{index}] {device['name']}"
                self.microphones.append((label, index))

        if self.mic_combo is None:
            return

        self.mic_combo["values"] = [label for label, _ in self.microphones]
        if self.microphones:
            current = self.mic_var.get()
            if current not in [label for label, _ in self.microphones]:
                self.mic_var.set(self.microphones[0][0])
            self._set_status("Microphone list updated. Ready to dictate.")
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
            self.hotkey_handle = keyboard.add_hotkey("ctrl+space", self._on_toggle_hotkey, suppress=False)
            self.hook_handle = keyboard.hook(self._on_key_event)
        except Exception as exc:
            messagebox.showerror("Hotkey error", f"Failed to register Ctrl+Space hotkey.\n\n{exc}")
            self._set_status("Hotkey registration failed. Run app with permissions and restart.")

    def _on_mode_changed(self) -> None:
        mode = self.mode_var.get()
        if mode == "toggle":
            self._set_status("Toggle mode active. Press Ctrl+Space to start, press again to stop.")
        else:
            self._set_status("Control mode active. Hold Ctrl+Space to record, release to transcribe.")

    def _on_toggle_hotkey(self) -> None:
        if self.mode_var.get() != "toggle":
            return
        if self.is_recording:
            self._stop_recording_and_transcribe()
        else:
            self._start_recording()

    def _on_key_event(self, event: keyboard.KeyboardEvent) -> None:
        if self.mode_var.get() != "control":
            return
        if event.name not in {"ctrl", "left ctrl", "right ctrl", "space"}:
            return

        combo_down = keyboard.is_pressed("ctrl") and keyboard.is_pressed("space")
        if combo_down and not self.combo_is_pressed:
            self.combo_is_pressed = True
            self._start_recording()
        elif not combo_down and self.combo_is_pressed:
            self.combo_is_pressed = False
            self._stop_recording_and_transcribe()

    def _start_recording(self) -> None:
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
            try:
                self.stream = sd.InputStream(
                    samplerate=SAMPLE_RATE,
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

        if self.mode_var.get() == "toggle":
            self._set_status("Listening... Press Ctrl+Space again to stop.")
        else:
            self._set_status("Listening... Release Ctrl+Space to stop.")

    def _audio_callback(self, indata: np.ndarray[Any, Any], frames: int, time_info: Any, status: Any) -> None:
        del frames, time_info
        if status:
            return
        self.audio_frames.append(indata.copy())

    def _stop_recording_and_transcribe(self) -> None:
        with self.state_lock:
            if not self.is_recording:
                return
            self.is_recording = False
            stream = self.stream
            self.stream = None

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

        if audio.size < SAMPLE_RATE * 0.2:
            self._set_status("Recording too short. Hold Ctrl+Space a bit longer.")
            return

        self.is_transcribing = True
        self._set_status("Transcribing...")
        threading.Thread(target=self._transcribe_worker, args=(audio,), daemon=True).start()

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
                initial_prompt=INITIAL_PROMPT,
            )
            text = " ".join(segment.text.strip() for segment in segments).strip()
        except Exception as exc:
            error = str(exc)

        self.root.after(0, lambda: self._on_transcription_complete(text, error))

    def _load_model(self) -> WhisperModel:
        with self.model_lock:
            if self.model is not None:
                return self.model

            self._set_status(
                "Loading speech model. First run can take a while because the free model is downloaded locally."
            )
            for compute_type in ("int8", "float16", "float32"):
                try:
                    self.model = WhisperModel(DEFAULT_MODEL, device="auto", compute_type=compute_type)
                    return self.model
                except Exception:
                    continue

        raise RuntimeError("Could not initialize local speech model.")

    def _on_transcription_complete(self, text: str, error: str) -> None:
        self.is_transcribing = False
        if error:
            self._set_status(f"Transcription failed: {error}")
            return

        if not text:
            self._set_status("No speech detected. Try speaking closer to the microphone.")
            return

        self.last_transcript = text
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.session_entries.append({"timestamp": timestamp, "text": text})
        self.pdf_manager.update(self.session_entries)
        self._append_transcript_entry(timestamp, text)
        self._paste_text(text)
        self._set_status("Transcribed and pasted at current cursor position.")

    def _append_transcript_entry(self, timestamp: str, text: str) -> None:
        if self.transcript_box is None:
            return
        self.transcript_box.configure(state="normal")
        self.transcript_box.insert("end", f"[{timestamp}] {text}\n\n")
        self.transcript_box.see("end")
        self.transcript_box.configure(state="disabled")

    def _paste_text(self, text: str) -> None:
        previous_clipboard: str | None = None
        try:
            previous_clipboard = pyperclip.paste()
        except Exception:
            previous_clipboard = None

        try:
            pyperclip.copy(text)
            time.sleep(0.05)
            keyboard.send("ctrl+v")
            time.sleep(0.05)
        except Exception as exc:
            self._set_status(f"Transcribed but auto-paste failed: {exc}")
            return
        finally:
            if previous_clipboard is not None:
                try:
                    pyperclip.copy(previous_clipboard)
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

        default_name = f"superflow-session-{datetime.now().strftime('%Y%m%d-%H%M%S')}.pdf"
        target = filedialog.asksaveasfilename(
            title="Export Session PDF",
            defaultextension=".pdf",
            filetypes=[("PDF files", "*.pdf")],
            initialfile=default_name,
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

        try:
            if self.hotkey_handle is not None:
                keyboard.remove_hotkey(self.hotkey_handle)
            if self.hook_handle is not None:
                keyboard.unhook(self.hook_handle)
        except Exception:
            pass

        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass

        self.pdf_manager.cleanup()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    SuperFlowApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

