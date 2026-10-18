"""Minimal live monitor GUI; Android remains the authority for Jank decisions."""

from __future__ import annotations

import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from collections import Counter, deque
from pathlib import Path
from tkinter import messagebox, ttk

from monitor_core import (Adb, Collector, JANK_BITS, PRIORITY, jank_type_label, number,
                          deadline_category, scene_label, event_scene_label, SeverityStats,
                          classify_severity, SEVERITY_LEVELS)

ROOT = Path(__file__).resolve().parent
MAX_VISIBLE_EVENTS = 600
EVENT_SCOPES = {"Deadline Jank": "deadline", "辅助标签": "auxiliary",
                "判定缺失": "unknown", "全部标签": "all"}


class MonitorWindow(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("MUST Jank Monitor")
        self.geometry("1240x820")
        self.minsize(980, 650)
        self.configure(bg="#f3f6fb")
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Treeview", rowheight=30, font=("Microsoft YaHei UI", 9))
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("TButton", padding=(12, 7))

        self.events: queue.Queue[tuple] = queue.Queue()
        self.collector: Collector | None = None
        self.selected_serial = ""
        self.running = self.starting = self.stopping = False
        self.device_poll_pending = False
        self.close_after_stop = False
        self.session_start_ns = 0
        self.started_at: float | None = None
        self.final_elapsed: float | None = None
        self.total_frames = self.valid_frames = self.device_jank_frames = 0
        self.received_jank_events = self.event_counter = 0
        self.event_categories: Counter[str] = Counter()
        self.recent_events: deque[dict] = deque(maxlen=MAX_VISIBLE_EVENTS)
        self.scoped_type_counts = {key: Counter() for key in ("deadline", "auxiliary", "unknown")}
        self.scoped_flag_counts = {key: Counter() for key in ("deadline", "auxiliary", "unknown")}
        self.jank_type_counts: Counter[str] = Counter()
        self.jank_flag_counts: Counter[str] = Counter()
        self.active_scenes: dict[str, dict] = {}
        self.data_dir: Path | None = None
        self.severity_stats = SeverityStats()
        self.last_severity_render = 0.0
        self.frequency_elapsed_final = None

        self.scope_var = tk.StringVar(value="Deadline Jank")
        self.level_filter_var = tk.StringVar(value="全部等级")
        self.device_var = tk.StringVar()
        self.adb_var = tk.StringVar(value="ADB：检测中")
        self.state_var = tk.StringVar(value="未开始")
        self.session_var = tk.StringVar(value="尚无监测会话")
        self.app_var = tk.StringVar(value="—")
        self.scene_var = tk.StringVar(value="等待场景数据")
        self.elapsed_var = tk.StringVar(value="00:00:00")
        self.frames_var = tk.StringVar(value="0 / 0")
        self.jank_var = tk.StringVar(value="0")
        self.rate_var = tk.StringVar(value="—")
        self.event_count_var = tk.StringVar(value="本地接收：Deadline 0 · 辅助标签 0 · 判定缺失 0")
        self.message_var = tk.StringVar(value="连接 OnePlus 11 并授权 USB 调试，然后开始监测。")

        self._build_ui()
        self._refresh_devices()
        self.after(100, self._drain_events)
        self.after(250, self._tick_elapsed)
        self.after(4000, self._periodic_device_refresh)
        self.protocol("WM_DELETE_WINDOW", self._close_window)

    def _build_ui(self) -> None:
        style = ttk.Style(self)
        style.configure("Main.TNotebook.Tab", padding=(18, 9))
        self.tabs = ttk.Notebook(self, style="Main.TNotebook")
        self.tabs.pack(fill="both", expand=True, padx=10, pady=8)
        self.monitor_page = tk.Frame(self.tabs, bg="#f3f6fb", padx=16, pady=12)
        self.statistics_page = tk.Frame(self.tabs, bg="#f3f6fb", padx=16, pady=12)
        self.tabs.add(self.monitor_page, text="Deadline Jank / 监测与事件")
        self.tabs.add(self.statistics_page, text="Jank type 统计与比较")
        page = self.monitor_page
        header = tk.Frame(page, bg="#f3f6fb")
        header.pack(fill="x", pady=(0, 14))
        tk.Label(header, text="MUST Jank Monitor", bg="#f3f6fb", fg="#14243b",
                 font=("Microsoft YaHei UI", 22, "bold")).pack(side="left")
        tk.Label(header, text="LineageOS 23.2 · 场景感知实时监测", bg="#f3f6fb",
                 fg="#61728a", font=("Microsoft YaHei UI", 10)).pack(side="left", padx=16, pady=(9, 0))
        tk.Label(header, textvariable=self.state_var, bg="#f3f6fb", fg="#147a99",
                 font=("Microsoft YaHei UI", 11, "bold")).pack(side="right", pady=(8, 0))

        controls = tk.Frame(page, bg="#e8edf5", padx=12, pady=10)
        controls.pack(fill="x", pady=(0, 12))
        tk.Label(controls, text="设备", bg="#e8edf5", fg="#34445b",
                 font=("Microsoft YaHei UI", 9, "bold")).pack(side="left")
        self.device_box = ttk.Combobox(controls, textvariable=self.device_var, state="readonly", width=28)
        self.device_box.pack(side="left", padx=(8, 8))
        self.refresh_button = ttk.Button(controls, text="刷新设备", command=self._refresh_devices)
        self.refresh_button.pack(side="left")
        ttk.Label(controls, textvariable=self.adb_var, background="#e8edf5",
                  foreground="#53647b").pack(side="left", padx=16)
        self.start_button = ttk.Button(controls, text="开始监测", command=self.start_monitor)
        self.start_button.pack(side="left", padx=(8, 5))
        self.stop_button = ttk.Button(controls, text="停止监测", command=self.stop_monitor, state="disabled")
        self.stop_button.pack(side="left", padx=5)

        context = tk.Frame(page, bg="#ffffff", padx=16, pady=13,
                           highlightthickness=1, highlightbackground="#e1e7ef")
        context.pack(fill="x", pady=(0, 12))
        self._context(context, "当前应用", self.app_var, 0)
        self._context(context, "当前交互 / 最近帧上下文", self.scene_var, 1)
        self._context(context, "运行时长（接回时为接回后）", self.elapsed_var, 2)
        tk.Label(context, textvariable=self.session_var, bg="#ffffff", fg="#62728a",
                 font=("Microsoft YaHei UI", 9)).grid(row=1, column=3, sticky="e", padx=(14, 0))
        for column in range(3):
            context.grid_columnconfigure(column, weight=1)

        metrics = tk.Frame(page, bg="#f3f6fb")
        metrics.pack(fill="x", pady=(0, 12))
        self._metric(metrics, "SurfaceFrame（有效 / 总计·全会话）", self.frames_var, "#e9f2f8", "#26749a")
        self._metric(metrics, "Deadline Jank 帧（设备全会话）", self.jank_var, "#fdebea", "#cf4444")
        self._metric(metrics, "Deadline Jank 比例（设备全会话）", self.rate_var, "#fff4df", "#bd8012")

        tk.Label(page, text="设备指标覆盖整个会话、所有应用/图层，不是手势或页面切换次数。下方本地事件从接入时开始；一个屏幕帧可能包含多个 SurfaceFrame。",
                 bg="#f3f6fb", fg="#53647b", wraplength=1150, justify="left").pack(anchor="w", pady=(0, 4))
        scope_bar = tk.Frame(page, bg="#f3f6fb")
        scope_bar.pack(fill="x", pady=(0, 8))
        tk.Label(scope_bar, text="列表 / 原始标签范围", bg="#f3f6fb").pack(side="left")
        scope_box = ttk.Combobox(scope_bar, textvariable=self.scope_var,
                                 values=tuple(EVENT_SCOPES), state="readonly", width=18)
        scope_box.pack(side="left", padx=8)
        scope_box.bind("<<ComboboxSelected>>", self._change_scope)
        tk.Label(scope_bar, text="事件等级", bg="#f3f6fb").pack(side="left")
        level_box = ttk.Combobox(scope_bar, textvariable=self.level_filter_var,
                                 values=("全部等级", "low", "medium", "high", "未分级"),
                                 state="readonly", width=12)
        level_box.pack(side="left", padx=8)
        level_box.bind("<<ComboboxSelected>>", self._change_scope)
        tk.Label(scope_bar, text="等级仅用于 Deadline Jank；单独 BufferStuffing 不计入。",
                 bg="#f3f6fb", fg="#61728a").pack(side="left")

        events_page = tk.Frame(page, bg="#f3f6fb", padx=4, pady=4)
        events_page.pack(fill="both", expand=True)
        types_page = self.statistics_page
        stats_header = tk.Frame(types_page, bg="#f3f6fb")
        stats_header.pack(fill="x", pady=(0, 10))
        tk.Label(stats_header, text="Jank type 统计与比较", bg="#f3f6fb", fg="#14243b",
                 font=("Microsoft YaHei UI", 18, "bold")).pack(side="left")
        ttk.Button(stats_header, text="返回监测", command=lambda: self.tabs.select(self.monitor_page)).pack(side="right")
        ttk.Button(stats_header, text="打开会话目录", command=self.open_session_folder).pack(side="right", padx=8)
        tk.Label(stats_header, textvariable=self.state_var, bg="#f3f6fb", fg="#147a99").pack(side="right", padx=12)
        tk.Label(types_page, textvariable=self.session_var, bg="#f3f6fb", fg="#61728a",
                 anchor="w").pack(fill="x", pady=(0, 6))
        type_views = ttk.Notebook(types_page)
        self.type_views = type_views
        type_views.pack(fill="both", expand=True)
        severity_page = tk.Frame(type_views, bg="#f3f6fb", padx=5, pady=5)
        raw_page = tk.Frame(type_views, bg="#f3f6fb", padx=5, pady=5)
        type_views.add(severity_page, text="low / medium / high 对比")
        type_views.add(raw_page, text="原始标签分布")
        self._build_severity_panel(severity_page)
        types_page = raw_page
        raw_controls = tk.Frame(raw_page, bg="#f3f6fb")
        raw_controls.pack(fill="x", pady=(0, 10))
        tk.Label(raw_controls, text="原始标签范围", bg="#f3f6fb").pack(side="left")
        raw_scope = ttk.Combobox(raw_controls, textvariable=self.scope_var,
                                 values=tuple(EVENT_SCOPES), state="readonly", width=20)
        raw_scope.pack(side="left", padx=8)
        raw_scope.bind("<<ComboboxSelected>>", self._change_scope)

        event_header = tk.Frame(events_page, bg="#f3f6fb")
        event_header.pack(fill="x", pady=(2, 7))
        tk.Label(event_header, text="最近帧标签", bg="#f3f6fb", fg="#14243b",
                 font=("Microsoft YaHei UI", 13, "bold")).pack(side="left")
        tk.Label(event_header, textvariable=self.event_count_var, bg="#f3f6fb", fg="#61728a",
                 font=("Microsoft YaHei UI", 9)).pack(side="left", padx=12)
        ttk.Button(event_header, text="清空视图", command=self.clear_view).pack(side="right", padx=(6, 0))
        ttk.Button(event_header, text="打开会话目录", command=self.open_session_folder).pack(side="right")

        table_frame = tk.Frame(events_page, bg="#ffffff")
        table_frame.pack(fill="both", expand=True)
        columns = ("time", "kind", "level", "owner", "scene", "jank_type", "delay")
        self.event_table = ttk.Treeview(table_frame, columns=columns, show="headings", height=14)
        for key, title, width in (
            ("time", "发生时间", 120), ("kind", "设备判定", 130),
            ("level", "程度 / 延迟周期数", 170), ("owner", "Jank owner（PID / UID / Layer）", 350),
            ("scene", "关联场景", 250), ("jank_type", "Android Jank type（原值 / 标志）", 330),
            ("delay", "时间偏差（ms）", 130),
        ):
            self.event_table.heading(key, text=title)
            self.event_table.column(key, width=width,
                                    anchor="w" if key in ("owner", "scene", "jank_type") else "center")
        for level, color in (("low", "#d9f0df"), ("medium", "#fff0cc"),
                             ("high", "#f9d6d6"), ("ungraded", "#eceff3")):
            self.event_table.tag_configure(level, background=color)
        ybar = ttk.Scrollbar(table_frame, orient="vertical", command=self.event_table.yview)
        xbar = ttk.Scrollbar(events_page, orient="horizontal", command=self.event_table.xview)
        self.event_table.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        self.event_table.pack(side="left", fill="both", expand=True)
        ybar.pack(side="right", fill="y")
        xbar.pack(fill="x")

        tk.Label(types_page, text="Android 上报的 jank_type 原始值组合统计与拆分比较",
                 bg="#f3f6fb", fg="#14243b", font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w")
        tk.Label(types_page,
                 text="范围由本页选择器决定，默认仅 deadline_jank=1。辅助标签保留但不计入 Deadline Jank。组合值互斥，拆分标志可重叠；标签不是根因结论。",
                 bg="#f3f6fb", fg="#61728a", wraplength=1120, justify="left").pack(anchor="w", pady=(5, 10))
        stats_body = tk.Frame(types_page, bg="#f3f6fb")
        stats_body.pack(fill="both", expand=True)
        exact_box = tk.LabelFrame(stats_body, text="原始 jank_type 组合（互斥分布）", bg="#f3f6fb", padx=8, pady=8)
        exact_box.pack(side="left", fill="both", expand=True, padx=(0, 6))
        flag_box = tk.LabelFrame(stats_body, text="标志位命中（可重叠比较）", bg="#f3f6fb", padx=8, pady=8)
        flag_box.pack(side="left", fill="both", expand=True, padx=(6, 0))
        self.exact_table = ttk.Treeview(exact_box, columns=("value", "meaning", "count", "share"), show="headings")
        for key, title, width in (("value", "原始值", 95), ("meaning", "解码标志", 250),
                                  ("count", "次数", 80), ("share", "占所选范围", 120)):
            self.exact_table.heading(key, text=title); self.exact_table.column(key, width=width, anchor="w" if key in ("meaning",) else "center")
        self.exact_table.pack(fill="both", expand=True)
        self.flag_table = ttk.Treeview(flag_box, columns=("flag", "count", "share", "compare"), show="headings")
        for key, title, width in (("flag", "标志位", 200), ("count", "命中次数", 90),
                                  ("share", "占所选范围", 120), ("compare", "相对比较", 220)):
            self.flag_table.heading(key, text=title); self.flag_table.column(key, width=width, anchor="w" if key in ("flag",) else "center")
        self.flag_table.pack(fill="both", expand=True)
        self.type_total_var = tk.StringVar(value="当前范围：Deadline Jank · 0 条")
        tk.Label(types_page, textvariable=self.type_total_var, bg="#f3f6fb", fg="#53647b").pack(anchor="w", pady=(8, 0))

        footer = tk.Frame(page, bg="#f3f6fb", pady=8)
        footer.pack(fill="x")
        tk.Label(footer, textvariable=self.message_var, bg="#f3f6fb", fg="#596b83",
                 anchor="w", font=("Microsoft YaHei UI", 9)).pack(fill="x")

    def _build_severity_panel(self, parent: tk.Widget) -> None:
        tk.Label(parent, text="呈现延迟分级（本地收到的 Deadline Jank）", bg="#f3f6fb",
                 fg="#14243b", font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w")
        tk.Label(parent, text="T = 1 / 当帧刷新率；low：0 < 延迟 < 1T；medium：1T ≤ 延迟 < 3T；high：延迟 ≥ 3T。缺时间/刷新率时未分级。此为展示规则，不是 Android 原生严重度或根因。",
                 bg="#f3f6fb", fg="#61728a", wraplength=1080, justify="left").pack(anchor="w", pady=4)
        self.severity_count_var = tk.StringVar(value="low 0 · medium 0 · high 0 · 未分级 0")
        tk.Label(parent, textvariable=self.severity_count_var, bg="#f3f6fb",
                 font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w", pady=3)
        self.severity_canvas = tk.Canvas(parent, height=82, bg="#ffffff", highlightthickness=0)
        self.severity_canvas.pack(fill="x", pady=3)
        self.severity_canvas.bind("<Configure>", lambda _event: self._render_severity_bars())
        tk.Label(parent, text="每个等级独立排名，列出数量、该级内占比和次/min；频率分母为本地观察时长（含无事件时段），不是设备历史全会话。标志位可重叠；BufferStuffing 仅在伴随 Deadline 时出现在分级统计。",
                 bg="#f3f6fb", fg="#61728a", wraplength=1080, justify="left").pack(anchor="w", pady=3)
        tk.Label(parent, text="每个等级独立按数量从多到少排列；数量相同按类型名称排序。",
                 bg="#f3f6fb", fg="#61728a").pack(anchor="w", pady=(0, 3))
        rankings = ttk.Notebook(parent)
        rankings.pack(fill="both", expand=True)
        self.severity_rank_tables = {"flags": {}, "combinations": {}}
        for key, title in (("flags", "Jank type 标签排名（可重叠）"),
                           ("combinations", "原始组合排名（互斥）")):
            page = tk.Frame(rankings, bg="#f3f6fb")
            rankings.add(page, text=title)
            page.grid_rowconfigure(0, weight=1)
            for column, level in enumerate(("low", "medium", "high")):
                page.grid_columnconfigure(column, weight=1, uniform="severity")
                box = tk.LabelFrame(page, text=level, padx=4, pady=4, bg="#ffffff")
                box.grid(row=0, column=column, sticky="nsew", padx=3, pady=3)
                self.severity_rank_tables[key][level] = self._make_ranking_table(box)
        page = tk.Frame(rankings, bg="#f3f6fb")
        rankings.add(page, text="未分级")
        page.grid_rowconfigure(0, weight=1)
        for column, (key, title) in enumerate((("flags", "未分级标签"), ("combinations", "未分级组合"))):
            page.grid_columnconfigure(column, weight=1, uniform="ungraded")
            box = tk.LabelFrame(page, text=title, padx=4, pady=4, bg="#ffffff")
            box.grid(row=0, column=column, sticky="nsew", padx=3, pady=3)
            self.severity_rank_tables[key]["ungraded"] = self._make_ranking_table(box)

    @staticmethod
    def _make_ranking_table(parent: tk.Widget) -> ttk.Treeview:
        table = ttk.Treeview(parent, columns=("rank", "type", "count", "share", "rate"),
                             show="headings", height=7)
        for column, title, width in (("rank", "排名", 35), ("type", "Jank type", 130),
                                     ("count", "数量", 45), ("share", "级内占比", 65),
                                     ("rate", "次/min", 65)):
            table.heading(column, text=title)
            table.column(column, width=width, minwidth=35,
                         stretch=column == "type", anchor="w" if column == "type" else "center")
        ybar = ttk.Scrollbar(parent, orient="vertical", command=table.yview)
        xbar = ttk.Scrollbar(parent, orient="horizontal", command=table.xview)
        table.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        parent.grid_rowconfigure(0, weight=1)
        parent.grid_columnconfigure(0, weight=1)
        table.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        return table

    def _render_severity_bars(self) -> None:
        canvas = self.severity_canvas
        canvas.delete("all")
        maximum = max((self.severity_stats.counts[level] for level in ("low", "medium", "high")), default=0)
        available = max(0, canvas.winfo_width() - 160)
        for index, (level, color) in enumerate((("low", "#49a56c"), ("medium", "#d99623"), ("high", "#d64d4d"))):
            y = 6 + index * 25
            count = self.severity_stats.counts[level]
            canvas.create_text(8, y + 9, text=level, anchor="w")
            width = available * count / maximum if maximum else 0
            canvas.create_rectangle(80, y, 80 + width, y + 18, fill=color, outline="")
            canvas.create_text(88 + width, y + 9, text=str(count), anchor="w")

    def _render_severity_tables(self) -> None:
        self.last_severity_render = time.monotonic()
        self.severity_count_var.set(" · ".join(
            f"{'未分级' if level == 'ungraded' else level} {self.severity_stats.counts[level]:,}"
            for level in SEVERITY_LEVELS))
        if self.collector is not None and hasattr(self.collector, "observation_start"):
            end = self.collector.observation_end
            elapsed = max(0, (end if end is not None else time.monotonic()) - self.collector.observation_start)
        elif self.frequency_elapsed_final is not None:
            elapsed = self.frequency_elapsed_final
        else:
            elapsed = (self.final_elapsed if self.final_elapsed is not None else
                       max(0, time.monotonic() - self.started_at) if self.started_at is not None else 0)
        summary = self.severity_stats.summary(elapsed)["levels"]
        for key, level_tables in self.severity_rank_tables.items():
            for level, table in level_tables.items():
                for row in table.get_children():
                    table.delete(row)
                # summary() ranks each grade independently by descending count.
                for name, item in summary[level][key].items():
                    label = (f"{name} · {jank_type_label(name)}" if key == "combinations" else
                             "BufferStuffing（伴随 Deadline）" if name == "BufferStuffing" else name)
                    frequency = item["per_minute"]
                    rate = f"{frequency:.2f}" if frequency is not None else "—"
                    table.insert("", "end", values=(item["rank"], label, f"{item['count']:,}",
                                                     f"{item['share_in_level']:.1%}", rate))
        self._render_severity_bars()

    @staticmethod
    def _context(parent: tk.Widget, title: str, value: tk.StringVar, column: int) -> None:
        item = tk.Frame(parent, bg="#ffffff")
        item.grid(row=0, column=column, sticky="w", padx=(0, 28))
        tk.Label(item, text=title, bg="#ffffff", fg="#718097",
                 font=("Microsoft YaHei UI", 9)).pack(anchor="w")
        tk.Label(item, textvariable=value, bg="#ffffff", fg="#20334d",
                 font=("Microsoft YaHei UI", 11, "bold"), wraplength=320, justify="left").pack(anchor="w", pady=(3, 0))

    @staticmethod
    def _metric(parent: tk.Widget, title: str, value: tk.StringVar,
                background: str, foreground: str) -> None:
        card = tk.Frame(parent, bg=background, padx=16, pady=12,
                        highlightthickness=1, highlightbackground="#e0e7ef")
        card.pack(side="left", fill="x", expand=True, padx=(0, 10))
        tk.Label(card, text=title, bg=background, fg="#586980",
                 font=("Microsoft YaHei UI", 9)).pack(anchor="w")
        tk.Label(card, textvariable=value, bg=background, fg=foreground,
                 font=("Microsoft YaHei UI", 21, "bold")).pack(anchor="w", pady=(4, 0))

    def _refresh_devices(self) -> None:
        if self.device_poll_pending:
            return
        self.device_poll_pending = True
        self.refresh_button.configure(state="disabled")

        def worker() -> None:
            try:
                self.events.put(("devices", Adb.devices(), None))
            except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
                self.events.put(("devices", [], str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def start_monitor(self) -> None:
        if self.starting or self.running or self.stopping:
            return
        serial = self.device_var.get().strip()
        if not serial:
            self.message_var.set("先连接并选择一台 ADB 状态为 device 的手机。")
            return
        self.selected_serial = serial
        self._reset_view()
        self.starting = True
        self.state_var.set("正在检查设备状态…")
        self.start_button.configure(state="disabled")
        self.refresh_button.configure(state="disabled")

        def worker() -> None:
            try:
                adb = Adb(serial)
                status = adb.command("shell", "cmd", "must_jank", "status")
                model = adb.command("shell", "getprop", "ro.product.model") or "unknown model"
                if "state=RUNNING" in status:
                    self.events.put(("already_running", adb, status, model))
                else:
                    self._create_session(adb, model)
            except Exception as exc:
                self.events.put(("error", str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def _create_session(self, adb: Adb, model: str) -> None:
        collector = None
        try:
            folder = ROOT / "sessions" / (
                datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f") + "_" + adb.serial.replace(":", "_"))
            collector = Collector(adb, folder,
                                  lambda tag, data: self.events.put(("log", tag, data)),
                                  model, adb.package_map())
            for field, prop in (("android_build_incremental", "ro.build.version.incremental"),
                                ("android_build_fingerprint", "ro.build.fingerprint")):
                try:
                    collector.session[field] = adb.command("shell", "getprop", prop) or "unknown"
                except (OSError, RuntimeError, subprocess.SubprocessError):
                    collector.session[field] = "unavailable"
            collector._save_session()
            collector.start_reader()
            if not collector.process or collector.process.poll() is not None:
                raise RuntimeError("ADB logcat 没有正常启动。")
            response = adb.command("shell", "cmd", "must_jank", "start")
            if "state=RUNNING" not in response:
                raise RuntimeError(response or "设备没有确认进入 RUNNING 状态。")
            match = re.search(r"\bsession=([^\s]+)", response)
            collector.bind_session(match.group(1) if match else "")
            self.events.put(("started", collector, model, response))
        except Exception as exc:
            if collector:
                collector.close()
            self.events.put(("error", str(exc)))

    def _attach_existing(self, adb: Adb, status: str, model: str) -> None:
        collector = None
        try:
            folder = ROOT / "sessions" / (
                datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f") + "_attached_" + adb.serial.replace(":", "_"))
            collector = Collector(adb, folder,
                                  lambda tag, data: self.events.put(("log", tag, data)),
                                  model, adb.package_map())
            match = re.search(r"\bsession=([^\s]+)", status)
            collector.session.update(
                state="RUNNING", session_id=match.group(1) if match else "unknown",
                start_time_wall=None, start_ts_ns=None, attached=True,
                attached_at=datetime.now().astimezone().isoformat())
            collector.bind_session(match.group(1) if match else "")
            collector.start_reader()
            try:
                stats = adb.command("shell", "cmd", "must_jank", "stats")
                fields = dict(re.findall(r"([A-Za-z_][A-Za-z0-9_]*)=([^\s]+)", stats))
                collector.session.update({key: number(fields.get(key), 0)
                                          for key in ("total_frames", "valid_frames", "janky_frames")})
            except (OSError, RuntimeError, subprocess.SubprocessError):
                pass
            collector._save_session()
            self.events.put(("attached", collector, model, status))
        except Exception as exc:
            if collector:
                collector.close()
            self.events.put(("error", f"连接设备上的现有会话失败：{exc}"))

    def stop_monitor(self) -> None:
        if self.stopping:
            return
        if not self.running or not self.collector:
            serial = self.selected_serial or self.device_var.get().strip()
            if serial and messagebox.askyesno("设备仍在监测", "设备端会话仍在运行。要发送 STOP 并结束它吗？"):
                self.stopping = True
                self.state_var.set("正在停止…")
                threading.Thread(target=self._stop_device_only, args=(serial,), daemon=True).start()
            return
        self.stopping = True
        self.state_var.set("正在停止…")
        self.stop_button.configure(state="disabled")
        collector = self.collector

        def worker() -> None:
            try:
                response = collector.adb.command("shell", "cmd", "must_jank", "stop")
                if "state=STOPPED" not in response:
                    raise RuntimeError(response or "设备没有确认 STOPPED 状态。")
                self.events.put(("stopping", response))
            except Exception as exc:
                self.events.put(("stop_error", str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def _stop_device_only(self, serial: str) -> None:
        try:
            response = Adb(serial).command("shell", "cmd", "must_jank", "stop")
            self.events.put(("device_stopped", response))
        except Exception as exc:
            self.events.put(("stop_error", str(exc)))

    def _reset_view(self) -> None:
        self.session_start_ns = 0
        self.started_at = self.final_elapsed = None
        self.total_frames = self.valid_frames = self.device_jank_frames = 0
        self.received_jank_events = 0
        self.event_categories.clear()
        self.severity_stats = SeverityStats()
        self.last_severity_render = 0.0
        self.frequency_elapsed_final = None
        self._render_severity_tables()
        self.recent_events.clear()
        for counts in (*self.scoped_type_counts.values(), *self.scoped_flag_counts.values()):
            counts.clear()
        self.jank_type_counts.clear()
        self.jank_flag_counts.clear()
        self._render_jank_types()
        self.active_scenes.clear()
        self.session_var.set("等待设备会话事件")
        self.app_var.set("—")
        self.scene_var.set("等待场景数据")
        self.elapsed_var.set("00:00:00")
        self.event_count_var.set("本地接收：Deadline 0 · 辅助标签 0 · 判定缺失 0")
        for row in self.event_table.get_children():
            self.event_table.delete(row)
        self._render_stats()

    def _on_log(self, tag: str, data: dict) -> None:
        if tag == "MUST_JANK_SESSION":
            if data.get("event") == "START":
                self.session_start_ns = number(data.get("ts_ns"))
                self.started_at = time.monotonic()
                self.final_elapsed = None
                self.session_var.set("会话：" + str(data.get("session", "unknown")))
            elif data.get("event") == "STOP" and self.started_at is not None:
                self.final_elapsed = time.monotonic() - self.started_at
                self.state_var.set("正在保存最终统计…")
        elif tag == "MUST_SCENE":
            event = data.get("event")
            if event == "CONTEXT":
                self.app_var.set(str(data.get("foreground") or data.get("package") or "unknown"))
            elif event == "BEGIN":
                self.active_scenes[str(data.get("scene_id", ""))] = data
                self.app_var.set(str(data.get("target_package") or data.get("to_package")
                                     or data.get("to") or data.get("package") or self.app_var.get()))
            elif event == "END":
                self.active_scenes.pop(str(data.get("scene_id", "")), None)
            scene = max(self.active_scenes.values(),
                        key=lambda row: PRIORITY.get(str(row.get("scene", "UNKNOWN")), 0), default=None)
            self.scene_var.set(scene_label(str(scene.get("scene", "UNKNOWN")))
                               if scene else "暂无已识别交互")
        elif tag == "MUST_JANK":
            data = {**data, **self.severity_stats.add(data)}
            if not self.active_scenes:
                self.scene_var.set("最近帧：" + event_scene_label(data))
            self.received_jank_events += 1
            category = deadline_category(data)
            self.event_categories[category] += 1
            self.event_count_var.set(
                f"本地接收：Deadline {self.event_categories['deadline']:,} · "
                f"辅助标签 {self.event_categories['auxiliary']:,} · "
                f"判定缺失 {self.event_categories['unknown']:,}")
            self.recent_events.append(dict(data))
            raw_type = str(data.get("jank_type", "unknown")).strip()
            try:
                mask = int(raw_type, 0)
            except ValueError:
                try:
                    mask = int(raw_type, 10)
                except ValueError:
                    mask = None
            combo = str(mask) if mask is not None else "unknown"
            self.jank_type_counts[combo] += 1
            self.scoped_type_counts[category][combo] += 1
            if mask is None:
                self.jank_flag_counts["无法解析的类型值"] += 1
                self.scoped_flag_counts[category]["无法解析的类型值"] += 1
            else:
                known_mask = 0
                for bit, label in JANK_BITS:
                    known_mask |= bit
                    if mask & bit:
                        self.jank_flag_counts[label] += 1
                        self.scoped_flag_counts[category][label] += 1
                unmapped = mask & ~known_mask
                if unmapped:
                    self.jank_flag_counts[f"未映射位 0x{unmapped:x}"] += 1
                    self.scoped_flag_counts[category][f"未映射位 0x{unmapped:x}"] += 1
            self._render_jank_types()
            self._append_event(data)
        elif tag == "MUST_JANK_STATS":
            self.total_frames = number(data.get("total_frames"), self.total_frames)
            self.valid_frames = number(data.get("valid_frames"), self.valid_frames)
            self.device_jank_frames = number(data.get("janky_frames"), self.device_jank_frames)
            self._render_stats()

    def _change_scope(self, _event=None) -> None:
        self._render_jank_types()
        for row in self.event_table.get_children():
            self.event_table.delete(row)
        for data in self.recent_events:
            self._append_event(data)

    def _append_event(self, data: dict) -> None:
        category = deadline_category(data)
        scope = EVENT_SCOPES[self.scope_var.get()]
        if scope != "all" and category != scope:
            return
        kind = {"deadline": "Deadline Jank", "auxiliary": "辅助标签", "unknown": "判定缺失"}[category]
        grade = classify_severity(data)
        level = grade["jank_level"]
        selected_level = self.level_filter_var.get()
        if selected_level == "未分级":
            selected_level = "ungraded"
        if selected_level != "全部等级" and level != selected_level:
            return
        ratio = grade["severity_ratio"]
        level_text = "未分级" if level == "ungraded" else ("—" if level == "not_applicable" else level)
        if ratio is not None:
            level_text += f" / {ratio:.2f}T"
        timestamp = number(data.get("ts_ns"))
        relative = max(0.0, (timestamp - self.session_start_ns) / 1e9) if self.session_start_ns else None
        time_text = f"+{relative:07.3f}s" if relative is not None else "—"
        packages = data.get("owner_packages", [])
        owner = packages[0] if isinstance(packages, list) and len(packages) == 1 else (
            f"UID {data.get('uid', '?')} · PID {data.get('pid', '?')}")
        if data.get("layer"):
            owner += "\n" + str(data["layer"])
        scene = event_scene_label(data)
        secondary = data.get("secondary_scenes", [])
        if isinstance(secondary, list) and secondary:
            scene += " + " + ", ".join(scene_label(str(value)) for value in secondary)
        raw_type = str(data.get("jank_type", "unknown"))
        label = jank_type_label(raw_type)
        deviation = number(data.get("delay_ns")) / 1e6
        self.event_counter += 1
        row_id = f"event-{self.event_counter}"
        self.event_table.insert("", "end", iid=row_id, values=(
            time_text, kind, level_text, owner, scene, f"{raw_type} · {label}", f"{deviation:.2f}"),
            tags=(level,))
        rows = self.event_table.get_children()
        for old in rows[:max(0, len(rows) - MAX_VISIBLE_EVENTS)]:
            self.event_table.delete(old)
        self.event_table.yview_moveto(1.0)

    def _render_jank_types(self) -> None:
        for table in (self.exact_table, self.flag_table):
            for row in table.get_children():
                table.delete(row)
        scope = EVENT_SCOPES[self.scope_var.get()]
        total = self.received_jank_events if scope == "all" else self.event_categories[scope]
        type_counts = self.jank_type_counts if scope == "all" else self.scoped_type_counts[scope]
        flag_counts = self.jank_flag_counts if scope == "all" else self.scoped_flag_counts[scope]
        self.type_total_var.set(f"当前范围：{self.scope_var.get()} · {total:,} 条（本地接收）")
        if not total:
            return
        for value, count in sorted(type_counts.items(), key=lambda item: (-item[1], item[0])):
            label = "无法解析" if value == "unknown" else jank_type_label(value)
            self.exact_table.insert("", "end", values=(value, label, f"{count:,}", f"{count / total:.1%}"))
        max_count = max(flag_counts.values(), default=1)
        for label, count in sorted(flag_counts.items(), key=lambda item: (-item[1], item[0])):
            width = 18
            bars = "\u2588" * max(1, round(width * count / max_count))
            self.flag_table.insert("", "end", values=(label, f"{count:,}", f"{count / total:.1%}", bars))

    def _render_stats(self) -> None:
        self.frames_var.set(f"{self.valid_frames:,} / {self.total_frames:,}")
        self.jank_var.set(f"{self.device_jank_frames:,}")
        self.rate_var.set(f"{self.device_jank_frames / self.valid_frames:.2%}" if self.valid_frames else "—")

    def _drain_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "devices":
                    self.device_poll_pending = False
                    serials, error = event[1], event[2]
                    previous = self.device_var.get()
                    self.device_box["values"] = serials
                    self.device_var.set(previous if previous in serials else (serials[0] if serials else ""))
                    self.adb_var.set(f"ADB：已连接 {len(serials)} 台设备" if serials else "ADB：未检测到设备")
                    self.refresh_button.configure(state="normal")
                    if error:
                        self.message_var.set(f"无法读取 ADB 设备列表：{error}")
                    elif not serials and not self.running:
                        self.message_var.set("未检测到已授权设备。检查 USB 调试授权后刷新。")
                    if self.running and self.selected_serial not in serials:
                        self.adb_var.set("ADB：监测设备已断开")
                        self.message_var.set("设备连接已断开；重连后可接回设备端现有会话。")
                elif kind == "already_running":
                    self.starting = False
                    adb, status, model = event[1:]
                    self.state_var.set("设备端正在监测")
                    self.start_button.configure(state="normal")
                    self.stop_button.configure(state="normal")
                    self.message_var.set("检测到设备端已有会话，可接回或停止。")
                    if messagebox.askyesno("发现运行中的会话", "设备监测已经在运行。接回这个会话吗？\n\n接回后只保存从现在开始收到的事件。"):
                        self.starting = True
                        self.start_button.configure(state="disabled")
                        threading.Thread(target=self._attach_existing,
                                         args=(adb, status, model), daemon=True).start()
                elif kind in ("started", "attached"):
                    collector, model, info = event[1:]
                    self.collector = collector
                    self.data_dir = collector.root
                    self.selected_serial = collector.adb.serial
                    self.running, self.starting, self.stopping = True, False, False
                    self.state_var.set("实时监测中" if kind == "started" else "已接回设备会话")
                    self.start_button.configure(state="disabled")
                    self.stop_button.configure(state="normal")
                    self.refresh_button.configure(state="normal")
                    self.session_var.set("会话：" + str(collector.session.get("session_id") or "等待设备事件"))
                    if kind == "attached":
                        if not self.active_scenes:
                            self.scene_var.set("已接回：等待新场景（历史交互未记录）")
                        self._on_log("MUST_JANK_STATS", collector.session)
                        self.started_at = time.monotonic()
                        self.message_var.set("已接回现有会话；本地记录从接回时开始，原始起始时间和之前事件未知。")
                    else:
                        self.message_var.set(f"设备：{model} · 会话文件：{collector.root}")
                elif kind == "log":
                    if event[1] == "READER_STOPPED" and self.running:
                        self.adb_var.set("ADB：日志连接已断开")
                        self.message_var.set("logcat 连接中断，本地会话数据可能不完整。")
                    else:
                        self._on_log(event[1], event[2])
                elif kind == "stopping":
                    self.state_var.set("正在接收最终统计…")
                    self.message_var.set("设备已停止，正在保存最后一批日志和统计。")
                    self.after(500, self._finish)
                elif kind == "device_stopped":
                    self.stopping = False
                    self.state_var.set("已停止")
                    self.stop_button.configure(state="disabled")
                    self.message_var.set("设备端监测已停止。")
                elif kind == "stop_error":
                    self.stopping = False
                    self.state_var.set("停止失败")
                    self.stop_button.configure(state="normal")
                    self.message_var.set("停止命令失败：" + str(event[1]))
                elif kind == "error":
                    self.starting = False
                    self.state_var.set("未开始")
                    self.start_button.configure(state="normal")
                    self.refresh_button.configure(state="normal")
                    self.message_var.set("启动失败：" + str(event[1]))
        except queue.Empty:
            pass
        self.after(100, self._drain_events)

    def _finish(self) -> None:
        if self.collector:
            self.collector.close()
            self.frequency_elapsed_final = max(0, self.collector.observation_end - self.collector.observation_start)
        self.collector = None
        self._render_severity_tables()
        self.running = self.starting = self.stopping = False
        self.state_var.set("已停止")
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.refresh_button.configure(state="normal")
        if self.close_after_stop:
            self.destroy()

    def _tick_elapsed(self) -> None:
        if self.started_at is not None:
            value = int(self.final_elapsed if self.final_elapsed is not None else time.monotonic() - self.started_at)
            hours, rem = divmod(value, 3600)
            minutes, seconds = divmod(rem, 60)
            self.elapsed_var.set(f"{hours:02d}:{minutes:02d}:{seconds:02d}")
        if time.monotonic() - self.last_severity_render >= 1:
            self._render_severity_tables()
        self.after(250, self._tick_elapsed)

    def _periodic_device_refresh(self) -> None:
        self._refresh_devices()
        self.after(4000, self._periodic_device_refresh)

    def clear_view(self) -> None:
        self.recent_events.clear()
        for row in self.event_table.get_children():
            self.event_table.delete(row)
        self.message_var.set("已清空屏幕事件列表；会话文件和统计没有删除。")

    def open_session_folder(self) -> None:
        folder = self.data_dir or ROOT / "sessions"
        folder.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform == "win32":
                import os
                os.startfile(str(folder))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except OSError as exc:
            self.message_var.set(f"无法打开会话目录：{exc}")

    def _close_window(self) -> None:
        if self.running:
            if not messagebox.askyesno("正在监测", "退出前先停止设备端监测并保存会话吗？"):
                return
            self.close_after_stop = True
            self.stop_monitor()
            return
        self.destroy()


if __name__ == "__main__":
    MonitorWindow().mainloop()
