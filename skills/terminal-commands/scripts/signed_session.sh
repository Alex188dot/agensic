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
  if command -v aiterminal >/dev/null 2>&1; then
    CLI=(aiterminal)
    return 0
  fi

  if python3 -c 'import ghostshell.cli.app' >/dev/null 2>&1; then
    CLI=(python3 -c 'from ghostshell.cli.app import app; app()')
    return 0
  fi

  if [[ -n "${GHOSTSHELL_CLI_PY:-}" ]]; then
    if [[ -f "$GHOSTSHELL_CLI_PY" ]]; then
      CLI=(python3 "$GHOSTSHELL_CLI_PY")
      return 0
    fi
    echo "Configured GHOSTSHELL_CLI_PY does not exist: $GHOSTSHELL_CLI_PY" >&2
    return 1
  fi

  local repo_cli=""
  if repo_cli="$(_find_repo_cli)"; then
    CLI=(python3 "$repo_cli")
    return 0
  fi

  echo "Neither 'aiterminal' nor a discoverable 'cli.py' is available." >&2
  echo "Install GhostShell, set GHOSTSHELL_CLI_PY, or place this skill under a repo tree that contains cli.py." >&2
  return 1
}

usage() {
  cat <<'USAGE'
Usage:
  signed_session.sh start [--agent <agent_id>] [--model <model_id>] [--agent-name <name>] [--ttl-minutes N]
  signed_session.sh stop
  signed_session.sh status

Preferred session flow (no eval):
  ghostshell_session_start --agent codex --model gpt-5.3 --agent-name 'Planner A' --ttl-minutes 120
  ghostshell_session_status
  ghostshell_session_stop

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
