#!/usr/bin/env python3
"""Unit tests for wxstat explicit state machines (UX/DX/AX elevation)."""

import json
import time
import unittest

import wx_server
from wx_server import (
    BatteryState,
    CollectorState,
    CollectorStateMachine,
    OllamaState,
    OllamaStateMachine,
    RainActivityState,
    RainStateMachine,
    SensorSignalState,
    TCPServerState,
    WeatherCategory,
    _rain_is_active,
    _simple_condition,
    analyze_weather,
)


class TestRainStateMachine(unittest.TestCase):
    def setUp(self):
        self.fsm = RainStateMachine(window_seconds=1800)

    def test_initial_state(self):
        self.assertEqual(self.fsm.state, RainActivityState.NO_DATA)
        self.assertFalse(self.fsm.is_active)
        self.assertIsNone(self.fsm.last_mm)

    def test_first_reading_baseline(self):
        t0 = 1000.0
        self.fsm.update(10.0, now=t0)
        self.assertEqual(self.fsm.state, RainActivityState.DRY)
        self.assertFalse(self.fsm.is_active)
        self.assertEqual(self.fsm.last_mm, 10.0)

    def test_active_rain_transition(self):
        t0 = 1000.0
        self.fsm.update(10.0, now=t0)
        # Counter increments -> ACTIVE_RAIN
        self.fsm.update(10.5, now=t0 + 60)
        self.assertEqual(self.fsm.state, RainActivityState.ACTIVE_RAIN)
        self.assertTrue(self.fsm.is_active)
        self.assertEqual(self.fsm.increased_at, t0 + 60)

    def test_recent_rain_transition(self):
        t0 = 1000.0
        self.fsm.update(10.0, now=t0)
        self.fsm.update(11.0, now=t0 + 60)  # ACTIVE_RAIN at 1060
        # Counter stays same 5 minutes later -> RECENT_RAIN
        self.fsm.update(11.0, now=t0 + 360)
        self.assertEqual(self.fsm.state, RainActivityState.RECENT_RAIN)
        self.assertTrue(self.fsm.is_active)

    def test_window_expiration_to_dry(self):
        t0 = 1000.0
        self.fsm.update(10.0, now=t0)
        self.fsm.update(11.0, now=t0 + 60)  # ACTIVE_RAIN at 1060
        # 31 minutes later (window=1800s, 1060 + 1860 = 2920) -> DRY
        self.fsm.update(11.0, now=1060 + 1801)
        self.assertEqual(self.fsm.state, RainActivityState.DRY)
        self.assertFalse(self.fsm.is_active)

    def test_counter_reset_recovery(self):
        t0 = 1000.0
        self.fsm.update(250.0, now=t0)
        # Sensor battery swap / reset -> counter drops to 0.5mm
        self.fsm.update(0.5, now=t0 + 60)
        self.assertEqual(self.fsm.state, RainActivityState.DRY)
        self.assertEqual(self.fsm.last_mm, 0.5)
        # Subsequent increase works properly
        self.fsm.update(1.2, now=t0 + 120)
        self.assertEqual(self.fsm.state, RainActivityState.ACTIVE_RAIN)

    def test_backward_compatible_facade(self):
        # Global _rain_is_active facade
        # Reset global rain_fsm
        wx_server.rain_fsm.last_mm = None
        wx_server.rain_fsm.increased_at = 0.0
        wx_server.rain_fsm.state = RainActivityState.NO_DATA

        res1 = _rain_is_active(5.0)
        self.assertFalse(res1)
        res2 = _rain_is_active(5.5)
        self.assertTrue(res2)


class TestCollectorStateMachine(unittest.TestCase):
    def setUp(self):
        self.fsm = CollectorStateMachine(frequency="868.3M")

    def test_lifecycle_transitions(self):
        self.assertEqual(self.fsm.state, CollectorState.STOPPED)

        self.fsm.on_starting(pid=12345, frequency="868.3M")
        self.assertEqual(self.fsm.state, CollectorState.STARTING)
        self.assertEqual(self.fsm.pid, 12345)

        self.fsm.on_packet_received()
        self.assertEqual(self.fsm.state, CollectorState.RECEIVING)
        self.assertEqual(self.fsm.packets_received, 1)

        self.fsm.on_retry_wait(error="USB device not found")
        self.assertEqual(self.fsm.state, CollectorState.RETRY_WAITING)
        self.assertEqual(self.fsm.restart_count, 1)
        self.assertEqual(self.fsm.last_error, "USB device not found")
        self.assertIsNone(self.fsm.pid)

        self.fsm.on_stopped()
        self.assertEqual(self.fsm.state, CollectorState.STOPPED)

    def test_status_observability(self):
        self.fsm.on_starting(pid=999)
        self.fsm.on_packet_received()
        status = self.fsm.get_status()
        self.assertEqual(status["state"], "RECEIVING")
        self.assertEqual(status["pid"], 999)
        self.assertEqual(status["packets_received"], 1)
        self.assertIn("uptime_s", status)


class TestOllamaStateMachine(unittest.TestCase):
    def setUp(self):
        self.fsm = OllamaStateMachine(
            enabled=True,
            url="http://localhost:11434",
            model="qwen2.5:0.5b",
            cache_ttl_s=300,
        )

    def test_warmup_and_inference_states(self):
        self.assertEqual(self.fsm.state, OllamaState.WARMING_UP)

        self.fsm.record_warmup_success("ok")
        self.assertEqual(self.fsm.state, OllamaState.READY)

        now = time.time()
        self.fsm.record_success("Clear", "☀️", "Hot and sunny.", 0.45, now)
        self.assertEqual(self.fsm.success_count, 1)
        self.assertEqual(self.fsm.last_latency_s, 0.45)
        self.assertEqual(self.fsm.get_cached_description(now + 10), "Hot and sunny.")

        # Degradation transition
        self.fsm.record_fallback("Connection refused")
        self.assertEqual(self.fsm.state, OllamaState.DEGRADED)
        self.assertEqual(self.fsm.fallback_count, 1)


class TestApiRoutes(unittest.TestCase):
    def setUp(self):
        self.client = wx_server.app.test_client()

    def test_api_status_endpoint(self):
        res = self.client.get("/api/status")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertIn("status", data)
        self.assertIn("collector", data)
        self.assertIn("rain", data)
        self.assertIn("ollama", data)
        self.assertIn("tcp_push", data)
        self.assertIn("sensor", data)
        self.assertIn("database", data)
        self.assertIn("uptime_s", data)
        # Check sensor FSM fields
        sensor = data["sensor"]
        self.assertIn(sensor["battery_state"], [b.value for b in BatteryState])
        self.assertIn(sensor["signal_state"], [s.value for s in SensorSignalState])

    def test_api_current_endpoint(self):
        res = self.client.get("/api/current")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertIn("temperature_C", data)
        self.assertIn("rain_state", data)
        self.assertIn("rain_is_active", data)
        self.assertIn("rain_today_mm", data)
        self.assertIn("battery_state", data)
        self.assertIn(data["battery_state"], [b.value for b in BatteryState])
        self.assertIn("signal_state", data)
        self.assertIn(data["signal_state"], [s.value for s in SensorSignalState])

    def test_api_history_endpoint(self):
        res = self.client.get("/api/history?limit=5")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertIsInstance(data, list)

    def test_api_stats_endpoint(self):
        res = self.client.get("/api/stats")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertIn("rain_today_mm", data)

    def test_dashboard_html_contains_fsm(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        self.assertIn("DashboardStateMachine", html)
        self.assertIn("DashboardState", html)
        self.assertIn("telemetry-bar", html)
        self.assertIn("telem-collector", html)
        self.assertIn("telem-rain", html)
        self.assertIn("battery-status", html)


class TestWeatherRuleFSM(unittest.TestCase):
    def test_weather_classification(self):
        # Active rain with strong wind -> Stormy
        wx_server.rain_fsm.last_mm = 10.0
        wx_server.rain_fsm.update(12.0)  # ACTIVE_RAIN
        res_stormy = _simple_condition({"wind_max_km_h": 45, "temperature_C": 15, "rain_mm": 12.0})
        self.assertEqual(res_stormy["condition"], WeatherCategory.STORMY.value)

        # Dry conditions with pleasant temp -> Clear
        wx_server.rain_fsm.last_mm = 12.0
        wx_server.rain_fsm.increased_at = 0.0
        wx_server.rain_fsm.state = RainActivityState.DRY
        res_clear = _simple_condition({"temperature_C": 25, "humidity": 40, "rain_mm": 12.0})
        self.assertEqual(res_clear["condition"], WeatherCategory.CLEAR.value)


class TestCliDiagnosticFSM(unittest.TestCase):
    def test_check_sdr_json_output(self):
        import subprocess
        proc = subprocess.run(
            ["python3", "check_sdr.py", "--json"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        self.assertIn("diagnostic_state", data)
        self.assertIn("hardware_state", data)
        self.assertIn("binary_installed", data)


if __name__ == "__main__":
    unittest.main()
