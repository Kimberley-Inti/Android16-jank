"""Regression checks for device decisions, session isolation, and GUI counts."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from monitor_core import (Adb, Collector, deadline_category, classify_severity, SeverityStats,
                          event_scene_label)


def frame(event_id="1", session="current", label="8", decision="1"):
    result = dict(session=session, event_id=event_id, ts_ns="1000",
                  jank_type=label, layer="test", uid="123", deadline_jank=decision)
    if decision is None:
        result.pop("deadline_jank")
    return result


class CollectorSemanticsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.emitted = []
        self.collector = Collector(Adb("test"), Path(self.tmp.name) / "session",
                                   lambda *event: self.emitted.append(event))

    def tearDown(self):
        self.collector.close()
        self.tmp.cleanup()

    def test_only_device_deadline_decisions_count(self):
        self.collector.bind_session("current")
        for event in (frame("1", label="64", decision="0"),
                      frame("2", label="72"),
                      frame("3", decision=None)):
            self.collector._event("MUST_JANK", event)
        session = self.collector.session
        self.assertEqual(session["received_label_events"], 3)
        self.assertEqual(session["total_jank"], 1)
        self.assertEqual(session["auxiliary_label_events"], 1)
        self.assertEqual(session["unknown_deadline_events"], 1)
        self.assertEqual(deadline_category(frame(label="8", decision=None)), "unknown")
        self.assertEqual(deadline_category(frame(label="64", decision="0")), "auxiliary")

    def test_foreign_sessions_and_repeated_ids_are_excluded(self):
        self.collector.bind_session("current")
        self.collector._event("MUST_JANK", frame(session="previous"))
        self.collector._event("MUST_JANK", frame())
        self.collector._event("MUST_JANK", frame())
        self.assertEqual(self.collector.session["total_jank"], 1)
        self.assertEqual(self.collector.session["duplicate_events"], 1)
        self.assertEqual(self.collector.session["ignored_foreign_events"], 1)
        self.assertEqual(len(self.emitted), 1)

    def test_binding_replays_only_the_requested_session(self):
        self.collector._event("MUST_JANK", frame(session="previous"))
        self.collector._event("MUST_SCENE", dict(session="current", event="CONTEXT"))
        self.collector._event("MUST_JANK_SESSION", dict(session="current", event="START"))
        self.assertFalse(self.emitted)
        self.collector.bind_session("current")
        self.assertEqual([tag for tag, _ in self.emitted], ["MUST_SCENE", "MUST_JANK_SESSION"])
        self.assertEqual(self.collector.session["state"], "RUNNING")

    def test_touch_scene_does_not_create_jank(self):
        self.collector.bind_session("current")
        self.collector._event("MUST_SCENE", dict(session="current", event="BEGIN",
                                               scene="SCROLL_TOUCH", scene_id="1", ts_ns="1"))
        self.assertEqual(self.collector.session["total_jank"], 0)
        self.assertEqual(self.collector.session["received_label_events"], 0)

    def test_global_stats_stay_distinct_and_are_saved(self):
        self.collector.bind_session("current")
        self.collector._event("MUST_JANK", frame())
        self.collector._event("MUST_JANK_STATS", dict(session="current", total_frames="100",
                                                    valid_frames="90", janky_frames="7"))
        saved = json.loads((self.collector.root / "session.json").read_text())
        self.assertEqual(saved["janky_frames"], 7)
        self.assertEqual(saved["total_jank"], 1)
        self.assertEqual(saved["received_label_events"], 1)

    def test_severity_is_saved_without_changing_device_fields(self):
        self.collector.bind_session("current")
        data = {**frame(label="72"), "refresh_rate": "120", "delay_ns": "25000000",
                "severity": "3"}
        self.collector._event("MUST_JANK", data)
        self.collector._event("MUST_JANK", {**frame("2", label="64", decision="0"),
                                           "refresh_rate": "120", "delay_ns": "999999999"})
        self.collector.close()
        summary = json.loads((self.collector.root / "severity_summary.json").read_text())
        self.assertEqual(summary["levels"]["high"]["count"], 1)
        self.assertEqual(sum(level["count"] for level in summary["levels"].values()), 1)
        rows = [json.loads(line) for line in (self.collector.root / "correlated_jank.jsonl").read_text().splitlines()]
        self.assertEqual(rows[0]["jank_level"], "high")
        self.assertEqual(rows[0]["severity"], "3")
        self.assertEqual(rows[1]["jank_level"], "not_applicable")


class SeveritySemanticsTest(unittest.TestCase):
    def grade(self, delay, rate=120, **overrides):
        return classify_severity({**frame(), "delay_ns": str(delay), "refresh_rate": str(rate), **overrides})

    def test_boundaries_and_refresh_rate(self):
        self.assertEqual(self.grade(5000000)["jank_level"], "low")
        self.assertEqual(self.grade(10000000)["jank_level"], "medium")
        self.assertEqual(self.grade(24999999)["jank_level"], "medium")
        self.assertEqual(self.grade(25000000)["jank_level"], "high")
        self.assertEqual(self.grade(10000000, 60)["jank_level"], "low")
        self.assertEqual(self.grade(50000000, 60)["jank_level"], "high")
        self.assertEqual(self.grade(10000000, 100)["jank_level"], "medium")

    def test_unusable_telemetry_not_fabricated_as_low(self):
        for rate in (0, -1, "nan", "inf", None):
            self.assertEqual(self.grade(50000000, rate)["jank_level"], "ungraded")
        for delay in (0, -1, "unknown"):
            self.assertEqual(self.grade(delay)["jank_level"], "ungraded")

    def test_non_deadline_and_mixed_buffer_stuffing(self):
        self.assertEqual(self.grade(999999999, deadline_jank="0", jank_type="64")["jank_level"], "not_applicable")
        self.assertEqual(self.grade(999999999, deadline_jank="", jank_type="8")["jank_level"], "not_applicable")
        self.assertEqual(self.grade(25000000, jank_type="72")["jank_level"], "high")

    def test_timestamps_preferred_over_inconsistent_delay(self):
        result = self.grade(999999999, expected_present_ns="1000000000", actual_present_ns="1005000000")
        self.assertEqual(result["jank_level"], "low")
        self.assertEqual(result["severity_lateness_ns"], 5000000)

    def test_type_frequency_uses_observation_time_and_preserves_overlap(self):
        stats = SeverityStats()
        for label in ("72", "8"):
            stats.add({**frame(label=label), "delay_ns": "25000000", "refresh_rate": "120"})
        summary = stats.summary(30)["levels"]["high"]
        self.assertEqual(summary["count"], 2)
        self.assertEqual(summary["per_minute"], 4)
        self.assertEqual(summary["flags"]["App deadline"]["count"], 2)
        self.assertEqual(summary["flags"]["BufferStuffing"]["count"], 1)
        self.assertEqual(summary["flags"]["BufferStuffing"]["per_minute"], 2)
        self.assertEqual(summary["flags"]["BufferStuffing"]["share_in_level"], .5)
        self.assertIsNone(stats.summary(0)["levels"]["high"]["per_minute"])

    def test_unknown_scene_gets_evidence_qualified_label(self):
        self.assertIn("系统界面", event_scene_label(dict(primary_scene="UNKNOWN", layer="NotificationShade#1")))
        self.assertIn("桌面", event_scene_label(dict(primary_scene="UNKNOWN", layer="QuickstepLauncher#1")))
        self.assertIn("未识别", event_scene_label(dict(primary_scene="UNKNOWN")))
        self.assertIn("推断", event_scene_label(dict(primary_scene="SCROLL_TOUCH", layer="NotificationShade#1")))

    def test_export_ranks_each_grade_by_count(self):
        stats = SeverityStats()
        for delay, labels in ((5000000, ("8", "8", "2")),
                              (10000000, ("2", "2", "4")),
                              (25000000, ("4", "4", "8"))):
            for label in labels:
                stats.add({**frame(label=label), "delay_ns": str(delay), "refresh_rate": "120"})
        levels = stats.summary(30)["levels"]
        self.assertEqual(list(levels["low"]["flags"]), ["App deadline", "SF CPU deadline"])
        self.assertEqual(list(levels["medium"]["flags"]), ["SF CPU deadline", "SF GPU deadline"])
        self.assertEqual(list(levels["high"]["flags"]), ["SF GPU deadline", "App deadline"])
        for level in ("low", "medium", "high"):
            for key in ("flags", "combinations"):
                values = list(levels[level][key].values())
                self.assertEqual([item["count"] for item in values], [2, 1])
                self.assertEqual([item["rank"] for item in values], [1, 2])


@unittest.skipUnless(os.environ.get("DISPLAY"), "Tk GUI checks require a display")
class GuiSemanticsTest(unittest.TestCase):
    def setUp(self):
        from gui import MonitorWindow
        with patch.object(MonitorWindow, "_refresh_devices"):
            self.window = MonitorWindow()
        self.window.withdraw()

    def tearDown(self):
        self.window.destroy()

    def test_default_filter_and_device_totals(self):
        window = self.window
        window._on_log("MUST_JANK", frame("1", label="64", decision="0"))
        window._on_log("MUST_JANK", frame("2"))
        self.assertEqual(window.received_jank_events, 2)
        self.assertEqual(len(window.event_table.get_children()), 1)
        self.assertIn("1 条", window.type_total_var.get())
        self.assertEqual(window.device_jank_frames, 0)
        window._on_log("MUST_JANK_STATS", dict(total_frames="100", valid_frames="90",
                                              janky_frames="7"))
        self.assertEqual(window.jank_var.get(), "7")
        window.scope_var.set("全部标签")
        window._change_scope()
        self.assertEqual(len(window.event_table.get_children()), 2)
        self.assertIn("2 条", window.type_total_var.get())
        window.scope_var.set("辅助标签")
        window._change_scope()
        self.assertEqual(len(window.event_table.get_children()), 1)
        self.assertEqual(window.jank_var.get(), "7")

    def test_scene_and_missing_decision_are_not_deadline_events(self):
        window = self.window
        window._on_log("MUST_SCENE", dict(event="BEGIN", scene="SCROLL_TOUCH", scene_id="1"))
        self.assertIn("推断", window.scene_var.get())
        self.assertEqual(window.received_jank_events, 0)
        window._on_log("MUST_JANK", frame(decision=None))
        self.assertEqual(window.event_categories["unknown"], 1)
        self.assertEqual(len(window.event_table.get_children()), 0)
        window._reset_view()
        self.assertEqual(sum(window.event_categories.values()), 0)

    def test_attach_shows_existing_device_total_immediately(self):
        window = self.window
        collector = SimpleNamespace(root=Path("/tmp/test-session"), adb=SimpleNamespace(serial="test"),
                                    session=dict(session_id="current", total_frames=100,
                                                 valid_frames=90, janky_frames=7))
        window.events.put(("attached", collector, "test", "session=current state=RUNNING"))
        window._drain_events()
        self.assertEqual(window.jank_var.get(), "7")
        self.assertEqual(window.received_jank_events, 0)

    def test_severity_comparison_and_list_filter(self):
        window = self.window
        window.started_at = __import__("time").monotonic() - 30
        for i, delay in enumerate((5000000, 10000000, 25000000)):
            window._on_log("MUST_JANK", {**frame(str(i)), "delay_ns": str(delay), "refresh_rate": "120"})
        window._on_log("MUST_JANK", frame("missing"))
        window._on_log("MUST_JANK", {**frame("aux", label="64", decision="0"),
                                    "delay_ns": "999999999", "refresh_rate": "120"})
        window._render_severity_tables()
        self.assertEqual(dict(window.severity_stats.counts), dict(low=1, medium=1, high=1, ungraded=1))
        for level in ("low", "medium", "high", "ungraded"):
            table = window.severity_rank_tables["flags"][level]
            self.assertEqual(len(table.get_children()), 1)
            values = table.item(table.get_children()[0], "values")
            self.assertEqual(values[1], "App deadline")
            self.assertEqual(values[2], "1")
            self.assertEqual(values[3], "100.0%")
            self.assertGreater(float(values[4]), 0)
        window.level_filter_var.set("high")
        window._change_scope()
        self.assertEqual(len(window.event_table.get_children()), 1)
        window._reset_view()
        self.assertEqual(sum(window.severity_stats.counts.values()), 0)

    def test_current_context_no_longer_shows_bare_unknown(self):
        window = self.window
        self.assertNotEqual(window.scene_var.get(), "UNKNOWN")
        window._on_log("MUST_JANK", {**frame(), "layer": "NotificationShade#1"})
        self.assertIn("系统界面", window.scene_var.get())
        self.assertIn("最近帧", window.scene_var.get())

    def test_frequency_uses_collector_observation_and_freezes_at_stop(self):
        import time
        window = self.window
        start = time.monotonic() - 60
        window.collector = SimpleNamespace(observation_start=start, observation_end=start + 30,
                                           close=lambda: None)
        window._on_log("MUST_JANK", {**frame(), "delay_ns": "25000000", "refresh_rate": "120"})
        window._render_severity_tables()
        table = window.severity_rank_tables["flags"]["high"]
        before = table.item(table.get_children()[0], "values")
        self.assertEqual(before[4], "2.00")
        window._finish()
        after = table.item(table.get_children()[0], "values")
        self.assertEqual(before, after)

    def test_gui_rankings_are_independent_and_descending(self):
        window = self.window
        window.started_at = __import__("time").monotonic() - 30
        for delay, labels in ((5000000, ("8", "8", "8", "2", "4")),
                              (10000000, ("2", "2", "2", "4", "4")),
                              (25000000, ("4", "4", "4", "8", "8"))):
            for label in labels:
                window._on_log("MUST_JANK", {**frame(label=label),
                                             "delay_ns": str(delay), "refresh_rate": "120"})
        window._render_severity_tables()
        expected = dict(low=["App deadline", "SF CPU deadline", "SF GPU deadline"],
                        medium=["SF CPU deadline", "SF GPU deadline"],
                        high=["SF GPU deadline", "App deadline"])
        for level, names in expected.items():
            table = window.severity_rank_tables["flags"][level]
            rows = [table.item(row, "values") for row in table.get_children()]
            self.assertEqual([row[1] for row in rows], names)
            counts = [int(row[2]) for row in rows]
            self.assertEqual(counts, sorted(counts, reverse=True))
            self.assertEqual([int(row[0]) for row in rows], list(range(1, len(rows) + 1)))
        # An updated count can reorder a single grade without disturbing another.
        for _ in range(4):
            window._on_log("MUST_JANK", {**frame(label="2"), "delay_ns": "5000000", "refresh_rate": "120"})
        window._render_severity_tables()
        low = window.severity_rank_tables["flags"]["low"]
        high = window.severity_rank_tables["flags"]["high"]
        self.assertEqual(low.item(low.get_children()[0], "values")[1], "SF CPU deadline")
        self.assertEqual(high.item(high.get_children()[0], "values")[1], "SF GPU deadline")

    def test_statistics_tab_uses_full_window_content(self):
        window = self.window
        window.deiconify()
        window.tabs.select(window.monitor_page)
        window.update_idletasks()
        self.assertTrue(window.start_button.winfo_ismapped())
        monitor_table_height = window.event_table.winfo_height()
        window.tabs.select(window.statistics_page)
        window.update_idletasks()
        self.assertEqual(window.tabs.master, window)
        self.assertTrue(window.statistics_page.winfo_ismapped())
        self.assertFalse(window.monitor_page.winfo_ismapped())
        self.assertFalse(window.start_button.winfo_ismapped())
        self.assertGreater(window.statistics_page.winfo_height(), window.winfo_height() * .85)
        table = window.severity_rank_tables["flags"]["low"]
        self.assertTrue(table.winfo_ismapped())
        self.assertGreater(table.winfo_height(), monitor_table_height)
        window.tabs.select(window.monitor_page)
        window.update_idletasks()
        self.assertTrue(window.start_button.winfo_ismapped())
        self.assertFalse(window.statistics_page.winfo_ismapped())

    def test_changing_tabs_preserves_collection_and_statistics(self):
        window = self.window
        window._on_log("MUST_JANK", {**frame(), "delay_ns": "25000000", "refresh_rate": "120"})
        window.running = True
        for page in (window.statistics_page, window.monitor_page, window.statistics_page):
            window.tabs.select(page)
            window.update_idletasks()
            self.assertTrue(window.running)
            self.assertEqual(window.severity_stats.counts["high"], 1)
            self.assertEqual(window.received_jank_events, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
