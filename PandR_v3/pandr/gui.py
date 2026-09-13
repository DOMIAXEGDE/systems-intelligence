"""Tkinter graphical workspace for Point & Resolve.

The runtime is the only writer of semantic session state. Background work sends
plain results through a queue; all Tk calls occur on the owning UI thread.
"""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
import wave

from .execution import ExecutionService


BLUE = "#2463dd"
INK = "#172940"
MUTED = "#63758b"
GRID = "#e8eef6"
BG = "#f2f5fa"
RAY_COLORS = {("v", "horizontal"): "#059669", ("h", "horizontal"): "#d97706",
              ("v", "vertical"): "#8b5cf6", ("h", "vertical"): "#dc4673"}


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _number(value) -> float:
    if isinstance(value, dict):
        from .geometry import number_to_float
        return number_to_float(value)
    return float(value)


def _exact(value) -> str:
    if isinstance(value, dict) and value.get("kind") == "rational":
        n, d = value.get("n", "0"), value.get("d", "1")
        return str(n) if str(d) == "1" else f"{n}/{d}"
    if isinstance(value, dict):
        return _json(value).replace("\n", " ")
    return str(value)


class PandRApp:
    """Graphical controller with acknowledged background operations.

    A caller may supply an existing Tk root and Runtime for embedding/testing.
    ``select_point``, ``select_layer``, ``set_code``, ``execute_script`` and
    ``run_automation`` provide the familiar controller surface.
    """

    def __init__(self, root: tk.Tk, runtime) -> None:
        self.root = root
        self.runtime = runtime
        self._ui_thread = threading.get_ident()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pandr-work")
        self.execution = ExecutionService()
        self._results: queue.Queue = queue.Queue()
        self._closed = False
        self._busy = 0
        self._plot_serial = 0
        self._plot_after = None
        self._poll_after = None
        self._snapshot = {}
        self._curve = []
        self._plot_segments = []
        self._measurements = []
        self._points = []
        self._selected_point = None
        self._selected_layer = None
        self._view = {"x": 0.0, "y": 0.0, "scale": 6.0}
        self._pan_anchor = None
        self._photo = None
        self._artifact_path = None
        self._glyph_pixels = []
        self._alphabet = None
        self._pending = []
        self._closing = False
        self._build()
        self._apply_snapshot(runtime.snapshot(), load_editors=True)
        self._poll_after = root.after(35, self._poll)
        root.protocol("WM_DELETE_WINDOW", self.close)

    def _build(self) -> None:
        root = self.root
        root.title("Point & Resolve · P&R")
        root.geometry("1510x940")
        root.minsize(1120, 730)
        root.configure(background=BG)
        style = ttk.Style(root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=INK, font=("Segoe UI", 10))
        style.configure("Muted.TLabel", foreground=MUTED, font=("Segoe UI", 9))
        style.configure("Title.TLabel", font=("Segoe UI Semibold", 22), foreground=INK)
        style.configure("TButton", font=("Segoe UI", 9), padding=(10, 6))
        style.configure("Accent.TButton", foreground="white", background=BLUE)
        style.map("Accent.TButton", background=[("active", "#174ba9")])
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(9, 7), font=("Segoe UI", 9))
        style.configure("Treeview", rowheight=25, font=("Segoe UI", 9), background="white")
        style.configure("Treeview.Heading", font=("Segoe UI Semibold", 9))
        style.configure("TLabelframe", background=BG)
        style.configure("TLabelframe.Label", background=BG, foreground=INK)
        menu = tk.Menu(root)
        session = tk.Menu(menu, tearoff=False)
        for label, action in (("New session…", self._new), ("Open session…", self._open),
                              ("Save view and editors", self._save_editors),
                              ("Save As…", self._save_as), ("Export portable bundle…", self._export)):
            session.add_command(label=label, command=action)
        session.add_separator()
        session.add_command(label="Exit", command=self.close)
        menu.add_cascade(label="Session", menu=session)
        tools_menu = tk.Menu(menu, tearoff=False)
        for label, action in (("Validate session", self._validate), ("Replay to new session…", self._replay),
                              ("Reload session", self._reload), ("Reset viewport", self._reset_view)):
            tools_menu.add_command(label=label, command=action)
        menu.add_cascade(label="Runtime", menu=tools_menu)
        root.configure(menu=menu)

        heading = ttk.Frame(root, padding=(18, 13, 18, 10))
        heading.pack(fill="x")
        ttk.Label(heading, text="Point & Resolve", style="Title.TLabel").pack(side="left")
        ttk.Label(heading, text=" FIXED POINTS / OPERATOR PROGRAMMED", style="Muted.TLabel").pack(side="left", padx=18)
        self.session_label = ttk.Label(heading, text="", style="Muted.TLabel")
        self.session_label.pack(side="right")
        self.body = ttk.Panedwindow(root, orient="horizontal")
        self.body.pack(fill="both", expand=True, padx=14)
        browser = ttk.Frame(self.body, width=190, padding=(0, 0, 10, 0))
        center = ttk.Frame(self.body, width=680)
        right = ttk.Frame(self.body, width=575, padding=(10, 0, 0, 0))
        self.body.add(browser, weight=0)
        self.body.add(center, weight=3)
        self.body.add(right, weight=2)
        self._build_browser(browser)
        self._build_plane(center)
        self.tabs = ttk.Notebook(right)
        self.tabs.pack(fill="both", expand=True)
        for label, builder in (("Function", self._build_function), ("Sequence", self._build_sequence),
                               ("Pixels", self._build_pixels), ("Python", self._build_script),
                               ("Signals", self._build_signals), ("Media", self._build_artifacts),
                               ("Journal", self._build_journal)):
            frame = ttk.Frame(self.tabs, padding=10)
            self.tabs.add(frame, text=label)
            builder(frame)
        footer = ttk.Frame(root, padding=(17, 8))
        footer.pack(fill="x")
        self.status = tk.StringVar(value="Ready")
        ttk.Label(footer, textvariable=self.status, style="Muted.TLabel").pack(side="left")
        self.progress = ttk.Progressbar(footer, mode="indeterminate", length=90)
        self.progress.pack(side="right")

    @staticmethod
    def _text(parent, height=10, **options) -> tk.Text:
        container = ttk.Frame(parent)
        container.pack(fill="both", expand=True, pady=(4, 7))
        widget = tk.Text(container, height=height, wrap="word", font=("Consolas", 10),
                         relief="flat", borderwidth=1, highlightthickness=1,
                         highlightbackground="#d4dce8", padx=8, pady=7,
                         undo=True, **options)
        scrollbar = ttk.Scrollbar(container, command=widget.yview)
        widget.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        widget.pack(side="left", fill="both", expand=True)
        return widget

    @staticmethod
    def _replace(widget: tk.Text, text: str) -> None:
        disabled = str(widget.cget("state")) == "disabled"
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.edit_modified(False)
        if disabled:
            widget.configure(state="disabled")

    @staticmethod
    def _entry(parent, label, value="", width=18) -> tk.StringVar:
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text=label, width=17).pack(side="left")
        variable = tk.StringVar(value=value)
        ttk.Entry(row, textvariable=variable, width=width).pack(side="left", fill="x", expand=True)
        return variable

    @staticmethod
    def _combo(parent, label, values, value) -> tk.StringVar:
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text=label, width=17).pack(side="left")
        variable = tk.StringVar(value=value)
        ttk.Combobox(row, textvariable=variable, values=values, state="readonly", width=21).pack(side="left", fill="x", expand=True)
        return variable

    def _build_browser(self, frame):
        ttk.Label(frame, text="FABRIC", font=("Segoe UI Semibold", 10)).pack(anchor="w", pady=(6, 6))
        self.filter_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.filter_var).pack(fill="x", pady=(0, 7))
        self.filter_var.trace_add("write", lambda *_: self._fill_points())
        self.point_list = tk.Listbox(frame, height=12, exportselection=False, relief="flat", font=("Consolas", 10),
                                    selectbackground=BLUE, background="white", highlightthickness=0)
        self.point_list.pack(fill="both", expand=True)
        self.point_list.bind("<<ListboxSelect>>", self._on_point_list)
        point_ops = ttk.Frame(frame)
        point_ops.pack(fill="x", pady=4)
        ttk.Button(point_ops, text="Add…", width=6, command=self._add_point).pack(side="left", padx=(0,2))
        ttk.Button(point_ops, text="Edit…", width=6, command=self._edit_point).pack(side="left", padx=2)
        ttk.Button(point_ops, text="Remove", width=7, command=self._remove_point).pack(side="right")
        self.layer_count = tk.StringVar(value="LAYERS · 0 / 17")
        ttk.Label(frame, textvariable=self.layer_count, font=("Segoe UI Semibold", 10)).pack(anchor="w", pady=(18, 6))
        self.layer_list = tk.Listbox(frame, height=8, exportselection=False, relief="flat", font=("Segoe UI", 9),
                                    selectbackground=BLUE, background="white", highlightthickness=0)
        self.layer_list.pack(fill="both", expand=True)
        self.layer_list.bind("<<ListboxSelect>>", self._on_layer_list)
        layer_ops = ttk.Frame(frame)
        layer_ops.pack(fill="x", pady=(7, 3))
        ttk.Button(layer_ops, text="Add…", width=6, command=self._add_layer).pack(side="left", padx=(0,2))
        ttk.Button(layer_ops, text="Toggle", width=6, command=self._toggle_layer).pack(side="left", padx=2)
        ttk.Button(layer_ops, text="Remove", width=7, command=self._remove_layer).pack(side="right")
        ttk.Label(frame, text="Points retain prime coordinates.\nTransforms change the curve.\nView changes do not change\nthe mathematical state.",
                  style="Muted.TLabel", justify="left").pack(anchor="w", pady=(16, 3))

    def _build_plane(self, frame):
        bar = ttk.Frame(frame)
        bar.pack(fill="x", pady=(2, 8))
        self.curve_label = ttk.Label(bar, text="f(x) = x", font=("Segoe UI Semibold", 11))
        self.curve_label.pack(side="left")
        ttk.Button(bar, text="Reset view", command=self._reset_view).pack(side="right")
        self.canvas = tk.Canvas(frame, background="white", highlightthickness=1, highlightbackground="#dce3ee")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda event: self._schedule_plot())
        self.canvas.bind("<Button-1>", self._canvas_press)
        self.canvas.bind("<B1-Motion>", self._canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", lambda event: self._schedule_plot())
        self.canvas.bind("<MouseWheel>", self._zoom)
        self.canvas.bind("<Button-4>", lambda event: self._zoom(event, 1.15))
        self.canvas.bind("<Button-5>", lambda event: self._zoom(event, 1 / 1.15))
        controls = ttk.Frame(frame)
        controls.pack(fill="x", pady=7)
        self.plane_var = tk.StringVar(value="both")
        self.channel_var = tk.StringVar(value="both")
        self.policy_var = tk.StringVar(value="unique")
        for text, var, values, width in (("Rays", self.plane_var, ("both", "horizontal", "vertical"), 11),
                                         ("Contact", self.channel_var, ("both", "v", "h"), 6),
                                         ("Root", self.policy_var, ("unique", "all", "leftmost", "rightmost", "nearest_contact"), 15)):
            ttk.Label(controls, text=text).pack(side="left", padx=(0, 4))
            combo = ttk.Combobox(controls, textvariable=var, values=values, width=width, state="readonly")
            combo.pack(side="left", padx=(0, 8))
            combo.bind("<<ComboboxSelected>>", lambda event: self._schedule_plot())
        ttk.Label(controls, text="Detail").pack(side="left", padx=(0, 4))
        self.plot_resolution = tk.StringVar(value="160")
        detail = ttk.Combobox(controls, textvariable=self.plot_resolution, values=(80, 160, 320, 640), width=5, state="readonly")
        detail.pack(side="left")
        detail.bind("<<ComboboxSelected>>", lambda event: self._schedule_plot())
        self.point_detail = tk.StringVar(value="Select a blue point · drag to pan · scroll to zoom")
        ttk.Label(frame, textvariable=self.point_detail, style="Muted.TLabel").pack(anchor="w", pady=(0, 7))
        self.trace = ttk.Treeview(frame, columns=("channel", "plane", "direction", "distance", "status"), show="headings", height=6)
        for name, width in (("channel", 60), ("plane", 90), ("direction", 85), ("distance", 155), ("status", 105)):
            self.trace.heading(name, text=name.title())
            self.trace.column(name, width=width, stretch=True)
        self.trace.pack(fill="x")
        self.trace.bind("<<TreeviewSelect>>", self._inspect_measurement)

    def _build_function(self, frame):
        ttk.Label(frame, text="Function and composition", font=("Segoe UI Semibold", 14)).pack(anchor="w")
        ttk.Label(frame, text="Expressions, equations, piecewise curves and unions: sin(x), abs(x), x**2+y**2=1", style="Muted.TLabel").pack(anchor="w", pady=(4, 9))
        self.function_var = self._entry(frame, "Curve(s)", "x")
        self.function_dirty = tk.StringVar(value="Editor · uncommitted until Apply")
        ttk.Label(frame, textvariable=self.function_dirty, style="Muted.TLabel").pack(anchor="w", pady=3)
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(6, 16))
        ttk.Button(buttons, text="Inspect / dry run", command=self._preview_function).pack(side="left")
        ttk.Button(buttons, text="Apply function", style="Accent.TButton", command=self._set_function).pack(side="right")
        transform = ttk.LabelFrame(frame, text="Transform the committed function", padding=10)
        transform.pack(fill="x", pady=4)
        self.dx_var = self._entry(transform, "Horizontal dx", "0")
        self.dy_var = self._entry(transform, "Vertical dy", "0")
        ttk.Button(transform, text="Translate · f(x − dx) + dy", command=lambda: self._transform("translate", dx=self.dx_var.get(), dy=self.dy_var.get())).pack(fill="x", pady=6)
        self.sx_var = self._entry(transform, "Horizontal sx", "1")
        self.sy_var = self._entry(transform, "Vertical sy", "1")
        ttk.Button(transform, text="Scale · sy × f(x / sx)", command=lambda: self._transform("scale", sx=self.sx_var.get(), sy=self.sy_var.get())).pack(fill="x", pady=6)
        reflection = ttk.Frame(transform)
        reflection.pack(fill="x")
        ttk.Button(reflection, text="Reflect x-axis", command=lambda: self._transform("reflect_x")).pack(side="left", expand=True, fill="x")
        ttk.Button(reflection, text="Reflect y-axis", command=lambda: self._transform("reflect_y")).pack(side="left", expand=True, fill="x", padx=(6, 0))
        composition = ttk.LabelFrame(frame, text="Composition · order is explicit", padding=10)
        composition.pack(fill="x", pady=(12, 4))
        self.compose_var = self._entry(composition, "g(x)", "2*x")
        ttk.Button(composition, text="Precompose · f(g(x))", command=lambda: self._transform("precompose", function=self.compose_var.get())).pack(fill="x", pady=4)
        ttk.Button(composition, text="Postcompose · g(f(x))", command=lambda: self._transform("postcompose", function=self.compose_var.get())).pack(fill="x", pady=4)
        self.function_info = self._text(frame, 6, state="disabled")

    def _build_sequence(self, frame):
        ttk.Label(frame, text="Ordered signal sequence", font=("Segoe UI Semibold", 14)).pack(anchor="w")
        ttk.Label(frame, text="Bare v17 and h37 use the default plane (initially horizontal).\nExplicit up/down terms retain their direction. '+' concatenates.", style="Muted.TLabel").pack(anchor="w", pady=(5, 7))
        self.sequence_text = self._text(frame, 4)
        self.sequence_text.insert("1.0", "v17 + v7 + v3 + v23 + h37 + v23")
        self.sequence_plane = self._combo(frame, "Default plane", ("horizontal", "vertical"), "horizontal")
        self.sequence_policy = self._combo(frame, "Root policy", ("unique", "all", "leftmost", "rightmost", "nearest_contact"), "unique")
        self.sequence_match = self._combo(frame, "Match policy", ("all", "unique", "first_ordered"), "all")
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=8)
        ttk.Button(row, text="Resolve preview", command=lambda: self._resolve_sequence(False)).pack(side="left")
        ttk.Button(row, text="Commit sequence", command=lambda: self._resolve_sequence(True), style="Accent.TButton").pack(side="right")
        ttk.Label(frame, text="Trace / exact events / occurrence boundaries", style="Muted.TLabel").pack(anchor="w", pady=(10, 3))
        self.sequence_result = self._text(frame, 19, state="disabled")

    def _build_pixels(self, frame):
        ttk.Label(frame, text="Original pixel alphabet", font=("Segoe UI Semibold", 14)).pack(anchor="w")
        ttk.Label(frame, text="Glyph IDs address original bitmaps. Click individual pixels.\nEvery saved edit creates a new manifest version.", style="Muted.TLabel").pack(anchor="w", pady=(5, 8))
        self.alphabet_info = tk.StringVar(value="")
        ttk.Label(frame, textvariable=self.alphabet_info, style="Muted.TLabel").pack(anchor="w")
        self.glyph_var = tk.StringVar()
        self.glyph_combo = ttk.Combobox(frame, textvariable=self.glyph_var, state="readonly")
        self.glyph_combo.pack(fill="x", pady=8)
        self.glyph_combo.bind("<<ComboboxSelected>>", lambda event: self._load_glyph())
        self.pixel_canvas = tk.Canvas(frame, width=340, height=340, background="white", highlightthickness=0)
        self.pixel_canvas.pack(pady=10)
        self.pixel_canvas.bind("<Button-1>", self._toggle_pixel)
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=5)
        ttk.Button(row, text="Clear pixels", command=self._clear_pixels).pack(side="left")
        ttk.Button(row, text="Commit glyph version", command=self._save_glyph, style="Accent.TButton").pack(side="right")
        ttk.Button(frame, text="Import alphabet manifest JSON…", command=self._import_alphabet).pack(fill="x", pady=8)
        self.pixel_info = self._text(frame, 9, state="disabled")

    def _build_script(self, frame):
        ttk.Label(frame, text="Python operator", font=("Segoe UI Semibold", 14)).pack(anchor="w")
        ttk.Label(frame, text="Trusted local Python · runs only when you press Run.\nPANDR_SESSION identifies this session; this is not a sandbox.", style="Muted.TLabel").pack(anchor="w", pady=(5, 7))
        self.script_id = self._entry(frame, "Script ID", "operator-main")
        self.script_text = self._text(frame, 17)
        self.script_text.insert("1.0", "import os\nfrom pandr import Runtime\n\npr = Runtime.open(os.environ['PANDR_SESSION'])\nplan = pr.plan(expected_revision=pr.revision)\nplan.transform.translate(dx='0', dy='2')\nreceipt = pr.commit(plan)\nprint(receipt)\n")
        self.script_timeout = self._entry(frame, "Timeout seconds", "30")
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=6)
        ttk.Button(buttons, text="Save script", command=self._save_script).pack(side="left")
        ttk.Button(buttons, text="Cancel", command=self._cancel_scripts).pack(side="right")
        ttk.Button(buttons, text="Run Python", style="Accent.TButton", command=self.execute_script).pack(side="right", padx=6)
        ttk.Label(frame, text="Bounded stdout / stderr", style="Muted.TLabel").pack(anchor="w", pady=(9, 0))
        self.console = self._text(frame, 10, state="disabled")

    def _build_signals(self, frame):
        ttk.Label(frame, text="P&R state communications", font=("Segoe UI Semibold", 14)).pack(anchor="w")
        ttk.Label(frame, text="Commit a durable envelope, then deliver the outbox.\nReceiving a payload never executes Python.", style="Muted.TLabel").pack(anchor="w", pady=(5, 8))
        self.destination_var = self._entry(frame, "Destination JSON", "")
        ttk.Button(frame, text="Choose destination…", command=self._choose_destination).pack(anchor="e", pady=3)
        self.payload_type = self._combo(frame, "Payload type", ("rank-stream", "text", "events", "artifact-reference"), "rank-stream")
        ttk.Label(frame, text='Payload JSON · rank-stream uses quoted decimal strings', style="Muted.TLabel").pack(anchor="w", pady=(8, 0))
        self.signal_payload = self._text(frame, 5)
        self.signal_payload.insert("1.0", '["17", "7", "3", "23", "37", "23"]')
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=6)
        ttk.Button(row, text="Enqueue signal", style="Accent.TButton", command=self._send_signal).pack(side="left")
        ttk.Button(row, text="Deliver outbox", command=self._drain).pack(side="right")
        ttk.Label(frame, text="Inbox / outbox / delivery receipts", style="Muted.TLabel").pack(anchor="w", pady=(10, 0))
        self.signal_log = self._text(frame, 17, state="disabled")

    def _build_artifacts(self, frame):
        ttk.Label(frame, text="Deterministic digital generation", font=("Segoe UI Semibold", 14)).pack(anchor="w")
        ttk.Label(frame, text="Text · original glyph PNG · PCM WAV · lossless APNG · code", style="Muted.TLabel").pack(anchor="w", pady=(5, 7))
        self.media_kind = self._combo(frame, "Output kind", ("text", "image", "audio", "video", "code"), "text")
        self.media_kind.trace_add("write", lambda *_: self._media_default())
        ttk.Label(frame, text="Payload JSON (text/code: string; image: glyph IDs; audio/video: ranks)", style="Muted.TLabel").pack(anchor="w", pady=(8, 0))
        self.media_payload = self._text(frame, 3)
        self.media_payload.insert("1.0", '"Hello, P&R!"')
        self.media_parameters = self._entry(frame, "Parameters JSON", "{}")
        ttk.Button(frame, text="Render and commit artifact", style="Accent.TButton", command=self._render).pack(fill="x", pady=7)
        self.artifact_list = ttk.Treeview(frame, columns=("kind", "bytes", "path"), show="headings", height=4)
        for key, width in (("kind", 60), ("bytes", 70), ("path", 280)):
            self.artifact_list.heading(key, text=key.title())
            self.artifact_list.column(key, width=width)
        self.artifact_list.pack(fill="x", pady=5)
        self.artifact_list.bind("<<TreeviewSelect>>", self._preview_artifact)
        self.preview_image = ttk.Label(frame, text="Select an artifact to preview.", anchor="center")
        self.preview_image.pack(fill="both", expand=True, pady=5)
        self.artifact_info = self._text(frame, 6, state="disabled")
        ttk.Button(frame, text="Open selected artifact", command=self._open_artifact).pack(fill="x", pady=3)

    def _build_journal(self, frame):
        ttk.Label(frame, text="Memory and contract receipts", font=("Segoe UI Semibold", 14)).pack(anchor="w")
        self.notes_text = self._text(frame, 7)
        ttk.Button(frame, text="Save operator notes", command=self._save_notes).pack(anchor="e", pady=3)
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=8)
        ttk.Button(row, text="Validate", command=self._validate).pack(side="left")
        ttk.Button(row, text="Reload JSON", command=self._reload).pack(side="left", padx=5)
        ttk.Button(row, text="Inspect domain", command=lambda: self._replace(self.receipt_text, _json(self._snapshot.get("domain", {})))).pack(side="right")
        ttk.Label(frame, text="Immutable receipts / contracts / diagnostics", style="Muted.TLabel").pack(anchor="w")
        self.receipt_text = self._text(frame, 22, state="disabled")

    def _submit(self, title, operation, on_success=None, *, refresh=False) -> Future:
        if self._closed:
            raise RuntimeError("The graphical controller is closed")
        self._busy += 1
        self.status.set(f"{title}…")
        self.progress.start(12)
        future = self._executor.submit(operation)
        self._pending.append(future)

        def completed(done):
            try:
                self._results.put((title, True, done.result(), on_success, refresh, done))
            except Exception as exception:
                self._results.put((title, False, exception, on_success, refresh, done))
        future.add_done_callback(completed)
        return future

    def _poll(self):
        if self._closed:
            return
        while True:
            try:
                item = self._results.get_nowait()
            except queue.Empty:
                break
            if item[0] == "__output__":
                self._append_console(item[1])
                continue
            title, success, result, callback, refresh, done = item
            if done in self._pending:
                self._pending.remove(done)
            self._busy = max(0, self._busy - 1)
            if success:
                self.status.set(f"{title} complete · revision {self.runtime.revision}")
                try:
                    if callback:
                        callback(result)
                    if refresh:
                        self._reload()
                except Exception as exception:
                    self._show_error(title, exception)
            else:
                self._show_error(title, result)
        if not self._busy:
            self.progress.stop()
        self._poll_after = self.root.after(35, self._poll)

    def _show_error(self, action, error):
        text = f"{action}: {type(error).__name__}: {error}"
        self.status.set(text)
        self._append_console(text + "\n")
        self._replace(self.receipt_text, text)

    def _append_console(self, text):
        self.console.configure(state="normal")
        self.console.insert("end", text)
        if int(self.console.index("end-1c").split(".")[0]) > 2500:
            self.console.delete("1.0", "1000.0")
        self.console.see("end")
        self.console.configure(state="disabled")

    def _commit(self, title, build, callback=None):
        runtime = self.runtime
        expected_revision = runtime.revision

        def operation():
            plan = runtime.plan(expected_revision=expected_revision)
            build(plan)
            receipt = runtime.commit(plan)
            return receipt, runtime.snapshot()

        def finished(result):
            receipt, snapshot = result
            self._apply_snapshot(snapshot)
            self._replace(self.receipt_text, _json(receipt))
            if callback:
                callback(receipt)
        return self._submit(title, operation, finished)

    def _apply_snapshot(self, snapshot, load_editors=False):
        self._snapshot = deepcopy(snapshot)
        domain = snapshot.get("domain", {})
        self._points = domain.get("fabric", {}).get("points", [])
        self.session_label.configure(text=f"{Path(self.runtime.path).name}  /  revision {snapshot.get('revision', 0)}")
        self.root.title(f"Point & Resolve · {Path(self.runtime.path).name}")
        from .geometry import Function
        record = domain.get("functions", {}).get("main")
        if record:
            function = Function.from_dict(record)
            from .expressions import Y
            if len(function.components) > 1:
                self.curve_label.configure(text=f"{len(function.components)} combined curves · numerical plot")
            elif function.is_implicit:
                self.curve_label.configure(text=f"{function.display()} = 0 · numerical plot")
            else:
                self.curve_label.configure(text=f"f(x) = {function.display()} · numerical plot")
            self.function_dirty.set(f"Committed revision {snapshot.get('revision', 0)} · editor applied only on command")
            self._replace(self.function_info, _json(record))
            if load_editors:
                self.function_var.set(function.display())
        self._fill_points()
        layers = domain.get("layers", [])
        self.layer_count.set(f"LAYERS · {len(layers)} / 17")
        self.layer_list.delete(0, "end")
        for layer in layers:
            self.layer_list.insert("end", f"{'●' if layer.get('enabled', True) else '○'} {layer.get('id')} · {layer.get('type')}")
        alphabets = domain.get("alphabets", {})
        selected = domain.get("selected_alphabet")
        self._alphabet = deepcopy(alphabets.get(selected) or next(iter(alphabets.values()), None))
        if self._alphabet:
            symbols = self._alphabet.get("symbols", [])
            self.glyph_combo.configure(values=symbols)
            if self.glyph_var.get() not in symbols:
                self.glyph_var.set(symbols[0] if symbols else "")
            self.alphabet_info.set(f"{self._alphabet.get('id')} · version {self._alphabet.get('version')} · {len(symbols)} original glyphs")
            self._load_glyph()
        if load_editors:
            saved_view = snapshot.get("ui", {}).get("viewport", {})
            for key in self._view:
                try:
                    value = float(saved_view.get(key, self._view[key]))
                    if math.isfinite(value):
                        self._view[key] = value
                except (ValueError, TypeError):
                    pass
            self._view["scale"] = min(500, max(0.05, self._view["scale"]))
            self._replace(self.notes_text, str(snapshot.get("memory", {}).get("workspace_notes", "")))
            editors = snapshot.get("ui", {}).get("editors", {})
            if isinstance(editors.get("function"), str):
                self.function_var.set(editors["function"])
            if isinstance(editors.get("sequence"), str):
                self._replace(self.sequence_text, editors["sequence"])
            for key, variable, values in (
                ("sequence_plane", self.sequence_plane, {"horizontal", "vertical"}),
                ("sequence_policy", self.sequence_policy, {"unique", "all", "leftmost", "rightmost", "nearest_contact"}),
                ("sequence_match", self.sequence_match, {"all", "unique", "first_ordered"}),
            ):
                if editors.get(key) in values:
                    variable.set(editors[key])
            programs = snapshot.get("programs", {})
            if isinstance(programs, dict) and programs:
                identifier = editors.get("script_id")
                if identifier not in programs:
                    identifier = next(iter(programs))
                program = programs[identifier]
                if isinstance(program, dict) and "source" in program:
                    self.script_id.set(identifier)
                    self._replace(self.script_text, program["source"])
            self._selected_point = snapshot.get("ui", {}).get("selected_point")
            self._selected_layer = snapshot.get("ui", {}).get("selected_layer")
        if any(point["id"] == self._selected_point for point in self._points):
            self.select_point(self._selected_point)
        else:
            self._selected_point = None
        if any(layer["id"] == self._selected_layer for layer in layers):
            self.select_layer(self._selected_layer)
        else:
            self._selected_layer = None
        self._replace(self.signal_log, _json({key: snapshot.get(key, []) for key in ("inbox", "outbox", "delivery_ledger")}))
        receipts = snapshot.get("receipts", [])
        self._replace(self.receipt_text, _json({"latest_receipts": receipts[-8:] if isinstance(receipts, list) else receipts,
                                              "contracts": domain.get("contracts", {})}))
        self.artifact_list.delete(*self.artifact_list.get_children())
        artifacts = snapshot.get("artifacts", [])
        self._artifacts = list(artifacts.values()) if isinstance(artifacts, dict) else artifacts
        for index, artifact in enumerate(self._artifacts):
            self.artifact_list.insert("", "end", iid=str(index), values=(artifact.get("kind", artifact.get("media_type", "support")),
                                      artifact.get("size", artifact.get("byte_length", artifact.get("bytes", ""))), artifact.get("path", "")))
        self._schedule_plot()

    def _fill_points(self):
        query = self.filter_var.get().lower()
        self._filtered_points = [point for point in self._points if query in f"{point['id']} {point['x']} {point['y']}".lower()]
        self.point_list.delete(0, "end")
        for point in self._filtered_points:
            self.point_list.insert("end", f"{point['id']}  ({point['x']}, {point['y']})")

    def _on_point_list(self, event=None):
        selection = self.point_list.curselection()
        if selection:
            self.select_point(self._filtered_points[selection[0]]["id"])

    def select_point(self, point_id):
        point = next((point for point in self._points if point["id"] == point_id), None)
        if point is None:
            raise ValueError(f"Unknown point: {point_id}")
        self._selected_point = point_id
        self.point_list.selection_clear(0, "end")
        for index, item in enumerate(self._filtered_points):
            if item["id"] == point_id:
                self.point_list.selection_set(index)
                self.point_list.see(index)
                break
        self.point_detail.set(f"{point_id} = ({point['x']}, {point['y']}) · H = ({point['x']}, 0) · V = (0, {point['y']})")
        self._draw()
        self._fill_measurements()

    def _on_layer_list(self, event=None):
        selection = self.layer_list.curselection()
        if selection:
            self.select_layer(self._snapshot["domain"]["layers"][selection[0]]["id"])

    def select_layer(self, layer_id):
        layer = next((layer for layer in self._snapshot["domain"]["layers"] if layer["id"] == layer_id), None)
        if layer is None:
            raise ValueError(f"Unknown layer: {layer_id}")
        self._selected_layer = layer_id
        self.layer_list.selection_clear(0, "end")
        index = next(index for index, item in enumerate(self._snapshot["domain"]["layers"]) if item["id"] == layer_id)
        self.layer_list.selection_set(index)
        self.layer_list.see(index)
        self._replace(self.receipt_text, _json(layer))

    def _add_layer(self):
        name = simpledialog.askstring("Add runtime layer", "Layer type (for example sequence, text, image, audio, video, code):", parent=self.root)
        if name:
            self._commit("Add layer", lambda plan: plan.add_layer(name.strip()))

    def _toggle_layer(self):
        if self._selected_layer:
            layer = next(layer for layer in self._snapshot["domain"]["layers"] if layer["id"] == self._selected_layer)
            identifier, enabled = layer["id"], not layer.get("enabled", True)
            self._commit("Configure layer", lambda plan: plan.configure_layer(identifier, {"enabled": enabled}))

    def _remove_layer(self):
        if self._selected_layer:
            self._commit("Remove layer", lambda plan: plan.remove_layer(self._selected_layer))

    def _add_point(self):
        coord = simpledialog.askstring("Add point", "Prime coordinates (e.g. 5,17):", parent=self.root)
        if not coord: return
        try:
            x_str, y_str = map(str.strip, coord.split(","))
            new_id = f"p{max([int(p['id'][1:]) for p in self._points if p['id'].startswith('p')] + [-1]) + 1}"
            points = self._points + [{"id": new_id, "x": x_str, "y": y_str}]
            self._commit("Add point", lambda plan: plan.set_fabric(points))
        except Exception as exception:
            self._show_error("Add point", exception)

    def _edit_point(self):
        if not self._selected_point: return
        point = next((p for p in self._points if p["id"] == self._selected_point), None)
        if not point: return
        coord = simpledialog.askstring("Edit point", f"Prime coordinates for {point['id']} (e.g. 5,17):", initialvalue=f"{point['x']},{point['y']}", parent=self.root)
        if not coord: return
        try:
            x_str, y_str = map(str.strip, coord.split(","))
            points = [{"id": p["id"], "x": x_str if p["id"] == self._selected_point else p["x"], "y": y_str if p["id"] == self._selected_point else p["y"]} for p in self._points]
            self._commit("Edit point", lambda plan: plan.set_fabric(points))
        except Exception as exception:
            self._show_error("Edit point", exception)

    def _remove_point(self):
        if not self._selected_point: return
        points = [p for p in self._points if p["id"] != self._selected_point]
        self._commit("Remove point", lambda plan: plan.set_fabric(points))

    def _screen(self, x, y):
        return (self.canvas.winfo_width() / 2 + (x - self._view["x"]) * self._view["scale"],
                self.canvas.winfo_height() / 2 - (y - self._view["y"]) * self._view["scale"])

    def _world(self, x, y):
        return (self._view["x"] + (x - self.canvas.winfo_width() / 2) / self._view["scale"],
                self._view["y"] - (y - self.canvas.winfo_height() / 2) / self._view["scale"])

    def _canvas_press(self, event):
        nearest = None
        distance = 100
        for point in self._points:
            sx, sy = self._screen(float(point["x"]), float(point["y"]))
            squared = (event.x - sx) ** 2 + (event.y - sy) ** 2
            if squared < distance:
                nearest, distance = point, squared
        if nearest:
            self.select_point(nearest["id"])
        self._pan_anchor = (event.x, event.y, self._view["x"], self._view["y"])

    def _canvas_drag(self, event):
        if self._pan_anchor:
            x, y, wx, wy = self._pan_anchor
            self._view["x"] = wx - (event.x - x) / self._view["scale"]
            self._view["y"] = wy + (event.y - y) / self._view["scale"]
            self._draw()

    def _zoom(self, event, factor=None):
        old_x, old_y = self._world(event.x, event.y)
        factor = factor or (1.15 if event.delta > 0 else 1 / 1.15)
        self._view["scale"] = min(500, max(0.05, self._view["scale"] * factor))
        new_x, new_y = self._world(event.x, event.y)
        self._view["x"] += old_x - new_x
        self._view["y"] += old_y - new_y
        self._draw()
        self._schedule_plot()

    def _reset_view(self):
        self._view.update(x=0.0, y=0.0, scale=6.0)
        self._schedule_plot()

    def _schedule_plot(self):
        if self._closed:
            return
        self._draw()
        if self._plot_after:
            self.root.after_cancel(self._plot_after)
        self._plot_after = self.root.after(140, self._calculate_plot)

    def _calculate_plot(self):
        self._plot_after = None
        if self._closed or not self._snapshot.get("domain"):
            return
        from .geometry import Function, measurements
        self._plot_serial += 1
        serial = self._plot_serial
        domain = deepcopy(self._snapshot["domain"])
        left, _ = self._world(0, 0)
        right, _ = self._world(self.canvas.winfo_width(), 0)
        _, top = self._world(0, 0)
        _, bottom = self._world(0, self.canvas.winfo_height())
        resolution = int(self.plot_resolution.get())
        plane, channel, policy = self.plane_var.get(), self.channel_var.get(), self.policy_var.get()
        planes = ("horizontal", "vertical") if plane == "both" else (plane,)
        channels = ("v", "h") if channel == "both" else (channel,)

        def calculate():
            function = Function.from_dict(domain["functions"]["main"])
            from .plotting import plot
            sampled = plot(function, x_range=(left, right), y_range=(bottom, top), resolution=resolution)
            results = []
            for ray_plane in planes:
                for ray_channel in channels:
                    group = measurements(function, domain["fabric"]["points"], plane=ray_plane, channel=ray_channel,
                                         policy=policy, window=domain.get("geometry", {}).get("window", ["-257", "257"]))
                    for result in group:
                        result = dict(result)
                        result["plane"], result["channel"] = ray_plane, ray_channel
                        results.append(result)
            return serial, sampled, results

        def calculated(result):
            if result[0] != self._plot_serial:
                return
            self._curve, self._measurements = result[1].points, result[2]
            self._plot_segments = result[1].segments
            self._draw()
            self._fill_measurements()
        self._submit("Resolve plot and contact rays", calculate, calculated)

    def _draw(self):
        canvas = self.canvas
        canvas.delete("all")
        width, height = canvas.winfo_width(), canvas.winfo_height()
        scale = self._view["scale"]
        step = 10 ** math.floor(math.log10(65 / scale))
        if step * scale < 32:
            step *= 5
        x0, ymax = self._world(0, 0)
        xmax, y0 = self._world(width, height)
        for axis in ("x", "y"):
            start, stop = (x0, xmax) if axis == "x" else (y0, ymax)
            first = math.floor(start / step) * step
            count = min(100, int((stop - first) / step) + 1)
            for i in range(count):
                value = first + i * step
                sx, sy = self._screen(value if axis == "x" else 0, value if axis == "y" else 0)
                if axis == "x":
                    canvas.create_line(sx, 0, sx, height, fill=GRID)
                    canvas.create_text(sx + 4, min(height - 13, max(13, sy + 13)), text=f"{value:g}", fill="#8796a9", anchor="w", font=("Segoe UI", 8))
                else:
                    canvas.create_line(0, sy, width, sy, fill=GRID)
                    if value:
                        canvas.create_text(min(width - 30, max(8, sx + 8)), sy - 8, text=f"{value:g}", fill="#8796a9", anchor="w", font=("Segoe UI", 8))
        ox, oy = self._screen(0, 0)
        canvas.create_line(0, oy, width, oy, fill="#26354b", dash=(6, 5), width=1)
        canvas.create_line(ox, 0, ox, height, fill="#26354b", dash=(6, 5), width=1)
        canvas.create_text(width - 16, min(height - 16, max(16, oy - 13)), text="x", fill=INK)
        canvas.create_text(min(width - 16, max(16, ox + 13)), 15, text="y", fill=INK)
        palette = (BLUE, '#e11d48', '#059669', '#9333ea', '#d97706')
        for index, (a, b) in self._plot_segments:
            canvas.create_line(*self._screen(*a), *self._screen(*b), fill=palette[index % len(palette)], width=1.7)
        offscreen = 0
        for point in self._points:
            sx, sy = self._screen(float(point["x"]), float(point["y"]))
            if not (0 <= sx <= width and 0 <= sy <= height):
                offscreen += 1
            selected = point["id"] == self._selected_point
            radius = 5 if selected else 2.8
            canvas.create_oval(sx - radius, sy - radius, sx + radius, sy + radius, fill=BLUE, outline="white" if selected else BLUE, width=2)
            if selected:
                canvas.create_line(sx, sy, sx, oy, fill="#96a8bf", dash=(3, 3))
                canvas.create_line(sx, sy, ox, sy, fill="#96a8bf", dash=(3, 3))
                canvas.create_text(sx + 9, sy - 11, text=point["id"], anchor="w", fill=INK, font=("Segoe UI Semibold", 9))
                canvas.create_text(sx + 5, oy + 15, text="h-contact", anchor="w", fill=MUTED, font=("Segoe UI", 8))
                canvas.create_text(ox + 5, sy + 15, text="v-contact", anchor="w", fill=MUTED, font=("Segoe UI", 8))
        for result in self._measurements:
            if result.get("point_id") != self._selected_point:
                continue
            for event in result.get("events", []):
                try:
                    contact = self._screen(*[_number(n) for n in event["contact"]])
                    endpoint = self._screen(*[_number(n) for n in event["endpoint"]])
                except (KeyError, TypeError, ValueError):
                    continue
                color = RAY_COLORS.get((event.get("channel"), event.get("plane")), BLUE)
                canvas.create_line(*contact, *endpoint, fill=color, width=2.4, arrow="last")
                canvas.create_oval(endpoint[0]-4, endpoint[1]-4, endpoint[0]+4, endpoint[1]+4, fill="white", outline=color, width=2)
        canvas.create_text(12, 12, text=f"contacts-xy-v2 · equal world scale · {offscreen} points outside view", anchor="nw", fill=MUTED, font=("Segoe UI", 8))
        canvas.create_text(12, height - 12, text="Horizontal: v green / h amber     Vertical: v violet / h rose", anchor="sw", fill=MUTED, font=("Segoe UI", 8))

    def _fill_measurements(self):
        self.trace.delete(*self.trace.get_children())
        self._trace_records = []
        for result in self._measurements:
            if self._selected_point and result.get("point_id") != self._selected_point:
                continue
            if not self._selected_point and len(self._trace_records) >= 100:
                break
            events = result.get("events", [])
            for event in events or [None]:
                event = event or {}
                index = len(self._trace_records)
                self._trace_records.append({"measurement": result, "selected_event": event})
                self.trace.insert("", "end", iid=str(index), values=(event.get("channel", result.get("channel", "")),
                                  event.get("plane", result.get("plane", "")), event.get("direction", "—"),
                                  _exact(event["magnitude"]) if "magnitude" in event else "—", result.get("status", "")))

    def _inspect_measurement(self, event=None):
        selection = self.trace.selection()
        if selection:
            self._replace(self.sequence_result, _json(self._trace_records[int(selection[0])]))
            self.tabs.select(1)

    def _preview_function(self):
        expression = self.function_var.get()
        runtime = self.runtime
        expected_revision = runtime.revision
        def preview():
            from .geometry import Function
            function = Function.parse(expression)
            if hasattr(runtime, "dry_run"):
                plan = runtime.plan(expected_revision=expected_revision)
                plan.set_function(function)
                return runtime.dry_run(plan)
            return {"preview_only": True, "function": function.to_dict(),
                    "components": len(function.components), "plotting": "numerical"}
        self._submit("Inspect function candidate", preview, lambda result: self._replace(self.function_info, _json(result)))

    def _set_function(self):
        expression = self.function_var.get()
        self._commit("Apply function", lambda plan: plan.set_function(expression))

    def _transform(self, name, **parameters):
        def apply(plan):
            from .geometry import Function
            if "function" in parameters:
                getattr(plan.transform, name)(Function.parse(parameters["function"]))
            else:
                getattr(plan.transform, name)(**parameters)
        self._commit(f"Function {name}", apply)

    def _resolve_sequence(self, commit):
        source = self.sequence_text.get("1.0", "end-1c")
        try:
            from .sequences import parse_sequence
            selectors = parse_sequence(source)
            # Only undecorated terms inherit the editor's default; an explicit
            # h-up or v-horizontal instruction must keep its authored plane.
            terms = re.findall(r"[vh](?:-[a-z]+)?[0-9]+", source)
            for term, selector in zip(terms, selectors):
                if re.fullmatch(r"[vh](?:-distance)?[0-9]+", term):
                    selector["plane"] = self.sequence_plane.get()
        except Exception as exception:
            self._show_error("Sequence syntax", exception)
            return None
        options = {"root_policy": self.sequence_policy.get(), "match_policy": self.sequence_match.get()}
        if commit:
            return self._commit("Commit sequence", lambda plan: plan.resolve(selectors, **options), lambda result: self._replace(self.sequence_result, _json(result)))
        else:
            runtime = self.runtime
            return self._submit("Resolve sequence", lambda: runtime.resolve(selectors, **options), lambda result: self._replace(self.sequence_result, _json(result)))

    def _load_glyph(self):
        if not self._alphabet:
            return
        glyph = self.glyph_var.get()
        self._glyph_pixels = [list(row) for row in self._alphabet.get("glyphs", {}).get(glyph, [])]
        self._draw_pixels()
        self._replace(self.pixel_info, _json({"glyph_id": glyph, "pixels": ["".join(row) for row in self._glyph_pixels], "alphabet_digest": self._alphabet.get("digest")}))

    def _draw_pixels(self):
        canvas = self.pixel_canvas
        canvas.delete("all")
        if not self._glyph_pixels:
            return
        height, width = len(self._glyph_pixels), len(self._glyph_pixels[0])
        cell = min(320 / width, 320 / height)
        self._pixel_cell = cell
        for y, row in enumerate(self._glyph_pixels):
            for x, value in enumerate(row):
                canvas.create_rectangle(10 + x*cell, 10+y*cell, 10+(x+1)*cell, 10+(y+1)*cell,
                                        fill=INK if value == "1" else "white", outline="#dce4ee")

    def _toggle_pixel(self, event):
        if not self._glyph_pixels:
            return
        x, y = int((event.x - 10) // self._pixel_cell), int((event.y - 10) // self._pixel_cell)
        if 0 <= y < len(self._glyph_pixels) and 0 <= x < len(self._glyph_pixels[y]):
            self._glyph_pixels[y][x] = "0" if self._glyph_pixels[y][x] == "1" else "1"
            self._draw_pixels()

    def _clear_pixels(self):
        self._glyph_pixels = [["0"] * len(row) for row in self._glyph_pixels]
        self._draw_pixels()

    def _save_glyph(self):
        from .alphabets import edit_glyph
        try:
            manifest = edit_glyph(self._alphabet, self.glyph_var.get(), ["".join(row) for row in self._glyph_pixels])
        except Exception as exception:
            self._show_error("Edit glyph", exception)
            return
        self._commit("Version original glyph", lambda plan: plan.define_alphabet(manifest))

    def _import_alphabet(self):
        path = filedialog.askopenfilename(parent=self.root, filetypes=[("Alphabet JSON", "*.json")])
        if path:
            def import_manifest(plan):
                from .canonical import strict_json
                plan.define_alphabet(strict_json(Path(path).read_text(encoding="utf-8")))
            self._commit("Import alphabet", import_manifest)

    def set_code(self, text):
        self._replace(self.script_text, text)

    def _save_script(self):
        identifier, source = self.script_id.get(), self.script_text.get("1.0", "end-1c")
        return self._commit("Save operator script", lambda plan: plan.save_script(identifier, source))

    def execute_script(self, script_id=None):
        if script_id is not None:
            self.script_id.set(script_id)
        source = self.script_text.get("1.0", "end-1c")
        identifier = self.script_id.get()
        try:
            timeout = float(self.script_timeout.get())
        except ValueError as exception:
            self._show_error("Run Python", exception)
            return None
        runtime, path = self.runtime, Path(self.runtime.path)
        self._replace(self.console, "")
        def run():
            plan = runtime.plan()
            plan.save_script(identifier, source)
            runtime.commit(plan)
            if hasattr(runtime, "execute_script"):
                return runtime.execute_script(identifier, timeout=timeout,
                    on_output=lambda chunk: self._results.put(("__output__", chunk)))
            result = self.execution.run_script(source, path, path.parent, timeout=timeout,
                on_output=lambda chunk: self._results.put(("__output__", chunk)))
            if hasattr(runtime, "record_execution"):
                runtime.record_execution(result, identifier)
            return result
        def complete(result):
            self._append_console(f"\n[{result.get('status')}; return code {result.get('returncode')} · {result.get('duration_ms')} ms]\n")
            self._replace(self.receipt_text, _json(result))
        return self._submit("Run trusted Python", run, complete, refresh=True)

    def _cancel_scripts(self):
        self.execution.cancel_all()
        if hasattr(self.runtime, "cancel_scripts"):
            self.runtime.cancel_scripts()

    def run_automation(self, callback):
        """Run a trusted callback in the worker; return an acknowledged Future."""
        runtime = self.runtime
        return self._submit("Operator automation", lambda: callback(runtime), refresh=True)

    def _choose_destination(self):
        path = filedialog.askopenfilename(parent=self.root, filetypes=[("P&R session", "*.json")])
        if path:
            self.destination_var.set(path)

    def _send_signal(self):
        try:
            payload = json.loads(self.signal_payload.get("1.0", "end-1c"))
        except ValueError as exception:
            self._show_error("Signal JSON", exception)
            return
        destination, payload_type = self.destination_var.get(), self.payload_type.get()
        self._commit("Enqueue signal", lambda plan: plan.send(destination, payload, payload_type=payload_type))

    def _drain(self):
        runtime = self.runtime
        self._submit("Deliver outbox", runtime.drain_outbox, lambda result: self._replace(self.signal_log, _json(result)), refresh=True)

    def _media_default(self):
        if not hasattr(self, "media_payload"):
            return
        kind = self.media_kind.get()
        defaults = {"text": "Hello, P&R!", "code": "print('Hello from P&R')\n", "audio": [17, 7, 3, 23, 37, 23],
                    "video": [17, 7, 3, 23, 37, 23], "image": (self._alphabet or {}).get("symbols", [])[:8]}
        self._replace(self.media_payload, _json(defaults[kind]))
        self.media_parameters.set('{"language": "python"}' if kind == "code" else "{}")

    def _render(self):
        try:
            payload = json.loads(self.media_payload.get("1.0", "end-1c"))
            parameters = json.loads(self.media_parameters.get())
            if not isinstance(parameters, dict):
                raise ValueError("Renderer parameters must be a JSON object")
        except ValueError as exception:
            self._show_error("Render JSON", exception)
            return
        kind = self.media_kind.get()
        def render(plan):
            if not any(layer["type"] == kind for layer in plan.snapshot["domain"]["layers"]):
                plan.add_layer(kind)
            plan.render(kind, payload, parameters)
        return self._commit(f"Render {kind}", render)

    def _preview_artifact(self, event=None):
        selection = self.artifact_list.selection()
        if not selection:
            return
        artifact = self._artifacts[int(selection[0])]
        path = (Path(self.runtime.path).parent / artifact.get("path", "")).resolve()
        root = Path(self.runtime.path).parent.resolve()
        if not path.is_relative_to(root) or not path.is_file():
            self._show_error("Artifact preview", ValueError("Artifact is missing or outside the session directory"))
            return
        self._artifact_path = path
        self.preview_image.configure(image="", text="")
        self._photo = None
        detail = _json(artifact)
        try:
            if path.suffix.lower() in (".png", ".apng", ".gif"):
                try:
                    from PIL import Image, ImageTk
                    with Image.open(path) as source:
                        source.thumbnail((470, 270))
                        self._photo = ImageTk.PhotoImage(source.copy(), master=self.root)
                except ImportError:
                    photo = tk.PhotoImage(file=str(path), master=self.root)
                    divisor = max(1, math.ceil(photo.width() / 470), math.ceil(photo.height() / 270))
                    self._photo = photo.subsample(divisor)
                self.preview_image.configure(image=self._photo, text="")
            elif path.suffix.lower() == ".wav":
                with wave.open(str(path), "rb") as audio:
                    text = f"PCM audio · {audio.getframerate()} Hz · {audio.getnchannels()} channel(s)\n{audio.getnframes()} samples · {audio.getnframes()/audio.getframerate():.3f} seconds"
                self.preview_image.configure(text=text)
            else:
                with path.open("rb") as handle:
                    text = handle.read(65536).decode("utf-8", errors="replace")
                self.preview_image.configure(text="UTF-8 artifact preview below")
                detail = text + "\n\n" + detail
        except Exception as exception:
            detail += f"\nPreview unavailable: {exception}"
        self._replace(self.artifact_info, detail)

    def _open_artifact(self):
        if self._artifact_path:
            if os.name == "nt":
                os.startfile(str(self._artifact_path))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(self._artifact_path)], shell=False)
            else:
                subprocess.Popen(["xdg-open", str(self._artifact_path)], shell=False)

    def _save_notes(self):
        text = self.notes_text.get("1.0", "end-1c")
        self._commit("Save operator notes", lambda plan: plan.set_notes(text))

    def _view_record(self):
        return {"viewport": {key: str(value) for key, value in self._view.items()},
                "selected_point": self._selected_point, "selected_layer": self._selected_layer,
                "editors": {"function": self.function_var.get(),
                            "sequence": self.sequence_text.get("1.0", "end-1c"),
                            "sequence_plane": self.sequence_plane.get(),
                            "sequence_policy": self.sequence_policy.get(),
                            "sequence_match": self.sequence_match.get(),
                            "script_id": self.script_id.get()}}

    def _save_editors(self):
        runtime = self.runtime
        view = self._view_record()
        notes = self.notes_text.get("1.0", "end-1c")
        identifier, source = self.script_id.get(), self.script_text.get("1.0", "end-1c")
        def save():
            plan = runtime.plan()
            plan.set_notes(notes)
            plan.save_script(identifier, source)
            receipt = runtime.commit(plan)
            runtime.save_ui(view)
            return receipt, runtime.snapshot()
        self._submit("Save view and editors", save, lambda result: self._apply_snapshot(result[1]))

    def _reload(self):
        from .runtime import Runtime
        path = self.runtime.path
        def loaded(runtime):
            self.runtime = runtime
            self._apply_snapshot(runtime.snapshot())
        return self._submit("Reload session", lambda: Runtime.open(path), loaded)

    def _validate(self):
        runtime = self.runtime
        self._submit("Validate session", runtime.validate, lambda result: self._replace(self.receipt_text, _json(result)))

    def _new(self):
        path = filedialog.asksaveasfilename(parent=self.root, title="Create P&R session", defaultextension=".json", filetypes=[("P&R session", "*.json")])
        if path:
            from .runtime import Runtime
            self._submit("Create session", lambda: Runtime.create(path), self._switch_runtime)

    def _open(self):
        path = filedialog.askopenfilename(parent=self.root, filetypes=[("P&R session", "*.json")])
        if path:
            from .runtime import Runtime
            self._submit("Open session", lambda: Runtime.open(path), self._switch_runtime)

    def _switch_runtime(self, runtime):
        self.runtime = runtime
        self._selected_point = None
        self._selected_layer = None
        self._curve, self._measurements = [], []
        self._plot_serial += 1
        self._apply_snapshot(runtime.snapshot(), load_editors=True)

    def _save_as(self):
        path = filedialog.asksaveasfilename(parent=self.root, defaultextension=".json", filetypes=[("P&R session", "*.json")])
        if path:
            runtime = self.runtime
            self._submit("Save session as", lambda: runtime.save_as(path), self._switch_runtime)

    def _export(self):
        path = filedialog.asksaveasfilename(parent=self.root, defaultextension=".zip", filetypes=[("Portable P&R bundle", "*.zip")])
        if path:
            runtime = self.runtime
            self._submit("Export portable bundle", lambda: runtime.export_bundle(path))

    def _replay(self):
        path = filedialog.asksaveasfilename(parent=self.root, title="Replay into a fresh session", defaultextension=".json", filetypes=[("P&R session", "*.json")])
        if path:
            runtime = self.runtime
            self._submit("Replay deterministic transitions", lambda: runtime.replay(path), lambda result: self._replace(self.receipt_text, _json(result)))

    def close(self):
        if self._closed or self._closing:
            return
        self._closing = True
        self._cancel_scripts()
        for future in self._pending:
            future.cancel()
        self._closed = True
        for after in (self._poll_after, self._plot_after):
            if after:
                try:
                    self.root.after_cancel(after)
                except tk.TclError:
                    pass
        view = self._view_record()
        runtime = self.runtime
        # Saving appearance remains queued behind an already-committing operation;
        # it never bypasses repository locking or creates Tk calls in a worker.
        self._executor.submit(lambda: runtime.save_ui(view))
        self._executor.shutdown(wait=False, cancel_futures=False)
        self.root.destroy()


def launch(session_path: str | Path) -> None:
    from .runtime import Runtime
    path = Path(session_path).resolve()
    runtime = Runtime.open(path) if path.exists() else Runtime.create(path)
    root = tk.Tk()
    PandRApp(root, runtime)
    root.mainloop()


__all__ = ["PandRApp", "launch"]
