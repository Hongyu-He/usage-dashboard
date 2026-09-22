# Usage dashboard

Personal dashboard, usually hosted on a remote dev machine; local browser access
is through an SSH tunnel. Do not deploy it as a shared or cloud service. Do not
upload session transcripts or credentials. Use Python standard library and
buildless static assets; no LLM calls.

Preserve ccusage source reports and provenance. Token totals include cache reads
and writes; reasoning tokens are already contained in output, never add twice.
Costs are API-equivalent estimates. Missing prices and failed scans must never
be presented as zero usage. Keep the last successful snapshot on collection errors.

Server binds only 127.0.0.1. Serve allowlisted static files and aggregated metrics,
never arbitrary paths, raw session logs, home directory files, or shell commands.
Validate exact process ownership before stopping the service. Do not edit code
while this project's server is running; stop it via control.py first.

Run `python3 -m unittest discover -s tests` and `node --check dist/app.js` for
relevant changes. Keep runtime data and user settings out of Git.
