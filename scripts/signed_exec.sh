#!/usr/bin/env bash
set -euo pipefail

agent=""
model=""
agent_name=""
command_string=""
defaulted_identity=0
verify_enabled=1
verify_mode="${AGENSIC_SIGNED_VERIFY_MODE:-warn}"
verify_max_wait_ms="${AGENSIC_SIGNED_VERIFY_MAX_WAIT_MS:-150}"

usage() {
  cat <<'USAGE'
Usage:
  signed_exec.sh [--agent <agent_id>] [--model <model_id>] [--agent-name <name>] -- <command ...>
  signed_exec.sh [--agent <agent_id>] [--model <model_id>] [--agent-name <name>] --command "<command string>"

Verification controls:
  --verify                  Enable post-run provenance verification (default: enabled)
  --no-verify               Disable post-run provenance verification
  --verify-mode <mode>      warn|strict (default: warn)
  --verify-max-wait-ms <N>  Max wait for async provenance row (default: 150)

Notes:
  - Missing identity defaults to agent=unknown and model=unknown-model (warning emitted once)
  - Use --command for operator-heavy command strings
USAGE
}

_emit_enforcement_message() {
  local level="$1"
  local reason="$2"
  cat >&2 <<EOF
${level}: terminal-commands enforcement triggered (${reason})

The command was not verifiably recorded as AI_EXECUTED provenance.
You must run shell commands through signed wrappers.

EXAMPLE (one-off):
  ./scripts/signed_exec.sh --agent <agent_id> --model <model_id> --agent-name '<agent_name>' --command '<shell_command>'

EXAMPLE (multi-command session):
  ./scripts/signed_session.sh start --agent <agent_id> --model <model_id> --agent-name '<agent_name>' --ttl-minutes <minutes>
  <command_1>
  <command_2>
  ...
  ./scripts/signed_session.sh stop

For all remaining commands in this task, keep using the signed session and run:
  ./scripts/signed_session.sh stop
when done.
EOF
}

_normalize_verify_mode() {
  verify_mode="$(printf '%s' "${verify_mode:-warn}" | tr '[:upper:]' '[:lower:]')"
  if [[ "$verify_mode" != "warn" && "$verify_mode" != "strict" ]]; then
    echo "Invalid --verify-mode: $verify_mode (expected warn|strict)" >&2
    exit 2
  fi
}

_safe_int_or_exit() {
  local value="$1"
  local name="$2"
  if [[ ! "$value" =~ ^[0-9]+$ ]]; then
    echo "Invalid $name: $value" >&2
    exit 2
  fi
}

_shlex_join() {
  python3 - "$@" <<'PY'
import shlex
import sys

print(shlex.join(sys.argv[1:]))
PY
}

_verify_ai_executed_for_command() {
  local expected_command="$1"
  local start_ts="$2"
  local max_wait_ms="$3"
  local state_home="${XDG_STATE_HOME:-$HOME/.local/state}"
  local db_path="${AGENSIC_STATE_DB_PATH:-$state_home/agensic/state.sqlite}"

  python3 - "$db_path" "$expected_command" "$start_ts" "$max_wait_ms" <<'PY'
import os
import sqlite3
import sys
import time

db_path = sys.argv[1]
expected = sys.argv[2]
start_ts = int(sys.argv[3] or "0")
max_wait_ms = int(sys.argv[4] or "0")
deadline = time.monotonic() + (max_wait_ms / 1000.0)

if not os.path.exists(db_path):
    print("status=db_missing")
    raise SystemExit(0)

while True:
    try:
        conn = sqlite3.connect(db_path, timeout=0.2)
        row = conn.execute(
            """
            SELECT label
            FROM command_runs
            WHERE command = ?
              AND ts >= ?
            ORDER BY ts DESC
            LIMIT 1
            """,
            (expected, max(0, start_ts - 1)),
        ).fetchone()
        conn.close()
    except Exception:
        row = None

    if row is not None:
        label = str(row[0] or "").strip()
        print(f"status=found label={label}")
        raise SystemExit(0)

    if time.monotonic() >= deadline:
        print("status=missing")
        raise SystemExit(0)
    time.sleep(0.03)
PY
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --agent)
      shift
      agent="${1:-}"
      ;;
    --model)
      shift
      model="${1:-}"
      ;;
    --agent-name)
      shift
      agent_name="${1:-}"
      ;;
    --command)
      shift
      command_string="${1:-}"
      ;;
    --verify)
      verify_enabled=1
      ;;
    --no-verify)
      verify_enabled=0
      ;;
    --verify-mode)
      shift
      verify_mode="${1:-}"
      ;;
    --verify-max-wait-ms)
      shift
      verify_max_wait_ms="${1:-}"
      ;;
    --)
      shift
      break
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift || true
done

_normalize_verify_mode
_safe_int_or_exit "$verify_max_wait_ms" "--verify-max-wait-ms"

if [[ -z "$agent" ]]; then
  agent="unknown"
  defaulted_identity=1
fi
agent="$(printf '%s' "$agent" | tr '[:upper:]' '[:lower:]')"

if [[ -z "$model" ]]; then
  model="unknown-model"
  defaulted_identity=1
fi

if [[ "$defaulted_identity" == "1" ]]; then
  echo "Warning: identity missing; defaulting to agent=unknown model=unknown-model" >&2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CLI=()

if command -v agensic >/dev/null 2>&1; then
  CLI=(agensic)
elif [[ -f "$REPO_ROOT/cli.py" ]]; then
  CLI=(python3 "$REPO_ROOT/cli.py")
else
  _emit_enforcement_message "ERROR" "wrapper_cli_unavailable"
  echo "Neither 'agensic' nor '$REPO_ROOT/cli.py' is available." >&2
  exit 127
fi

base=("${CLI[@]}" ai-exec --agent "$agent" --model "$model")
if [[ -n "$agent_name" ]]; then
  base+=(--agent-name "$agent_name")
fi

run_args=()
if [[ -n "$command_string" ]]; then
  if [[ $# -gt 0 ]]; then
    echo "Cannot mix --command with argv mode after --" >&2
    usage >&2
    exit 2
  fi
  run_args=("zsh" "-lc" "$command_string")
elif [[ $# -gt 0 ]]; then
  run_args=("$@")
else
  echo "Command is required" >&2
  usage >&2
  exit 2
fi

expected_command="$(_shlex_join "${run_args[@]}")"
start_ts="$(date +%s 2>/dev/null || echo 0)"

set +e
"${base[@]}" -- "${run_args[@]}"
run_exit="$?"
set -e

if [[ "$verify_enabled" == "1" ]]; then
  verify_result="$(_verify_ai_executed_for_command "$expected_command" "$start_ts" "$verify_max_wait_ms")"
  verify_status="${verify_result%% *}"
  label=""
  if [[ "$verify_result" == *"label="* ]]; then
    label="${verify_result##*label=}"
  fi
  if [[ "$verify_status" != "status=found" || "$label" != "AI_EXECUTED" ]]; then
    if [[ "$verify_mode" == "strict" ]]; then
      _emit_enforcement_message "ERROR" "${verify_result:-verification_failed}"
      exit 86
    fi
    _emit_enforcement_message "WARNING" "${verify_result:-verification_failed}"
  fi
fi

exit "$run_exit"
