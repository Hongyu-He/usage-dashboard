import copy
import http.client
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import collector
import server


def report(agent="codex"):
    cost_key = "costUSD" if agent == "codex" else "totalCost"
    tokens = {"inputTokens": 10, "outputTokens": 20, "cacheReadTokens": 30, "cacheCreationTokens": 40}
    row = {"date": "2026-09-01", **tokens, "totalTokens": 100, cost_key: 1.25}
    if agent == "codex":
        row["models"] = {"test-model": {"totalTokens": 100}}
        row["reasoningOutputTokens"] = 12
    else:
        row["modelBreakdowns"] = [{"modelName": "test-model", **tokens, "cost": 1.25}]
    return {"daily": [row], "totals": {**tokens, "totalTokens": 100, cost_key: 1.25}}


def fake_ccusage(directory):
    """A stand-in binary that only answers --version; tests mock the reports."""
    cfg = collector.config()
    binary = Path(directory) / "ccusage"
    binary.write_text(f"#!/bin/sh\necho 'ccusage {cfg['expected_ccusage_version']}'\n")
    binary.chmod(0o755)
    return {**cfg, "ccusage_binary": str(binary)}


class CollectorTests(unittest.TestCase):
    def test_normalization_reconciles_and_does_not_double_count_reasoning(self):
        for agent in ("codex", "claude"):
            rows = collector.normalize(report(agent), agent, "2026-08-10", "2026-09-09")
            self.assertEqual(rows[0]["tokens"], 100)
            self.assertEqual(rows[0]["cost"], 1.25)

    def test_bad_totals_dates_components_and_prices_rejected(self):
        cases = []
        bad = report(); bad["totals"]["totalTokens"] = 101; cases.append((bad, "codex"))
        bad = report(); bad["daily"][0]["inputTokens"] = 11; cases.append((bad, "codex"))
        bad = report(); bad["daily"].append(copy.deepcopy(bad["daily"][0])); cases.append((bad, "codex"))
        bad = report(); bad["daily"][0]["date"] = "2026-09-30"; cases.append((bad, "codex"))
        bad = report("claude"); bad["daily"][0]["modelBreakdowns"][0]["cost"] = 0; cases.append((bad, "claude"))
        bad = report(); bad["daily"][0]["costUSD"] = float("nan"); cases.append((bad, "codex"))
        for data, agent in cases:
            with self.subTest(agent=agent), self.assertRaises(ValueError):
                collector.normalize(data, agent, "2026-08-10", "2026-09-09")

    def test_historical_revisions_exclude_today(self):
        old = [{"date": "2026-09-01", "agent": "codex", "tokens": 100, "cost": 1},
               {"date": "2026-09-09", "agent": "codex", "tokens": 100, "cost": 1}]
        changes = collector.revisions(old, [], "2026-09-09")
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["tokenDelta"], -100)

    def test_ccusage_binary_lookup(self):
        def lookup(value):
            return collector.ccusage_path({"ccusage_binary": value})
        with tempfile.TemporaryDirectory() as tmp, patch.object(collector, "ROOT", Path(tmp)):
            binary = Path(tmp) / ".tools/bin/ccusage"
            binary.parent.mkdir(parents=True)
            binary.write_text("#!/bin/sh\n")
            with self.assertRaisesRegex(RuntimeError, "chmod"):
                lookup(".tools/bin/ccusage")
            binary.chmod(0o755)
            self.assertEqual(lookup(".tools/bin/ccusage"), binary)
            self.assertEqual(lookup(str(binary)), binary)
            self.assertEqual(lookup("sh"), Path(shutil.which("sh")))
            for missing in ("no-such-ccusage", ".tools/no-such/ccusage"):
                with self.subTest(missing=missing), self.assertRaisesRegex(RuntimeError, "not found"):
                    lookup(missing)

    def test_complete_publish_and_failed_second_agent_keep_previous(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = fake_ccusage(tmp)
            def fake_report(cfg, agent, today, cancel_event=None):
                return report(agent), {"agent": agent, "seconds": .001}
            with patch.object(collector, "run_report", side_effect=fake_report), patch("intraday.collect_intraday", return_value=({"rows": []}, {}, [])):
                snapshot = collector.collect(cfg, tmp)
            path = Path(tmp) / "latest.json"
            previous = path.read_bytes()
            self.assertEqual(len(snapshot["rows"]), 2)
            self.assertEqual(len(list((Path(tmp) / "snapshots").glob("*.json.gz"))), 1)
            with patch.object(collector, "run_report", side_effect=[(report(), {"seconds": .001}), RuntimeError("offline missing pricing")]):
                with self.assertRaisesRegex(RuntimeError, "pricing"):
                    collector.collect(cfg, tmp)
            self.assertEqual(path.read_bytes(), previous)
            with patch.object(collector, "run_report", side_effect=fake_report), patch("intraday.collect_intraday", side_effect=ValueError("intraday missing pricing")):
                with self.assertRaisesRegex(ValueError, "intraday"):
                    collector.collect(cfg, tmp)
            self.assertEqual(path.read_bytes(), previous)

    def test_cancel_terminates_only_its_spawned_child(self):
        original = subprocess.Popen
        children = []
        def fake_popen(command, **kwargs):
            child = original([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
            children.append(child)
            return child
        event = threading.Event()
        timer = threading.Timer(.1, event.set)
        timer.start()
        try:
            # Popen is mocked, so the binary only has to exist.
            cfg = {**collector.config(), "ccusage_binary": sys.executable}
            with patch.object(collector.subprocess, "Popen", side_effect=fake_popen):
                with self.assertRaisesRegex(RuntimeError, "cancelled"):
                    collector.run_report(cfg, "codex", "2026-09-09", event)
            self.assertIsNotNone(children[0].poll())
        finally:
            timer.cancel()
            for child in children:
                if child.poll() is None: child.kill(); child.wait()


class StateTests(unittest.TestCase):
    def test_scheduler_manual_dedup_and_failure_keep_cache(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(server, "ROOT", Path(tmp)):
            cfg = {"timezone": "Asia/Shanghai", "refresh_seconds": .05}
            calls = []
            successful = {"collectedAt": collector.utcnow(), "rows": [], "timings": []}
            second = threading.Event()
            def scan(cfg):
                calls.append(1)
                if len(calls) > 1:
                    second.set()
                    raise RuntimeError("expected test failure")
                return successful
            state = server.State(cfg, scan)
            self.assertTrue(state.request())
            self.assertFalse(state.request())
            thread = threading.Thread(target=state.loop)
            with self.assertLogs(level="ERROR"):
                thread.start()
                self.assertTrue(second.wait(2), "automatic refresh did not occur")
                state.stop.set(); state.wake.set(); thread.join(2)
            self.assertIs(state.data, successful)
            self.assertIn("expected test failure", state.public_status()["error"])
            self.assertFalse(thread.is_alive())

    def test_api_routes_security_and_manual_refresh(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(server, "ROOT", Path(tmp)):
            root = Path(tmp)
            (root / "dist").mkdir()
            for file in ("index.html", "style.css", "app.js"):
                (root / "dist" / file).write_text("test asset")
            state = server.State({"timezone": "Asia/Shanghai", "refresh_seconds": 900})
            httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.handler_for(state))
            thread = threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
            port = httpd.server_address[1]
            def request(method, path, headers=None):
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                conn.request(method, path, headers=headers or {})
                response = conn.getresponse(); body = response.read(); result = response.status, body
                conn.close(); return result
            try:
                self.assertEqual(request("GET", "/api/usage")[0], 503)
                for path in ("/", "/app.js", "/style.css", "/api/status", "/healthz"):
                    self.assertEqual(request("GET", path)[0], 200)
                for path in ("/../collector.py", "/data/latest.json", "/config.json"):
                    self.assertEqual(request("GET", path)[0], 404)
                self.assertEqual(request("GET", "/api/status", {"Host": "evil.example"})[0], 403)
                self.assertEqual(request("POST", "/api/refresh")[0], 403)
                headers = {"X-Refresh-Token": state.csrf, "Origin": "https://evil.example"}
                self.assertEqual(request("POST", "/api/refresh", headers)[0], 403)
                headers["Origin"] = f"http://127.0.0.1:{port}"
                self.assertEqual(request("POST", "/api/refresh", headers)[0], 202)
                self.assertEqual(request("POST", "/api/refresh", headers)[0], 200)
                state.data = {"collectedAt": "2026-09-09T00:00:00+00:00", "rows": []}
                self.assertEqual(json.loads(request("GET", "/api/usage")[1]), state.data)
            finally:
                httpd.shutdown(); httpd.server_close(); thread.join(2)


if __name__ == "__main__":
    unittest.main()
