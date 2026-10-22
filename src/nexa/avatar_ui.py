
"""NEXA Phase 6 — Speech-synchronized avatar image switching.

Run from the AI Model project root:
PYTHONPATH=src python src/nexa/avatar_ui.py
"""

from __future__ import annotations

import math
import random
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from datetime import datetime

try:
    from nexa.voice_bridge import VoiceBridge
except ImportError:
    VoiceBridge = None

try:
    from PIL import Image, ImageTk, ImageOps, ImageEnhance
except ImportError:
    Image = ImageTk = ImageOps = ImageEnhance = None

try:
    from nexa.main import ask_nexa
except ImportError:
    ask_nexa = None

from nexa.conversation import get_history, add_exchange


# Keep the existing NEXA voice unchanged.
VOICE = "Samantha"
VOICE_RATE = "175"

BG = "#060a16"
PANEL = "#0d1730"
PANEL2 = "#142443"
CYAN = "#55e5ff"
PURPLE = "#b19aff"
TEXT = "#ffffff"
MUTED = "#c0cbe4"
BUTTON = "#20385f"
BUTTON_HOVER = "#2d4e80"
BORDER = "#35558a"


class NexaAvatarApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("NEXA — Your AI Companion")
        self.root.geometry("1280x800")
        self.root.minsize(900, 600)
        self.root.configure(bg=BG)

        self.fullscreen = False
        self.state = "Ready"
        self.subtitle_text = "NEXA subtitles will appear here."
        self.mood = "Normal"
        self.wave_phase = 0.0

        self.avatar_original = None
        self.avatar_speaking = None
        self.avatar_mouth_poses = {}
        self.avatar_photo = None
        self.speaking_pose_toggle = False
        self.avatar_animation_job = None
        self.animation_phase = 0.0
        self.animation_mix = 0.0

        self.speech_process = None
        self.request_in_progress = False
        self.speech_generation = 0
        self.voice_bridge = None
        self.voice_enabled = False
        self._closing = False

        self._build_ui()
        self.root.bind("<Configure>", self._responsive_layout, add="+")
        self._load_avatar_images()
        self._animate_wave()
        self._clock()
        self._animate_idle_hud()

        self.root.bind("<Escape>", lambda _e: self._set_fullscreen(False))
        self.root.bind("<F11>", lambda _e: self._set_fullscreen(not self.fullscreen))
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def _make_button(self, parent, text, command, *,
                     bg=None, fg=None, active_fg=None,
                     anchor="center", padx=12, pady=9,
                     font=("Helvetica Neue", 11), **kwargs):
        """Create a consistently styled NEXA button on macOS."""
        base = bg or BUTTON
        label = tk.Label(
            parent,
            text=text,
            bg=base,
            fg=fg or TEXT,
            activebackground=BUTTON_HOVER,
            font=font,
            anchor=anchor,
            padx=padx,
            pady=pady,
            cursor="hand2",
            relief="flat",
            bd=0,
        )
        label.bind("<Button-1>", lambda _event: command())
        label.bind("<Enter>", lambda _event: label.configure(
            bg=BUTTON_HOVER, fg=active_fg or CYAN
        ))
        label.bind("<Leave>", lambda _event: label.configure(
            bg=base, fg=fg or TEXT
        ))
        return label

    # --------------------------------------------------
    # UI
    # --------------------------------------------------

    def _build_ui(self):
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=24, pady=(16, 8))

        tk.Label(
            header, text="N E X A",
            font=("Helvetica Neue", 25, "bold"),
            fg=CYAN, bg=BG
        ).pack(side="left")

        tk.Label(
            header, text="  YOUR PERSONAL AI COMPANION",
            font=("Helvetica Neue", 10),
            fg=MUTED, bg=BG
        ).pack(side="left", pady=(10, 0))

        self.clock_label = tk.Label(
            header, text="",
            font=("Helvetica Neue", 11),
            fg=TEXT, bg=BG
        )
        self.clock_label.pack(side="right", padx=(10, 0))

        self._make_button(
            header, "⛶  Full screen",
            lambda: self._set_fullscreen(not self.fullscreen),
            bg=PANEL2, fg=TEXT, padx=12, pady=7
        ).pack(side="right")

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=14, pady=6)
        # Phase 8.1.5: responsive three-column layout.
        # Keep the avatar as the primary area while allowing side panels
        # to adapt as the window grows.
        body.columnconfigure(0, weight=2, minsize=145)
        body.columnconfigure(1, weight=7, minsize=330)
        body.columnconfigure(2, weight=3, minsize=190)
        body.rowconfigure(0, weight=1)


        # Left navigation
        left = tk.Frame(
            body, bg=PANEL,
            highlightbackground=BORDER,
            highlightthickness=1
        )
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))

        tk.Label(
            left, text="WORKSPACE", bg=PANEL, fg=MUTED,
            font=("Helvetica Neue", 9, "bold")
        ).pack(anchor="w", padx=16, pady=(20, 12))

        for name, symbol in [
            ("Home", "⌂"), ("Chat", "▤"), ("Voice", "♫"),
            ("Music", "♪"), ("YouTube", "▶"),
            ("Mac Control", "▣"), ("Settings", "⚙")
        ]:
            active = name == "Home"
            self._make_button(
                left, f"  {symbol}    {name}",
                lambda n=name: self._nav_action(n),
                bg=BUTTON if active else PANEL2,
                fg=CYAN if active else TEXT,
                anchor="w", padx=12, pady=12,
                font=("Helvetica Neue", 11)
            ).pack(fill="x", padx=9, pady=3)

        tk.Frame(left, bg=PANEL).pack(fill="both", expand=True)

        tk.Label(
            left, text="LOCAL-FIRST DESIGN", bg=PANEL, fg=PURPLE,
            font=("Helvetica Neue", 9, "bold")
        ).pack(anchor="w", padx=16, pady=(4, 3))

        tk.Label(
            left, text="Ollama • macOS • Private",
            bg=PANEL, fg=MUTED,
            font=("Helvetica Neue", 9)
        ).pack(anchor="w", padx=16, pady=(0, 18))

        # Center avatar
        center = tk.Frame(
            body, bg="#090f20",
            highlightbackground=BORDER,
            highlightthickness=1
        )
        center.grid(row=0, column=1, sticky="nsew")
        center.rowconfigure(0, weight=1)
        center.columnconfigure(0, weight=1)

        self.avatar_canvas = tk.Canvas(
            center, bg="#090f20", highlightthickness=0
        )
        self.avatar_canvas.grid(row=0, column=0, sticky="nsew")
        self.avatar_canvas.bind(
            "<Configure>", lambda _e: self._draw_avatar_scene()
        )
        self.avatar_canvas.bind(
            "<Double-Button-1>",
            lambda _e: self._set_fullscreen(not self.fullscreen)
        )

        # Persistent subtitles: status updates no longer overwrite the spoken text.
        subtitle_frame = tk.Frame(
            center, bg="#101a33",
            highlightbackground=BORDER,
            highlightthickness=1
        )
        subtitle_frame.grid(
            row=1, column=0, sticky="ew", padx=20, pady=(0, 8)
        )
        self.subtitle_value = tk.Label(
            subtitle_frame,
            text=self.subtitle_text,
            bg="#101a33", fg=TEXT,
            font=("Helvetica Neue", 11),
            justify="center", anchor="center",
            wraplength=700, padx=12, pady=9
        )
        self.subtitle_value.pack(fill="x")
        center.bind(
            "<Configure>",
            lambda event: self.subtitle_value.config(
                wraplength=max(260, event.width - 70)
            )
        )

        bottom = tk.Frame(
            center, bg=BG,
            highlightbackground=BORDER,
            highlightthickness=1
        )
        bottom.grid(row=2, column=0, sticky="ew", padx=14, pady=10)
        bottom.columnconfigure(0, weight=1)

        self.entry = tk.Entry(
            bottom, bg="#0b1428", fg=TEXT,
            insertbackground=CYAN, relief="flat",
            font=("Helvetica Neue", 12)
        )
        self.entry.insert(0, "Ask NEXA something…")
        self.entry.bind("<FocusIn>", self._clear_placeholder)
        self.entry.bind("<Return>", self._send_message)
        self.entry.grid(
            row=0, column=0, sticky="ew",
            padx=(14, 8), pady=13, ipady=6
        )

        self.send_button = self._make_button(
            bottom, "➤", self._send_message,
            bg=BUTTON, fg=CYAN,
            font=("Helvetica Neue", 15, "bold"), padx=15
        )
        self.send_button.grid(
            row=0, column=1, padx=(0, 6), pady=7
        )

        self.mic_button = self._make_button(
            bottom, "🎙 Start Voice", self._toggle_voice,
            bg=BUTTON, fg=CYAN, padx=10, pady=9
        )
        self.mic_button.grid(row=0, column=2, padx=(0, 10), pady=7)

        tk.Label(
            center, text="Local AI chat • Samantha voice output",
            bg="#090f20", fg=MUTED,
            font=("Helvetica Neue", 9)
        ).grid(row=3, column=0, pady=(0, 9))

        # Right status panels
        right = tk.Frame(body, bg=BG)
        right.grid(row=0, column=2, sticky="nsew", padx=(12, 0))
        right.columnconfigure(0, weight=1)

        self._panel_title(right, "NEXA STATUS")

        self.status_value = tk.Label(
            right, text="● READY", bg=PANEL, fg=CYAN,
            font=("Helvetica Neue", 12, "bold"),
            anchor="w", padx=15, pady=14
        )
        self.status_value.pack(fill="x", pady=(0, 12))

        self._panel_title(right, "VOICE ACTIVITY")

        wave_frame = tk.Frame(
            right, bg=PANEL,
            highlightbackground=BORDER,
            highlightthickness=1
        )
        wave_frame.pack(fill="x", pady=(0, 12))

        self.wave_canvas = tk.Canvas(
            wave_frame, height=105, bg=PANEL,
            highlightthickness=0
        )
        self.wave_canvas.pack(fill="x", padx=8, pady=8)

        self.wave_label = tk.Label(
            wave_frame, text="Ready when you are",
            bg=PANEL, fg=MUTED,
            font=("Helvetica Neue", 10),
            wraplength=220, justify="center"
        )
        self.wave_label.pack(fill="x", padx=8, pady=(0, 12))

        self._panel_title(right, "MOOD THEME")

        mood_box = tk.Frame(
            right, bg=PANEL,
            highlightbackground=BORDER,
            highlightthickness=1
        )
        mood_box.pack(fill="x", pady=(0, 12))

        for mood in ("Normal", "Happy", "Focused", "Chill"):
            self._make_button(
                mood_box, mood,
                lambda m=mood: self._set_mood(m),
                bg=BUTTON if mood == "Normal" else PANEL2,
                fg=TEXT, padx=8, pady=8
            ).pack(fill="x", padx=9, pady=4)

        self._panel_title(right, "QUICK ACTIONS")

        quick = tk.Frame(
            right, bg=PANEL,
            highlightbackground=BORDER,
            highlightthickness=1
        )
        quick.pack(fill="x")

        for label, state in [
            ("🎵  Music", "Music shortcut selected"),
            ("▶  YouTube", "YouTube shortcut selected"),
            ("⌨  Mac controls", "Mac controls selected"),
            ("📝  Notes", "Notes shortcut selected")
        ]:
            self._make_button(
                quick, label,
                lambda s=state: self._set_status(s),
                bg=PANEL2, fg=TEXT, anchor="w",
                padx=13, pady=10
            ).pack(fill="x", padx=4, pady=1)

    def _responsive_layout(self, event=None):
        """Keep the three-column layout usable as the window is resized."""
        if event is not None and event.widget is not self.root:
            return
        width = self.root.winfo_width()
        try:
            body = self.root.winfo_children()[1]
            if width < 1080:
                body.grid_columnconfigure(0, minsize=138)
                body.grid_columnconfigure(1, minsize=310)
                body.grid_columnconfigure(2, minsize=180)
            else:
                body.grid_columnconfigure(0, minsize=145)
                body.grid_columnconfigure(1, minsize=330)
                body.grid_columnconfigure(2, minsize=190)
        except (tk.TclError, IndexError):
            pass

    def _panel_title(self, parent, title):
        tk.Label(
            parent, text=title, bg=BG, fg=MUTED,
            font=("Helvetica Neue", 9, "bold"),
            anchor="w"
        ).pack(fill="x", pady=(7, 7))

    # --------------------------------------------------
    # Load both avatar poses
    # --------------------------------------------------

    def _load_avatar_images(self):
        if Image is None:
            self._set_status("Pillow missing; install pillow")
            self._draw_avatar_scene()
            return

        folder = Path(__file__).resolve().parent

        portrait_path = folder / "nexa_avatar_portrait.png"
        speaking_path = folder / "nexa_avatar_speaking.png"
        project_root = folder.parent.parent
        mouth_folder = project_root / "assets" / "nexa_mouth_poses"

        try:
            if portrait_path.is_file() and portrait_path.stat().st_size > 0:
                self.avatar_original = Image.open(
                    portrait_path
                ).convert("RGB")
            else:
                self._set_status("Portrait image missing")

            if speaking_path.is_file() and speaking_path.stat().st_size > 0:
                self.avatar_speaking = Image.open(
                    speaking_path
                ).convert("RGB")
            else:
                self._set_status("Speaking image missing; using portrait")

            # Phase 8.3: load optional mouth poses without changing voice logic.
            pose_names = (
                "mouth_closed",
                "mouth_slightly_open",
                "mouth_open",
                "mouth_rounded",
            )
            for pose_name in pose_names:
                pose_path = mouth_folder / f"{pose_name}.png"
                if pose_path.is_file() and pose_path.stat().st_size > 0:
                    try:
                        self.avatar_mouth_poses[pose_name] = Image.open(
                            pose_path
                        ).convert("RGB")
                    except Exception as pose_exc:
                        print(
                            f"[NEXA] Could not load {pose_path.name}: {pose_exc}",
                            flush=True,
                        )

            if self.avatar_mouth_poses:
                print(
                    f"[NEXA] Loaded {len(self.avatar_mouth_poses)} mouth pose(s).",
                    flush=True,
                )
            else:
                print(
                    "[NEXA] Mouth poses not found; using existing avatar animation.",
                    flush=True,
                )

        except Exception as exc:
            print(f"Avatar image loading error: {exc}")
            self._set_status("Could not load avatar image")

        self._draw_avatar_scene()

    def _draw_avatar_scene(self):
        if not hasattr(self, "avatar_canvas"):
            return

        canvas = self.avatar_canvas
        canvas.delete("all")

        width = max(canvas.winfo_width(), 400)
        height = max(canvas.winfo_height(), 350)

        for x in range(0, width, 42):
            canvas.create_line(x, 0, x, height, fill="#101d38")
        for y in range(0, height, 42):
            canvas.create_line(0, y, width, y, fill="#101d38")

        canvas.create_oval(
            width * .12, height * .04,
            width * .88, height * 1.02,
            outline="#172d55", width=2
        )
        canvas.create_oval(
            width * .18, height * .10,
            width * .82, height * .92,
            outline="#183c61", width=1
        )

        # Phase 8.1: subtle holographic HUD rings around the avatar.
        # These use the existing animation phase; no voice code is changed.
        phase = getattr(self, "animation_phase", 0.0)

        # Phase 8.1.4: subtle state-aware halo pulse.
        if self.state == "Listening":
            halo_color = "#55f2c5"
        elif self.state == "Speaking":
            halo_color = CYAN
        elif "think" in self.state.lower() or "processing" in self.state.lower():
            halo_color = PURPLE
        else:
            halo_color = "#183c61"

        import math
        pulse = (math.sin(phase * 0.18) + 1.0) / 2.0
        halo_width = 1 + int(pulse * 2)
        canvas.create_oval(
            width * .13, height * .055,
            width * .87, height * .90,
            outline=halo_color,
            width=halo_width
        )
        ring_box = (
            width * .075, height * .035,
            width * .925, height * .895
        )
        canvas.create_oval(
            *ring_box, outline="#163858", width=1
        )

        canvas.create_arc(
            *ring_box,
            start=int((phase * 70) % 360),
            extent=72,
            style="arc",
            outline=CYAN,
            width=2
        )
        canvas.create_arc(
            *ring_box,
            start=int((-phase * 48 + 165) % 360),
            extent=48,
            style="arc",
            outline=PURPLE,
            width=2
        )

        # Small HUD markers add a restrained sci-fi instrument-panel feel.
        cx, cy = width / 2, height * .465
        rx, ry = width * .425, height * .43
        import math as _math
        for angle in (0, 90, 180, 270):
            theta = _math.radians(angle + phase * 24)
            px = cx + rx * _math.cos(theta)
            py = cy + ry * _math.sin(theta)
            canvas.create_oval(
                px - 2, py - 2, px + 2, py + 2,
                fill=CYAN, outline=""
            )

        # Phase 6.2: smoothly cross-fade between the two poses instead of
        # abruptly swapping full images. This is not phoneme-level lip-sync.
        if Image is not None and self.avatar_original is not None:
            size = (
                max(280, int(width * .83)),
                max(300, int(height * .90))
            )
            resting_frame = ImageOps.fit(
                self.avatar_original, size,
                method=Image.Resampling.LANCZOS,
                centering=(0.52, 0.42)
            )

            if self.state == "Speaking" and self.avatar_mouth_poses:
                # Cycle through mouth poses while speech is active.
                # This is pose-based animation, not phoneme-accurate lip-sync.
                pose_order = (
                    "mouth_closed",
                    "mouth_slightly_open",
                    "mouth_open",
                    "mouth_slightly_open",
                    "mouth_rounded",
                    "mouth_slightly_open",
                )
                cycle = getattr(self, "animation_phase", 0.0) % (2 * math.pi)
                pose_index = int(
                    (cycle / (2 * math.pi)) * len(pose_order)
                ) % len(pose_order)
                pose_name = pose_order[pose_index]
                pose_image = self.avatar_mouth_poses.get(pose_name)
                if pose_image is None:
                    pose_image = self.avatar_mouth_poses.get("mouth_slightly_open")
                if pose_image is None:
                    pose_image = self.avatar_mouth_poses.get("mouth_open")
                if pose_image is not None:
                    image = ImageOps.fit(
                        pose_image, size,
                        method=Image.Resampling.LANCZOS,
                        centering=(0.52, 0.42)
                    )
                else:
                    image = resting_frame
            elif self.state == "Speaking" and self.avatar_speaking is not None:
                speaking_frame = ImageOps.fit(
                    self.avatar_speaking, size,
                    method=Image.Resampling.LANCZOS,
                    centering=(0.52, 0.42)
                )
                image = Image.blend(
                    resting_frame, speaking_frame,
                    max(0.0, min(1.0, self.animation_mix))
                )
            else:
                image = resting_frame

            # Apply identical colour treatment after blending to reduce flicker.
            image = ImageEnhance.Color(image).enhance(0.88)
            image = ImageEnhance.Contrast(image).enhance(1.05)

            self.avatar_photo = ImageTk.PhotoImage(image)
            canvas.create_image(
                width / 2, height * .47,
                image=self.avatar_photo, anchor="center"
            )
            canvas.create_rectangle(
                0, height * .79, width, height,
                fill="#080d1b", stipple="gray50", outline=""
            )
        else:
            canvas.create_oval(
                width * .34, height * .18,
                width * .66, height * .65,
                fill="#19294a", outline=CYAN, width=2
            )
            canvas.create_text(
                width / 2, height * .38,
                text="NEXA", fill=CYAN,
                font=("Helvetica Neue", 32, "bold")
            )
            canvas.create_text(
                width / 2, height * .48,
                text="Add avatar PNG files beside this script",
                fill=MUTED, font=("Helvetica Neue", 10)
            )

        canvas.create_text(
            24, 24, text="NEXA // AVATAR CORE",
            anchor="nw", fill=CYAN,
            font=("Menlo", 10, "bold")
        )
        canvas.create_text(
            width / 2, height * .84,
            text="I'm NEXA.", fill=TEXT,
            font=("Helvetica Neue", 24, "bold")
        )
        canvas.create_text(
            width / 2, height * .90,
            text="Your personal AI companion",
            fill="#b6c7e8", font=("Helvetica Neue", 11)
        )
        canvas.create_text(
            width / 2, height * .96,
            text=f"● {self.state.upper()}",
            fill=CYAN, font=("Menlo", 9, "bold")
        )

    # --------------------------------------------------
    # Phase 8.1.2: continuous idle HUD animation
    # --------------------------------------------------

    def _animate_idle_hud(self):
        """Animate the holographic scene while NEXA is idle."""
        try:
            if not self.root.winfo_exists() or self._closing:
                return

            # The speaking animation already redraws the avatar.
            if self.state != "Speaking":
                self.animation_phase = (
                    getattr(self, "animation_phase", 0.0) + 0.12
                ) % 360
                self._draw_avatar_scene()

            self.root.after(180, self._animate_idle_hud)
        except tk.TclError:
            # The window may have closed while an animation was queued.
            return

    # --------------------------------------------------
    # Waveform and clock
    # --------------------------------------------------

    def _animate_wave(self):
        if not self.root.winfo_exists():
            return

        canvas = self.wave_canvas
        canvas.delete("all")

        width = max(canvas.winfo_width(), 210)
        height = max(canvas.winfo_height(), 85)

        for index in range(32):
            x = 8 + index * (width - 16) / 32
            amplitude = 8 + (
                math.sin(index * .7 + self.wave_phase) * .5 + .5
            ) * 27

            if self.state in ("Speaking", "Thinking"):
                amplitude *= random.uniform(.7, 1.45)

            canvas.create_line(
                x, height / 2 - amplitude,
                x, height / 2 + amplitude,
                fill=CYAN if index % 3 else PURPLE,
                width=3, capstyle="round"
            )

        self.wave_phase += .35
        self.root.after(100, self._animate_wave)

    def _clock(self):
        self.clock_label.config(
            text=datetime.now().strftime("%a, %d %b  •  %I:%M %p")
        )
        self.root.after(1000, self._clock)

    # --------------------------------------------------
    # AI and speech
    # --------------------------------------------------

    def _send_message(self, _event=None):
        message = self.entry.get().strip()

        if not message or message == "Ask NEXA something…":
            return

        if self.request_in_progress:
            self._set_status("Please wait")
            return

        if ask_nexa is None:
            self._set_status("AI connection unavailable")
            self._set_response(
                "Could not import ask_nexa. Launch from the AI Model project root."
            )
            return

        self.entry.delete(0, "end")
        self.request_in_progress = True
        self.send_button.config(state="disabled")
        self._set_status("Thinking")
        self._set_response("NEXA is thinking...")

        threading.Thread(
            target=self._get_ai_response,
            args=(message,),
            daemon=True
        ).start()

    def _get_ai_response(self, message):
        try:
            reply = str(ask_nexa(message, get_history()) or "").strip()
            if not reply:
                reply = "I couldn't generate a response just now."

            add_exchange(message, reply)

            self.root.after(
                0, lambda answer=reply: self._show_reply(answer)
            )
        except Exception as exc:
            error = f"AI request failed: {type(exc).__name__}: {exc}"
            self.root.after(
                0, lambda message=error: self._show_error(message)
            )

    def _show_reply(self, reply):
        self.request_in_progress = False
        self.send_button.config(state="normal")
        self._set_response(reply)

        # Use the same speech controller as microphone commands.
        # This prevents two independent macOS "say" processes from competing.
        self.speech_generation += 1
        generation = self.speech_generation
        self._set_status("Speaking")

        def speak_reply():
            try:
                from nexa import voice
                voice.configure_gui(
                    callback=self._emit_voice_event_threadsafe,
                    stop_event=(
                        self.voice_bridge._stop_event
                        if self.voice_bridge is not None
                        else None
                    ),
                )
                voice.speak(reply)
                process = voice.SPEECH_PROCESS
                if process is not None:
                    process.wait()
                self.root.after(
                    0, lambda: self._speech_finished(generation)
                )
            except Exception as exc:
                self.root.after(
                    0, lambda err=str(exc): self._show_error(
                        f"Speech failed: {err}"
                    )
                )

        threading.Thread(target=speak_reply, daemon=True).start()

    def _toggle_voice(self):
        if self.voice_bridge is None:
            if VoiceBridge is None:
                self._set_status("Voice bridge unavailable")
                return
            self.voice_bridge = VoiceBridge(self.root, self._on_voice_event)

        if not self.voice_enabled:
            started = self.voice_bridge.start()
            if started:
                self.voice_enabled = True
                self.mic_button.config(text="🎙 Voice Active")
                self._set_status("Starting microphone")
            else:
                self._set_status("Voice listener already started")
        else:
            self._set_status("Voice stopping requested")
            self.voice_bridge.stop()
            self.voice_enabled = False
            self.mic_button.config(text="🎙 Voice Stopped")

    def _emit_voice_event_threadsafe(self, event, text=""):
        """Forward voice events to Tkinter's main thread."""
        try:
            self.root.after(
                0, lambda e=event, t=text: self._on_voice_event(e, t)
            )
        except tk.TclError:
            pass

    def _on_voice_event(self, event, text):
        if self._closing:
            return

        if event == "transcript":
            self._set_response(f"You: {text}")
            self._set_status("Processing voice command")
        elif event == "reply":
            self._set_response(text)
            # voice.py owns speech playback for voice commands.
            self._set_status("Speaking")
        elif event == "status":
            if text == "Listening" and self.voice_enabled:
                self._set_status("Listening")
            elif text == "Speaking":
                self._set_status("Speaking")
            elif text == "Voice listener stopped":
                self.voice_enabled = False
                if hasattr(self, "mic_button"):
                    self.mic_button.config(text="🎙 Start Voice")
                self._set_status("Ready")
            elif text:
                self._set_status(text)
        elif event == "farewell_complete":
            # voice.py only emits this after the farewell playback process exits.
            self.root.after(150, self._close)
        elif event == "error":
            self._set_status("Voice error")
            self._set_response(text)

    def _wait_for_speech(self, process, generation):
        process.wait()
        try:
            self.root.after(
                0,
                lambda: self._speech_finished(generation)
            )
        except tk.TclError:
            pass

    def _speech_finished(self, generation):
        if generation != self.speech_generation:
            return

        self._set_status("Ready")

    def _show_error(self, message):
        self.request_in_progress = False
        self.send_button.config(state="normal")
        self._set_status("AI error")
        self._set_response(message)

    # --------------------------------------------------
    # Status and controls
    # --------------------------------------------------

    def _start_avatar_animation(self):
        """Start a smooth cross-fade while Samantha is speaking."""
        if self.avatar_animation_job is None:
            self.animation_phase = 0.0
            self.animation_mix = 0.0
            self.avatar_animation_job = self.root.after(
                50, self._animate_avatar_pose
            )

    def _animate_avatar_pose(self):
        self.avatar_animation_job = None

        if self.state != "Speaking" or not self.root.winfo_exists():
            self.animation_mix = 0.0
            self._draw_avatar_scene()
            return

        # Cosine easing creates a gradual transition in both directions.
        self.animation_phase += 0.20
        self.animation_mix = (1.0 - math.cos(self.animation_phase)) / 2.0
        if self.animation_phase >= 2 * math.pi:
            self.animation_phase -= 2 * math.pi

        self._draw_avatar_scene()
        self.avatar_animation_job = self.root.after(
            50, self._animate_avatar_pose
        )

    def _stop_avatar_animation(self):
        """Cancel animation and restore the resting portrait."""
        if self.avatar_animation_job is not None:
            try:
                self.root.after_cancel(self.avatar_animation_job)
            except tk.TclError:
                pass
            self.avatar_animation_job = None

        self.speaking_pose_toggle = False
        self.animation_mix = 0.0

    def _set_status(self, message):
        self.state = message

        # Phase 8.1.3: visual feedback for the current assistant state.
        label = message.lower()
        if "error" in label or "failed" in label:
            status_color = "#ff647c"
        elif "speak" in label:
            status_color = CYAN
        elif "listen" in label or "microphone" in label:
            status_color = "#55f2c5"
        elif "think" in label or "processing" in label:
            status_color = PURPLE
        elif "stop" in label:
            status_color = "#f6bd60"
        else:
            status_color = CYAN

        if message == "Speaking":
            self._start_avatar_animation()
        else:
            self._stop_avatar_animation()

        if hasattr(self, "status_value"):
            self.status_value.config(
                text=f"● {message.upper()}",
                fg=status_color
            )

        if hasattr(self, "wave_label"):
            self.wave_label.config(
                text=message,
                fg=status_color
            )

        self._draw_avatar_scene()

    def _set_response(self, message):
        # Keep the full latest transcript/reply visible while status changes.
        self.subtitle_text = str(message or "").strip()
        if hasattr(self, "subtitle_value"):
            self.subtitle_value.config(
                text=self.subtitle_text or "NEXA subtitles will appear here."
            )
        self.wave_label.config(
            text=self.subtitle_text or "Ready when you are",
            wraplength=220
        )

    def _set_mood(self, mood):
        self.mood = mood
        self._set_status(f"Mood theme: {mood}")

    def _nav_action(self, name):
        if name == "Home":
            self._set_status("Ready")
        else:
            self._set_status(f"{name} panel selected — integration pending")

    def _clear_placeholder(self, _event=None):
        if self.entry.get() == "Ask NEXA something…":
            self.entry.delete(0, "end")

    def _set_fullscreen(self, enabled):
        self.fullscreen = bool(enabled)
        self.root.attributes("-fullscreen", self.fullscreen)

    def _close(self):
        self._closing = True
        if self.voice_bridge is not None:
            self.voice_bridge.stop()
        try:
            from nexa import voice
            voice.stop_speech()
        except Exception:
            pass
        self.speech_generation += 1
        self._stop_avatar_animation()

        if (
            self.speech_process is not None
            and self.speech_process.poll() is None
        ):
            self.speech_process.terminate()

        self.root.destroy()


def main():
    # Prevent a second NEXA GUI instance from opening another listener.
    from nexa.voice import acquire_single_instance

    try:
        acquire_single_instance()
    except SystemExit:
        return
    except Exception as exc:
        print(
            f"[NEXA] Could not acquire single-instance lock: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )
        return

    root = tk.Tk()
    try:
        NexaAvatarApp(root)
        root.mainloop()
    except Exception:
        root.destroy()
        raise


if __name__ == "__main__":
    main()
