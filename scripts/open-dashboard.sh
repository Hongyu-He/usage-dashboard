#!/usr/bin/env bash
# Run this file on your laptop, NOT on the remote machine.
set -euo pipefail
dashboard_host=${1:-}
dashboard_local_port=${2:-18763}
dashboard_remote_port=${3:-18763}
# Project directory on the remote machine; the quoted ~ expands there, not here.
dashboard_dir=${USAGE_DASHBOARD_DIR:-"~/usage-dashboard"}
if [[ -z "$dashboard_host" || "$dashboard_host" == -* || "$dashboard_host" == *$'\n'* ]]; then
  echo "Usage: [USAGE_DASHBOARD_DIR=REMOTE_DIR] bash open-dashboard.sh YOUR_SSH_ALIAS [LOCAL_PORT] [REMOTE_PORT]" >&2
  exit 2
fi
for dashboard_port in "$dashboard_local_port" "$dashboard_remote_port"; do
  if [[ ! "$dashboard_port" =~ ^[0-9]{4,5}$ ]] || (( 10#$dashboard_port < 1024 || 10#$dashboard_port > 65535 )); then
    echo "Ports must be integers between 1024 and 65535." >&2; exit 2
  fi
done
# The directory is spliced into the remote shell command: plain paths only.
dashboard_dir_pattern='^[A-Za-z0-9_./~-]+$'
if [[ ! "$dashboard_dir" =~ $dashboard_dir_pattern || "$dashboard_dir" == -* ]]; then
  echo "USAGE_DASHBOARD_DIR must be a plain path without spaces or shell characters." >&2; exit 2
fi
for dashboard_command in ssh curl mktemp; do
  command -v "$dashboard_command" >/dev/null || { echo "Missing: $dashboard_command" >&2; exit 1; }
done
ssh -- "$dashboard_host" "python3 $dashboard_dir/control.py start"
dashboard_tmp=$(mktemp -d "${TMPDIR:-/tmp}/usage-dashboard.XXXXXX")
dashboard_socket="$dashboard_tmp/ssh"
dashboard_connected=0
dashboard_cleanup() {
  if [[ "$dashboard_connected" == 1 ]]; then
    ssh -S "$dashboard_socket" -O exit -- "$dashboard_host" >/dev/null 2>&1 || true
  fi
  rmdir "$dashboard_tmp" 2>/dev/null || true
}
trap dashboard_cleanup EXIT
trap 'exit 130' INT TERM
ssh -M -S "$dashboard_socket" -fNT -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
  -L "127.0.0.1:$dashboard_local_port:127.0.0.1:$dashboard_remote_port" -- "$dashboard_host"
dashboard_connected=1
dashboard_url="http://127.0.0.1:$dashboard_local_port"
dashboard_health=$(curl --noproxy '*' -fsS --max-time 10 "$dashboard_url/healthz")
case "$dashboard_health" in
  *'"service": "usage-dashboard"'*) ;;
  *) echo "Tunnel target is not usage-dashboard; not opening browser." >&2; exit 1 ;;
esac
if command -v open >/dev/null; then
  open "$dashboard_url/" || true
elif command -v xdg-open >/dev/null; then
  xdg-open "$dashboard_url/" >/dev/null 2>&1 || true
fi
echo "Dashboard: $dashboard_url/"
echo "Keep this terminal open. Press Enter or Ctrl+C to close ONLY this tunnel."
echo "The remote machine keeps collecting while it stays up."
read -r || true
