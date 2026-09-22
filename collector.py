"""Read-only ccusage adapter. No model/API inference, no session-text export."""
from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
FIELDS = ("inputTokens", "outputTokens", "cacheReadTokens", "cacheCreationTokens")


def utcnow():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def config():
    values = json.loads((ROOT / "config.json").read_text())
    local = ROOT / "local-settings.json"
    if local.exists():
        values.update(json.loads(local.read_text()))
    if not 1024 <= int(values["port"]) <= 65535:
        raise ValueError("port must be between 1024 and 65535")
    if int(values["refresh_seconds"]) < 30:
        raise ValueError("refresh_seconds must be >= 30")
    if not 1 <= int(values["collector_workers"]) <= 2:
        raise ValueError("collector_workers must be 1 or 2")
    if not 5 <= int(values["timeout_seconds"]) <= 300:
        raise ValueError("timeout_seconds must be between 5 and 300")
    dt.date.fromisoformat(values["since"])
    ZoneInfo(values["timezone"])
    return values


def atomic_json(path, value):
    """Publish only a completely encoded file; readers never see a half-write."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(value, handle, ensure_ascii=False, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def number(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Missing or nonnumeric {field}")
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"Invalid {field}: {value}")
    return value


def normalize(report, agent, since, until, *, sessions=False):
    cost_key = "costUSD" if agent == "codex" else "totalCost"
    records_key, period_key = ("sessions", "sessionId") if sessions else ("daily", "date")
    if not isinstance(report.get(records_key), list) or not isinstance(report.get("totals"), dict):
        raise ValueError(f"{agent}: ccusage schema does not contain {records_key}/totals")
    rows = []
    dates = set()
    for raw in report[records_key]:
        day = raw[period_key]
        if not sessions:
            dt.date.fromisoformat(day)
        if day in dates or (not sessions and not since <= day <= until):
            raise ValueError(f"{agent}: duplicate/out-of-range date {day}")
        dates.add(day)
        row = {"date": day, "agent": agent, "cost": number(raw.get(cost_key), cost_key)}
        row.update({field: number(raw.get(field), field) for field in FIELDS})
        row["tokens"] = number(raw.get("totalTokens"), "totalTokens")
        if sum(row[field] for field in FIELDS) != row["tokens"]:
            raise ValueError(f"{agent}: token components do not reconcile on {day}")
        row["reasoningTokens"] = number(raw.get("reasoningOutputTokens", 0), "reasoningOutputTokens")
        models = []
        if agent == "codex":
            for name, value in raw.get("models", {}).items():
                models.append({"name": name, "tokens": number(value.get("totalTokens"), name)})
        else:
            for value in raw.get("modelBreakdowns", []):
                tokens = sum(number(value.get(field), field) for field in FIELDS)
                model_cost = number(value.get("cost"), "model cost")
                if tokens > 0 and model_cost == 0:
                    raise ValueError(f"{agent}: missing/zero pricing for {value['modelName']}; previous snapshot retained")
                models.append({"name": value["modelName"], "tokens": tokens})
        if sum(m["tokens"] for m in models) != row["tokens"]:
            raise ValueError(f"{agent}: model tokens do not reconcile on {day}")
        if row["tokens"] > 0 and row["cost"] == 0:
            raise ValueError(f"{agent}: nonzero tokens priced as zero on {day}")
        row["models"] = models
        rows.append(row)
    for field in FIELDS:
        if sum(r[field] for r in rows) != number(report["totals"].get(field), field):
            raise ValueError(f"{agent}: {field} daily sum differs from total")
    if sum(r["tokens"] for r in rows) != number(report["totals"].get("totalTokens"), "totalTokens"):
        raise ValueError(f"{agent}: token total mismatch")
    if not math.isclose(sum(r["cost"] for r in rows), number(report["totals"].get(cost_key), cost_key), abs_tol=1e-6):
        raise ValueError(f"{agent}: cost total mismatch")
    return sorted(rows, key=lambda x: x["date"])


def revisions(old_rows, new_rows, today):
    """Flag any historical token loss or material repricing; never silently freeze it."""
    new = {(r["date"], r["agent"]): r for r in new_rows}
    changed = []
    for old in old_rows:
        if old["date"] >= today:
            continue
        row = new.get((old["date"], old["agent"]), {"tokens": 0, "cost": 0})
        token_delta = row["tokens"] - old["tokens"]
        cost_delta = row["cost"] - old["cost"]
        if token_delta < 0 or abs(cost_delta) > max(0.02, old["cost"] * 0.005):
            changed.append({"date": old["date"], "agent": old["agent"], "previousCost": old["cost"], "currentCost": row["cost"], "tokenDelta": token_delta})
    return changed


def ccusage_path(cfg):
    """Bare names are looked up on PATH; relative paths start at the project root."""
    value = cfg["ccusage_binary"]
    if "/" not in value:
        found = shutil.which(value)
        if found:
            return Path(found)
    else:
        path = ROOT / Path(value).expanduser()  # an absolute path replaces ROOT
        if path.is_file():
            # npm ships the native binary without +x; its Node launcher adds it on first run.
            if not os.access(path, os.X_OK):
                raise RuntimeError(f"ccusage is not executable: {path}; run chmod +x (see README)")
            return path
    raise RuntimeError(f"ccusage not found: {value}; see README setup")


def run_report(cfg, agent, until, cancel_event=None, *, kind="daily", source_env=None):
    binary = ccusage_path(cfg)
    args = [str(binary), agent, kind, "--since", cfg["since"], "--until", until,
            "--timezone", cfg["timezone"], "--json"]
    if kind == "session":
        # Staged buckets are filtered by exact event time. Native Claude session
        # date filters compare lastActivity timestamps and can drop the final day.
        args = [str(binary), agent, kind, "--timezone", cfg["timezone"], "--json"]
    args += ["--speed", "auto"] if agent == "codex" else ["--mode", "calculate", "--single-thread"]
    command = args[:]
    if hasattr(os, "sched_getaffinity") and shutil.which("taskset"):
        cpus = sorted(os.sched_getaffinity(0))[:min(2, int(cfg["collector_workers"]))]
        command = [shutil.which("taskset"), "-c", ",".join(map(str, cpus)), *command]
    if shutil.which("nice"):
        command = [shutil.which("nice"), "-n", "10", *command]
    started = time.monotonic()
    env = dict(os.environ, RAYON_NUM_THREADS="2", NO_COLOR="1")
    if source_env:
        env.update(source_env)
    child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, env=env, cwd=ROOT, start_new_session=True)
    while True:
        cancelled = cancel_event is not None and cancel_event.is_set()
        if cancelled or time.monotonic() - started > cfg["timeout_seconds"]:
            # Exact group created by this scan, never a broad process-name kill.
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
            child.communicate()
            reason = "cancelled" if cancelled else "timed out"
            raise RuntimeError(f"{agent}: ccusage {reason}; previous snapshot retained")
        try:
            stdout, stderr = child.communicate(timeout=0.3)
            break
        except subprocess.TimeoutExpired:
            continue
    if child.returncode:
        raise RuntimeError(f"{agent}: ccusage exit {child.returncode}: {stderr[-1500:]}")
    if any(term in stderr.lower() for term in ("missing", "excludes", "failed", "pricing unavailable")):
        raise RuntimeError(f"{agent}: ccusage warning: {stderr[-1500:]}")
    try:
        raw = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{agent}: invalid ccusage JSON: {exc}") from exc
    return raw, {"agent": agent, "command": args, "seconds": round(time.monotonic() - started, 3), "stderr": stderr.strip()}


def collect(cfg, data_dir=None, cancel_event=None):
    data_dir = Path(data_dir or ROOT / "data")
    today = dt.datetime.now(ZoneInfo(cfg["timezone"])).date().isoformat()
    start = utcnow()
    binary = ccusage_path(cfg)
    version = subprocess.check_output([str(binary), "--version"], text=True, timeout=10).strip()
    if version != "ccusage " + cfg["expected_ccusage_version"]:
        raise RuntimeError(f"ccusage version changed: {version}; explicitly review expected_ccusage_version")
    raw, timings, rows = {}, [], []
    # Serial scans keep the host load bounded; frontend always reads the cache.
    for agent in ("codex", "claude"):
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("Collection cancelled")
        raw[agent], timing = run_report(cfg, agent, today, cancel_event)
        timings.append(timing)
        rows.extend(normalize(raw[agent], agent, cfg["since"], today))
    # A separate event-time view: historical snapshot differences are not usage.
    from intraday import collect_intraday
    intraday, intraday_raw, intraday_timings = collect_intraday(cfg, start, run_report, normalize, cancel_event)
    raw["intraday"] = intraday_raw
    timings.extend(intraday_timings)
    latest = data_dir / "latest.json"
    old = json.loads(latest.read_text()) if latest.exists() else {}
    changes = revisions(old.get("rows", []), rows, today)
    notes = []
    if changes:
        notes.append({"kind": "revision", "message": f"本次有 {len(changes)} 条历史日统计发生变化；已保存原始报告供核查。", "changes": changes})
    snapshot = {
        "schemaVersion": 2, "collectedAt": utcnow(), "startedAt": start,
        "since": cfg["since"], "until": today, "timezone": cfg["timezone"],
        "ccusageVersion": version, "pricing": "online API-equivalent estimate",
        "binarySHA256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "source": "这台开发机的 Codex / Claude Code 本地日志", "rows": rows,
        "intraday": intraday,
        "warnings": notes, "timings": timings,
    }
    archive = data_dir / "snapshots" / (snapshot["collectedAt"].replace(":", "-") + ".json.gz")
    archive.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(archive, "wt", encoding="utf-8") as handle:
        json.dump({"snapshot": snapshot, "raw": raw}, handle, ensure_ascii=False, allow_nan=False)
    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError("Collection cancelled; previous snapshot retained")
    atomic_json(latest, snapshot)
    return snapshot


if __name__ == "__main__":
    result = collect(config())
    print(json.dumps({"collectedAt": result["collectedAt"], "rows": len(result["rows"]),
                      "totals": {agent: sum(r["cost"] for r in result["rows"] if r["agent"] == agent)
                                 for agent in ("codex", "claude")}, "timings": result["timings"]}, ensure_ascii=False))
