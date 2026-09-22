"""Event-time buckets, priced by the pinned ccusage binary, never snapshot deltas.

Only usage metadata is staged in a private temporary directory, then removed.
The native session report prices each bucket (including cache, long context and
speed tiers); native deduplication also runs across all staged buckets.
Codex cumulative/replay handling follows ccusage v20.0.20's parser/replay modules:
https://github.com/ccusage/ccusage/tree/v20.0.20/rust/adapters/codex/src
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import tempfile
import time
from zoneinfo import ZoneInfo

UTC = dt.timezone.utc
STEP = 900
TOKEN_KEYS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens", "total_tokens")


def timestamp(value):
    if isinstance(value, (int, float)):
        return value / 1000 if value > 10_000_000_000 else value
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Usage timestamp must include a timezone")
    return parsed.timestamp()


def iso(stamp):
    return dt.datetime.fromtimestamp(stamp, UTC).isoformat(timespec="milliseconds")


def read_records(path, check):
    # Read only the size observed on open; concurrent appends belong to next scan.
    with path.open("rb") as handle:
        remaining = os.fstat(handle.fileno()).st_size
        while remaining:
            check()
            line = handle.readline(remaining)
            remaining -= len(line)
            if not line:
                break
            if not any(key in line for key in (b'"usage"', b'"token_count"', b'"turn_context"', b'"thread_settings_applied"')):
                continue
            try:
                yield json.loads(line)
            except ValueError:
                # A writer may be midway through the final JSONL record.
                if remaining or line.endswith(b"\n"):
                    raise ValueError("Malformed usage log; previous snapshot retained")


def model_in(value):
    return value.get("model") or value.get("model_name") or (value.get("metadata") or {}).get("model")


def token_values(value):
    aliases = (("input_tokens", "prompt_tokens", "input"),
               ("cached_input_tokens", "cache_read_input_tokens", "cached_tokens"),
               ("output_tokens", "completion_tokens", "output"),
               ("reasoning_output_tokens", "reasoning_tokens"), ("total_tokens",))
    result = {key: next((value[name] for name in names if value.get(name) is not None), 0)
              for key, names in zip(TOKEN_KEYS, aliases)}
    if any(isinstance(n, bool) or not isinstance(n, int) or n < 0 for n in result.values()):
        raise ValueError("Invalid Codex usage tokens")
    if not result["total_tokens"]:
        result["total_tokens"] = result["input_tokens"] + result["output_tokens"]
    return result


def codex_events(path, check):
    """Read the full prefix before filtering: totals can span the window boundary."""
    previous, model, tier = None, None, "unknown"
    events = []
    for record in read_records(path, check):
        payload = record.get("payload") or {}
        kind = record.get("type")
        if kind == "turn_context":
            model = model_in(payload) or model
            continue
        if kind == "event_msg" and payload.get("type") == "thread_settings_applied":
            settings = payload.get("thread_settings") or {}
            if "service_tier" in settings:
                tier = settings["service_tier"] or "unknown"
            continue
        if kind == "event_msg" and payload.get("type") == "token_count":
            info = payload.get("info") or {}
            total = token_values(info["total_token_usage"]) if info.get("total_token_usage") else None
            last = info.get("last_token_usage")
            if last is not None and (total is None or total != previous):
                usage = token_values(last)
            elif total is not None:
                usage = {key: max(0, total[key] - (previous or {}).get(key, 0)) for key in TOKEN_KEYS}
            else:
                continue
            if total is not None:
                previous = total
            if not any(usage[key] for key in TOKEN_KEYS[:-1]):
                continue
            model = model_in(payload) or model_in(info) or model or "gpt-5"
            stamp = timestamp(record["timestamp"])
        elif kind in ("turn.completed", "result") and record.get("usage"):
            usage = token_values(record["usage"])
            model = model_in(record) or model or "gpt-5"
            stamp = timestamp(record.get("timestamp", path.stat().st_mtime))
        else:
            # Fail closed for new headless formats instead of reporting zero.
            if record.get("usage") or any((record.get(key) or {}).get("usage") for key in ("data", "result", "response") if isinstance(record.get(key), dict)):
                raise ValueError("Unsupported Codex usage format; previous snapshot retained")
            continue
        usage["cached_input_tokens"] = min(usage["cached_input_tokens"], usage["input_tokens"])
        events.append({"timestamp": iso(stamp), "model": model, "tier": tier, "usage": usage})
    return events


def signature(event):
    return tuple(event["usage"][key] for key in TOKEN_KEYS)


def without_replay(events, parent_events):
    if parent_events is None:
        return events
    index = 0
    while index < min(len(events), len(parent_events)) and signature(events[index]) == signature(parent_events[index]):
        index += 1
    if index:
        return events[index:]
    if len(events) >= 2 and 0 <= timestamp(events[1]["timestamp"]) - timestamp(events[0]["timestamp"]) <= 1:
        index = 1
        while index < len(events) and 0 <= timestamp(events[index]["timestamp"]) - timestamp(events[index - 1]["timestamp"]) <= 1:
            index += 1
        return events[index:]
    return events


def source_paths(agent):
    if agent == "codex":
        homes = [Path(p.strip()).expanduser() for p in os.environ.get("CODEX_HOME", str(Path.home() / ".codex")).split(",") if p.strip()]
        roots = []
        for home in homes:
            candidates = [home / name for name in ("sessions", "archived_sessions") if (home / name).is_dir()]
            roots.extend(candidates or [home])
    else:
        configured = os.environ.get("CLAUDE_CONFIG_DIR")
        homes = [Path(p.strip()).expanduser() for p in configured.split(",") if p.strip()] if configured else [Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "claude", Path.home() / ".claude"]
        roots = [p if p.name == "projects" else p / "projects" for p in homes]
    roots = list(dict.fromkeys(p for p in roots if p.is_dir()))
    if not roots:
        raise ValueError(f"{agent}: no readable usage directory; previous snapshot retained")
    # Keep sessions before archived_sessions, as native ccusage does for replay.
    return homes, list(dict.fromkeys(p for root in roots for p in sorted(root.rglob("*.jsonl"))))


def stage_sources(root, start, end, check):
    bucket_names = set()
    handles = {}
    def write(agent, stamp, record):
        name = str(int(stamp // STEP) * STEP) if start <= stamp < end else "outside"
        bucket_names.add(name)
        directory = root / agent / ("sessions" if agent == "codex" else "projects/buckets")
        directory.mkdir(parents=True, exist_ok=True)
        key = (agent, name)
        if key not in handles:
            handles[key] = (directory / (name + ".jsonl")).open("w")
        handles[key].write(json.dumps(record, separators=(",", ":")) + "\n")
    try:
        homes, paths = source_paths("codex")
        metadata, by_id = {}, {}
        for path in paths:
            check()
            with path.open("rb") as handle:
                try:
                    record = json.loads(handle.readline())
                except ValueError:
                    record = {}
            meta = record.get("payload", {}) if record.get("type") == "session_meta" else {}
            metadata[path] = (meta, record.get("timestamp"))
            if meta.get("id"):
                by_id.setdefault(meta["id"], path)
        cached = {}
        def events_for(path):
            if path not in cached:
                cached[path] = codex_events(path, check)
            return cached[path]
        for path in paths:
            if path.stat().st_mtime < start:
                continue
            meta, fork_stamp = metadata[path]
            source = meta.get("source")
            subagent = source.get("subagent") if isinstance(source, dict) else None
            spawn = subagent.get("thread_spawn", {}) if isinstance(subagent, dict) else {}
            parent_id = meta.get("forked_from_id") or spawn.get("parent_thread_id")
            parent = None
            if parent_id:
                parent = events_for(by_id[parent_id]) if parent_id in by_id else []
                if fork_stamp:
                    parent = [event for event in parent if timestamp(event["timestamp"]) <= timestamp(fork_stamp)]
            for event in without_replay(events_for(path), parent):
                stamp = timestamp(event["timestamp"])
                write("codex", stamp, {"timestamp": event["timestamp"], "type": "event_msg", "payload": {"type": "thread_settings_applied", "thread_settings": {"service_tier": event["tier"]}}})
                write("codex", stamp, {"timestamp": event["timestamp"], "type": "event_msg", "payload": {"type": "token_count", "model": event["model"], "info": {"last_token_usage": event["usage"]}}})
        # Preserve only the native auto-speed default, never credentials/config text.
        fast = False
        for home in homes:
            config = home / "config.toml"
            if config.is_file():
                for line in config.read_text().splitlines():
                    key, sep, value = line.split("#", 1)[0].partition("=")
                    if sep and key.strip() == "service_tier" and value.strip().strip("\"'") in ("fast", "priority"):
                        fast = True
        (root / "codex/sessions").mkdir(parents=True, exist_ok=True)
        (root / "codex/config.toml").write_text('service_tier = "' + ("fast" if fast else "standard") + '"\n')
        _, paths = source_paths("claude")
        for path in paths:
            for record in read_records(path, check):
                message = record.get("message") or {}
                if not isinstance(message, dict) or not message.get("usage"):
                    # ccusage's daily adapter includes nested agent_progress usage.
                    nested = (record.get("data") or {}).get("message")
                    if isinstance(nested, dict) and isinstance(nested.get("message"), dict):
                        record = nested
                        message = record["message"]
                if not isinstance(message, dict) or not message.get("usage"):
                    continue
                # Retain records outside the window so native sidechain/streaming
                # deduplication can prefer their canonical originals as usual.
                clean = {key: record[key] for key in ("timestamp", "version", "sessionId", "requestId", "isSidechain", "isApiErrorMessage") if key in record}
                clean["message"] = {key: message[key] for key in ("id", "model", "usage") if key in message}
                write("claude", timestamp(record["timestamp"]), clean)
        (root / "claude/projects/buckets").mkdir(parents=True, exist_ok=True)
    finally:
        for handle in handles.values():
            handle.close()
    return bucket_names


def collect_intraday(cfg, as_of, run_report, normalize, cancel_event=None):
    end = timestamp(as_of)
    start = end - 86400
    deadline = time.monotonic() + cfg["timeout_seconds"]
    def check():
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("Intraday collection cancelled; previous snapshot retained")
        if time.monotonic() > deadline:
            raise RuntimeError("Intraday scan timed out; previous snapshot retained")
    raw, rows, timings = {}, [], []
    with tempfile.TemporaryDirectory(prefix="usage-intraday-") as tmp:
        root = Path(tmp)
        names = stage_sources(root, start, end, check)
        for agent in ("codex", "claude"):
            source_env = {"CODEX_HOME" if agent == "codex" else "CLAUDE_CONFIG_DIR": str(root / agent)}
            until = dt.datetime.fromtimestamp(end, ZoneInfo(cfg["timezone"])).date().isoformat()
            raw[agent], timing = run_report(cfg, agent, until, cancel_event, kind="session", source_env=source_env)
            timing["scope"] = "intraday"
            timings.append(timing)
            for row in normalize(raw[agent], agent, cfg["since"], until, sessions=True):
                name = row.pop("date").removesuffix(".jsonl")
                if name not in names:
                    raise ValueError(f"{agent}: unrecognized intraday bucket")
                if name == "outside":
                    continue
                stamp = int(name)
                row["start"] = iso(max(start, stamp))
                row["end"] = iso(min(end, stamp + STEP))
                rows.append(row)
    return {"start": iso(start), "end": iso(end), "bucketSeconds": STEP, "rows": rows}, raw, timings
