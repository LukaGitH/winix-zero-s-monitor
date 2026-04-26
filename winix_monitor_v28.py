#!/usr/bin/env python3
"""
Winix Zero-S UART Monitor v28 (RX only)

Known section:
- Power
- Fan mode / speed
- Particle filtered
- AQ LED state
- Ambient light filtered
- Ambient light raw
- Plasma state
- Motor feedback m3/h

Debug section:
- selected unknown / exploratory raw fields

TX note:
- Serial command attempts on the debug header did not work, so TX controls are removed.
"""

from __future__ import annotations

import argparse
import collections
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Deque, Dict, Optional, List

APP_NAME = "Winix Zero-S Monitor v28"

BG = "#121417"
PANEL_BG = "#1b1f24"
PANEL_BG_ALT = "#222831"
TEXT = "#e6edf3"
MUTED_TEXT = "#9aa4af"
BORDER = "#3a424d"
INPUT_BG = "#0f1318"
SELECT_BG = "#2f80ed"
GRID = "#303842"
PLOT_BG = "#151a20"
HISTORY_STEP_SECONDS = 30
MIN_HISTORY_SECONDS = 30
MAX_MOTOR_FEEDBACK_RAW = 220000
MAX_AIRFLOW_M3H = 410.0
MOTOR_AXIS_MAX_M3H = 420.0

try:
    import serial
    import serial.tools.list_ports
except Exception as exc:
    serial = None  # type: ignore
    SERIAL_IMPORT_ERROR = exc
else:
    SERIAL_IMPORT_ERROR = None

import tkinter as tk
from tkinter import ttk, messagebox

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure


@dataclass
class WinixState:
    power: bool = False
    mode: str = "unknown"
    fan_mode_code: int = 0

    particle_filtered: Optional[int] = None
    aq_led_code: Optional[int] = None
    aq_led_label: str = "unknown"

    ambient_light_filtered: Optional[int] = None
    ambient_light_raw: Optional[int] = None

    plasma_flag_10_7: Optional[int] = None

    motor_feedback: Optional[int] = None

    last_frame_id: Optional[int] = None
    frame_count: int = 0
    checksum_errors: int = 0
    last_update_monotonic: float = field(default_factory=time.monotonic)

    def fan_mode_label(self) -> str:
        mapping = {
            1: "auto",
            2: "speed 1",
            3: "speed 2",
            4: "speed 3",
            5: "max",
            0: "sleep",
        }
        return mapping.get(self.fan_mode_code, f"unknown({self.fan_mode_code})")

    def plasma_label(self) -> str:
        mapping = {
            0x01: "off",
            0x61: "on",
        }
        if self.plasma_flag_10_7 is None:
            return "unknown"
        return mapping.get(self.plasma_flag_10_7, f"unknown({self.plasma_flag_10_7})")

    def plasma_state_value(self) -> Optional[int]:
        if self.plasma_flag_10_7 == 0x01:
            return 0
        if self.plasma_flag_10_7 == 0x61:
            return 1
        return None

    def motor_feedback_m3h(self) -> Optional[float]:
        if not self.power:
            return 0.0
        if self.motor_feedback is None:
            return None
        return max(0.0, min(MAX_AIRFLOW_M3H, self.motor_feedback / MAX_MOTOR_FEEDBACK_RAW * MAX_AIRFLOW_M3H))

    def known_rows(self) -> List[tuple[str, str]]:
        def fmt(v):
            if v is None:
                return "—"
            if isinstance(v, bool):
                return "ON" if v else "OFF"
            return str(v)

        def fmt_m3h(v):
            if v is None:
                return "—"
            return f"{v:.1f}"

        return [
            ("Power", fmt(self.power)),
            ("Mode", self.mode),
            ("Fan mode code", fmt(self.fan_mode_code)),
            ("Particle filtered", fmt(self.particle_filtered)),
            ("AQ LED code", fmt(self.aq_led_code)),
            ("AQ LED state", self.aq_led_label),
            ("Ambient light filtered", fmt(self.ambient_light_filtered)),
            ("Ambient light raw", fmt(self.ambient_light_raw)),
            ("Plasma state", self.plasma_label()),
            ("Motor feedback raw", fmt(self.motor_feedback)),
            ("Motor feedback m3/h", fmt_m3h(self.motor_feedback_m3h())),
        ]

    def debug_rows(self) -> List[tuple[str, str]]:
        def fmt(v):
            if v is None:
                return "—"
            return str(v)

        return [
            ("Last frame ID", fmt(self.last_frame_id)),
            ("Frame count", str(self.frame_count)),
            ("Checksum errors", str(self.checksum_errors)),
        ]


class WinixProtocolParser:
    def __init__(self) -> None:
        self.buf: List[int] = []
        self.state = WinixState()

    @staticmethod
    def _valid_checksum(frame: List[int]) -> bool:
        return (sum(frame[:-1]) & 0xFF) == frame[-1]

    @staticmethod
    def _be16(hi: int, lo: int) -> int:
        return ((hi & 0xFF) << 8) | (lo & 0xFF)

    @staticmethod
    def _motor_feedback_candidate(fr: List[int]) -> int:
        low16 = WinixProtocolParser._be16(fr[8], fr[9])
        high_bits = fr[7] & 0x03
        return (high_bits << 16) | low16

    def ingest_byte(self, b: int) -> Optional[WinixState]:
        if not self.buf:
            if b == 0xF0:
                self.buf.append(b)
            return None

        self.buf.append(b)

        if len(self.buf) == 3:
            frame_len = self.buf[2]
            if frame_len < 4 or frame_len > 64:
                self.buf.clear()
            return None

        if len(self.buf) >= 3:
            frame_len = self.buf[2]
            if len(self.buf) == frame_len:
                frame = self.buf[:]
                self.buf.clear()
                if not self._valid_checksum(frame):
                    self.state.checksum_errors += 1
                    self.state.last_update_monotonic = time.monotonic()
                    return self.state
                self._decode(frame)
                self.state.frame_count += 1
                self.state.last_frame_id = frame[1]
                self.state.last_update_monotonic = time.monotonic()
                return self.state
            if len(self.buf) > frame_len:
                self.buf.clear()
        return None

    def _decode(self, frame: List[int]) -> None:
        frame_id = frame[1]
        if frame_id == 0x10:
            self._decode_10(frame)
        elif frame_id == 0x11:
            self._decode_11(frame)
        elif frame_id == 0x13:
            self._decode_13(frame)
        elif frame_id == 0x14:
            self._decode_14(frame)

    def _decode_10(self, fr: List[int]) -> None:
        if len(fr) <= 12:
            return

        power = fr[4] != 0
        fan_code = fr[12]
        self.state.power = power
        self.state.fan_mode_code = fan_code
        self.state.plasma_flag_10_7 = fr[7] if len(fr) > 7 else None

        if not power:
            self.state.mode = "off"
            return

        if fan_code == 1:
            self.state.mode = "auto"
        elif fan_code == 2:
            self.state.mode = "speed 1"
        elif fan_code == 3:
            self.state.mode = "speed 2"
        elif fan_code == 4:
            self.state.mode = "speed 3"
        elif fan_code == 5:
            self.state.mode = "max"
        elif fan_code == 0:
            self.state.mode = "sleep"
        else:
            self.state.mode = f"unknown({fan_code})"

    def _decode_11(self, fr: List[int]) -> None:
        if len(fr) > 14:
            self.state.particle_filtered = fr[12]
            code = fr[14]
            self.state.aq_led_code = code
            mapping = {1: "blue", 2: "orange", 3: "red"}
            self.state.aq_led_label = mapping.get(code, f"unknown({code})")

    def _decode_13(self, fr: List[int]) -> None:
        if len(fr) > 7:
            self.state.ambient_light_filtered = fr[4]
            self.state.ambient_light_raw = fr[7]

    def _decode_14(self, fr: List[int]) -> None:
        if len(fr) > 9:
            self.state.motor_feedback = self._motor_feedback_candidate(fr)


class SerialReader(threading.Thread):
    def __init__(self, port: str, baudrate: int, out_queue: queue.Queue):
        super().__init__(daemon=True)
        self.port = port
        self.baudrate = baudrate
        self.out_queue = out_queue
        self._stop_event = threading.Event()
        self._ser = None
        self._parser = WinixProtocolParser()

    def stop(self) -> None:
        self._stop_event.set()
        try:
            if self._ser is not None:
                self._ser.close()
        except Exception:
            pass

    def run(self) -> None:
        if serial is None:
            self.out_queue.put(("error", f"pyserial import failed: {SERIAL_IMPORT_ERROR}"))
            return

        try:
            self._ser = serial.Serial(self.port, self.baudrate, timeout=0.1)
        except Exception as exc:
            self.out_queue.put(("error", f"Could not open {self.port}: {exc}"))
            return

        self.out_queue.put(("status", f"Connected to {self.port} @ {self.baudrate}"))
        while not self._stop_event.is_set():
            try:
                data = self._ser.read(512)
            except Exception as exc:
                if self._stop_event.is_set():
                    return
                self.out_queue.put(("error", f"Serial read failed: {exc}"))
                return

            if not data:
                continue

            for b in data:
                state = self._parser.ingest_byte(b)
                if state is not None:
                    snapshot = WinixState(**state.__dict__)
                    self.out_queue.put(("state", snapshot))


class App:
    def __init__(self, root: tk.Tk, port: Optional[str], baudrate: int, history_seconds: int):
        self.root = root
        self.root.title(APP_NAME)
        self._apply_dark_theme()
        self.queue: queue.Queue = queue.Queue()
        self.reader: Optional[SerialReader] = None
        self.history_seconds = history_seconds

        self.state = WinixState()

        self.times: Deque[float] = collections.deque(maxlen=8000)
        self.particle_filtered: Deque[float] = collections.deque(maxlen=8000)
        self.ambient_light_filtered: Deque[float] = collections.deque(maxlen=8000)
        self.ambient_light_raw: Deque[float] = collections.deque(maxlen=8000)
        self.motor_feedback_m3h: Deque[float] = collections.deque(maxlen=8000)

        self.port_var = tk.StringVar(value=port or self._auto_port())
        self.baud_var = tk.StringVar(value=str(baudrate))
        self.status_var = tk.StringVar(value="Idle")
        self.history_var = tk.StringVar(value=self._history_label())

        self.show_particle = tk.BooleanVar(value=True)
        self.show_light_filtered = tk.BooleanVar(value=True)
        self.show_light_raw = tk.BooleanVar(value=True)
        self.show_motor_feedback = tk.BooleanVar(value=False)

        self.autoscale_main = tk.BooleanVar(value=True)

        self.known_value_labels: Dict[str, ttk.Label] = {}
        self.debug_value_labels: Dict[str, ttk.Label] = {}

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(100, self._poll_queue)
        self.root.after(500, self._refresh_plot)

    def _apply_dark_theme(self) -> None:
        self.root.configure(bg=BG)
        self.root.option_add("*TCombobox*Listbox.background", INPUT_BG)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", SELECT_BG)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(".", background=BG, foreground=TEXT, fieldbackground=INPUT_BG)
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("TLabelframe", background=BG, foreground=TEXT, bordercolor=BORDER)
        style.configure("TLabelframe.Label", background=BG, foreground=TEXT)
        style.configure(
            "TButton",
            background=PANEL_BG_ALT,
            foreground=TEXT,
            bordercolor=BORDER,
            focusthickness=1,
            focuscolor=BORDER,
            padding=(8, 4),
        )
        style.map(
            "TButton",
            background=[("active", "#2a313a"), ("pressed", "#343d48")],
            foreground=[("disabled", MUTED_TEXT)],
        )
        style.configure(
            "TCheckbutton",
            background=BG,
            foreground=TEXT,
            indicatorcolor=INPUT_BG,
            indicatormargin=4,
        )
        style.map(
            "TCheckbutton",
            background=[("active", BG)],
            foreground=[("disabled", MUTED_TEXT)],
            indicatorcolor=[("selected", SELECT_BG), ("!selected", INPUT_BG)],
        )
        style.configure(
            "TEntry",
            fieldbackground=INPUT_BG,
            foreground=TEXT,
            insertcolor=TEXT,
            bordercolor=BORDER,
            lightcolor=BORDER,
            darkcolor=BORDER,
        )
        style.configure(
            "TCombobox",
            fieldbackground=INPUT_BG,
            background=PANEL_BG_ALT,
            foreground=TEXT,
            arrowcolor=TEXT,
            bordercolor=BORDER,
            lightcolor=BORDER,
            darkcolor=BORDER,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", INPUT_BG)],
            foreground=[("readonly", TEXT)],
            selectbackground=[("readonly", SELECT_BG)],
            selectforeground=[("readonly", "#ffffff")],
        )

    def _auto_port(self) -> str:
        if serial is None:
            return ""
        ports = list(serial.tools.list_ports.comports())
        if not ports:
            return ""
        for p in ports:
            name = (p.device or "").lower()
            desc = (p.description or "").lower()
            if any(k in name for k in ("ttyusb", "ttyacm", "cu.usb", "com")) or "usb" in desc:
                return p.device
        return ports[0].device

    def _history_label(self) -> str:
        return f"{self.history_seconds}s"

    def _change_history(self, delta: int) -> None:
        self.history_seconds = max(MIN_HISTORY_SECONDS, self.history_seconds + delta)
        self.history_var.set(self._history_label())
        self._trim_history()

    def _trim_history(self) -> None:
        if not self.times:
            return

        newest = self.times[-1]
        while self.times and (newest - self.times[0]) > self.history_seconds:
            self.times.popleft()
            self.particle_filtered.popleft()
            self.ambient_light_filtered.popleft()
            self.ambient_light_raw.popleft()
            self.motor_feedback_m3h.popleft()

    def _aq_led_color(self) -> str:
        return {1: "#2f80ed", 2: "#f2994a", 3: "#eb5757"}.get(self.state.aq_led_code, "#808080")

    def _light_color(self) -> str:
        raw = self.state.ambient_light_raw
        if raw is None:
            return "#808080"
        # grayscale for quick visual feel; 0=dark, 255=bright
        v = max(0, min(255, int(raw)))
        return f"#{v:02x}{v:02x}{v:02x}"

    def _bool_color(self, value: str) -> str:
        return {"on": "#27ae60", "off": "#eb5757", "unknown": "#808080"}.get(value.lower(), "#808080")

    def _speed_color(self, value: str) -> str:
        return {
            "auto": "#2f80ed",
            "1": "#27ae60",
            "2": "#f2c94c",
            "3": "#f2994a",
            "max": "#eb5757",
            "sleep": "#9b51e0",
            "unknown": "#808080",
        }.get(value.lower(), "#808080")

    def _build_ui(self) -> None:
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill="x")

        ttk.Label(top, text="Port").grid(row=0, column=0, sticky="w")
        self.port_combo = ttk.Combobox(top, textvariable=self.port_var, width=24, values=self._list_ports())
        self.port_combo.grid(row=0, column=1, padx=4, sticky="w")

        ttk.Label(top, text="Baud").grid(row=0, column=2, sticky="w")
        ttk.Entry(top, textvariable=self.baud_var, width=10).grid(row=0, column=3, padx=4, sticky="w")

        ttk.Button(top, text="Refresh Ports", command=self._refresh_ports).grid(row=0, column=4, padx=4)
        ttk.Button(top, text="Connect", command=self.connect).grid(row=0, column=5, padx=4)
        ttk.Button(top, text="Disconnect", command=self.disconnect).grid(row=0, column=6, padx=4)

        ttk.Label(top, textvariable=self.status_var).grid(row=1, column=0, columnspan=7, sticky="w", pady=(6, 0))

        middle = ttk.Frame(self.root, padding=(8, 0, 8, 8))
        middle.pack(fill="both", expand=True)

        left = ttk.Frame(middle)
        left.pack(side="left", fill="y")

        indicator_box = ttk.LabelFrame(left, text="Known Indicators", padding=8)
        indicator_box.pack(fill="x", pady=(0, 8))

        ttk.Label(indicator_box, text="AQ LED").grid(row=0, column=0, sticky="w")
        self.aq_led_text = ttk.Label(indicator_box, text="unknown", font=("Segoe UI", 10, "bold"))
        self.aq_led_text.grid(row=0, column=1, sticky="w", padx=8)
        self.aq_led_canvas = tk.Canvas(
            indicator_box,
            width=100,
            height=24,
            bg=PANEL_BG,
            highlightthickness=1,
            highlightbackground=BORDER,
        )
        self.aq_led_canvas.grid(row=0, column=2, padx=6, pady=2)
        self.aq_led_rect = self.aq_led_canvas.create_rectangle(4, 4, 96, 20, fill=self._aq_led_color(), outline="")

        ttk.Label(indicator_box, text="Ambient light").grid(row=1, column=0, sticky="w")
        self.light_text = ttk.Label(indicator_box, text="unknown", font=("Segoe UI", 10, "bold"))
        self.light_text.grid(row=1, column=1, sticky="w", padx=8)
        self.light_canvas = tk.Canvas(
            indicator_box,
            width=100,
            height=24,
            bg=PANEL_BG,
            highlightthickness=1,
            highlightbackground=BORDER,
        )
        self.light_canvas.grid(row=1, column=2, padx=6, pady=2)
        self.light_rect = self.light_canvas.create_rectangle(4, 4, 96, 20, fill=self._light_color(), outline="")

        known_box = ttk.LabelFrame(left, text="Known Live RX Values", padding=8)
        known_box.pack(fill="x", pady=(0, 8))
        for i, (k, v) in enumerate(self.state.known_rows()):
            ttk.Label(known_box, text=k).grid(row=i, column=0, sticky="w", padx=(0, 12), pady=2)
            lbl = ttk.Label(known_box, text=v, width=20)
            lbl.grid(row=i, column=1, sticky="w", pady=2)
            self.known_value_labels[k] = lbl

        debug_box = ttk.LabelFrame(left, text="Debug Values", padding=8)
        debug_box.pack(fill="x")
        for i, (k, v) in enumerate(self.state.debug_rows()):
            ttk.Label(debug_box, text=k).grid(row=i, column=0, sticky="w", padx=(0, 12), pady=2)
            lbl = ttk.Label(debug_box, text=v, width=20)
            lbl.grid(row=i, column=1, sticky="w", pady=2)
            self.debug_value_labels[k] = lbl

        right = ttk.LabelFrame(middle, text="Live Graphs", padding=8)
        right.pack(side="left", fill="both", expand=True)

        fig = Figure(figsize=(11, 5), dpi=100, facecolor=BG)
        self.ax_main = fig.add_subplot(111)
        self.ax_motor = self.ax_main.twinx()
        self.ax_motor.patch.set_alpha(0)

        self.ax_main.set_xlabel("Seconds ago")
        self.ax_main.set_ylabel("Particle filtered")
        self.ax_motor.set_ylabel(f"Ambient light / Motor feedback (0-{MOTOR_AXIS_MAX_M3H:.0f})")
        self._style_axis(self.ax_main)
        self._style_axis(self.ax_motor)
        self.ax_main.grid(True, color=GRID, linewidth=0.8)

        self.line_particle, = self.ax_main.plot([], [], label="Particle filtered", color="#3fb950")
        self.line_light_filtered, = self.ax_motor.plot([], [], label="Ambient light filtered", color="#58a6ff")
        self.line_light_raw, = self.ax_motor.plot([], [], label="Ambient light", color="#f2cc60")
        self.line_motor_feedback, = self.ax_motor.plot([], [], label="Motor feedback m3/h", color="#ff7b72")

        lines = [
            self.line_particle,
            self.line_light_filtered,
            self.line_light_raw,
            self.line_motor_feedback,
        ]
        labels = [line.get_label() for line in lines]
        legend = self.ax_main.legend(lines, labels, loc="upper left")
        legend.get_frame().set_facecolor(PANEL_BG)
        legend.get_frame().set_edgecolor(BORDER)
        for text in legend.get_texts():
            text.set_color(TEXT)

        self.canvas = FigureCanvasTkAgg(fig, master=right)
        self.canvas.get_tk_widget().configure(bg=BG, highlightthickness=0)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        controls = ttk.Frame(right)
        controls.pack(fill="x", pady=4)

        ttk.Checkbutton(controls, text="Particle filtered", variable=self.show_particle).grid(row=0, column=0, sticky="w", padx=4)
        ttk.Checkbutton(controls, text="Ambient light filtered", variable=self.show_light_filtered).grid(row=0, column=1, sticky="w", padx=4)
        ttk.Checkbutton(controls, text="Ambient light", variable=self.show_light_raw).grid(row=0, column=2, sticky="w", padx=4)
        ttk.Checkbutton(controls, text="Motor feedback m3/h", variable=self.show_motor_feedback).grid(row=0, column=3, sticky="w", padx=4)

        ttk.Checkbutton(controls, text="Autoscale main axis", variable=self.autoscale_main).grid(row=1, column=0, sticky="w", padx=4, pady=(6, 0))
        ttk.Label(controls, text="Graph window").grid(row=1, column=1, sticky="e", padx=(16, 4), pady=(6, 0))
        ttk.Button(
            controls,
            text="-30s",
            width=6,
            command=lambda: self._change_history(-HISTORY_STEP_SECONDS),
        ).grid(row=1, column=2, sticky="w", padx=2, pady=(6, 0))
        ttk.Label(controls, textvariable=self.history_var, width=8).grid(row=1, column=3, sticky="w", padx=4, pady=(6, 0))
        ttk.Button(
            controls,
            text="+30s",
            width=6,
            command=lambda: self._change_history(HISTORY_STEP_SECONDS),
        ).grid(row=1, column=4, sticky="w", padx=2, pady=(6, 0))

    def _style_axis(self, ax) -> None:
        ax.set_facecolor(PLOT_BG)
        ax.tick_params(colors=MUTED_TEXT)
        ax.xaxis.label.set_color(TEXT)
        ax.yaxis.label.set_color(TEXT)
        for spine in ax.spines.values():
            spine.set_color(BORDER)

    def _list_ports(self) -> List[str]:
        if serial is None:
            return []
        return [p.device for p in serial.tools.list_ports.comports()]

    def _refresh_ports(self) -> None:
        ports = self._list_ports()
        self.port_combo["values"] = ports
        if self.port_var.get() not in ports and ports:
            self.port_var.set(ports[0])

    def connect(self) -> None:
        self.disconnect()

        port = self.port_var.get().strip()
        if not port:
            messagebox.showerror(APP_NAME, "Select a serial port first.")
            return

        try:
            baudrate = int(self.baud_var.get().strip())
        except ValueError:
            messagebox.showerror(APP_NAME, "Baud rate must be an integer.")
            return

        self.reader = SerialReader(port, baudrate, self.queue)
        self.reader.start()
        self.status_var.set(f"Connecting to {port}...")

    def disconnect(self) -> None:
        if self.reader is not None:
            reader = self.reader
            self.reader = None
            reader.stop()
            reader.join(timeout=0.5)
            self._drain_queue()
        self.status_var.set("Disconnected")

    def _drain_queue(self) -> None:
        try:
            while True:
                self.queue.get_nowait()
        except queue.Empty:
            pass

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "error":
                    self.status_var.set(payload)
                    messagebox.showerror(APP_NAME, payload)
                    self.disconnect()
                elif kind == "status":
                    self.status_var.set(payload)
                elif kind == "state":
                    self.state = payload
                    self._push_history(payload)
                    self._refresh_values()
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _push_history(self, st: WinixState) -> None:
        t = time.monotonic()
        self.times.append(t)
        self.particle_filtered.append(float(st.particle_filtered or 0))
        self.ambient_light_filtered.append(float(st.ambient_light_filtered or 0))
        self.ambient_light_raw.append(float(st.ambient_light_raw or 0))
        self.motor_feedback_m3h.append(float(st.motor_feedback_m3h() or 0))
        self._trim_history()

    def _refresh_values(self) -> None:
        for key, value in self.state.known_rows():
            lbl = self.known_value_labels.get(key)
            if lbl is not None:
                lbl.configure(text=value)

        for key, value in self.state.debug_rows():
            lbl = self.debug_value_labels.get(key)
            if lbl is not None:
                lbl.configure(text=value)

        self.aq_led_text.configure(text=self.state.aq_led_label)
        self.aq_led_canvas.itemconfig(self.aq_led_rect, fill=self._aq_led_color())

        light_txt = f"filt={self.state.ambient_light_filtered} raw={self.state.ambient_light_raw}"
        self.light_text.configure(text=light_txt)
        self.light_canvas.itemconfig(self.light_rect, fill=self._light_color())

    def _set_line_data(self, line, xs: List[float], ys: List[float], enabled: bool) -> None:
        if enabled:
            line.set_data(xs, ys)
        else:
            line.set_data([], [])

    def _refresh_plot(self) -> None:
        if self.times:
            now = time.monotonic()
            xs = [-(now - t) for t in self.times]

            self._set_line_data(self.line_particle, xs, list(self.particle_filtered), self.show_particle.get())
            self._set_line_data(self.line_light_filtered, xs, list(self.ambient_light_filtered), self.show_light_filtered.get())
            self._set_line_data(self.line_light_raw, xs, list(self.ambient_light_raw), self.show_light_raw.get())
            self._set_line_data(self.line_motor_feedback, xs, list(self.motor_feedback_m3h), self.show_motor_feedback.get())

            self.ax_main.set_xlim(-self.history_seconds, 0)
            self.ax_motor.set_xlim(-self.history_seconds, 0)

            if self.autoscale_main.get():
                self.ax_main.relim()
                self.ax_main.autoscale_view(scalex=False, scaley=True)

            self.ax_motor.set_ylim(0, MOTOR_AXIS_MAX_M3H)

        self.canvas.draw_idle()
        self.root.after(500, self._refresh_plot)

    def on_close(self) -> None:
        self.disconnect()
        self.root.destroy()


def main() -> int:
    parser = argparse.ArgumentParser(description="Winix Zero-S live UART monitor")
    parser.add_argument("--port", help="Serial port, e.g. COM5")
    parser.add_argument("--baud", type=int, default=38400, help="Baud rate (default: 38400)")
    parser.add_argument("--history", type=int, default=900, help="Seconds of history to keep")
    args = parser.parse_args()

    root = tk.Tk()
    App(root, args.port, args.baud, args.history)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
