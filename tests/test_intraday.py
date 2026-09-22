import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import collector
import intraday


def write_log(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, separators=(",", ":")) + "\n" for row in records))


def usage(n=100, output=10):
    return {"input_tokens": n, "cached_input_tokens": n // 2, "output_tokens": output,
            "reasoning_output_tokens": 3, "total_tokens": n + output}


def event(stamp, last=None, total=None):
    info = {}
    if last is not None:
        info["last_token_usage"] = last
    if total is not None:
        info["total_token_usage"] = total
    return {"timestamp": stamp, "type": "event_msg", "payload": {"type": "token_count", "info": info}}


class IntradayTests(unittest.TestCase):
    def test_headless_aliases_do_not_add_reasoning_twice(self):
        values = intraday.token_values({"prompt_tokens": 100, "completion_tokens": 20, "cached_tokens": 40, "reasoning_tokens": 10})
        self.assertEqual(values["total_tokens"], 120)
        self.assertEqual(values["cached_input_tokens"], 40)

    def test_cumulative_prefix_and_repeated_totals(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.jsonl"
            write_log(path, [
                {"type": "turn_context", "payload": {"model": "gpt-5"}},
                event("2026-09-18T15:59:00Z", total=usage()),
                event("2026-09-18T16:00:00Z", total=usage(300, 30)),
                event("2026-09-18T16:00:01Z", last=usage(200, 20), total=usage(300, 30)),
            ])
            rows = intraday.codex_events(path, lambda: None)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[1]["usage"]["input_tokens"], 200)
            self.assertEqual(rows[1]["usage"]["output_tokens"], 20)

    def test_fork_replay_and_burst_crossing_a_second(self):
        rows = [{"timestamp": stamp, "usage": usage(n)} for stamp, n in [
            ("2026-09-19T00:00:00.990Z", 100), ("2026-09-19T00:00:01.010Z", 200),
            ("2026-09-19T00:00:08.000Z", 300)]]
        self.assertEqual(intraday.without_replay(rows, rows[:2]), rows[2:])
        self.assertEqual(intraday.without_replay(rows, []), rows[2:])
        self.assertEqual(intraday.without_replay(rows, None), rows)

    def test_native_pricing_reconciliation_and_exact_window(self):
        """Frozen source vs staged native reports: cache, tiers, forks, duplicates,
        nested Claude usage, and events exactly on both sides of midnight."""
        cfg = collector.config()
        try:
            collector.ccusage_path(cfg)
        except RuntimeError:
            self.skipTest("needs the pinned ccusage binary; see README")
        original_popen = subprocess.Popen
        def offline_popen(command, **kwargs):
            return original_popen([*command, "--offline"], **kwargs)
        with tempfile.TemporaryDirectory() as tmp, patch.object(collector.subprocess, "Popen", side_effect=offline_popen):
            root = Path(tmp)
            codex = root / "source-codex"
            claude = root / "source-claude"
            begin = "2026-09-18T16:00:00Z"
            end = "2026-09-19T16:00:00Z"
            old = "2026-09-18T15:59:59Z"
            meta = {"type": "session_meta", "timestamp": old, "payload": {"id": "parent"}}
            context = {"type": "turn_context", "payload": {"model": "gpt-5"}}
            write_log(codex / "sessions/parent.jsonl", [meta, context,
                event(old, usage(), usage()), event(begin, usage(300_000, 30), usage(300_100, 40)),
                event("2026-09-18T16:00:01Z", usage(300_000, 30), usage(300_100, 40)),
                {"type": "event_msg", "timestamp": begin, "payload": {"type": "thread_settings_applied", "thread_settings": {"service_tier": "fast"}}},
                event("2026-09-19T15:59:59Z", usage(200, 20)), event(end, usage(400, 40))])
            write_log(codex / "sessions/child.jsonl", [
                {"type": "session_meta", "timestamp": "2026-09-18T16:10:00Z", "payload": {"id": "child", "forked_from_id": "parent"}}, context,
                event("2026-09-18T16:10:00Z", usage()), event("2026-09-18T16:10:00.020Z", usage(300_000, 30)),
                event("2026-09-18T16:10:10Z", usage(50, 5))])
            def claude_record(stamp, identifier, output=5):
                return {"timestamp": stamp, "requestId": identifier, "message": {"id": identifier, "model": "claude-sonnet-4-20250514", "content": "PRIVATE CONVERSATION", "usage": {"input_tokens": 10, "output_tokens": output, "cache_creation_input_tokens": 20, "cache_read_input_tokens": 30}}}
            records = [claude_record(old, "old"), claude_record(begin, "stream"), claude_record(begin, "stream", 15), claude_record(end, "end"), {"type": "progress", "data": {"message": claude_record("2026-09-18T16:15:00Z", "nested")}}]
            write_log(claude / "projects/test/source.jsonl", records)
            paths = {"codex": ([codex], sorted(codex.rglob("*.jsonl"))), "claude": ([claude], sorted(claude.rglob("*.jsonl")))}
            with patch.object(intraday, "source_paths", side_effect=lambda agent: paths[agent]):
                data, _, _ = intraday.collect_intraday(cfg, end, collector.run_report, collector.normalize)
                staged = root / "staged"
                intraday.stage_sources(staged, intraday.timestamp(begin), intraday.timestamp(end), lambda: None)
            self.assertEqual(data["start"], "2026-09-18T16:00:00.000+00:00")
            codex_rows = [row for row in data["rows"] if row["agent"] == "codex"]
            claude_rows = [row for row in data["rows"] if row["agent"] == "claude"]
            self.assertEqual(sum(row["tokens"] for row in codex_rows), 300_030 + 220 + 55)
            self.assertEqual(sum(row["tokens"] for row in claude_rows), 75 + 65)
            self.assertTrue(all(intraday.timestamp(row["end"]) <= intraday.timestamp(end) for row in data["rows"]))
            self.assertNotIn("PRIVATE CONVERSATION", "".join(p.read_text() for p in staged.rglob("*.jsonl")))
            for agent, source in (("codex", codex), ("claude", claude)):
                env_key = "CODEX_HOME" if agent == "codex" else "CLAUDE_CONFIG_DIR"
                reference, _ = collector.run_report(cfg, agent, "2026-09-20", source_env={env_key: str(source)})
                actual, _ = collector.run_report(cfg, agent, "2026-09-20", kind="session", source_env={env_key: str(staged / agent)})
                for key, expected in reference["totals"].items():
                    if isinstance(expected, (int, float)):
                        self.assertAlmostEqual(actual["totals"][key], expected, places=8, msg=f"{agent} {key}")


if __name__ == "__main__":
    unittest.main()
