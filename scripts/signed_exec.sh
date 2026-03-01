#!/usr/bin/env bash
set -euo pipefail

agent=""
model=""
agent_name=""
command_string=""
defaulted_identity=0

usage() {
  cat <<'USAGE'
Usage:
  signed_exec.sh [--agent <agent_id>] [--model <model_id>] [--agent-name <name>] -- <command ...>
  signed_exec.sh [--agent <agent_id>] [--model <model_id>] [--agent-name <name>] --command "<command string>"

Notes:
  - Missing identity defaults to agent=unknown and model=unknown-model (warning emitted once)
  - Use --command for operator-heavy command strings
USAGE
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

if command -v aiterminal >/dev/null 2>&1; then
  CLI=(aiterminal)
elif [[ -f "$REPO_ROOT/cli.py" ]]; then
  CLI=(python3 "$REPO_ROOT/cli.py")
else
  echo "Neither 'aiterminal' nor '$REPO_ROOT/cli.py' is available; deterministic AI_EXECUTED signing unavailable" >&2
  exit 127
fi

base=("${CLI[@]}" ai-exec --agent "$agent" --model "$model")
if [[ -n "$agent_name" ]]; then
  base+=(--agent-name "$agent_name")
fi

if [[ -n "$command_string" ]]; then
  if [[ $# -gt 0 ]]; then
    echo "Cannot mix --command with argv mode after --" >&2
    usage >&2
    exit 2
  fi
  base+=(-- zsh -lc "$command_string")
  exec "${base[@]}"
fi

if [[ $# -eq 0 ]]; then
  echo "Command is required" >&2
  usage >&2
  exit 2
fi

base+=(--)
base+=("$@")
exec "${base[@]}"
