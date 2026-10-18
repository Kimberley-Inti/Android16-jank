"""ADB transport, structured log parsing, and reproducible scene correlation.

The device decides Jank. This module never classifies a frame from its duration.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
import sys
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable


TAGS = ("MUST_JANK_SESSION", "MUST_SCENE", "MUST_JANK", "MUST_JANK_STATS")
LINE = re.compile(r"[VDIWEF]/(MUST_[A-Z_]+)\s*\(\s*\d+\):\s*(.*)$")
FIELD = re.compile(r'([a-zA-Z_][a-zA-Z_0-9]*)=(?:"([^"\\]*(?:\\.[^"\\]*)*)"|(\S+))')
PRIORITY = {
    "APP_LAUNCH": 100, "APP_SWITCH": 95, "CONFIG_CHANGE": 90,
    "SYSTEM_UI": 85, "IME": 80, "WINDOW_TRANSITION": 75,
    "SCROLL_FLING": 70, "SCROLL_TOUCH": 65, "GESTURE": 60,
    "ANIMATION": 50, "PASSIVE_UPDATE": 20, "UNKNOWN": 0,
}
NO_CONSOLE = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
JANK_BITS = ((0x1, "DisplayHAL"), (0x2, "SF CPU deadline"),
             (0x4, "SF GPU deadline"), (0x8, "App deadline"),
             (0x10, "PredictionError"), (0x20, "SFScheduling"),
             (0x40, "BufferStuffing"), (0x80, "Unknown"),
             (0x100, "SFStuffing"), (0x200, "Dropped"))


def parse_line(line: str) -> tuple[str, dict] | None:
    match = LINE.search(line)
    if not match or match.group(1) not in TAGS:
        return None
    fields = {m.group(1): m.group(2) if m.group(2) is not None else m.group(3)
              for m in FIELD.finditer(match.group(2))}
    return match.group(1), fields


def number(value: str | None, default: int = 0) -> int:
    try:
        return int(value or "")
    except ValueError:
        return default


def jank_type_label(value: str | None) -> str:
    bits = number(value)
    return ", ".join(label for bit, label in JANK_BITS if bits & bit) or "None"


def deadline_category(data: dict) -> str:
    """Use the device's explicit decision, never the presence of a jank label."""
    value = str(data.get("deadline_jank", "")).strip().lower()
    if value in ("1", "true"):
        return "deadline"
    if value in ("0", "false"):
        return "auxiliary"
    return "unknown"


def scene_label(scene: str) -> str:
    return {"UNKNOWN": "未识别具体交互（缺少场景证据）",
            "APP_LAUNCH": "应用启动", "APP_SWITCH": "应用切换",
            "CONFIG_CHANGE": "屏幕 / 配置变化", "SYSTEM_UI": "系统界面交互",
            "IME": "输入法交互", "WINDOW_TRANSITION": "窗口过渡",
            "SCROLL_TOUCH": "手指拖动（触摸位移推断）",
            "SCROLL_FLING": "惯性滑动请求（速度推断）",
            "GESTURE": "手势交互", "ANIMATION": "动画",
            "PASSIVE_UPDATE": "被动更新"}.get(scene, scene)


def event_scene_label(data: dict) -> str:
    scene = str(data.get("primary_scene", "UNKNOWN"))
    if scene not in ("UNKNOWN", "", "unknown"):
        return scene_label(scene)
    layer = str(data.get("layer", "")).lower()
    packages = data.get("owner_packages", [])
    if not isinstance(packages, list):
        packages = []
    owner = " ".join(str(package) for package in packages).lower()
    if any(token in layer for token in ("inputmethod", "imecontainer", "softkeyboard")):
        return "输入法图层更新（交互未识别，图层推断）"
    if "taskbar" in layer:
        return "任务栏图层更新（交互未识别，图层推断）"
    if any(token in layer for token in ("notificationshade", "statusbar", "navigationbar")):
        return "系统界面图层更新（交互未识别，图层推断）"
    if "launcher" in layer or "com.android.launcher3" in owner:
        return "桌面图层更新（交互未识别，归属推断）"
    if "com.android.systemui" in owner:
        return "系统界面图层更新（交互未识别，归属推断）"
    if layer or packages:
        return "应用图层更新（具体交互未识别）"
    return scene_label("UNKNOWN")


SEVERITY_LEVELS = ("low", "medium", "high", "ungraded")
SEVERITY_METHOD = "presentation_lateness_refresh_periods_v1"


def classify_severity(data: dict) -> dict:
    """Presentation-lateness display grade, applied only AFTER device Jank detection."""
    result = dict(jank_level="not_applicable", severity_method=SEVERITY_METHOD,
                  severity_ratio=None, severity_lateness_ns=None, severity_period_ns=None)
    if deadline_category(data) != "deadline":
        return result
    result["jank_level"] = "ungraded"
    try:
        rate = float(data.get("refresh_rate", ""))
        if not math.isfinite(rate) or rate <= 0:
            return result
        expected = int(data.get("expected_present_ns", 0))
        actual = int(data.get("actual_present_ns", 0))
        lateness = actual - expected if expected > 0 and actual > 0 else int(data["delay_ns"])
        if lateness <= 0:
            return result
    except (TypeError, ValueError, KeyError, OverflowError):
        return result
    period = 1e9 / rate
    ratio = lateness * rate / 1e9
    if not math.isfinite(period) or not math.isfinite(ratio) or ratio <= 0:
        return result
    result.update(jank_level="low" if ratio < 1 else "medium" if ratio < 3 else "high",
                  severity_ratio=ratio, severity_lateness_ns=lateness, severity_period_ns=period)
    return result


@dataclass
class SeverityStats:
    counts: Counter = field(default_factory=Counter)
    combinations: dict = field(default_factory=lambda: {key: Counter() for key in SEVERITY_LEVELS})
    flags: dict = field(default_factory=lambda: {key: Counter() for key in SEVERITY_LEVELS})

    def add(self, data: dict) -> dict:
        grade = classify_severity(data)
        level = grade["jank_level"]
        if level not in SEVERITY_LEVELS:
            return grade
        self.counts[level] += 1
        try:
            mask = int(str(data["jank_type"]), 0)
        except (KeyError, ValueError, TypeError):
            mask = None
        combo = str(mask) if mask is not None else "unknown"
        self.combinations[level][combo] += 1
        if mask is None or mask < 0:
            self.flags[level]["无法解析的类型值"] += 1
        else:
            known_mask = 0
            for bit, label in JANK_BITS:
                known_mask |= bit
                if mask & bit:
                    self.flags[level][label] += 1
            if mask & ~known_mask:
                self.flags[level][f"未映射位 0x{mask & ~known_mask:x}"] += 1
        return grade

    def summary(self, elapsed_s: float) -> dict:
        def rate(count):
            return count * 60 / elapsed_s if elapsed_s > 0 else None
        levels = {}
        for level in SEVERITY_LEVELS:
            count = self.counts[level]
            def distribution(counter):
                return {key: dict(rank=index, count=value, per_minute=rate(value),
                                  share_in_level=value / count if count else None)
                        for index, (key, value) in enumerate(
                            sorted(counter.items(), key=lambda item: (-item[1], item[0])), 1)}
            levels[level] = dict(count=count, per_minute=rate(count),
                                 combinations=distribution(self.combinations[level]),
                                 flags=distribution(self.flags[level]))
        return dict(method=SEVERITY_METHOD, scope="locally_received_deadline_events",
                    elapsed_s=elapsed_s,
                    ranking_order="count_descending_then_type_name",
                    thresholds_periods=dict(low="0 < ratio < 1", medium="1 <= ratio < 3",
                                            high="ratio >= 3"), levels=levels)


@dataclass
class Correlator:
    """Retain complete scene intervals; preserve overlapping secondary scenes."""

    scenes: dict[str, dict] = field(default_factory=dict)

    def add_scene(self, event: dict) -> None:
        key = f"{event.get('session')}:{event.get('scene_id')}"
        if event.get("event") == "BEGIN":
            self.scenes[key] = dict(event)
        elif event.get("event") == "END" and key in self.scenes:
            self.scenes[key]["end_ns"] = number(event.get("ts_ns"))

    def correlate(self, jank: dict) -> dict:
        timestamp = number(jank.get("ts_ns"))
        session = jank.get("session")
        active = [scene for scene in self.scenes.values()
                  if scene.get("session") == session
                  and number(scene.get("ts_ns")) <= timestamp
                  and ("end_ns" not in scene or timestamp <= scene["end_ns"])]
        owner_uid = jank.get("uid")
        active.sort(key=lambda scene: (
            PRIORITY.get(scene.get("scene", "UNKNOWN"), 0),
            scene.get("owner_uid") == owner_uid and owner_uid is not None,
            number(scene.get("ts_ns"))), reverse=True)
        result = dict(jank)
        result["primary_scene"] = active[0].get("scene") if active else "UNKNOWN"
        result["secondary_scenes"] = [scene.get("scene") for scene in active[1:]]
        result["scene_ids"] = [scene.get("scene_id") for scene in active]
        result["scene_confidence"] = active[0].get("confidence") if active else "LOW"
        return result


class Adb:
    def __init__(self, serial: str = "") -> None:
        self.serial = serial

    def command(self, *args: str, timeout: int = 15) -> str:
        command = ["adb"] + (["-s", self.serial] if self.serial else []) + list(args)
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=timeout, check=False, creationflags=NO_CONSOLE)
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout).strip())
        return result.stdout.strip()

    @staticmethod
    def devices() -> list[str]:
        result = subprocess.run(["adb", "devices"], capture_output=True,
                                text=True, timeout=10, check=True, creationflags=NO_CONSOLE)
        return [line.split()[0] for line in result.stdout.splitlines()[1:]
                if len(line.split()) >= 2 and line.split()[1] == "device"]

    def package_map(self) -> dict[str, list[str]]:
        """Best-effort UID mapping; shared UIDs deliberately retain all packages."""
        mapping: dict[str, list[str]] = {}
        try:
            output = self.command("shell", "cmd", "package", "list", "packages", "-U")
        except (OSError, RuntimeError, subprocess.SubprocessError):
            return mapping
        for line in output.splitlines():
            match = re.search(r"package:(\S+)\s+uid:(\d+)", line)
            if match:
                mapping.setdefault(match.group(2), []).append(match.group(1))
        return mapping


class Collector:
    def __init__(self, adb: Adb, root: Path, emit: Callable[[str, dict], None],
                 model: str = "unknown", packages: dict[str, list[str]] | None = None) -> None:
        self.adb = adb
        self.root = root
        self.emit = emit
        self.process: subprocess.Popen | None = None
        self.thread: threading.Thread | None = None
        self.correlator = Correlator()
        self._event_lock = threading.RLock()
        self._pending_events: list[tuple[str, dict]] = []
        self._seen_event_ids: set[tuple[str, str]] = set()
        self.severity_stats = SeverityStats()
        self.observation_start = time.monotonic()
        self.observation_end: float | None = None
        self.packages = packages or {}
        self.session: dict = {"state": "STARTING", "device_serial": adb.serial,
                              "device_model": model,
                              "start_time_wall": datetime.now().astimezone().isoformat(),
                              "session_id": None, "start_ts_ns": None,
                              "stop_time_wall": None, "stop_ts_ns": None,
                              "total_frames": 0, "valid_frames": 0,
                              "janky_frames": 0, "total_jank": 0,
                              "total_jank_scope": "received_deadline_events",
                              "received_label_events": 0, "auxiliary_label_events": 0,
                              "unknown_deadline_events": 0,
                              "ignored_foreign_events": 0, "duplicate_events": 0,
                              "events_without_event_id": 0, "pending_events_dropped": 0}
        root.mkdir(parents=True, exist_ok=False)
        for name in ("raw.log", "jank.jsonl", "scenes.jsonl",
                     "correlated_jank.jsonl", "stats.jsonl"):
            (root / name).touch()
        self._save_session()

    def _save_session(self) -> None:
        with self._event_lock:
            elapsed = max(0, (self.observation_end if self.observation_end is not None
                              else time.monotonic()) - self.observation_start)
            summary = self.severity_stats.summary(elapsed)
            summary.update(session_id=self.session["session_id"],
                           device_serial=self.session["device_serial"])
            self.session["severity_counts"] = dict(self.severity_stats.counts)
            self.session["severity_method"] = SEVERITY_METHOD
            (self.root / "severity_summary.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            (self.root / "session.json").write_text(
                json.dumps(self.session, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _append(self, name: str, row: dict) -> None:
        with (self.root / name).open("a", encoding="utf-8") as file:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")

    def start_reader(self) -> None:
        command = ["adb", "-s", self.adb.serial, "logcat", "-v", "brief", "-T", "1",
                   "-s"] + [f"{tag}:I" for tag in TAGS]
        self.process = subprocess.Popen(command, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True,
                                        bufsize=1, creationflags=NO_CONSOLE)
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self) -> None:
        assert self.process and self.process.stdout
        try:
            with (self.root / "raw.log").open("a", encoding="utf-8") as raw:
                for line in self.process.stdout:
                    raw.write(line)
                    parsed = parse_line(line)
                    if parsed:
                        self._event(*parsed)
        finally:
            self.emit("READER_STOPPED", {})

    def bind_session(self, session_id: str) -> None:
        """Bind to the command response before replaying initial logcat records."""
        if not session_id or session_id in ("none", "unknown"):
            raise ValueError("设备没有返回有效 session ID")
        with self._event_lock:
            self.session["session_id"] = session_id
            pending, self._pending_events = self._pending_events, []
            for tag, data in pending:
                self._event(tag, data)
            self._save_session()

    def _event(self, tag: str, data: dict) -> None:
        with self._event_lock:
            if self.session["session_id"] is None:
                if len(self._pending_events) >= 4096:
                    self._pending_events.pop(0)
                    self.session["pending_events_dropped"] += 1
                self._pending_events.append((tag, dict(data)))
                return
            if data.get("session") != self.session["session_id"]:
                self.session["ignored_foreign_events"] += 1
                return
            if tag == "MUST_JANK":
                event_id = data.get("event_id")
                if event_id is not None:
                    key = (str(data["session"]), str(event_id))
                    if key in self._seen_event_ids:
                        self.session["duplicate_events"] += 1
                        return
                    self._seen_event_ids.add(key)
                else:
                    self.session["events_without_event_id"] += 1
            self._accepted_event(tag, data)

    def _accepted_event(self, tag: str, data: dict) -> None:
        if tag == "MUST_JANK_SESSION":
            if data.get("event") == "START":
                self.session.update(session_id=data.get("session"),
                                    start_ts_ns=number(data.get("ts_ns")), state="RUNNING")
            elif data.get("event") == "STOP":
                self.observation_end = time.monotonic()
                self.session.update(stop_ts_ns=number(data.get("ts_ns")),
                                    stop_time_wall=datetime.now().astimezone().isoformat(),
                                    state="STOPPED")
            self._save_session()
        elif tag == "MUST_SCENE":
            self.correlator.add_scene(data)
            self._append("scenes.jsonl", data)
        elif tag == "MUST_JANK":
            self._append("jank.jsonl", data)
            correlated = self.correlator.correlate(data)
            correlated["owner_packages"] = self.packages.get(data.get("uid", ""), [])
            correlated["owner_package_confidence"] = (
                "HIGH" if len(correlated["owner_packages"]) == 1 else "UNKNOWN")
            correlated.update(self.severity_stats.add(correlated))
            correlated["scene_display"] = event_scene_label(correlated)
            self._append("correlated_jank.jsonl", correlated)
            self.session["received_label_events"] += 1
            category = deadline_category(data)
            if category == "deadline":
                self.session["total_jank"] += 1
            elif category == "auxiliary":
                self.session["auxiliary_label_events"] += 1
            else:
                self.session["unknown_deadline_events"] += 1
            data = correlated
        elif tag == "MUST_JANK_STATS":
            self._append("stats.jsonl", data)
            self.session["total_frames"] = number(data.get("total_frames"),
                                                   self.session["total_frames"])
            self.session["valid_frames"] = number(data.get("valid_frames"),
                                                   self.session["valid_frames"])
            self.session["janky_frames"] = number(data.get("janky_frames"),
                                                   self.session["janky_frames"])
            self._save_session()
        self.emit(tag, data)

    def close(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
        if self.thread:
            self.thread.join(timeout=3)
        if self.observation_end is None:
            self.observation_end = time.monotonic()
        if self.session["state"] != "STOPPED":
            self.session.update(state="PARTIAL",
                                stop_time_wall=datetime.now().astimezone().isoformat())
        self._save_session()
