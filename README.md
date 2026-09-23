# Codex / Claude Code Usage Dashboard

A personal dashboard for your dev machine. The browser reads a cached snapshot while a background job re-tallies the local logs every 15 minutes. Compare Codex and Claude Code by date — API-equivalent cost, tokens, daily and cumulative trends, model mix — and export the selected range as CSV.

Usage is computed by a pinned version of [ccusage](https://github.com/ccusage/ccusage). The project itself uses only the Python standard library and buildless static web files; it makes no model API calls and uploads no logs. It is not affiliated with OpenAI or Anthropic.

## Install

On the machine where Codex / Claude Code runs (usually a remote dev machine):

```bash
git clone https://github.com/Hongyu-He/usage-dashboard.git ~/usage-dashboard
cd ~/usage-dashboard
npm install --prefix .tools --no-audit --no-fund ccusage@20.0.20
chmod +x .tools/node_modules/@ccusage/ccusage-linux-x64/bin/ccusage
.tools/node_modules/@ccusage/ccusage-linux-x64/bin/ccusage --version
python3 control.py start
```

Requires Python 3.10+ (standard library only). npm is only used to install ccusage; at runtime the dashboard calls ccusage's native binary directly. That binary ships in the npm package without execute permission (ccusage's own Node launcher adds it on first run), hence the `chmod +x`. The default configuration targets Linux x64; for other platforms or install locations, see [Configuration](#configuration).

If the dashboard runs on the computer in front of you, just open http://127.0.0.1:18763/. If it runs on a remote dev machine, connect from your computer as described next.

## Open the remote dashboard from your computer

Do the following on **your own computer**, not in a terminal on the dev machine. Replace `YOUR_SSH_ALIAS` with the SSH host alias you normally use for the dev machine (so that `ssh YOUR_SSH_ALIAS` logs you in with your existing keys and jump-host setup). You don't need to copy any logs to your computer or install Python or Node locally. The steps below assume the project lives at `~/usage-dashboard` on the remote machine; substitute the real path if it doesn't.

### Recommended: have your local coding agent build a double-click app

This repo doesn't ship a local app: every OS does it differently, and a coding agent on your computer (such as Claude Code or Codex) can build one that fits your setup. In the prompt below, replace `YOUR_SSH_ALIAS` under "My details" with your alias (adjust the other values if needed), then send the whole prompt to the agent. Once it's built, double-click the icon: it makes sure the remote service is running, opens a tunnel in the background and opens your browser. No commands to remember, no terminal window to keep open.

```text
Please build a double-click launcher on this computer for the usage dashboard that runs on my remote dev machine (https://github.com/Hongyu-He/usage-dashboard).

My details (if the SSH alias is still YOUR_SSH_ALIAS, ask me first):
- SSH alias: YOUR_SSH_ALIAS (ssh YOUR_SSH_ALIAS logs in directly)
- Remote project directory: ~/usage-dashboard
- Remote port: 18763
- Local port: 18763

The dashboard listens only on the remote machine's 127.0.0.1, so it has to be reached through SSH local port forwarding. When I double-click:
1. If http://127.0.0.1:<local port>/healthz already returns JSON whose service is usage-dashboard, just open the browser; don't create another tunnel. Bypass HTTP proxies for this check (for example curl --noproxy '*').
2. Otherwise run ssh <SSH alias> "python3 <remote project directory>/control.py start" (safe to repeat; it never starts a second copy).
3. Open a background forward from 127.0.0.1:<local port> to the remote 127.0.0.1:<remote port> with ExitOnForwardFailure=yes, ServerAliveInterval=30 and ServerAliveCountMax=3. Manage it with an SSH control socket: reuse a working connection, and detect and rebuild one that died after sleep or a network drop.
4. Only after /healthz returns usage-dashboard, open http://127.0.0.1:<local port>/ in the default browser. If the local port is taken by another program, report an error instead of opening it.
5. No terminal window should need to stay open. On errors, show the reason in a system dialog or notification (SSH authentication, port in use, dev machine offline, and so on) and write logs to the launcher's own folder. If SSH needs a password, MFA or a host-key confirmation, open a terminal window so I can complete it; never hang in the background.

Format and constraints:
- macOS: an .app I can keep on the Desktop or in the Dock. Windows: a desktop shortcut. Linux: a .desktop launcher. Prefer built-in system tools; don't install extra dependencies.
- Use the dashboard's own icon: once the tunnel is up, download it from http://127.0.0.1:<local port>/app-icon.icns (macOS), /favicon.ico (Windows) or /app-icon.png.
- Also give me a way to close the connection. It closes only the tunnel this launcher opened and does not stop the remote service.
- Listen on 127.0.0.1 only. Don't modify ~/.ssh/config or keys, don't disable host-key checking, don't store passwords, and don't use admin rights. Only end processes and connections you created yourself; never kill processes by name.
- Put files in a per-user folder, for example ~/Library/Application Support/UsageDashboard/ on macOS, %LOCALAPPDATA%\UsageDashboard\ on Windows or ~/.local/share/usage-dashboard/ on Linux.
- For the connection and health-check logic, see scripts/open-dashboard.sh and scripts/open-dashboard.ps1 in the repo (also under <remote project directory>/scripts/ on the remote machine).

When you're done, run it once for real and confirm the dashboard opens in the browser. Then tell me where the files are, how to close the connection and how to uninstall.
```

### Manual: two commands

Run these in order in a local terminal (macOS, Linux or Windows PowerShell):

```bash
ssh YOUR_SSH_ALIAS "python3 ~/usage-dashboard/control.py start"
ssh -NT -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -L 127.0.0.1:18763:127.0.0.1:18763 YOUR_SSH_ALIAS
```

Then open **http://127.0.0.1:18763/** in your local browser. The second command keeps running with no output; that's normal and means the tunnel is up. Closing the page doesn't stop collection, and pressing Ctrl+C in the terminal only closes the tunnel; the remote dashboard keeps running.

If local port `18763` is taken, change the first `18763` in the second command to `18764` and open `http://127.0.0.1:18764/` instead; the remote port on the right stays `18763`. If SSH warns that the host key has changed, verify the host first; don't turn off host-key checking.

### Manual: helper script

macOS / Linux, first download (run this in the local folder where you want to keep the script):

```bash
scp YOUR_SSH_ALIAS:usage-dashboard/scripts/open-dashboard.sh ./open-dashboard.sh
bash ./open-dashboard.sh YOUR_SSH_ALIAS
```

After that, you only need the second command. The script makes sure the remote service is running, opens a dedicated SSH tunnel, checks the health endpoint and opens the browser. Press Enter or Ctrl+C to close this tunnel; remote collection continues. To use another local port: `bash ./open-dashboard.sh YOUR_SSH_ALIAS 18764`. If the project isn't at `~/usage-dashboard` on the remote machine, set an environment variable: `USAGE_DASHBOARD_DIR=/path/to/usage-dashboard bash ./open-dashboard.sh YOUR_SSH_ALIAS`.

Windows PowerShell, first download and run:

```powershell
scp YOUR_SSH_ALIAS:usage-dashboard/scripts/open-dashboard.ps1 ./open-dashboard.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File ./open-dashboard.ps1 -SshAlias YOUR_SSH_ALIAS
```

If the project is in a different folder, add `-RemoteDir /path/to/usage-dashboard`. `ExecutionPolicy Bypass` applies only to this one script process and doesn't change global settings; if your organization's policy forbids it, use the two SSH commands above. The script may open an SSH authentication window; complete authentication and keep that window open. The `.ps1` script hasn't been tested on Windows yet.

## How usage is counted

- On load, the dashboard shows the last 30 days (including today), limited to the range that has data. "All time" runs from the configured `since` date to the latest collection, with days split in the configured `timezone`. Presets: last 24 hours, last 7 days, last 30 days, this month and all time, plus a custom range.
- "Last 24 hours" is a rolling 24-hour window that ends when the latest collection started, bucketed every 15 minutes by log timestamp; the first and last buckets are trimmed to the window. Totals, sparklines, model and token mix, the table and the CSV all use the same window. Other ranges are shown by day.
- 15-minute costs are still priced by the pinned ccusage, keeping its deduplication, cache, long-context and speed-tier rules. Temporary files contain only usage metadata and are removed after each collection. If an old snapshot lacks 15-minute data, the page asks you to refresh instead of showing uncollected periods as zero.
- Collection runs every 15 minutes, and the page checks the cache status every 10 seconds. The refresh button requests a collection immediately; several open windows never trigger duplicate scans.
- Costs are **USD estimates recomputed at each model's API prices** — not your actual subscription bill, remaining plan quota or the provider's full usage records.
- Only Codex / Claude Code logs currently readable on this machine are included; other devices and deleted logs are out of scope. A day with no logs shows zero, which means nothing was recorded, not that there was no usage.
- Token totals include input, output, cache reads and cache writes. Reasoning tokens are already part of output and are never added twice. The two tools count cache differently, so token counts aren't a direct measure of work.
- Before the first collection the page shows a waiting state. If either tool fails to collect, a price is missing or totals don't reconcile, the new snapshot isn't published at all: the last successful result stays and an error is shown. If the connection drops, the page keeps the last view and reconnects automatically.
- A past day can be recomputed because of late or archived logs, upstream parser changes or price changes. Each collection archives the raw reports, and a drop in past tokens or a notable cost change is flagged. Don't subtract totals across snapshots and treat the difference as new usage.

## Managing the service

In the project folder:

```bash
cd ~/usage-dashboard
python3 control.py status
python3 control.py start
python3 control.py stop
```

`start` is safe to repeat and never starts a second copy. `stop` stops this project only when the port, absolute project path, user and process command line all match. Stop the service before changing code or configuration, and start it again afterwards. The background process is detached from your SSH session, but it **does not start again by itself after the machine shuts down or reboots**; rerun the local connect command or helper script to bring it back. Nothing is collected while the machine is offline; the next collection rescans the whole range.

The service binds only to `127.0.0.1:18763` and serves only aggregated data plus allow-listed web and icon files, never raw session content. Don't change it to `0.0.0.0` or expose it to the internet. Other processes of the same user or on the same host can still reach the port; the access boundary is the machine and its SSH accounts, not a multi-user login.

### App icon

The page sets a browser favicon and an Apple touch icon: a dark-blue rounded tile with blue and orange usage bars. The design source and generation notes are in `assets/icon-design/`; nothing at runtime depends on image tools.

A desktop shortcut may keep the icon it was created with. At the current dashboard address, `/app-icon.png` downloads a 1024 px PNG and `/app-icon.icns` a macOS icon file. On macOS you can replace an existing shortcut's icon via Get Info, or recreate the web shortcut. If you changed the local forwarding port, use that port in the download address.

### Project layout

```text
collector.py         ccusage collection, validation and snapshot publishing
intraday.py          last-24-hours event bucketing and ccusage pricing
server.py            loopback HTTP server and background scheduler
control.py           safe start/stop and status
dist/                web page source, no build step or external CDN
scripts/             SSH helper scripts for your computer
tests/               regression tests for totals, caching and the HTTP API
config.json          default configuration
local-settings.json  optional overrides (not in Git)
.tools/              pinned ccusage installed by npm (not in Git)
data/latest.json     latest successful aggregate (created at runtime)
data/snapshots/      compressed reports and provenance for each collection (no conversation text)
run/server.log       service and collection log
```

## Configuration

`config.json` holds the defaults. To change something, create `local-settings.json` in the project folder (it isn't tracked by Git) with only the keys you want to override, for example:

```json
{
  "since": "2026-08-01",
  "timezone": "America/Los_Angeles",
  "refresh_seconds": 1800
}
```

- `since`: the first day counted (inclusive), default `2026-01-01`. Click refresh after changing it; the old cache keeps its own range until a new collection succeeds.
- `timezone`: the IANA time zone used to split days, default `Asia/Singapore`.
- `port`: the service port, default `18763`. If you change it, also change the SSH forwarding target and the helper script's third argument (`-RemotePort` in PowerShell).
- `refresh_seconds`: the automatic collection interval, default 900 seconds, minimum 30.
- `ccusage_binary`: the ccusage executable. A bare name is looked up on `PATH`, a relative path starts at the project folder, and absolute paths work too. The default is the Linux x64 native binary under `.tools`; on macOS and other platforms, point it at the matching folder that actually exists under `.tools/node_modules/@ccusage/` (for example `ccusage-darwin-arm64`), `chmod +x` it and check it with `--version`.
- `expected_ccusage_version`: pinned to `20.0.20`. Don't upgrade silently; if the version changes, re-check totals for the same past dates and the model prices before updating it.

Point `ccusage_binary` at the native binary, not `node_modules/.bin/ccusage`: the latter is a Node launcher script, and `node` may not be on `PATH` when the service is started over SSH.

Collection needs network access to fetch the price list. If prices are missing while offline, the previous result is kept; a missing price is never treated as zero.

Resources: the daily and 15-minute reports for both tools are scanned one after another, with ccusage limited to 2 CPUs at low priority. Each report times out after at most 120 seconds, and event bucketing has its own 120-second limit. Otherwise only a lightweight Python service runs, with no GPU and no model API calls. If growing logs cause sustained heavy load, lower the collection frequency and re-evaluate rather than adding parallelism. Every snapshot is kept in full and never deleted automatically, so keep an eye on disk quota.

## Checks and troubleshooting

```bash
python3 -m unittest discover -s tests
node --check dist/app.js
node tests/test_frontend.js
bash -n scripts/open-dashboard.sh
```

The integration test that compares against native ccusage needs ccusage 20.0.20 installed and is skipped when it can't be found.

- SSH connection fails: first make sure your usual `ssh YOUR_SSH_ALIAS` works; the dashboard scripts never change your SSH config or keys.
- Forwarding port in use: pick another local port, or close your own earlier tunnel; don't kill processes you don't recognize.
- Page shows stale data: check the last-updated time and any error message. On the machine running the service, look at `run/server.log`, fix the network or dependency issue, then click Refresh.
- `ccusage not found`: make sure ccusage is installed as described in [Install](#install), or set the right `ccusage_binary` in `local-settings.json`. `ccusage is not executable`: run `chmod +x` on that file.
- Machine rebooted: rerun your local helper script; it restarts the dashboard.
- You want to stop background collection: run `python3 control.py stop`. Files and past snapshots are kept.

## What has been verified

Totals validation, keeping the last cache on failure, scheduling and the HTTP API, and the frontend's pure data functions and syntax all have regression tests. The last-24-hours view, the 15-minute and cumulative charts, token metrics, CSV export, date switching and the mobile layout have been checked by hand in Chromium. The Windows script hasn't been tested on Windows yet.

Browsers that support `document.modelContext` can use an optional `set_usage_date_range` structured filter; it hasn't been tested yet. It doesn't affect the normal controls and never starts a collection or calls a model.

## License

[MIT](LICENSE)
