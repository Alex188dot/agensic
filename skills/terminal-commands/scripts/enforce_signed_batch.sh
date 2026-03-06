#!/usr/bin/env bash
set -euo pipefail

mode="strict"
input_file=""

usage() {
  cat <<'USAGE'
Usage:
  enforce_signed_batch.sh [--mode warn|strict] [--file <path>]
  <commands> | enforce_signed_batch.sh [--mode warn|strict]

Checks that command batches are wrapper-compliant:
  - One-off commands must use signed_exec/ai-exec wrappers, or
  - Commands must run inside a signed session block (start ... stop)
USAGE
}

_emit_enforcement_message() {
  local level="$1"
  local reason="$2"
  cat >&2 <<EOF
${level}: terminal-commands batch enforcement triggered (${reason})

Detected raw commands outside approved signed wrappers/session flow.
Every command must be executed through signed wrappers or inside an active signed session.

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

_normalize_mode() {
  mode="$(printf '%s' "${mode:-strict}" | tr '[:upper:]' '[:lower:]')"
  if [[ "$mode" != "warn" && "$mode" != "strict" ]]; then
    echo "Invalid --mode: $mode (expected warn|strict)" >&2
    exit 2
  fi
}

_trim_line() {
  local line="$1"
  line="${line#"${line%%[![:space:]]*}"}"
  line="${line%"${line##*[![:space:]]}"}"
  printf '%s' "$line"
}

_strip_launcher_prefixes() {
  local line="$1"

  if [[ "$line" == bash\ * ]]; then
    line="${line#bash }"
  elif [[ "$line" == zsh\ * ]]; then
    line="${line#zsh }"
  elif [[ "$line" == sh\ * ]]; then
    line="${line#sh }"
  fi

  printf '%s' "$line"
}

_is_session_start() {
  local line="$1"
  line="$(_strip_launcher_prefixes "$line")"
  [[ "$line" == "./scripts/signed_session.sh start"* \
    || "$line" == "scripts/signed_session.sh start"* \
    || "$line" == "signed_session.sh start"* \
    || "$line" == */"signed_session.sh start"* \
    || "$line" == "ghostshell_session_start"* \
    || "$line" == "aiterminal ai-session start"* \
    || "$line" == "python3 "*"/cli.py ai-session start"* ]]
}

_is_session_stop() {
  local line="$1"
  line="$(_strip_launcher_prefixes "$line")"
  [[ "$line" == "./scripts/signed_session.sh stop"* \
    || "$line" == "scripts/signed_session.sh stop"* \
    || "$line" == "signed_session.sh stop"* \
    || "$line" == */"signed_session.sh stop"* \
    || "$line" == "ghostshell_session_stop"* \
    || "$line" == "aiterminal ai-session stop"* \
    || "$line" == "python3 "*"/cli.py ai-session stop"* ]]
}

_is_signed_oneoff() {
  local line="$1"
  line="$(_strip_launcher_prefixes "$line")"
  [[ "$line" == "./scripts/signed_exec.sh "* \
    || "$line" == "scripts/signed_exec.sh "* \
    || "$line" == "signed_exec.sh "* \
    || "$line" == */"signed_exec.sh "* \
    || "$line" == "aiterminal ai-exec "* \
    || "$line" == "python3 "*"/cli.py ai-exec "* ]]
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)
      shift
      mode="${1:-}"
      ;;
    --file)
      shift
      input_file="${1:-}"
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

_normalize_mode

if [[ -n "$input_file" && ! -f "$input_file" ]]; then
  echo "Input file not found: $input_file" >&2
  exit 2
fi

session_active=0
wrapper_seen=0
violations=0
first_reason=""

process_line() {
  local raw="$1"
  local line
  line="$(_trim_line "$raw")"
  [[ -z "$line" ]] && return 0
  [[ "$line" == \#* ]] && return 0

  if _is_session_start "$line"; then
    session_active=1
    wrapper_seen=1
    return 0
  fi

  if _is_session_stop "$line"; then
    wrapper_seen=1
    session_active=0
    return 0
  fi

  if [[ "$session_active" == "1" ]]; then
    return 0
  fi

  if _is_signed_oneoff "$line"; then
    wrapper_seen=1
    return 0
  fi

  violations=$((violations + 1))
  if [[ -z "$first_reason" ]]; then
    first_reason="raw_command_detected: $line"
  fi
}

if [[ -n "$input_file" ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    process_line "$line"
  done < "$input_file"
else
  while IFS= read -r line || [[ -n "$line" ]]; do
    process_line "$line"
  done
fi

if [[ "$session_active" == "1" ]]; then
  violations=$((violations + 1))
  if [[ -z "$first_reason" ]]; then
    first_reason="session_not_stopped"
  fi
fi

if [[ "$wrapper_seen" == "0" ]]; then
  violations=$((violations + 1))
  if [[ -z "$first_reason" ]]; then
    first_reason="no_signed_wrapper_usage_detected"
  fi
fi

if [[ "$violations" -gt 0 ]]; then
  if [[ "$mode" == "strict" ]]; then
    _emit_enforcement_message "ERROR" "$first_reason"
    exit 86
  fi
  _emit_enforcement_message "WARNING" "$first_reason"
fi

exit 0
