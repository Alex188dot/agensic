#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  signed_session.sh start [--agent <agent_id>] [--model <model_id>] [--agent-name <name>] [--ttl-minutes N]
  signed_session.sh stop
  signed_session.sh status

Preferred session flow (no eval):
  agensic_session_start --agent codex --model gpt-5.3 --agent-name 'Planner A' --ttl-minutes 120
  agensic_session_status
  agensic_session_stop
USAGE
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CLI=()
STATE_HOME="${XDG_STATE_HOME:-$HOME/.local/state}"
INSTALL_RUNTIME="$STATE_HOME/agensic/install/.venv/bin/python"
CLI_PYTHON="${AGENSIC_CLI_PYTHON:-}"

if [[ -n "$CLI_PYTHON" && -x "$CLI_PYTHON" && -f "$REPO_ROOT/cli.py" ]]; then
  CLI=("$CLI_PYTHON" "$REPO_ROOT/cli.py")
elif [[ -x "$INSTALL_RUNTIME" && -f "$REPO_ROOT/cli.py" ]]; then
  CLI=("$INSTALL_RUNTIME" "$REPO_ROOT/cli.py")
elif [[ -x "$REPO_ROOT/.venv/bin/python" && -f "$REPO_ROOT/cli.py" ]]; then
  CLI=("$REPO_ROOT/.venv/bin/python" "$REPO_ROOT/cli.py")
elif [[ -f "$REPO_ROOT/cli.py" ]]; then
  CLI=(python3 "$REPO_ROOT/cli.py")
elif command -v agensic >/dev/null 2>&1; then
  CLI=(agensic)
else
  echo "Neither 'agensic' nor '$REPO_ROOT/cli.py' is available; deterministic AI_EXECUTED signing unavailable" >&2
  exit 127
fi

if [[ $# -lt 1 ]]; then
  usage >&2
  exit 2
fi

subcommand="$1"
shift || true

case "$subcommand" in
  start)
    exec "${CLI[@]}" ai-session start "$@"
    ;;
  stop)
    exec "${CLI[@]}" ai-session stop "$@"
    ;;
  status)
    exec "${CLI[@]}" ai-session status "$@"
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    echo "Unknown subcommand: $subcommand" >&2
    usage >&2
    exit 2
    ;;
esac
