#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

_find_repo_cli() {
  local dir="$SCRIPT_DIR"

  while [[ "$dir" != "/" ]]; do
    if [[ -f "$dir/cli.py" ]]; then
      printf '%s\n' "$dir/cli.py"
      return 0
    fi
    dir="$(dirname "$dir")"
  done

  return 1
}

_resolve_cli() {
  if command -v agensic >/dev/null 2>&1; then
    CLI=(agensic)
    return 0
  fi

  if python3 -c 'import agensic.cli.app' >/dev/null 2>&1; then
    CLI=(python3 -c 'from agensic.cli.app import app; app()')
    return 0
  fi

  if [[ -n "${AGENSIC_CLI_PY:-}" ]]; then
    if [[ -f "$AGENSIC_CLI_PY" ]]; then
      CLI=(python3 "$AGENSIC_CLI_PY")
      return 0
    fi
    echo "Configured AGENSIC_CLI_PY does not exist: $AGENSIC_CLI_PY" >&2
    return 1
  fi

  local repo_cli=""
  if repo_cli="$(_find_repo_cli)"; then
    CLI=(python3 "$repo_cli")
    return 0
  fi

  echo "Neither 'agensic' nor a discoverable 'cli.py' is available." >&2
  echo "Install Agensic, set AGENSIC_CLI_PY, or place this skill under a repo tree that contains cli.py." >&2
  return 1
}

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

Fallback export flow (deprecated, still supported):
  eval "$(signed_session.sh start --agent codex --model gpt-5.3 --agent-name 'Planner A')"
  signed_session.sh status
  eval "$(signed_session.sh stop)"
USAGE
}

CLI=()
if ! _resolve_cli; then
  echo "Deterministic AI_EXECUTED signing unavailable" >&2
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
