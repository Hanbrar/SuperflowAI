from __future__ import annotations

import ctypes
import json
import logging
import math
import os
import random
import re
import shutil
import sys
import tempfile
import textwrap
import threading
import time
import urllib.error
import urllib.request
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
from tkinter import font as tkfont
from tkinter import filedialog, messagebox, ttk

APP_TITLE = "Super Flow"
APP_VERSION = "1.0.5"
DEFAULT_MODEL = "tiny.en"
SAMPLE_RATE = 16000
CHANNELS = 1
TRANSCRIBE_BEAM_SIZE = 1
TRANSCRIBE_VAD_FILTER = False
GITHUB_REPO = "Hanbrar/SuperflowAI"
UPDATE_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
UPDATE_DOWNLOAD_URL = "https://github.com/Hanbrar/SuperflowAI/releases/latest"

warnings.filterwarnings("ignore", message="`huggingface_hub` cache-system uses symlinks.*")
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
hf_logging.set_verbosity_error()


def resource_path(filename: str) -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / filename
    return Path(__file__).resolve().parent.parent / filename


def enable_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _version_key(version: str) -> tuple[int, ...]:
    clean = version.strip().lstrip("vV")
    parts = []
    for part in re.split(r"[.\-+_]", clean):
        match = re.match(r"(\d+)", part)
        if match is None:
            break
        parts.append(int(match.group(1)))
    return tuple(parts)


def _is_newer_version(candidate: str, current: str) -> bool:
    candidate_key = _version_key(candidate)
    current_key = _version_key(current)
    width = max(len(candidate_key), len(current_key))
    candidate_key += (0,) * (width - len(candidate_key))
    current_key += (0,) * (width - len(current_key))
    return candidate_key > current_key


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


class ModernButton(tk.Canvas):
    def __init__(
        self,
        parent: tk.Misc,
        text: str,
        command: Any,
        *,
        width: int = 170,
        height: int = 46,
        radius: int = 18,
        fill: str = "#132640",
        hover_fill: str = "#203756",
        active_fill: str = "#0d1f35",
        text_fill: str = "#ffffff",
        border_fill: str = "",
        font: tuple[str, int] = ("Segoe UI Semibold", 10),
    ) -> None:
        bg = parent.cget("bg") if hasattr(parent, "cget") else "#ffffff"
        super().__init__(
            parent,
            width=width,
            height=height,
            bg=bg,
            highlightthickness=0,
            bd=0,
            relief="flat",
            cursor="hand2",
            takefocus=1,
        )
        self.command = command
        self.text = text
        self.width = width
        self.height = height
        self.radius = radius
        self.fill = fill
        self.hover_fill = hover_fill
        self.active_fill = active_fill
        self.text_fill = text_fill
        self.border_fill = border_fill
        self.font = tkfont.Font(family=font[0], size=font[1], weight="bold")
        self._pressed = False

        self._draw(fill)

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<KeyPress-Return>", lambda _e: self._invoke())
        self.bind("<KeyPress-space>", lambda _e: self._invoke())

    def _rounded_points(self, x1: int, y1: int, x2: int, y2: int, radius: int) -> list[int]:
        return [
            x1 + radius, y1,
            x1 + radius, y1,
            x2 - radius, y1,
            x2 - radius, y1,
            x2, y1,
            x2, y1 + radius,
            x2, y1 + radius,
            x2, y2 - radius,
            x2, y2 - radius,
            x2, y2,
            x2 - radius, y2,
            x2 - radius, y2,
            x1 + radius, y2,
            x1 + radius, y2,
            x1, y2,
            x1, y2 - radius,
            x1, y2 - radius,
            x1, y1 + radius,
            x1, y1 + radius,
            x1, y1,
        ]

    def _draw(self, fill: str) -> None:
        self.delete("all")
        points = self._rounded_points(2, 2, self.width - 2, self.height - 2, self.radius)
        outline = self.border_fill if self.border_fill else fill
        self.create_polygon(points, smooth=True, splinesteps=24, fill=fill, outline=outline, width=1)
        self.create_text(
            self.width // 2,
            self.height // 2,
            text=self.text,
            fill=self.text_fill,
            font=self.font,
        )

    def _on_enter(self, _event: tk.Event) -> None:
        if not self._pressed:
            self._draw(self.hover_fill)

    def _on_leave(self, _event: tk.Event) -> None:
        self._pressed = False
        self._draw(self.fill)

    def _on_press(self, _event: tk.Event) -> None:
        self._pressed = True
        self._draw(self.active_fill)

    def _on_release(self, event: tk.Event) -> None:
        inside = 0 <= event.x <= self.width and 0 <= event.y <= self.height
        self._pressed = False
        self._draw(self.hover_fill if inside else self.fill)
        if inside:
            self._invoke()

    def _invoke(self) -> None:
        if callable(self.command):
            self.command()


class ChoiceChip(ModernButton):
    def __init__(
        self,
        parent: tk.Misc,
        text: str,
        variable: tk.StringVar,
        value: str,
        command: Any,
        *,
        width: int,
    ) -> None:
        self.variable = variable
        self.value = value
        self.change_command = command
        super().__init__(
            parent,
            text=text,
            command=self._choose,
            width=width,
            height=40,
            radius=14,
            fill="#f3ebe1",
            hover_fill="#ece1d4",
            active_fill="#e2d2c0",
            text_fill="#132640",
            border_fill="#dccfbe",
            font=("Segoe UI", 10),
        )
        self.variable.trace_add("write", self._sync_from_state)
        self._sync_from_state()

    def _choose(self) -> None:
        if self.variable.get() != self.value:
            self.variable.set(self.value)
        if callable(self.change_command):
            self.change_command()

    def _sync_from_state(self, *_args: Any) -> None:
        selected = self.variable.get() == self.value
        if selected:
            self.fill = "#132640"
            self.hover_fill = "#1d3553"
            self.active_fill = "#0d1f35"
            self.text_fill = "#ffffff"
            self.border_fill = "#132640"
        else:
            self.fill = "#f3ebe1"
            self.hover_fill = "#ece1d4"
            self.active_fill = "#e2d2c0"
            self.text_fill = "#132640"
            self.border_fill = "#dccfbe"
        self._draw(self.fill)


class SuperFlowApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("860x960")
        self.root.minsize(760, 860)
        self.root.configure(bg="#f5ede3")

        self.status_var = tk.StringVar(
            value="Ready. Hold Ctrl+Alt+Space, speak, release, and paste instantly."
        )
        self.mode_var = tk.StringVar(value="control")
        self.recorder_view_var = tk.StringVar(value="mini")
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
        self.app_icon_photo: ImageTk.PhotoImage | None = None
        self.transcript_box: tk.Text | None = None
        self.mic_combo: ttk.Combobox | None = None

        self.recording_popup: tk.Toplevel | None = None
        self.wave_canvas: tk.Canvas | None = None
        self.wave_rects: list[int] = []
        self.wave_timer: str | None = None
        self.audio_level = 0.0
        self.wave_midline = 54.0
        self.update_check_in_progress = False
        self.last_prompted_update_version: str | None = None
        self._entry_count: int = 0

        self._apply_window_icon(self.root)
        self._build_ui()
        self._refresh_microphones()
        self._register_hotkeys()
        threading.Thread(target=self._preload_model, daemon=True).start()
        self.root.after(1800, self._check_for_updates_silent)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _apply_window_icon(self, window: tk.Misc) -> None:
        icon_ico = resource_path("app_icon.ico")
        if icon_ico.exists():
            try:
                window.iconbitmap(str(icon_ico))
            except Exception:
                pass

        icon_png = resource_path("faviconupdated.png")
        fallback_png = resource_path("logo.png")
        icon_source = icon_png if icon_png.exists() else fallback_png
        if icon_source.exists():
            try:
                image = Image.open(icon_source).convert("RGBA")
                image.thumbnail((256, 256), Image.Resampling.LANCZOS)
                self.app_icon_photo = ImageTk.PhotoImage(image)
                window.iconphoto(True, self.app_icon_photo)
            except Exception:
                pass

    def _build_ui(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background="#f5ede3")
        style.configure("TLabel", background="#f5ede3", foreground="#132640", font=("Segoe UI", 10))
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 28), foreground="#132640")
        style.configure("Subtitle.TLabel", font=("Segoe UI", 11), foreground="#375273")
        style.configure(
            "SuperFlow.TCombobox",
            fieldbackground="#f7f0e7",
            background="#efe5da",
            foreground="#132640",
            bordercolor="#d7cbbd",
            arrowcolor="#132640",
            lightcolor="#d7cbbd",
            darkcolor="#d7cbbd",
            padding=6,
        )
        style.map(
            "SuperFlow.TCombobox",
            fieldbackground=[("readonly", "#f7f0e7"), ("focus", "#fff7ef")],
            background=[("readonly", "#efe5da")],
            foreground=[("readonly", "#132640")],
            bordercolor=[("focus", "#ffb08e"), ("readonly", "#d7cbbd")],
            arrowcolor=[("readonly", "#132640"), ("active", "#132640")],
        )

        self._build_header(self.root)

        outer = ttk.Frame(self.root, padding=20)
        outer.pack(fill="both", expand=True)

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
        self.mic_combo = ttk.Combobox(
            mic_row,
            textvariable=self.mic_var,
            state="readonly",
            width=52,
            style="SuperFlow.TCombobox",
        )
        self.mic_combo.pack(side="left", padx=8, fill="x", expand=True)
        ModernButton(
            mic_row,
            text="Refresh",
            command=self._refresh_microphones,
            width=108,
            height=42,
            radius=14,
            fill="#efe5da",
            hover_fill="#e5d7c8",
            active_fill="#dbcbb9",
            text_fill="#132640",
            border_fill="#dbcfc1",
        ).pack(side="left")

        mode_row = tk.Frame(control_card, bg="#ffffff")
        mode_row.pack(fill="x", pady=(0, 6))
        tk.Label(mode_row, text="Activation mode", bg="#ffffff", fg="#132640", font=("Segoe UI Semibold", 10)).pack(side="left")
        ChoiceChip(
            mode_row,
            text="Toggle",
            variable=self.mode_var,
            value="toggle",
            command=self._on_mode_changed,
            width=92,
        ).pack(side="left", padx=(12, 0))
        ChoiceChip(
            mode_row,
            text="Hold Ctrl+Alt+Space",
            variable=self.mode_var,
            value="control",
            command=self._on_mode_changed,
            width=226,
        ).pack(side="left", padx=8)

        view_row = tk.Frame(control_card, bg="#ffffff")
        view_row.pack(fill="x", pady=(0, 6))
        tk.Label(view_row, text="Recorder view", bg="#ffffff", fg="#132640", font=("Segoe UI Semibold", 10)).pack(side="left")
        ChoiceChip(
            view_row,
            text="Large",
            variable=self.recorder_view_var,
            value="show",
            command=self._on_view_changed,
            width=92,
        ).pack(side="left", padx=(12, 0))
        ChoiceChip(
            view_row,
            text="Minimized",
            variable=self.recorder_view_var,
            value="mini",
            command=self._on_view_changed,
            width=122,
        ).pack(side="left", padx=8)
        ChoiceChip(
            view_row,
            text="Background",
            variable=self.recorder_view_var,
            value="hidden",
            command=self._on_view_changed,
            width=118,
        ).pack(side="left", padx=8)

        tk.Label(
            control_card,
            text="Hold Ctrl+Alt+Space and talk. Release to paste where your cursor is.",
            wraplength=580,
            bg="#ffffff",
            fg="#375273",
            font=("Segoe UI", 11),
            anchor="w",
            justify="left",
        ).pack(fill="x", pady=(2, 10))

        action_row = tk.Frame(control_card, bg="#ffffff")
        action_row.pack(fill="x")
        ModernButton(
            action_row,
            text="Export Session PDF",
            command=self._export_session_pdf,
            width=188,
            height=48,
            fill="#ff6d37",
            hover_fill="#f46531",
            active_fill="#e85d28",
            text_fill="#ffffff",
            border_fill="#ff6d37",
        ).pack(side="left")
        ModernButton(
            action_row,
            text="Copy Last Transcript",
            command=self._copy_last_transcript,
            width=202,
            height=48,
            fill="#f4ece2",
            hover_fill="#eadfce",
            active_fill="#dfd0bc",
            text_fill="#132640",
            border_fill="#deceb9",
        ).pack(side="left", padx=10)
        ModernButton(
            action_row,
            text="Clear Session",
            command=self._clear_session,
            width=150,
            height=48,
            fill="#fff3ee",
            hover_fill="#fde6dc",
            active_fill="#f8d7cc",
            text_fill="#c0392b",
            border_fill="#f1c5b8",
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
        update_link = ttk.Label(footer, text="Check for Updates", foreground="#0f5ea8", cursor="hand2")
        update_link.pack(side="right")
        update_link.bind("<Button-1>", lambda _: self._check_for_updates(user_initiated=True))
        ttk.Label(footer, text=f"v{APP_VERSION}", foreground="#6a7f9b").pack(side="right", padx=(0, 10))

    def _build_header(self, parent: tk.Misc) -> None:
        canvas_h = 230
        header = tk.Canvas(parent, height=canvas_h, bd=0, highlightthickness=0, bg="#f5ede3")
        header.pack(fill="x")

        # Warm gradient spanning full window width — fades to exactly #f5ede3 at bottom
        def _draw_gradient(w: int = 2000) -> None:
            header.delete("gradient")
            for y in range(canvas_h):
                t = (y / (canvas_h - 1)) ** 0.55
                r_v = int(round(255 + (245 - 255) * t))
                g_v = int(round(218 + (237 - 218) * t))
                b_v = int(round(188 + (227 - 188) * t))
                header.create_line(0, y, w, y, fill=f"#{r_v:02x}{g_v:02x}{b_v:02x}", tags="gradient")

        _draw_gradient()

        subtitle_id = header.create_text(
            0, 188,
            text="Simple voice dictation for free.",
            fill="#375273",
            font=("Segoe UI", 11),
            anchor="n",
        )

        logo_path = resource_path("logo.png")
        if not logo_path.exists():
            header.coords(subtitle_id, 400, 188)
            header.bind("<Configure>", lambda e, sid=subtitle_id: (
                _draw_gradient(e.width),
                header.coords(sid, e.width // 2, 188),
                header.tag_raise(sid),
            ))
            return
        try:
            image = Image.open(logo_path).convert("RGBA")
            data = np.array(image, dtype=np.uint8)
            white = (data[:, :, 0] > 238) & (data[:, :, 1] > 238) & (data[:, :, 2] > 238) & (data[:, :, 3] > 10)
            data[white, 3] = 0
            image = Image.fromarray(data, "RGBA")
            bbox = image.getchannel("A").getbbox()
            if bbox:
                image = image.crop(bbox)
            image.thumbnail((600, 170), Image.Resampling.LANCZOS)
            self.logo_photo = ImageTk.PhotoImage(image)
            logo_id = header.create_image(400, 84, image=self.logo_photo, anchor="center")
            header.bind("<Configure>", lambda e, lid=logo_id, sid=subtitle_id: (
                _draw_gradient(e.width),
                header.coords(lid, e.width // 2, 84),
                header.coords(sid, e.width // 2, 188),
                header.tag_raise(lid),
                header.tag_raise(sid),
            ))
        except Exception:
            pass

    def _get_cursor_monitor(self) -> dict:
        """Return the rect of whichever monitor the cursor is currently on."""
        try:
            class POINT(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
            class RECT(ctypes.Structure):
                _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                             ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

            user32 = ctypes.windll.user32

            # GetCursorPos and EnumDisplayMonitors share the same virtual-desktop
            # coordinate space, so they always agree across monitors.
            pt = POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            cx, cy = pt.x, pt.y

            monitors: list[dict] = []
            MonitorEnumProc = ctypes.WINFUNCTYPE(
                ctypes.c_bool, ctypes.c_ulong, ctypes.c_ulong,
                ctypes.POINTER(RECT), ctypes.c_long,
            )

            def _cb(hmon: int, hdc: int, lprect: "ctypes.POINTER[RECT]", lparam: int) -> bool:
                r = lprect.contents
                monitors.append({"left": r.left, "top": r.top,
                                  "right": r.right, "bottom": r.bottom})
                return True

            user32.EnumDisplayMonitors(None, None, MonitorEnumProc(_cb), 0)

            for m in monitors:
                if m["left"] <= cx < m["right"] and m["top"] <= cy < m["bottom"]:
                    return m
            if monitors:
                return monitors[0]
        except Exception:
            pass
        return {"left": 0, "top": 0,
                "right": self.root.winfo_screenwidth(),
                "bottom": self.root.winfo_screenheight()}

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
            self.hotkey_handles.append(keyboard.add_hotkey("ctrl+alt+space", self._on_space_hotkey, suppress=False))
            self.hotkey_handles.append(keyboard.add_hotkey("esc", self._on_escape_hotkey, suppress=False))
            self.hook_handle = keyboard.hook(self._on_key_event)
        except Exception as exc:
            messagebox.showerror("Hotkey error", f"Failed to register hotkeys.\n\n{exc}")
            self._set_status("Hotkey registration failed. Try running the app as Administrator.")

    def _on_mode_changed(self) -> None:
        self.combo_is_pressed = False
        if self.mode_var.get() == "toggle":
            self._set_status("Toggle mode active. Press Ctrl+Alt+Space to start and press again to stop.")
        else:
            self._set_status("Control mode active. Hold Ctrl+Alt+Space, release to transcribe and paste.")

    def _on_view_changed(self) -> None:
        view = self.recorder_view_var.get()
        if view == "show":
            self._set_status("Recorder popup enabled.")
        elif view == "mini":
            self._set_status("Minimized recorder enabled.")
        else:
            self._set_status("Background-only mode enabled. No popup while recording.")

        if not self.is_recording:
            return

        self._hide_recording_popup()
        if view in {"show", "mini"}:
            self._show_recording_popup()

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
        if event.name not in {"ctrl", "left ctrl", "right ctrl", "alt", "left alt", "right alt", "space"}:
            return

        combo_down = keyboard.is_pressed("ctrl") and keyboard.is_pressed("alt") and keyboard.is_pressed("space")
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

        if self.recorder_view_var.get() in {"show", "mini"}:
            self.root.after(0, self._show_recording_popup)

        if self.mode_var.get() == "toggle":
            self._set_status("Listening. Press Ctrl+Alt+Space again to stop.")
        else:
            self._set_status("Listening. Release Ctrl+Alt+Space to stop.")

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

        self.root.after(0, self._hide_recording_popup)
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

        self.root.after(0, self._hide_recording_popup)
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
                initial_prompt=(
                    "GitHub, Vercel, SuperFlow, PyInstaller, Whisper, API, "
                    "VS Code, ChatGPT, Claude, OpenAI, Anthropic, "
                    "Python, JavaScript, TypeScript, React, Next.js, "
                    "vibe coding, pull request, commit, deploy, repo, "
                    "Ctrl, Alt, Space, hotkey, transcribe, dictation."
                ),
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
            self._set_status("Ready. Hold Ctrl+Alt+Space, then release to transcribe and paste at cursor.")
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

        if len(text.split()) == 1:
            text = text.rstrip(".,!?;:").lower()

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
        copy_btn = ModernButton(
            self.transcript_box,
            text="Copy",
            command=lambda t=text: self._copy_entry_text(t),
            width=56,
            height=24,
            radius=10,
            fill="#f1e4d2",
            hover_fill="#ead8c0",
            active_fill="#deccb3",
            text_fill="#8b6339",
            border_fill="#e2d0b9",
            font=("Segoe UI", 8),
        )
        self.transcript_box.window_create("end", window=copy_btn)
        self.transcript_box.insert("end", "\n\n", "msg")
        self.transcript_box.see("end")
        self.transcript_box.configure(state="disabled")

    def _paste_text(self, text: str) -> None:
        try:
            pyperclip.copy(text)
            time.sleep(0.08)
            keyboard.send("ctrl+v")
        except Exception as exc:
            self._set_status(f"Transcribed but auto-paste failed: {exc}")

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

        if self.recorder_view_var.get() == "mini":
            self._show_mini_recording_popup()
            return

        self._show_large_recording_popup()

    def _show_large_recording_popup(self) -> None:
        self.wave_midline = 40.0

        popup = tk.Toplevel(self.root)
        popup.title("Super Flow Recorder")
        self._apply_window_icon(popup)
        mon = self._get_cursor_monitor()
        px = mon["left"] + max(0, (mon["right"] - mon["left"] - 720) // 2)
        py = mon["top"] + 40
        popup.geometry(f"720x188+{px}+{py}")
        popup.minsize(720, 188)
        popup.configure(bg="#f5ede3")
        popup.attributes("-topmost", True)
        popup.resizable(False, False)
        popup.protocol("WM_DELETE_WINDOW", self._cancel_recording)

        box = tk.Frame(popup, bg="#f5ede3", highlightthickness=1, highlightbackground="#e0d5c8")
        box.pack(fill="both", expand=True, padx=14, pady=14)

        wave_wrap = tk.Frame(box, bg="#f5ede3")
        wave_wrap.pack(fill="x", padx=26, pady=(16, 8))
        self.wave_canvas = tk.Canvas(wave_wrap, height=80, bg="#f7efe4", highlightthickness=0)
        self.wave_canvas.pack(fill="x")

        self.wave_rects.clear()
        bar_count = 60
        bar_w = 5
        gap = 4
        total = (bar_count * bar_w) + ((bar_count - 1) * gap)
        start_x = max(10, int((660 - total) / 2))
        for i in range(bar_count):
            x1 = start_x + (i * (bar_w + gap))
            x2 = x1 + bar_w
            rect = self.wave_canvas.create_rectangle(x1, 40, x2, 40, fill="#132640", width=0)
            self.wave_rects.append(rect)

        divider = tk.Frame(box, bg="#e0d5c8", height=1)
        divider.pack(fill="x", padx=0, pady=(8, 0))

        footer = tk.Frame(box, bg="#f5ede3")
        footer.pack(fill="x", padx=0, pady=(0, 0))

        left = tk.Frame(footer, bg="#f5ede3")
        left.pack(side="left", padx=16, pady=10)
        dot = tk.Canvas(left, width=13, height=13, bg="#f5ede3", highlightthickness=0)
        dot.pack(side="left", padx=(0, 8))
        dot.create_oval(2, 2, 11, 11, fill="#ff4b43", outline="")

        tk.Label(
            left,
            text="Recording",
            fg="#132640",
            bg="#f5ede3",
            font=("Segoe UI Semibold", 12),
        ).pack(side="left")

        right = tk.Frame(footer, bg="#f5ede3")
        right.pack(side="right", padx=16, pady=8)
        if self.mode_var.get() == "control":
            stop_hint = "Release"
        else:
            stop_hint = "Ctrl+Alt+Space"
        tk.Label(
            right,
            text="Stop",
            fg="#6a7f9b",
            bg="#f5ede3",
            font=("Segoe UI", 12),
        ).pack(side="left", padx=(0, 8))
        ModernButton(
            right,
            text=stop_hint,
            command=self._stop_recording_and_transcribe,
            width=120 if self.mode_var.get() == "toggle" else 108,
            height=34,
            radius=11,
            fill="#ff6d37",
            hover_fill="#f46531",
            active_fill="#e85d28",
            text_fill="#ffffff",
            border_fill="#ff6d37",
            font=("Segoe UI Semibold", 11),
        ).pack(side="left")
        tk.Label(right, text="|", fg="#c8bfb5", bg="#f5ede3", font=("Segoe UI", 14)).pack(side="left", padx=12)
        tk.Label(
            right,
            text="Exit",
            fg="#6a7f9b",
            bg="#f5ede3",
            font=("Segoe UI", 12),
        ).pack(side="left", padx=(0, 8))
        ModernButton(
            right,
            text="Esc",
            command=self._stop_recording_and_transcribe,
            width=70,
            height=34,
            radius=11,
            fill="#e7dbcf",
            hover_fill="#dccdbd",
            active_fill="#cfbea9",
            text_fill="#132640",
            border_fill="#d8c8b4",
            font=("Segoe UI Semibold", 11),
        ).pack(side="left")

        popup.bind("<space>", lambda _e: self._stop_recording_and_transcribe())
        popup.bind("<Escape>", lambda _e: self._stop_recording_and_transcribe())
        self.wave_canvas.bind("<Button-1>", lambda _e: self._stop_recording_and_transcribe())

        self.recording_popup = popup
        self._tick_waveform()

    def _show_mini_recording_popup(self) -> None:
        self.wave_midline = 26.0

        popup = tk.Toplevel(self.root)
        popup.title("Super Flow Recorder")
        self._apply_window_icon(popup)
        popup.configure(bg="#f5ede3")
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        popup.resizable(False, False)
        popup.protocol("WM_DELETE_WINDOW", self._cancel_recording)

        width = 430
        height = 88
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = max(12, (sw - width) // 2)
        y = sh - height - 60
        popup.geometry(f"{width}x{height}+{x}+{y}")

        box = tk.Frame(
            popup,
            bg="#fcf7f1",
            highlightthickness=1,
            highlightbackground="#d9cab7",
            padx=14,
            pady=10,
        )
        box.pack(fill="both", expand=True)

        top_row = tk.Frame(box, bg="#fcf7f1")
        top_row.pack(fill="x")

        dot = tk.Canvas(top_row, width=12, height=12, bg="#fcf7f1", highlightthickness=0)
        dot.pack(side="left", padx=(0, 8))
        dot.create_oval(2, 2, 10, 10, fill="#ff4b43", outline="")

        tk.Label(
            top_row,
            text="Recording",
            fg="#132640",
            bg="#fcf7f1",
            font=("Segoe UI Semibold", 10),
        ).pack(side="left")

        tk.Label(
            top_row,
            text="Esc to stop",
            fg="#6a7f9b",
            bg="#fcf7f1",
            font=("Segoe UI", 9),
        ).pack(side="right")

        self.wave_canvas = tk.Canvas(box, height=52, bg="#fcf7f1", highlightthickness=0)
        self.wave_canvas.pack(fill="x", pady=(8, 0))

        self.wave_rects.clear()
        bar_count = 34
        bar_w = 6
        gap = 5
        total = (bar_count * bar_w) + ((bar_count - 1) * gap)
        start_x = max(6, int((width - 30 - total) / 2))
        for i in range(bar_count):
            x1 = start_x + (i * (bar_w + gap))
            x2 = x1 + bar_w
            rect = self.wave_canvas.create_rectangle(x1, self.wave_midline, x2, self.wave_midline, fill="#132640", width=0)
            self.wave_rects.append(rect)

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
        try:
            canvas_h = int(float(self.wave_canvas.cget("height")))
        except Exception:
            return
        mid = self.wave_midline
        is_mini = mid <= 26.0
        base = max(3.0 if is_mini else 4.0, canvas_h * (0.1 if is_mini else 0.12))
        max_amp = max(12.0 if is_mini else 10.0, (canvas_h / 2) - (4.0 if is_mini else 6.0))
        reactivity = 2.45 if is_mini else 1.45
        amp = max(base, min(max_amp, self.audio_level * (max_amp * reactivity)))
        count = len(self.wave_rects)
        for i, rect in enumerate(self.wave_rects):
            center_falloff = 1.0 - (abs((count / 2) - i) / (count / 2))
            wobble = math.sin((time.time() * 8.5) + (i * 0.34)) * 0.5 + 0.5
            jitter = random.uniform(-2.2, 2.2) if is_mini else random.uniform(-1.4, 1.4)
            h = base + (amp * center_falloff * wobble) + jitter
            x1, _y1, x2, _y2 = self.wave_canvas.coords(rect)
            top = mid - h
            bot = mid + h
            self.wave_canvas.coords(rect, x1, top, x2, bot)
        self.wave_timer = self.root.after(45, self._tick_waveform)

    def _check_for_updates_silent(self) -> None:
        self._check_for_updates(user_initiated=False)

    def _check_for_updates(self, user_initiated: bool) -> None:
        if self.update_check_in_progress:
            if user_initiated:
                self._set_status("Already checking for updates.")
            return
        self.update_check_in_progress = True
        if user_initiated:
            self._set_status("Checking for updates...")
        threading.Thread(target=self._update_check_worker, args=(user_initiated,), daemon=True).start()

    def _update_check_worker(self, user_initiated: bool) -> None:
        release_version = ""
        release_name = ""
        error_message = ""
        try:
            request = urllib.request.Request(
                UPDATE_API_URL,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": f"SuperFlow/{APP_VERSION}",
                },
            )
            with urllib.request.urlopen(request, timeout=8) as response:
                payload = json.loads(response.read().decode("utf-8"))
            release_name = str(payload.get("name") or "").strip()
            release_version = str(payload.get("tag_name") or "").strip()
            if not release_version:
                error_message = "GitHub did not return a release tag."
        except urllib.error.HTTPError as exc:
            error_message = f"GitHub returned HTTP {exc.code}."
        except urllib.error.URLError:
            error_message = "Could not reach GitHub."
        except Exception as exc:
            error_message = str(exc) or "Unknown update check error."

        self.root.after(
            0,
            lambda: self._finish_update_check(
                release_version=release_version,
                release_name=release_name,
                error_message=error_message,
                user_initiated=user_initiated,
            ),
        )

    def _finish_update_check(
        self,
        *,
        release_version: str,
        release_name: str,
        error_message: str,
        user_initiated: bool,
    ) -> None:
        self.update_check_in_progress = False

        if error_message:
            if user_initiated:
                messagebox.showerror("Update check failed", error_message)
                self._set_status(f"Update check failed: {error_message}")
            return

        latest_version = release_version.lstrip("vV")
        if latest_version and _is_newer_version(latest_version, APP_VERSION):
            self._set_status(f"Update available: v{latest_version}.")
            if not user_initiated and self.last_prompted_update_version == latest_version:
                return
            self.last_prompted_update_version = latest_version
            release_label = release_name or f"v{latest_version}"
            choice = messagebox.askyesno(
                "Update Available",
                (
                    f"{APP_TITLE} {release_label} is available.\n"
                    f"You are currently on v{APP_VERSION}.\n\n"
                    "Open the download page now?"
                ),
            )
            if choice:
                webbrowser.open(UPDATE_DOWNLOAD_URL)
                self._set_status(f"Opened download page for v{latest_version}.")
            return

        if user_initiated:
            messagebox.showinfo(
                "Up To Date",
                f"You are on the latest version of {APP_TITLE} (v{APP_VERSION}).",
            )
            self._set_status(f"{APP_TITLE} is up to date.")

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
