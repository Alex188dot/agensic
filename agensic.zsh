# Agensic Zsh Plugin - Space-triggered LLM fallback model

typeset -g -a AGENSIC_SUGGESTIONS
AGENSIC_SUGGESTIONS=()
typeset -g -a AGENSIC_DISPLAY_TEXTS
AGENSIC_DISPLAY_TEXTS=()
typeset -g -a AGENSIC_ACCEPT_MODES
AGENSIC_ACCEPT_MODES=()
typeset -g -a AGENSIC_SUGGESTION_KINDS
AGENSIC_SUGGESTION_KINDS=()
typeset -g AGENSIC_SUGGESTION_INDEX=1
typeset -g AGENSIC_STATUS_PREFIX="__AGENSIC_STATUS__:"
typeset -g AGENSIC_MAX_LLM_CALLS_PER_LINE=4
typeset -g AGENSIC_LLM_BUDGET_UNLIMITED=0
typeset -g AGENSIC_AUTOCOMPLETE_ENABLED=1
typeset -g AGENSIC_AUTO_SESSIONS_ENABLED=1
typeset -g AGENSIC_LLM_BUDGET_REACHED_HINT="LLM budget reached for this command line"
typeset -g AGENSIC_LINE_LLM_CALLS_USED=0
typeset -g AGENSIC_LINE_HAS_SPACE=0
typeset -g AGENSIC_SHOW_CTRL_SPACE_HINT=0
typeset -g AGENSIC_LAST_FETCH_USED_AI=0
typeset -g AGENSIC_LAST_NL_INPUT=""
typeset -g AGENSIC_LAST_NL_KIND=""
typeset -g AGENSIC_LAST_NL_COMMAND=""
typeset -g AGENSIC_LAST_NL_EXPLANATION=""
typeset -g AGENSIC_LAST_NL_ALTERNATIVES=""
typeset -g AGENSIC_LAST_NL_ASSIST=""
typeset -g AGENSIC_LAST_NL_QUESTION=""
typeset -g AGENSIC_LAST_NL_AI_AGENT=""
typeset -g AGENSIC_LAST_NL_AI_PROVIDER=""
typeset -g AGENSIC_LAST_NL_AI_MODEL=""
typeset -g -a AGENSIC_INTENT_OPTIONS
AGENSIC_INTENT_OPTIONS=()
typeset -g AGENSIC_INTENT_OPTION_INDEX=1
typeset -g AGENSIC_INTENT_ACTIVE=0

# Timer for pause detection
typeset -g AGENSIC_TIMER_FD=""
typeset -g AGENSIC_TIMER_PID=""
typeset -g AGENSIC_LAST_BUFFER=""
typeset -g AGENSIC_LAST_EXECUTED_CMD=""
typeset -g AGENSIC_LAST_EXECUTED_STARTED_AT_MS=0
typeset -g AGENSIC_RUNTIME_CAPTURE_STDOUT_PATH=""
typeset -g AGENSIC_RUNTIME_CAPTURE_STDERR_PATH=""
typeset -g AGENSIC_RUNTIME_CAPTURE_ORIG_STDOUT_FD=""
typeset -g AGENSIC_RUNTIME_CAPTURE_ORIG_STDERR_FD=""
typeset -g AGENSIC_FORCE_RUNTIME_OUTPUT_CAPTURE=0
typeset -g AGENSIC_RUNTIME_CAPTURE_ENV_ACTIVE=0
typeset -g AGENSIC_RUNTIME_CAPTURE_SAVED_FORCE_COLOR=""
typeset -g AGENSIC_RUNTIME_CAPTURE_SAVED_FORCE_COLOR_SET=0
typeset -g AGENSIC_RUNTIME_CAPTURE_SAVED_CLICOLOR_FORCE=""
typeset -g AGENSIC_RUNTIME_CAPTURE_SAVED_CLICOLOR_FORCE_SET=0
typeset -g AGENSIC_RUNTIME_CAPTURE_SAVED_PY_COLORS=""
typeset -g AGENSIC_RUNTIME_CAPTURE_SAVED_PY_COLORS_SET=0
typeset -g AGENSIC_RUNTIME_CAPTURE_SAVED_TTY_COMPATIBLE=""
typeset -g AGENSIC_RUNTIME_CAPTURE_SAVED_TTY_COMPATIBLE_SET=0
typeset -g AGENSIC_RUNTIME_CAPTURE_SAVED_TTY_INTERACTIVE=""
typeset -g AGENSIC_RUNTIME_CAPTURE_SAVED_TTY_INTERACTIVE_SET=0
typeset -g AGENSIC_RUNTIME_CAPTURE_SAVED_NO_COLOR=""
typeset -g AGENSIC_RUNTIME_CAPTURE_SAVED_NO_COLOR_SET=0
typeset -g AGENSIC_LINE_LAST_ACTION=""
typeset -g AGENSIC_LINE_ACCEPTED_ORIGIN=""
typeset -g AGENSIC_LINE_ACCEPTED_MODE=""
typeset -g AGENSIC_LINE_ACCEPTED_KIND=""
typeset -g AGENSIC_LINE_MANUAL_EDIT_AFTER_ACCEPT=0
typeset -g AGENSIC_LINE_ACCEPTED_AI_AGENT=""
typeset -g AGENSIC_LINE_ACCEPTED_AI_PROVIDER=""
typeset -g AGENSIC_LINE_ACCEPTED_AI_MODEL=""
typeset -g AGENSIC_LAST_FETCH_AI_AGENT=""
typeset -g AGENSIC_LAST_FETCH_AI_PROVIDER=""
typeset -g AGENSIC_LAST_FETCH_AI_MODEL=""
typeset -g AGENSIC_PENDING_LAST_ACTION=""
typeset -g AGENSIC_PENDING_ACCEPTED_ORIGIN=""
typeset -g AGENSIC_PENDING_ACCEPTED_MODE=""
typeset -g AGENSIC_PENDING_ACCEPTED_KIND=""
typeset -g AGENSIC_PENDING_MANUAL_EDIT_AFTER_ACCEPT=0
typeset -g AGENSIC_PENDING_AI_AGENT=""
typeset -g AGENSIC_PENDING_AI_PROVIDER=""
typeset -g AGENSIC_PENDING_AI_MODEL=""
typeset -g AGENSIC_PENDING_AGENT_NAME=""
typeset -g AGENSIC_PENDING_PROOF_LABEL=""
typeset -g AGENSIC_PENDING_PROOF_AGENT=""
typeset -g AGENSIC_PENDING_PROOF_MODEL=""
typeset -g AGENSIC_PENDING_PROOF_TRACE=""
typeset -g AGENSIC_PENDING_PROOF_TIMESTAMP=0
typeset -g AGENSIC_PENDING_PROOF_SIGNATURE=""
typeset -g AGENSIC_PENDING_PROOF_SIGNER_SCOPE=""
typeset -g AGENSIC_PENDING_PROOF_KEY_FINGERPRINT=""
typeset -g AGENSIC_PENDING_PROOF_HOST_FINGERPRINT=""
typeset -g AGENSIC_NEXT_PROOF_LABEL=""
typeset -g AGENSIC_NEXT_PROOF_AGENT=""
typeset -g AGENSIC_NEXT_PROOF_MODEL=""
typeset -g AGENSIC_NEXT_PROOF_TRACE=""
typeset -g AGENSIC_NEXT_PROOF_TIMESTAMP=0
typeset -g AGENSIC_NEXT_PROOF_SIGNATURE=""
typeset -g AGENSIC_NEXT_PROOF_SIGNER_SCOPE=""
typeset -g AGENSIC_NEXT_PROOF_KEY_FINGERPRINT=""
typeset -g AGENSIC_NEXT_PROOF_HOST_FINGERPRINT=""
typeset -g AGENSIC_AI_SESSION_COUNTER=0
typeset -g AGENSIC_AI_SESSION_TIMER_PID=""
typeset -g AGENSIC_AI_SESSION_OWNER_SHELL_PID=""
typeset -g AGENSIC_AI_SESSION_AUTO_STOP_ARMED=0
typeset -g AGENSIC_HOOKS_REGISTERED=0
typeset -gA AGENSIC_NATIVE_ESC_WIDGET
AGENSIC_NATIVE_ESC_WIDGET=()
typeset -g -a AGENSIC_DISABLED_PATTERNS
AGENSIC_DISABLED_PATTERNS=()
typeset -g AGENSIC_SOURCE_PATH="${(%):-%N}"
typeset -g AGENSIC_CONFIG_HOME="${XDG_CONFIG_HOME:-${HOME}/.config}"
typeset -g AGENSIC_STATE_HOME="${XDG_STATE_HOME:-${HOME}/.local/state}"
typeset -g AGENSIC_CACHE_HOME="${XDG_CACHE_HOME:-${HOME}/.cache}"
if [[ -z "$AGENSIC_SOURCE_PATH" || "$AGENSIC_SOURCE_PATH" == "zsh" ]]; then
    AGENSIC_SOURCE_PATH="${AGENSIC_STATE_HOME}/agensic/install/agensic.zsh"
fi
typeset -g AGENSIC_SOURCE_DIR="${AGENSIC_SOURCE_PATH:A:h}"
typeset -g AGENSIC_HOME="${AGENSIC_STATE_HOME}/agensic"
typeset -g AGENSIC_CONFIG_PATH="${AGENSIC_CONFIG_HOME}/agensic/config.json"
typeset -g AGENSIC_AUTH_PATH="${AGENSIC_CONFIG_HOME}/agensic/auth.json"
typeset -g AGENSIC_SHARED_HELPERS_PATH="${AGENSIC_SOURCE_DIR}/shell/agensic_shared.sh"
typeset -g AGENSIC_CLIENT_HELPER="${AGENSIC_SOURCE_DIR}/shell_client.py"
typeset -g AGENSIC_RUNTIME_PYTHON="${AGENSIC_RUNTIME_PYTHON:-}"
if [[ -z "$AGENSIC_RUNTIME_PYTHON" ]]; then
    AGENSIC_RUNTIME_PYTHON="${AGENSIC_HOME}/install/.venv/bin/python"
fi
if [[ ! -x "$AGENSIC_RUNTIME_PYTHON" ]]; then
    AGENSIC_RUNTIME_PYTHON="${AGENSIC_SOURCE_DIR}/.venv/bin/python"
fi
if [[ ! -x "$AGENSIC_RUNTIME_PYTHON" ]]; then
    AGENSIC_RUNTIME_PYTHON="python3"
fi
typeset -g AGENSIC_PLUGIN_LOG="${AGENSIC_HOME}/plugin.log"
typeset -g AGENSIC_AI_SESSION_STATE_PATH="${AGENSIC_HOME}/ai_session.env"
typeset -g AGENSIC_UNINSTALL_SENTINEL="${TMPDIR:-/tmp}/agensic-shell-uninstalled-${UID:-${EUID:-0}}"
typeset -g AGENSIC_SESSION_DISABLED=0
typeset -g AGENSIC_CONFIG_MTIME=""
typeset -g AGENSIC_AUTH_MTIME=""
typeset -g AGENSIC_AUTH_TOKEN=""
typeset -g AGENSIC_FETCH_ATTEMPT_COUNT=0
typeset -g AGENSIC_FETCH_SUCCESS_COUNT=0
typeset -g AGENSIC_LAST_FETCH_ERROR_CODE=""
typeset -g AGENSIC_FETCH_LOG_THROTTLE_SECONDS=10
typeset -g AGENSIC_FETCH_LOG_LAST_KEY=""
typeset -g AGENSIC_FETCH_LOG_LAST_TS=0
typeset -g AGENSIC_AUTO_SESSION_REGISTRY_STATE=""
typeset -g -a AGENSIC_AUTO_SESSION_WRAPPERS
AGENSIC_AUTO_SESSION_WRAPPERS=()
typeset -g -a AGENSIC_PATH_HEAVY_EXECUTABLES
AGENSIC_PATH_HEAVY_EXECUTABLES=(cd ls cat less more head tail vi vim nvim nano code source open cp mv mkdir rmdir touch find grep rg sed awk bat)
typeset -g -a AGENSIC_SCRIPT_EXECUTABLES
AGENSIC_SCRIPT_EXECUTABLES=(python python3 python3.11 python3.12 node bash sh zsh ruby perl php lua)
typeset -g -a AGENSIC_BLOCKED_EXECUTABLES
AGENSIC_BLOCKED_EXECUTABLES=(
    rm
    dd
    wipefs
    shred
    fdisk
    sfdisk
    cfdisk
    parted
    diskutil
    mkfs
    newfs
    mdadm
    zpool
    lvremove
    vgremove
    pvremove
    cryptsetup
    passwd
    chpasswd
    usermod
    userdel
    groupdel
)
typeset -g -a AGENSIC_BLOCKED_EXECUTABLE_PREFIXES
AGENSIC_BLOCKED_EXECUTABLE_PREFIXES=(mkfs. mkfs_ newfs)
typeset -g -a AGENSIC_TTY_SENSITIVE_EXECUTABLES
AGENSIC_TTY_SENSITIVE_EXECUTABLES=(less more man top htop watch vi vim nvim nano emacs fzf tig ssh sftp scp ftp telnet tmux screen)
typeset -g -a AGENSIC_AUTO_SESSION_RESERVED_WORDS
AGENSIC_AUTO_SESSION_RESERVED_WORDS=(continue)

if [[ -f "$AGENSIC_SHARED_HELPERS_PATH" ]]; then
    source "$AGENSIC_SHARED_HELPERS_PATH"
fi

_agensic_extract_executable_token() {
    local command="$1"
    local -a tokens
    local token=""
    local exe=""
    local i=1

    tokens=(${(z)command})
    if (( ${#tokens[@]} == 0 )); then
        return 1
    fi

    while (( i <= ${#tokens[@]} )); do
        token="${tokens[$i]}"
        if [[ -z "$token" ]]; then
            (( i++ ))
            continue
        fi

        case "$token" in
            sudo|command)
                (( i++ ))
                continue
                ;;
            env|/usr/bin/env)
                (( i++ ))
                while (( i <= ${#tokens[@]} )); do
                    token="${tokens[$i]}"
                    if [[ -z "$token" || "$token" == -* || "$token" == *=* ]]; then
                        (( i++ ))
                        continue
                    fi
                    break
                done
                continue
                ;;
            -*|*=*)
                (( i++ ))
                continue
                ;;
            *)
                exe="$token"
                break
                ;;
        esac
    done

    if [[ -z "$exe" ]]; then
        return 1
    fi

    exe="${exe:t}"
    print -r -- "${(L)exe}"
    return 0
}

_agensic_normalize_pattern_token() {
    local raw="$1"
    local -a tokens
    local token=""

    tokens=(${(z)raw})
    if (( ${#tokens[@]} == 0 )); then
        print -r -- ""
        return
    fi
    token="${tokens[1]}"
    token="${token:t}"
    print -r -- "${(L)token}"
}

_agensic_get_config_mtime() {
    if [[ ! -f "$AGENSIC_CONFIG_PATH" ]]; then
        print -r -- ""
        return
    fi
    local mtime
    mtime="$(stat -f '%m' "$AGENSIC_CONFIG_PATH" 2>/dev/null)"
    print -r -- "$mtime"
}

_agensic_get_auth_mtime() {
    if [[ ! -f "$AGENSIC_AUTH_PATH" ]]; then
        print -r -- ""
        return
    fi
    local mtime
    mtime="$(stat -f '%m' "$AGENSIC_AUTH_PATH" 2>/dev/null)"
    print -r -- "$mtime"
}

_agensic_get_agent_registry_state() {
    local builtin_path="${AGENSIC_SOURCE_DIR}/agensic/engine/data/agents_builtin.json"
    local local_override_path="${AGENSIC_CONFIG_HOME}/agensic/agent_registry.local.json"
    local builtin_mtime=""
    local local_mtime=""

    if [[ -f "$builtin_path" ]]; then
        builtin_mtime="$(stat -f '%m' "$builtin_path" 2>/dev/null)"
    fi
    if [[ -f "$local_override_path" ]]; then
        local_mtime="$(stat -f '%m' "$local_override_path" 2>/dev/null)"
    fi
    print -r -- "${builtin_mtime}|${local_mtime}"
}

_agensic_reload_auth_token_if_needed() {
    local current_mtime
    current_mtime="$(_agensic_get_auth_mtime)"
    if [[ "$current_mtime" == "$AGENSIC_AUTH_MTIME" ]]; then
        return
    fi
    AGENSIC_AUTH_MTIME="$current_mtime"
    AGENSIC_AUTH_TOKEN=""
    if [[ -z "$current_mtime" ]]; then
        return
    fi

    local escaped_path="${AGENSIC_AUTH_PATH//\'/\'\\\'\'}"
    local token
    token="$(
        python3 -c "
import json
path = '''$escaped_path'''
try:
    with open(path, 'r', encoding='utf-8') as fh:
        payload = json.load(fh)
except Exception:
    payload = {}
if not isinstance(payload, dict):
    payload = {}
print(str(payload.get('auth_token', '') or '').strip())
" 2>/dev/null
    )"
    AGENSIC_AUTH_TOKEN="$token"
}

_agensic_reload_disabled_patterns_if_needed() {
    local current_mtime
    current_mtime="$(_agensic_get_config_mtime)"
    if [[ "$current_mtime" == "$AGENSIC_CONFIG_MTIME" ]]; then
        return
    fi
    AGENSIC_CONFIG_MTIME="$current_mtime"
    AGENSIC_DISABLED_PATTERNS=()
    AGENSIC_MAX_LLM_CALLS_PER_LINE=4
    AGENSIC_LLM_BUDGET_UNLIMITED=0
    AGENSIC_AUTOCOMPLETE_ENABLED=1
    AGENSIC_AUTO_SESSIONS_ENABLED=1

    if [[ -z "$current_mtime" ]]; then
        return
    fi

    local escaped_path="${AGENSIC_CONFIG_PATH//\'/\'\\\'\'}"
    local sep=$'\x1f'
    local response
    response=$(python3 -c "
import json, os, shlex
path = '''$escaped_path'''

def normalize(value):
    raw = str(value or '').strip()
    if not raw:
        return ''
    try:
        parts = shlex.split(raw, posix=True)
        token = parts[0] if parts else ''
    except Exception:
        token = raw.split()[0] if raw.split() else ''
    token = os.path.basename(token).strip().lower()
    return token

patterns = []
seen = set()
budget = 4
unlimited = False
try:
    with open(path, 'r', encoding='utf-8') as fh:
        payload = json.load(fh)
except Exception:
    payload = {}

for value in (payload.get('disabled_command_patterns') or []):
    normalized = normalize(value)
    if normalized and normalized not in seen:
        seen.add(normalized)
        patterns.append(normalized)

raw_budget = payload.get('llm_calls_per_line', 4)
try:
    parsed_budget = int(raw_budget)
except Exception:
    parsed_budget = 4
if parsed_budget < 0 or parsed_budget > 99:
    parsed_budget = 4
budget = parsed_budget
unlimited = bool(payload.get('llm_budget_unlimited', False))
autocomplete_enabled = bool(payload.get('autocomplete_enabled', True))
auto_sessions_enabled = bool(payload.get('automatic_agensic_sessions_enabled', True))

print(str(budget))
print('1' if unlimited else '0')
print('1' if autocomplete_enabled else '0')
print('1' if auto_sessions_enabled else '0')
print('\x1f'.join(patterns))
" 2>/dev/null)

    if [[ -n "$response" ]]; then
        local -a response_lines
        response_lines=("${(@f)response}")
        if [[ "${response_lines[1]}" == <-> ]]; then
            AGENSIC_MAX_LLM_CALLS_PER_LINE="${response_lines[1]}"
        fi
        if [[ "${response_lines[2]}" == "1" ]]; then
            AGENSIC_LLM_BUDGET_UNLIMITED=1
        fi
        if [[ "${response_lines[3]}" == "0" ]]; then
            AGENSIC_AUTOCOMPLETE_ENABLED=0
        fi
        if [[ "${response_lines[4]}" == "0" ]]; then
            AGENSIC_AUTO_SESSIONS_ENABLED=0
        fi
        local patterns_line="${response_lines[5]}"
        if [[ -n "$patterns_line" ]]; then
            AGENSIC_DISABLED_PATTERNS=("${(ps:$sep:)patterns_line}")
        fi
    fi
}

_agensic_autocomplete_is_disabled() {
    _agensic_reload_disabled_patterns_if_needed
    [[ "${AGENSIC_AUTOCOMPLETE_ENABLED:-1}" != "1" ]]
}

_agensic_matches_disabled_pattern() {
    local command="$1"
    local exe=""
    local pattern

    _agensic_reload_disabled_patterns_if_needed
    if (( ${#AGENSIC_DISABLED_PATTERNS[@]} == 0 )); then
        return 1
    fi

    exe="$(_agensic_extract_executable_token "$command")"
    if [[ -z "$exe" ]]; then
        return 1
    fi

    for pattern in "${AGENSIC_DISABLED_PATTERNS[@]}"; do
        if [[ "$exe" == "$pattern"* || "$pattern" == "$exe"* ]]; then
            return 0
        fi
    done
    return 1
}

_agensic_should_preserve_native_tab() {
    local buffer="$BUFFER"
    local exe=""
    local -a tokens
    local token=""

    exe="$(_agensic_extract_executable_token "$buffer")"
    if [[ -z "$exe" ]]; then
        return 1
    fi

    if _agensic_value_in_array "$exe" "${AGENSIC_PATH_HEAVY_EXECUTABLES[@]}"; then
        return 0
    fi

    tokens=(${(z)buffer})
    for token in "${tokens[@]}"; do
        if _agensic_token_looks_path_or_file "$token"; then
            return 0
        fi
    done

    if _agensic_value_in_array "$exe" "${AGENSIC_SCRIPT_EXECUTABLES[@]}"; then
        if [[ "$buffer" == *[[:space:]] || ${#tokens[@]} -ge 2 ]]; then
            return 0
        fi
    fi

    return 1
}

_agensic_should_skip_agensic_for_buffer() {
    if _agensic_matches_disabled_pattern "$BUFFER"; then
        return 0
    fi
    _agensic_is_blocked_runtime_command "$BUFFER"
}

_agensic_print_manual_session_hint() {
    local executable="${1:l}"
    case "$executable" in
        ollama)
            zle -I 2>/dev/null || true
            print -P -- "%F{red}To enable Agensic Sessions with Ollama, use: agensic run ollama%f" >&2
            ;;
    esac
}

_agensic_auto_session_wrapper_is_valid() {
    local executable="${1:l}"
    if [[ -z "$executable" ]]; then
        return 1
    fi
    if ! [[ "$executable" =~ '^[A-Za-z_][A-Za-z0-9._-]*$' ]]; then
        return 1
    fi
    if _agensic_value_in_array "$executable" "${AGENSIC_AUTO_SESSION_RESERVED_WORDS[@]}"; then
        return 1
    fi
    return 0
}

_agensic_unregister_auto_session_wrappers() {
    local wrapper=""
    for wrapper in "${AGENSIC_AUTO_SESSION_WRAPPERS[@]}"; do
        unfunction -- "$wrapper" 2>/dev/null || true
    done
    AGENSIC_AUTO_SESSION_WRAPPERS=()
}

_agensic_define_auto_session_wrapper() {
    local executable="${1:l}"
    local mode="${2:l}"
    if ! _agensic_auto_session_wrapper_is_valid "$executable"; then
        return
    fi
    eval "${executable}() { _agensic_auto_session_exec ${(qqq)executable} ${(qqq)mode} \"\$@\"; }"
    AGENSIC_AUTO_SESSION_WRAPPERS+=("$executable")
}

_agensic_load_auto_session_wrappers() {
    if [[ -z "$AGENSIC_RUNTIME_PYTHON" ]]; then
        return 1
    fi
    "$AGENSIC_RUNTIME_PYTHON" -c "
from collections import OrderedDict

try:
    from agensic.engine.agent_registry import AgentRegistry
except Exception:
    raise SystemExit(0)

try:
    registry = AgentRegistry()
except Exception:
    raise SystemExit(0)

entries = OrderedDict()
for agent in registry.list_agents():
    if not isinstance(agent, dict):
        continue
    agent_id = str(agent.get('agent_id', '') or '').strip().lower()
    mode = 'manual_hint' if agent_id == 'ollama' else 'track'
    for raw_executable in (agent.get('executables') or []):
        executable = str(raw_executable or '').strip().lower()
        if not executable or executable in entries:
            continue
        entries[executable] = mode

for executable, mode in entries.items():
    print(f'{mode}\t{executable}')
" 2>/dev/null
}

_agensic_refresh_auto_session_wrappers_if_needed() {
    local state="$(_agensic_get_agent_registry_state)"
    if [[ "$state" == "$AGENSIC_AUTO_SESSION_REGISTRY_STATE" && ${#AGENSIC_AUTO_SESSION_WRAPPERS[@]} -gt 0 ]]; then
        return
    fi

    local entries=""
    local -a entry_lines
    local line=""
    local mode=""
    local executable=""

    entries="$(_agensic_load_auto_session_wrappers)"
    _agensic_unregister_auto_session_wrappers
    AGENSIC_AUTO_SESSION_REGISTRY_STATE="$state"

    if [[ -z "$entries" ]]; then
        return
    fi

    entry_lines=("${(@f)entries}")
    for line in "${entry_lines[@]}"; do
        mode="${line%%$'\t'*}"
        executable="${line#*$'\t'}"
        if [[ -z "$mode" || -z "$executable" ]]; then
            continue
        fi
        _agensic_define_auto_session_wrapper "$executable" "$mode"
    done
}

_agensic_auto_session_exec() {
    local executable="${1:l}"
    local mode="${2:l}"
    shift 2

    _agensic_reload_disabled_patterns_if_needed
    if [[ "${AGENSIC_TRACK_ACTIVE:-0}" == "1" || "${AGENSIC_AUTO_SESSIONS_ENABLED:-1}" != "1" ]]; then
        command "$executable" "$@"
        return $?
    fi

    case "$mode" in
        track)
            agensic run "$executable" "$@"
            return $?
            ;;
        manual_hint)
            _agensic_print_manual_session_hint "$executable"
            command "$executable" "$@"
            return $?
            ;;
    esac

    command "$executable" "$@"
    return $?
}

# ======================================================
# 1. CORE LOGIC (Fetch, Display, Feedback)
# ======================================================

_agensic_now_epoch() {
    local now
    now="$(date +%s 2>/dev/null)"
    if [[ -z "$now" ]]; then
        now="0"
    fi
    print -r -- "$now"
}

_agensic_disable_mouse_reporting() {
    if [[ ! -t 1 ]]; then
        return
    fi
    # Defensive reset for xterm mouse tracking modes to avoid leaked escape streams.
    printf '\033[?1000l\033[?1002l\033[?1003l\033[?1006l\033[?1015l' > /dev/tty 2>/dev/null || true
}

_agensic_is_self_cli_command() {
    local command="$1"
    local exe=""
    local -a tokens
    local token=""
    local normalized=""

    exe="$(_agensic_extract_executable_token "$command")"
    case "$exe" in
        agensic)
            return 0
            ;;
    esac

    tokens=(${(z)command})
    if (( ${#tokens[@]} == 0 )); then
        return 1
    fi

    for token in "${tokens[@]}"; do
        if [[ -z "$token" || "$token" == -* || "$token" == *=* ]]; then
            continue
        fi
        normalized="${token:A}"
        if [[ "$normalized" == "${AGENSIC_HOME:A}/cli.py" || "$normalized" == "${AGENSIC_SOURCE_DIR:A}/cli.py" ]]; then
            return 0
        fi
    done
    return 1
}

_agensic_now_epoch_ms() {
    if [[ -z "${EPOCHREALTIME:-}" ]]; then
        zmodload zsh/datetime 2>/dev/null || true
    fi
    if [[ -n "${EPOCHREALTIME:-}" ]]; then
        local now_ms="$(( EPOCHREALTIME * 1000 ))"
        print -r -- "${now_ms%.*}"
        return
    fi
    local now
    now="$(_agensic_now_epoch)"
    if [[ "$now" != <-> ]]; then
        now="0"
    fi
    print -r -- "$(( now * 1000 ))"
}

_agensic_disable_session() {
    AGENSIC_SESSION_DISABLED=1
    _agensic_clear_suggestions
    _agensic_reset_line_state
    POSTDISPLAY=""
    region_highlight=()
}

_agensic_session_is_disabled() {
    if (( AGENSIC_SESSION_DISABLED == 1 )); then
        return 0
    fi
    if [[ -f "$AGENSIC_UNINSTALL_SENTINEL" ]]; then
        _agensic_disable_session
        return 0
    fi
    return 1
}

_agensic_log_fetch_error() {
    if _agensic_session_is_disabled; then
        return
    fi
    local error_code="$1"
    local trigger_source="$2"
    local buffer_len="$3"
    if [[ -z "$error_code" ]]; then
        return
    fi

    local key="${error_code}|${trigger_source}"
    local now="$(_agensic_now_epoch)"
    local throttle="${AGENSIC_FETCH_LOG_THROTTLE_SECONDS:-10}"

    if [[ "$key" == "$AGENSIC_FETCH_LOG_LAST_KEY" ]]; then
        if [[ "$now" == <-> && "$AGENSIC_FETCH_LOG_LAST_TS" == <-> ]]; then
            local delta=$(( now - AGENSIC_FETCH_LOG_LAST_TS ))
            if (( delta >= 0 && delta < throttle )); then
                return
            fi
        fi
    fi

    AGENSIC_FETCH_LOG_LAST_KEY="$key"
    AGENSIC_FETCH_LOG_LAST_TS="$now"

    mkdir -p "$AGENSIC_HOME" 2>/dev/null
    chmod 700 "$AGENSIC_HOME" 2>/dev/null
    {
        printf "%s error=%s trigger=%s buffer_len=%s\n" \
            "$(date '+%Y-%m-%d %H:%M:%S' 2>/dev/null)" \
            "$error_code" \
            "$trigger_source" \
            "$buffer_len"
    } >> "$AGENSIC_PLUGIN_LOG" 2>/dev/null
    chmod 600 "$AGENSIC_PLUGIN_LOG" 2>/dev/null
}

_agensic_fetch_suggestions() {
    if _agensic_session_is_disabled; then
        AGENSIC_SUGGESTIONS=()
        AGENSIC_DISPLAY_TEXTS=()
        AGENSIC_ACCEPT_MODES=()
        AGENSIC_SUGGESTION_KINDS=()
        AGENSIC_SUGGESTION_INDEX=1
        return
    fi
    if _agensic_autocomplete_is_disabled; then
        AGENSIC_SUGGESTIONS=()
        AGENSIC_DISPLAY_TEXTS=()
        AGENSIC_ACCEPT_MODES=()
        AGENSIC_SUGGESTION_KINDS=()
        AGENSIC_SUGGESTION_INDEX=1
        return
    fi
    local allow_ai="${1:-1}"
    local trigger_source="${2:-unknown}"
    local buffer_content="$BUFFER"
    local sep=$'\x1f'
    AGENSIC_LAST_FETCH_USED_AI=0
    AGENSIC_LAST_FETCH_AI_AGENT=""
    AGENSIC_LAST_FETCH_AI_PROVIDER=""
    AGENSIC_LAST_FETCH_AI_MODEL=""
    AGENSIC_FETCH_ATTEMPT_COUNT=$((AGENSIC_FETCH_ATTEMPT_COUNT + 1))
    
    # Don't fetch if buffer is too short
    if [[ ${#buffer_content} -lt 2 ]]; then
        AGENSIC_SUGGESTIONS=()
        AGENSIC_DISPLAY_TEXTS=()
        AGENSIC_ACCEPT_MODES=()
        AGENSIC_SUGGESTION_KINDS=()
        return
    fi

    local request_json
    request_json="$(
        AGENSIC_REQ_BUFFER="$buffer_content" \
        AGENSIC_REQ_CURSOR="$CURSOR" \
        AGENSIC_REQ_CWD="$PWD" \
        AGENSIC_REQ_ALLOW_AI="$allow_ai" \
        AGENSIC_REQ_TRIGGER_SOURCE="$trigger_source" \
        python3 -c "
import json, os
payload = {
    'command_buffer': os.environ.get('AGENSIC_REQ_BUFFER', ''),
    'cursor_position': int(os.environ.get('AGENSIC_REQ_CURSOR', '0') or '0'),
    'working_directory': os.environ.get('AGENSIC_REQ_CWD', ''),
    'shell': 'zsh',
    'allow_ai': bool(int(os.environ.get('AGENSIC_REQ_ALLOW_AI', '1') or '1')),
    'trigger_source': os.environ.get('AGENSIC_REQ_TRIGGER_SOURCE', 'unknown'),
}
print(json.dumps(payload, separators=(',', ':')))
" 2>/dev/null
    )"

    if [[ -z "$request_json" ]]; then
        AGENSIC_LAST_FETCH_ERROR_CODE="payload_build_error"
        _agensic_log_fetch_error "$AGENSIC_LAST_FETCH_ERROR_CODE" "$trigger_source" "${#buffer_content}"
        AGENSIC_SUGGESTIONS=()
        AGENSIC_DISPLAY_TEXTS=()
        AGENSIC_ACCEPT_MODES=()
        AGENSIC_SUGGESTION_KINDS=()
        AGENSIC_SUGGESTION_INDEX=1
        return
    fi

    if [[ ! -f "$AGENSIC_CLIENT_HELPER" ]]; then
        AGENSIC_LAST_FETCH_ERROR_CODE="helper_missing"
        _agensic_log_fetch_error "$AGENSIC_LAST_FETCH_ERROR_CODE" "$trigger_source" "${#buffer_content}"
        AGENSIC_SUGGESTIONS=()
        AGENSIC_DISPLAY_TEXTS=()
        AGENSIC_ACCEPT_MODES=()
        AGENSIC_SUGGESTION_KINDS=()
        AGENSIC_SUGGESTION_INDEX=1
        return
    fi

    _agensic_reload_auth_token_if_needed
    local response_json
    local -a helper_cmd
    helper_cmd=("$AGENSIC_RUNTIME_PYTHON" "$AGENSIC_CLIENT_HELPER" --timeout 3.0)
    if [[ -n "$AGENSIC_AUTH_TOKEN" ]]; then
        helper_cmd+=("--auth-token=$AGENSIC_AUTH_TOKEN")
    fi
    response_json="$(printf '%s' "$request_json" | "${helper_cmd[@]}" 2>/dev/null)"

    local parsed
    parsed="$(
        AGENSIC_CLIENT_RESPONSE="$response_json" \
        python3 -c "
import json, os
sep = '\x1f'
raw = os.environ.get('AGENSIC_CLIENT_RESPONSE', '')
try:
    data = json.loads(raw)
except Exception:
    print('ok=0')
    print('error_code=bad_client_json')
    raise SystemExit(0)

ok = bool(data.get('ok', False))
error_code = str(data.get('error_code', '') or '')
used_ai = '1' if bool(data.get('used_ai', False)) else '0'
ai_agent = str(data.get('ai_agent', '') or '')
ai_provider = str(data.get('ai_provider', '') or '')
ai_model = str(data.get('ai_model', '') or '')

if not ok:
    print('ok=0')
    print('error_code=' + error_code)
    print('used_ai=' + used_ai)
    print('ai_agent=' + ai_agent)
    print('ai_provider=' + ai_provider)
    print('ai_model=' + ai_model)
    raise SystemExit(0)

def clean_list(value):
    if not isinstance(value, list):
        return []
    return [str(item or '') for item in value]

pool = clean_list(data.get('pool'))[:20]
display = clean_list(data.get('display'))[:20]
modes = clean_list(data.get('modes'))[:20]
kinds = clean_list(data.get('kinds'))[:20]

print('ok=1')
print('error_code=')
print('used_ai=' + used_ai)
print('ai_agent=' + ai_agent)
print('ai_provider=' + ai_provider)
print('ai_model=' + ai_model)
print('pool=' + sep.join(pool))
print('display=' + sep.join(display))
print('modes=' + sep.join(modes))
print('kinds=' + sep.join(kinds))
" 2>/dev/null
    )"

    local -a parsed_lines
    parsed_lines=("${(@f)parsed}")
    local ok_value="${parsed_lines[1]#ok=}"
    local error_code="${parsed_lines[2]#error_code=}"
    local used_ai_line="${parsed_lines[3]#used_ai=}"
    local ai_agent_line="${parsed_lines[4]#ai_agent=}"
    local ai_provider_line="${parsed_lines[5]#ai_provider=}"
    local ai_model_line="${parsed_lines[6]#ai_model=}"
    local pool_line="${parsed_lines[7]#pool=}"
    local display_line="${parsed_lines[8]#display=}"
    local mode_line="${parsed_lines[9]#modes=}"
    local kind_line="${parsed_lines[10]#kinds=}"

    if [[ "$ok_value" != "1" ]]; then
        AGENSIC_LAST_FETCH_ERROR_CODE="${error_code:-client_fetch_failed}"
        _agensic_log_fetch_error "$AGENSIC_LAST_FETCH_ERROR_CODE" "$trigger_source" "${#buffer_content}"
        AGENSIC_SUGGESTIONS=()
        AGENSIC_DISPLAY_TEXTS=()
        AGENSIC_ACCEPT_MODES=()
        AGENSIC_SUGGESTION_KINDS=()
        AGENSIC_SUGGESTION_INDEX=1
        return
    fi

    AGENSIC_FETCH_SUCCESS_COUNT=$((AGENSIC_FETCH_SUCCESS_COUNT + 1))
    AGENSIC_LAST_FETCH_ERROR_CODE=""
    AGENSIC_LAST_FETCH_AI_AGENT="$ai_agent_line"
    AGENSIC_LAST_FETCH_AI_PROVIDER="$ai_provider_line"
    AGENSIC_LAST_FETCH_AI_MODEL="$ai_model_line"

    if [[ "$used_ai_line" == "1" ]]; then
        AGENSIC_LAST_FETCH_USED_AI=1
    fi

    if [[ -n "$pool_line" ]]; then
        AGENSIC_SUGGESTIONS=("${(ps:$sep:)pool_line}")
        if (( ${#AGENSIC_SUGGESTIONS[@]} > 20 )); then
            AGENSIC_SUGGESTIONS=("${AGENSIC_SUGGESTIONS[@][1,20]}")
        fi
        if [[ -n "$display_line" ]]; then
            AGENSIC_DISPLAY_TEXTS=("${(ps:$sep:)display_line}")
        else
            AGENSIC_DISPLAY_TEXTS=("${AGENSIC_SUGGESTIONS[@]}")
        fi
        if [[ -n "$mode_line" ]]; then
            AGENSIC_ACCEPT_MODES=("${(ps:$sep:)mode_line}")
        else
            AGENSIC_ACCEPT_MODES=()
        fi
        if [[ -n "$kind_line" ]]; then
            AGENSIC_SUGGESTION_KINDS=("${(ps:$sep:)kind_line}")
        else
            AGENSIC_SUGGESTION_KINDS=()
        fi
        local i
        for (( i=1; i<=${#AGENSIC_SUGGESTIONS[@]}; i++ )); do
            [[ -z "${AGENSIC_DISPLAY_TEXTS[$i]}" ]] && AGENSIC_DISPLAY_TEXTS[$i]="${AGENSIC_SUGGESTIONS[$i]}"
            [[ -z "${AGENSIC_ACCEPT_MODES[$i]}" ]] && AGENSIC_ACCEPT_MODES[$i]="suffix_append"
            [[ -z "${AGENSIC_SUGGESTION_KINDS[$i]}" ]] && AGENSIC_SUGGESTION_KINDS[$i]="normal"
        done
    else
        AGENSIC_SUGGESTIONS=()
        AGENSIC_DISPLAY_TEXTS=()
        AGENSIC_ACCEPT_MODES=()
        AGENSIC_SUGGESTION_KINDS=()
    fi
    
    AGENSIC_SUGGESTION_INDEX=1
}

_agensic_is_status_suggestion() {
    local value="$1"
    [[ "$value" == "$AGENSIC_STATUS_PREFIX"* ]]
}

_agensic_set_status_message() {
    local message="$1"
    if [[ -z "$message" ]]; then
        AGENSIC_SUGGESTIONS=()
        AGENSIC_DISPLAY_TEXTS=()
        AGENSIC_ACCEPT_MODES=()
        AGENSIC_SUGGESTION_KINDS=()
        AGENSIC_SUGGESTION_INDEX=1
        return
    fi
    AGENSIC_SUGGESTIONS=("${AGENSIC_STATUS_PREFIX}${message}")
    AGENSIC_DISPLAY_TEXTS=("${AGENSIC_STATUS_PREFIX}${message}")
    AGENSIC_ACCEPT_MODES=("suffix_append")
    AGENSIC_SUGGESTION_KINDS=("status")
    AGENSIC_SUGGESTION_INDEX=1
}

_agensic_is_double_hash_assist() {
    [[ "$BUFFER" == '##'* ]]
}

_agensic_is_single_hash_intent() {
    [[ "$BUFFER" == '#'* && "$BUFFER" != '##'* ]]
}

_agensic_buffer_has_hash() {
    [[ "$BUFFER" == *"#"* ]]
}

_agensic_print_intent_preview() {
    local question="$1"
    local command="$2"
    local explanation="$3"
    local alternatives="$4"
    local copy_block="$5"
    zle -I
    print -r -- ""
    print -r -- "Agensic command mode (#)"
    print -r -- "Question: $question"
    print -r -- ""
    local markdown=""
    markdown+="Copy-ready command:"
    markdown+=$'\n\n'
    markdown+='```bash'
    markdown+=$'\n'
    markdown+="$copy_block"
    markdown+=$'\n'
    markdown+='```'
    markdown+=$'\n'
    if [[ -n "$explanation" ]]; then
        markdown+=$'\n'
        markdown+="$explanation"
        markdown+=$'\n'
    fi
    if [[ -n "$alternatives" ]]; then
        local -a alt_items
        alt_items=("${(@s:|||:)alternatives}")
        if [[ ${#alt_items[@]} -gt 0 ]]; then
            markdown+=$'\n'
            markdown+="Alternatives:"
            markdown+=$'\n'
            local alt
            for alt in "${alt_items[@]}"; do
                if [[ -n "$alt" ]]; then
                    markdown+="- $alt"
                    markdown+=$'\n'
                fi
            done
        fi
    fi
    _agensic_render_markdown_or_plain "$markdown"
}

_agensic_print_intent_refusal() {
    local question="$1"
    local explanation="$2"
    zle -I
    print -r -- ""
    print -r -- "Agensic command mode (#)"
    print -r -- "Question: $question"
    print -r -- ""
    print -r -- "${explanation:-I can only help with terminal commands. Use '##' for general questions.}"
}

_agensic_decode_common_escapes() {
    local text="$1"
    local newline=$'\n'
    local tab=$'\t'
    local prev="$text"
    local i
    for i in 1 2; do
        if [[ "$text" != *\\n* && "$text" != *\\r* && "$text" != *\\t* ]]; then
            break
        fi
        text="${text//\\r\\n/$newline}"
        text="${text//\\n/$newline}"
        text="${text//\\r/$newline}"
        text="${text//\\t/$tab}"
        if [[ "$text" == "$prev" ]]; then
            break
        fi
        prev="$text"
    done
    print -r -- "$text"
}

_agensic_render_markdown_or_plain() {
    local text="$1"
    text="$(_agensic_decode_common_escapes "$text")"
    local rendered=0
    if command -v python3 >/dev/null 2>&1; then
        AGENSIC_MARKDOWN_TEXT="$text" \
        python3 -c "
import os
text = os.environ.get('AGENSIC_MARKDOWN_TEXT', '')
try:
    from rich.console import Console
    from rich.markdown import Markdown
    console = Console(soft_wrap=True)
    console.print(Markdown(text))
except Exception:
    print(text)
" 2>/dev/null
        if [[ "$?" -eq 0 ]]; then
            rendered=1
        fi
    fi
    if [[ "$rendered" -ne 1 ]]; then
        print -r -- "$text"
    fi
}

_agensic_reset_intent_state() {
    AGENSIC_INTENT_ACTIVE=0
    AGENSIC_INTENT_OPTIONS=()
    AGENSIC_INTENT_OPTION_INDEX=1
}

_agensic_activate_intent_options() {
    local primary="$1"
    local alternatives="$2"
    _agensic_reset_intent_state
    if [[ -n "$primary" ]]; then
        AGENSIC_INTENT_OPTIONS+=("$primary")
    fi
    if [[ -n "$alternatives" ]]; then
        local -a alt_items
        alt_items=("${(@s:|||:)alternatives}")
        local alt
        for alt in "${alt_items[@]}"; do
            if [[ -n "$alt" ]]; then
                AGENSIC_INTENT_OPTIONS+=("$alt")
            fi
        done
    fi
    if [[ ${#AGENSIC_INTENT_OPTIONS[@]} -gt 0 ]]; then
        AGENSIC_INTENT_ACTIVE=1
        AGENSIC_INTENT_OPTION_INDEX=1
    fi
}

_agensic_update_intent_hint() {
    if (( AGENSIC_INTENT_ACTIVE == 1 )); then
        local count=${#AGENSIC_INTENT_OPTIONS[@]}
        if (( count > 1 )); then
            POSTDISPLAY="  (Option $AGENSIC_INTENT_OPTION_INDEX/$count, Ctrl+P/N)"
            region_highlight=("${#BUFFER} $((${#BUFFER} + ${#POSTDISPLAY})) fg=242")
            return
        fi
    fi
    POSTDISPLAY=""
    region_highlight=()
}

_agensic_print_assist_reply() {
    local answer="$1"
    zle -I
    print -r -- ""
    print -r -- "Agensic assistant (##)"
    _agensic_render_markdown_or_plain "$answer"
}

_agensic_resolve_intent_command() {
    local raw="$1"
    local body="${raw#\#}"
    while [[ "$body" == [[:space:]]* ]]; do
        body="${body# }"
    done

    if [[ -z "$body" ]]; then
        _agensic_print_intent_refusal "" "Add a terminal request after '#'."
        _agensic_reset_intent_state
        zle -R
        return 1
    fi
    if _agensic_autocomplete_is_disabled; then
        _agensic_print_intent_refusal "$body" "Autocomplete is turned off. Turn it on in 'agensic setup' to use '#' intent mode."
        _agensic_reset_intent_state
        zle -R
        return 1
    fi

    if [[ "$AGENSIC_LAST_NL_KIND" == "intent" && "$AGENSIC_LAST_NL_INPUT" == "$raw" && -n "$AGENSIC_LAST_NL_COMMAND" ]]; then
        BUFFER="$AGENSIC_LAST_NL_COMMAND"
        CURSOR=${#BUFFER}
        _agensic_set_suggestion_accept_state "ai" "replace_full" "intent_command" "$AGENSIC_LAST_NL_AI_AGENT" "$AGENSIC_LAST_NL_AI_PROVIDER" "$AGENSIC_LAST_NL_AI_MODEL"
        _agensic_activate_intent_options "$AGENSIC_LAST_NL_COMMAND" "$AGENSIC_LAST_NL_ALTERNATIVES"
        _agensic_print_intent_preview "$AGENSIC_LAST_NL_QUESTION" "$AGENSIC_LAST_NL_COMMAND" "$AGENSIC_LAST_NL_EXPLANATION" "$AGENSIC_LAST_NL_ALTERNATIVES" "$AGENSIC_LAST_NL_COMMAND"
        _agensic_update_intent_hint
        return 0
    fi

    local platform_name="$(uname -s 2>/dev/null || echo unknown)"
    _agensic_reload_auth_token_if_needed
    local response
    local -a helper_cmd
    helper_cmd=(
        "$AGENSIC_RUNTIME_PYTHON" "$AGENSIC_CLIENT_HELPER"
        --op intent
        --format shell_lines_v1
        --timeout 3.0
        --intent-text "$body"
        --working-directory "$PWD"
        --shell "zsh"
        --terminal "$TERM"
        --platform "$platform_name"
    )
    if [[ -n "$AGENSIC_AUTH_TOKEN" ]]; then
        helper_cmd+=("--auth-token=$AGENSIC_AUTH_TOKEN")
    fi
    response="$("${helper_cmd[@]}" 2>/dev/null)"

    local nl_status="error"
    local nl_primary=""
    local nl_explanation="Could not resolve command mode right now."
    local nl_alternatives=""
    local nl_copy_block=""
    local nl_ai_agent=""
    local nl_ai_provider=""
    local nl_ai_model=""
    local -a response_lines
    response_lines=("${(@f)response}")
    if (( ${#response_lines[@]} >= 12 )) \
        && [[ "${response_lines[1]}" == "agensic_shell_lines_v1" ]] \
        && [[ "${response_lines[2]}" == "intent" ]]; then
        nl_status="${response_lines[5]}"
        nl_primary="${response_lines[6]}"
        nl_explanation="${response_lines[7]}"
        nl_alternatives="${response_lines[8]}"
        nl_copy_block="${response_lines[9]}"
        nl_ai_agent="${response_lines[10]}"
        nl_ai_provider="${response_lines[11]}"
        nl_ai_model="${response_lines[12]}"
        if [[ -z "$nl_status" ]]; then
            nl_status="error"
        fi
        if [[ -z "$nl_explanation" ]]; then
            nl_explanation="Could not resolve command mode right now."
        fi
    fi

    if [[ "$nl_status" != "ok" || -z "$nl_primary" ]]; then
        _agensic_print_intent_refusal "$body" "${nl_explanation:-No command generated.}"
        _agensic_reset_intent_state
        zle -R
        return 1
    fi

    BUFFER="$nl_primary"
    CURSOR=${#BUFFER}
    _agensic_set_suggestion_accept_state "ai" "replace_full" "intent_command" "$nl_ai_agent" "$nl_ai_provider" "$nl_ai_model"
    AGENSIC_LAST_NL_INPUT="$raw"
    AGENSIC_LAST_NL_KIND="intent"
    AGENSIC_LAST_NL_QUESTION="$body"
    AGENSIC_LAST_NL_COMMAND="$nl_primary"
    AGENSIC_LAST_NL_EXPLANATION="$nl_explanation"
    AGENSIC_LAST_NL_ALTERNATIVES="$nl_alternatives"
    AGENSIC_LAST_NL_AI_AGENT="$nl_ai_agent"
    AGENSIC_LAST_NL_AI_PROVIDER="$nl_ai_provider"
    AGENSIC_LAST_NL_AI_MODEL="$nl_ai_model"
    _agensic_activate_intent_options "$nl_primary" "$nl_alternatives"
    _agensic_print_intent_preview "$body" "$nl_primary" "$nl_explanation" "$nl_alternatives" "${nl_copy_block:-$nl_primary}"
    _agensic_update_intent_hint
    return 0
}

_agensic_resolve_general_assist() {
    local raw="$1"
    local body="${raw#\#\#}"
    while [[ "$body" == [[:space:]]* ]]; do
        body="${body# }"
    done

    if [[ -z "$body" ]]; then
        _agensic_set_status_message "Add a question after '##'."
        _agensic_update_display
        zle -R
        return 1
    fi
    if _agensic_autocomplete_is_disabled; then
        _agensic_print_assist_reply "Autocomplete is turned off. Turn it on in 'agensic setup' to use '##' assistant mode."
        BUFFER=""
        CURSOR=0
        return 1
    fi

    if [[ "$AGENSIC_LAST_NL_KIND" == "assist" && "$AGENSIC_LAST_NL_INPUT" == "$raw" && -n "$AGENSIC_LAST_NL_ASSIST" ]]; then
        _agensic_print_assist_reply "$AGENSIC_LAST_NL_ASSIST"
        BUFFER=""
        CURSOR=0
        return 0
    fi

    local platform_name="$(uname -s 2>/dev/null || echo unknown)"
    _agensic_reload_auth_token_if_needed
    local response
    local -a helper_cmd
    helper_cmd=(
        "$AGENSIC_RUNTIME_PYTHON" "$AGENSIC_CLIENT_HELPER"
        --op assist
        --format shell_lines_v1
        --timeout 4.0
        --prompt-text "$body"
        --working-directory "$PWD"
        --shell "zsh"
        --terminal "$TERM"
        --platform "$platform_name"
    )
    if [[ -n "$AGENSIC_AUTH_TOKEN" ]]; then
        helper_cmd+=("--auth-token=$AGENSIC_AUTH_TOKEN")
    fi
    response="$("${helper_cmd[@]}" 2>/dev/null)"

    local answer
    answer="Could not fetch assistant reply right now."
    local -a response_lines
    response_lines=("${(@f)response}")
    if (( ${#response_lines[@]} >= 5 )) \
        && [[ "${response_lines[1]}" == "agensic_shell_lines_v1" ]] \
        && [[ "${response_lines[2]}" == "assist" ]]; then
        local answer_count_raw="${response_lines[5]}"
        if [[ "$answer_count_raw" == <-> ]]; then
            local answer_count="$answer_count_raw"
            local answer_last_index=$((5 + answer_count))
            if (( ${#response_lines[@]} >= answer_last_index )); then
                if (( answer_count > 0 )); then
                    answer="${(j:\n:)response_lines[6,$answer_last_index]}"
                else
                    answer=""
                fi
            else
                # Fallback for malformed or mixed versions: treat line 5 as legacy answer text.
                answer="${response_lines[5]}"
            fi
        else
            # Backward compatibility with older shell_lines_v1 assist shape (single answer line).
            answer="${response_lines[5]}"
        fi
        if [[ -z "$answer" ]]; then
            if [[ "${response_lines[3]}" == "1" ]]; then
                answer="No response."
            else
                answer="Could not fetch assistant reply right now."
            fi
        fi
    fi
    # Some providers return markdown with literal escape sequences ("\\n") instead of real newlines.
    # Decode common escapes so terminal markdown rendering preserves structure.
    answer="$(_agensic_decode_common_escapes "$answer")"

    AGENSIC_LAST_NL_INPUT="$raw"
    AGENSIC_LAST_NL_KIND="assist"
    AGENSIC_LAST_NL_ASSIST="$answer"
    _agensic_print_assist_reply "$answer"
    BUFFER=""
    CURSOR=0
    return 0
}

_agensic_filter_pool() {
    # Filter the suggestion pool based on current buffer (typed since last fetch)
    local buffer="$BUFFER"
    
    # If buffer is shorter than last fetch, we can't reliably filter the suffixes
    if [[ ${#buffer} -lt ${#AGENSIC_LAST_BUFFER} ]]; then
        AGENSIC_SUGGESTIONS=()
        return
    fi
    
    local typed_since_fetch="${buffer#$AGENSIC_LAST_BUFFER}"
    
    # Filter suggestions that still match what the user typed
    local -a new_suggestions=()
    local -a new_displays=()
    local -a new_modes=()
    local -a new_kinds=()
    local i
    for (( i=1; i<=${#AGENSIC_SUGGESTIONS[@]}; i++ )); do
        local sugg="${AGENSIC_SUGGESTIONS[$i]}"
        local display="${AGENSIC_DISPLAY_TEXTS[$i]}"
        local mode="${AGENSIC_ACCEPT_MODES[$i]}"
        local kind="${AGENSIC_SUGGESTION_KINDS[$i]}"
        if _agensic_is_status_suggestion "$sugg"; then
            new_suggestions+=("$sugg")
            new_displays+=("$display")
            new_modes+=("$mode")
            new_kinds+=("$kind")
            continue
        fi
        if [[ "$mode" == "replace_full" ]]; then
            if [[ -n "$sugg" ]]; then
                new_suggestions+=("$sugg")
                new_displays+=("$display")
                new_modes+=("$mode")
                new_kinds+=("$kind")
            fi
        elif [[ "$sugg" == "$typed_since_fetch"* ]]; then
            new_suggestions+=("$sugg")
            new_displays+=("$display")
            new_modes+=("${mode:-suffix_append}")
            new_kinds+=("${kind:-normal}")
        fi
    done
    
    if [[ ${#new_suggestions[@]} -gt 0 ]]; then
        AGENSIC_SUGGESTIONS=("${new_suggestions[@]}")
        AGENSIC_DISPLAY_TEXTS=("${new_displays[@]}")
        AGENSIC_ACCEPT_MODES=("${new_modes[@]}")
        AGENSIC_SUGGESTION_KINDS=("${new_kinds[@]}")
        AGENSIC_SUGGESTION_INDEX=1
        _agensic_update_display
    else
        # Pool exhausted; wait for the next explicit trigger.
        AGENSIC_SUGGESTIONS=()
        AGENSIC_DISPLAY_TEXTS=()
        AGENSIC_ACCEPT_MODES=()
        AGENSIC_SUGGESTION_KINDS=()
        _agensic_update_display
    fi
}

_agensic_clear_ai_session_env() {
    _agensic_stop_ai_session_timer
    if [[ -n "${AGENSIC_AI_SESSION_STATE_PATH:-}" ]]; then
        command rm -f -- "$AGENSIC_AI_SESSION_STATE_PATH" 2>/dev/null
    fi
    unset AGENSIC_AI_SESSION_ACTIVE
    unset AGENSIC_AI_SESSION_AGENT
    unset AGENSIC_AI_SESSION_MODEL
    unset AGENSIC_AI_SESSION_AGENT_NAME
    unset AGENSIC_AI_SESSION_ID
    unset AGENSIC_AI_SESSION_STARTED_TS
    unset AGENSIC_AI_SESSION_EXPIRES_TS
    unset AGENSIC_AI_SESSION_COUNTER
    unset AGENSIC_AI_SESSION_TIMER_PID
    unset AGENSIC_AI_SESSION_OWNER_SHELL_PID
    AGENSIC_AI_SESSION_AUTO_STOP_ARMED=0
}

_agensic_write_ai_session_state_file() {
    local state_path="${AGENSIC_AI_SESSION_STATE_PATH:-}"
    if [[ -z "$state_path" ]]; then
        return
    fi

    local state_dir="${state_path:h}"
    command mkdir -p -- "$state_dir" 2>/dev/null || return

    local tmp_path="${state_path}.tmp.$$"
    {
        print -r -- "AGENSIC_AI_SESSION_ACTIVE"$'\t'"${AGENSIC_AI_SESSION_ACTIVE:-0}"
        print -r -- "AGENSIC_AI_SESSION_AGENT"$'\t'"${AGENSIC_AI_SESSION_AGENT:-}"
        print -r -- "AGENSIC_AI_SESSION_MODEL"$'\t'"${AGENSIC_AI_SESSION_MODEL:-}"
        print -r -- "AGENSIC_AI_SESSION_AGENT_NAME"$'\t'"${AGENSIC_AI_SESSION_AGENT_NAME:-}"
        print -r -- "AGENSIC_AI_SESSION_ID"$'\t'"${AGENSIC_AI_SESSION_ID:-}"
        print -r -- "AGENSIC_AI_SESSION_STARTED_TS"$'\t'"${AGENSIC_AI_SESSION_STARTED_TS:-}"
        print -r -- "AGENSIC_AI_SESSION_EXPIRES_TS"$'\t'"${AGENSIC_AI_SESSION_EXPIRES_TS:-}"
        print -r -- "AGENSIC_AI_SESSION_COUNTER"$'\t'"${AGENSIC_AI_SESSION_COUNTER:-0}"
        print -r -- "AGENSIC_AI_SESSION_TIMER_PID"$'\t'
        print -r -- "AGENSIC_AI_SESSION_OWNER_SHELL_PID"$'\t'"${AGENSIC_AI_SESSION_OWNER_SHELL_PID:-}"
    } >| "$tmp_path" || {
        command rm -f -- "$tmp_path" 2>/dev/null
        return
    }

    command chmod 600 "$tmp_path" 2>/dev/null || true
    command mv -f -- "$tmp_path" "$state_path" 2>/dev/null || {
        command rm -f -- "$tmp_path" 2>/dev/null
    }
}

_agensic_load_ai_session_state_file() {
    local state_path="${AGENSIC_AI_SESSION_STATE_PATH:-}"
    if [[ -z "$state_path" || ! -f "$state_path" ]]; then
        return 1
    fi

    local active="" agent="" model="" agent_name="" session_id="" started_ts="" expires_ts="" counter="" owner_shell_pid=""
    local key value
    while IFS=$'\t' read -r key value || [[ -n "$key" ]]; do
        case "$key" in
            AGENSIC_AI_SESSION_ACTIVE) active="$value" ;;
            AGENSIC_AI_SESSION_AGENT) agent="$value" ;;
            AGENSIC_AI_SESSION_MODEL) model="$value" ;;
            AGENSIC_AI_SESSION_AGENT_NAME) agent_name="$value" ;;
            AGENSIC_AI_SESSION_ID) session_id="$value" ;;
            AGENSIC_AI_SESSION_STARTED_TS) started_ts="$value" ;;
            AGENSIC_AI_SESSION_EXPIRES_TS) expires_ts="$value" ;;
            AGENSIC_AI_SESSION_COUNTER) counter="$value" ;;
            AGENSIC_AI_SESSION_OWNER_SHELL_PID) owner_shell_pid="$value" ;;
        esac
    done < "$state_path"

    if [[ "$active" != "1" ]]; then
        return 1
    fi
    if [[ -n "$owner_shell_pid" ]] && ! kill -0 "$owner_shell_pid" 2>/dev/null; then
        command rm -f -- "$state_path" 2>/dev/null
        return 1
    fi
    if [[ -n "$owner_shell_pid" && "$owner_shell_pid" != "$$" ]]; then
        return 1
    fi

    export AGENSIC_AI_SESSION_ACTIVE="$active"
    export AGENSIC_AI_SESSION_AGENT="$agent"
    export AGENSIC_AI_SESSION_MODEL="$model"
    export AGENSIC_AI_SESSION_AGENT_NAME="$agent_name"
    export AGENSIC_AI_SESSION_ID="$session_id"
    export AGENSIC_AI_SESSION_STARTED_TS="$started_ts"
    export AGENSIC_AI_SESSION_EXPIRES_TS="$expires_ts"
    export AGENSIC_AI_SESSION_COUNTER="${counter:-0}"
    export AGENSIC_AI_SESSION_OWNER_SHELL_PID="$owner_shell_pid"
    return 0
}

_agensic_sync_ai_session_from_state_file() {
    local state_path="${AGENSIC_AI_SESSION_STATE_PATH:-}"
    local had_active="${AGENSIC_AI_SESSION_ACTIVE:-0}"
    local previous_session_id="${AGENSIC_AI_SESSION_ID:-}"

    if [[ -z "$state_path" || ! -f "$state_path" ]]; then
        if [[ "$had_active" == "1" ]]; then
            _agensic_clear_ai_session_env
        fi
        return
    fi

    if ! _agensic_load_ai_session_state_file; then
        if [[ "$had_active" == "1" ]]; then
            _agensic_clear_ai_session_env
        fi
        return
    fi

    if _agensic_enforce_ai_session_expiry; then
        return
    fi

    if [[ "$had_active" != "1" || "$previous_session_id" != "${AGENSIC_AI_SESSION_ID:-}" ]]; then
        _agensic_stop_ai_session_timer
    fi
}

_agensic_pid_is_alive() {
    local pid="$1"
    if [[ -z "$pid" || "$pid" != <-> ]]; then
        return 1
    fi
    kill -0 "$pid" 2>/dev/null
}

_agensic_stop_ai_session_timer() {
    if _agensic_pid_is_alive "${AGENSIC_AI_SESSION_TIMER_PID:-}"; then
        kill "${AGENSIC_AI_SESSION_TIMER_PID}" 2>/dev/null
    fi
    AGENSIC_AI_SESSION_TIMER_PID=""
}

_agensic_now_ts() {
    local now_ts
    now_ts="$(date +%s 2>/dev/null)"
    if [[ -z "$now_ts" ]]; then
        now_ts="0"
    fi
    print -r -- "$now_ts"
}

_agensic_now_time_component() {
    local component
    component="$(
        python3 - <<'PY' 2>/dev/null
import time

print(int(time.time_ns()))
PY
    )"
    if [[ -n "$component" && "$component" == <-> ]]; then
        print -r -- "$component"
        return
    fi
    _agensic_now_ts
}

_agensic_ai_session_is_expired() {
    if [[ "${AGENSIC_AI_SESSION_ACTIVE:-0}" != "1" ]]; then
        return 1
    fi
    local expires_ts="${AGENSIC_AI_SESSION_EXPIRES_TS:-0}"
    if [[ -z "$expires_ts" || "$expires_ts" == "0" ]]; then
        return 1
    fi
    local now_ts
    now_ts="$(_agensic_now_ts)"
    if [[ "$expires_ts" == <-> && "$now_ts" == <-> && "$now_ts" -ge "$expires_ts" ]]; then
        return 0
    fi
    return 1
}

_agensic_schedule_ai_session_expiry_timer() {
    setopt localoptions nobgnice
    _agensic_stop_ai_session_timer
    if [[ "${AGENSIC_AI_SESSION_ACTIVE:-0}" != "1" ]]; then
        return
    fi
    local expires_ts="${AGENSIC_AI_SESSION_EXPIRES_TS:-0}"
    if [[ -z "$expires_ts" || "$expires_ts" == "0" || "$expires_ts" != <-> ]]; then
        return
    fi
    local now_ts wait_seconds
    now_ts="$(_agensic_now_ts)"
    if [[ "$now_ts" != <-> ]]; then
        return
    fi
    wait_seconds=$(( expires_ts - now_ts ))
    if (( wait_seconds <= 0 )); then
        kill -USR2 $$ 2>/dev/null
        return
    fi
    (
        sleep "$wait_seconds"
        kill -USR2 $$ 2>/dev/null
    ) >/dev/null 2>&1 < /dev/null &!
    AGENSIC_AI_SESSION_TIMER_PID="$!"
}

_agensic_enforce_ai_session_expiry() {
    if _agensic_ai_session_is_expired; then
        _agensic_clear_ai_session_env
        return 0
    fi
    return 1
}

_agensic_ensure_ai_session_timer() {
    _agensic_sync_ai_session_from_state_file
    if [[ "${AGENSIC_AI_SESSION_ACTIVE:-0}" != "1" ]]; then
        _agensic_stop_ai_session_timer
        return
    fi
    if _agensic_enforce_ai_session_expiry; then
        return
    fi
    if ! _agensic_pid_is_alive "${AGENSIC_AI_SESSION_TIMER_PID:-}"; then
        _agensic_schedule_ai_session_expiry_timer
    fi
}

agensic_session_start() {
    print -r -- "agensic_session_start has been removed. Use 'agensic run <agent>'." >&2
    return 2
}

agensic_session_stop() {
    print -r -- "agensic_session_stop has been removed. Use 'agensic run <agent>'." >&2
    return 2
}

agensic_session_status() {
    print -r -- "agensic_session_status has been removed. Use 'agensic run <agent>'." >&2
    return 2
}

_agensic_generate_ai_proof() {
    local label="${1:-AI_EXECUTED}"
    local agent="$2"
    local model="$3"
    local trace="$4"
    local timestamp="$5"
    if [[ -z "$agent" || -z "$model" || -z "$trace" || -z "$timestamp" ]]; then
        return 1
    fi
    AGENSIC_SOURCE_DIR="$AGENSIC_SOURCE_DIR" \
    AGENSIC_HOME="$AGENSIC_HOME" \
    AGENSIC_PROOF_LABEL="$label" \
    AGENSIC_PROOF_AGENT="$agent" \
    AGENSIC_PROOF_MODEL="$model" \
    AGENSIC_PROOF_TRACE="$trace" \
    AGENSIC_PROOF_TS="$timestamp" \
    "$AGENSIC_RUNTIME_PYTHON" - <<'PY' 2>/dev/null
import os
import sys

source_dir = str(os.environ.get("AGENSIC_SOURCE_DIR", "") or "").strip()
agensic_home = str(os.environ.get("AGENSIC_HOME", "") or "").strip()
for candidate in (source_dir, agensic_home):
    if candidate and os.path.isdir(candidate) and candidate not in sys.path:
        sys.path.insert(0, candidate)

from agensic.engine.provenance import build_local_proof_metadata, sign_proof_payload

label = str(os.environ.get("AGENSIC_PROOF_LABEL", "AI_EXECUTED") or "").strip()
agent = str(os.environ.get("AGENSIC_PROOF_AGENT", "") or "").strip().lower()
model = str(os.environ.get("AGENSIC_PROOF_MODEL", "") or "").strip()
trace = str(os.environ.get("AGENSIC_PROOF_TRACE", "") or "").strip()
timestamp = int(os.environ.get("AGENSIC_PROOF_TS", "0") or "0")

signature = sign_proof_payload(label, agent, model, trace, timestamp)
metadata = build_local_proof_metadata()
print(signature)
print(str(metadata.get("proof_key_fingerprint", "") or ""))
print(str(metadata.get("proof_host_fingerprint", "") or ""))
print(str(metadata.get("proof_signer_scope", "") or ""))
PY
}

_agensic_session_sign_if_active() {
    return 0
}

agensic_mark_ai_executed() {
    print -r -- "agensic_mark_ai_executed has been removed. Use 'agensic run <agent>'." >&2
    return 1
}

_agensic_send_feedback() {
    local buffer="$1"
    local accepted="$2"
    local accept_mode="${3:-suffix_append}"
    if _agensic_autocomplete_is_disabled; then
        return
    fi
    if _agensic_matches_disabled_pattern "$buffer" || _agensic_matches_disabled_pattern "$accepted"; then
        return
    fi
    _agensic_reload_auth_token_if_needed
    # Fire and forget feedback to server for zvec feedback stats
    (
        local escaped_buf="${buffer//\'/\'\\\'\'}"
        local escaped_acc="${accepted//\'/\'\\\'\'}"
        local escaped_pwd="${PWD//\'/\'\\\'\'}"
        local json_data="{\"command_buffer\": \"$escaped_buf\", \"accepted_suggestion\": \"$escaped_acc\", \"accept_mode\": \"${accept_mode}\", \"working_directory\": \"$escaped_pwd\"}"
        local -a auth_headers
        auth_headers=()
        if [[ -n "$AGENSIC_AUTH_TOKEN" ]]; then
            auth_headers=(-H "Authorization: Bearer $AGENSIC_AUTH_TOKEN" -H "X-Agensic-Auth: $AGENSIC_AUTH_TOKEN")
        fi
        curl -s -X POST "http://127.0.0.1:22000/feedback" \
             "${auth_headers[@]}" \
             -H "Content-Type: application/json" \
             -d "$json_data" > /dev/null 2>&1
    ) &!
}

_agensic_cleanup_runtime_capture_paths() {
    local path
    for path in "$@"; do
        if [[ -n "$path" ]]; then
            command rm -f -- "$path" >/dev/null 2>&1
        fi
    done
}

_agensic_should_capture_runtime_output() {
    local command="$1"
    if [[ "$AGENSIC_FORCE_RUNTIME_OUTPUT_CAPTURE" != "1" ]]; then
        if [[ ! -t 1 || ! -t 2 ]]; then
            return 1
        fi
    fi

    local -a tokens
    local token=""
    local exe=""
    local i=1
    tokens=(${(z)command})
    while (( i <= ${#tokens[@]} )); do
        token="${tokens[$i]}"
        if [[ -z "$token" ]]; then
            (( i++ ))
            continue
        fi
        case "$token" in
            sudo|command)
                (( i++ ))
                continue
                ;;
            env|/usr/bin/env)
                (( i++ ))
                while (( i <= ${#tokens[@]} )); do
                    token="${tokens[$i]}"
                    if [[ -z "$token" || "$token" == -* || "$token" == *=* ]]; then
                        (( i++ ))
                        continue
                    fi
                    break
                done
                continue
                ;;
            -*|*=*)
                (( i++ ))
                continue
                ;;
            *)
                exe="${(L)${token:t}}"
                break
                ;;
        esac
    done

    if [[ -z "$exe" ]]; then
        return 0
    fi
    if _agensic_is_self_cli_command "$command"; then
        return 1
    fi
    if _agensic_value_in_array "$exe" "${AGENSIC_TTY_SENSITIVE_EXECUTABLES[@]}"; then
        return 1
    fi
    return 0
}

_agensic_apply_runtime_capture_env() {
    if [[ ${+FORCE_COLOR} -eq 1 ]]; then
        AGENSIC_RUNTIME_CAPTURE_SAVED_FORCE_COLOR_SET=1
        AGENSIC_RUNTIME_CAPTURE_SAVED_FORCE_COLOR="$FORCE_COLOR"
    else
        AGENSIC_RUNTIME_CAPTURE_SAVED_FORCE_COLOR_SET=0
        AGENSIC_RUNTIME_CAPTURE_SAVED_FORCE_COLOR=""
    fi
    if [[ ${+CLICOLOR_FORCE} -eq 1 ]]; then
        AGENSIC_RUNTIME_CAPTURE_SAVED_CLICOLOR_FORCE_SET=1
        AGENSIC_RUNTIME_CAPTURE_SAVED_CLICOLOR_FORCE="$CLICOLOR_FORCE"
    else
        AGENSIC_RUNTIME_CAPTURE_SAVED_CLICOLOR_FORCE_SET=0
        AGENSIC_RUNTIME_CAPTURE_SAVED_CLICOLOR_FORCE=""
    fi
    if [[ ${+PY_COLORS} -eq 1 ]]; then
        AGENSIC_RUNTIME_CAPTURE_SAVED_PY_COLORS_SET=1
        AGENSIC_RUNTIME_CAPTURE_SAVED_PY_COLORS="$PY_COLORS"
    else
        AGENSIC_RUNTIME_CAPTURE_SAVED_PY_COLORS_SET=0
        AGENSIC_RUNTIME_CAPTURE_SAVED_PY_COLORS=""
    fi
    if [[ ${+TTY_COMPATIBLE} -eq 1 ]]; then
        AGENSIC_RUNTIME_CAPTURE_SAVED_TTY_COMPATIBLE_SET=1
        AGENSIC_RUNTIME_CAPTURE_SAVED_TTY_COMPATIBLE="$TTY_COMPATIBLE"
    else
        AGENSIC_RUNTIME_CAPTURE_SAVED_TTY_COMPATIBLE_SET=0
        AGENSIC_RUNTIME_CAPTURE_SAVED_TTY_COMPATIBLE=""
    fi
    if [[ ${+TTY_INTERACTIVE} -eq 1 ]]; then
        AGENSIC_RUNTIME_CAPTURE_SAVED_TTY_INTERACTIVE_SET=1
        AGENSIC_RUNTIME_CAPTURE_SAVED_TTY_INTERACTIVE="$TTY_INTERACTIVE"
    else
        AGENSIC_RUNTIME_CAPTURE_SAVED_TTY_INTERACTIVE_SET=0
        AGENSIC_RUNTIME_CAPTURE_SAVED_TTY_INTERACTIVE=""
    fi
    if [[ ${+NO_COLOR} -eq 1 ]]; then
        AGENSIC_RUNTIME_CAPTURE_SAVED_NO_COLOR_SET=1
        AGENSIC_RUNTIME_CAPTURE_SAVED_NO_COLOR="$NO_COLOR"
    else
        AGENSIC_RUNTIME_CAPTURE_SAVED_NO_COLOR_SET=0
        AGENSIC_RUNTIME_CAPTURE_SAVED_NO_COLOR=""
    fi

    export FORCE_COLOR=1
    export CLICOLOR_FORCE=1
    export PY_COLORS=1
    export TTY_COMPATIBLE=1
    export TTY_INTERACTIVE=1
    unset NO_COLOR
    AGENSIC_RUNTIME_CAPTURE_ENV_ACTIVE=1
}

_agensic_restore_runtime_capture_env() {
    if [[ "$AGENSIC_RUNTIME_CAPTURE_ENV_ACTIVE" != "1" ]]; then
        return
    fi

    if [[ "$AGENSIC_RUNTIME_CAPTURE_SAVED_FORCE_COLOR_SET" == "1" ]]; then
        export FORCE_COLOR="$AGENSIC_RUNTIME_CAPTURE_SAVED_FORCE_COLOR"
    else
        unset FORCE_COLOR
    fi
    if [[ "$AGENSIC_RUNTIME_CAPTURE_SAVED_CLICOLOR_FORCE_SET" == "1" ]]; then
        export CLICOLOR_FORCE="$AGENSIC_RUNTIME_CAPTURE_SAVED_CLICOLOR_FORCE"
    else
        unset CLICOLOR_FORCE
    fi
    if [[ "$AGENSIC_RUNTIME_CAPTURE_SAVED_PY_COLORS_SET" == "1" ]]; then
        export PY_COLORS="$AGENSIC_RUNTIME_CAPTURE_SAVED_PY_COLORS"
    else
        unset PY_COLORS
    fi
    if [[ "$AGENSIC_RUNTIME_CAPTURE_SAVED_TTY_COMPATIBLE_SET" == "1" ]]; then
        export TTY_COMPATIBLE="$AGENSIC_RUNTIME_CAPTURE_SAVED_TTY_COMPATIBLE"
    else
        unset TTY_COMPATIBLE
    fi
    if [[ "$AGENSIC_RUNTIME_CAPTURE_SAVED_TTY_INTERACTIVE_SET" == "1" ]]; then
        export TTY_INTERACTIVE="$AGENSIC_RUNTIME_CAPTURE_SAVED_TTY_INTERACTIVE"
    else
        unset TTY_INTERACTIVE
    fi
    if [[ "$AGENSIC_RUNTIME_CAPTURE_SAVED_NO_COLOR_SET" == "1" ]]; then
        export NO_COLOR="$AGENSIC_RUNTIME_CAPTURE_SAVED_NO_COLOR"
    else
        unset NO_COLOR
    fi

    AGENSIC_RUNTIME_CAPTURE_ENV_ACTIVE=0
}

_agensic_end_runtime_capture() {
    local stdout_fd="$AGENSIC_RUNTIME_CAPTURE_ORIG_STDOUT_FD"
    local stderr_fd="$AGENSIC_RUNTIME_CAPTURE_ORIG_STDERR_FD"
    if [[ -n "$stdout_fd" ]]; then
        exec 1>&$stdout_fd
        exec {stdout_fd}>&-
    fi
    if [[ -n "$stderr_fd" ]]; then
        exec 2>&$stderr_fd
        exec {stderr_fd}>&-
    fi
    AGENSIC_RUNTIME_CAPTURE_ORIG_STDOUT_FD=""
    AGENSIC_RUNTIME_CAPTURE_ORIG_STDERR_FD=""
    _agensic_restore_runtime_capture_env
}

_agensic_wait_for_runtime_capture_flush() {
    local stdout_path="${1:-}"
    local stderr_path="${2:-}"
    python3 - "$stdout_path" "$stderr_path" <<'PY' 2>/dev/null
import os
import sys
import time

paths = [path for path in sys.argv[1:] if str(path or "").strip()]
if not paths:
    raise SystemExit(0)

prev = None
stable = 0
for _ in range(10):
    sizes = tuple(os.path.getsize(path) if os.path.exists(path) else -1 for path in paths)
    if sizes == prev:
        stable += 1
        if stable >= 1:
            break
    else:
        stable = 0
    prev = sizes
    time.sleep(0.01)
PY
}

_agensic_begin_runtime_capture() {
    local command="$1"
    local stdout_path=""
    local stderr_path=""
    local stdout_fd=""
    local stderr_fd=""
    local capture_dir="${AGENSIC_HOME}/runtime_capture"

    _agensic_end_runtime_capture
    _agensic_cleanup_runtime_capture_paths \
        "$AGENSIC_RUNTIME_CAPTURE_STDOUT_PATH" \
        "$AGENSIC_RUNTIME_CAPTURE_STDERR_PATH"
    AGENSIC_RUNTIME_CAPTURE_STDOUT_PATH=""
    AGENSIC_RUNTIME_CAPTURE_STDERR_PATH=""

    if ! _agensic_should_capture_runtime_output "$command"; then
        return 1
    fi

    command mkdir -p "$capture_dir" >/dev/null 2>&1 || return 1
    stdout_path="$(mktemp "$capture_dir/stdout.XXXXXX" 2>/dev/null)" || return 1
    stderr_path="$(mktemp "$capture_dir/stderr.XXXXXX" 2>/dev/null)" || {
        _agensic_cleanup_runtime_capture_paths "$stdout_path"
        return 1
    }

    exec {stdout_fd}>&1 || {
        _agensic_cleanup_runtime_capture_paths "$stdout_path" "$stderr_path"
        return 1
    }
    exec {stderr_fd}>&2 || {
        exec {stdout_fd}>&-
        _agensic_cleanup_runtime_capture_paths "$stdout_path" "$stderr_path"
        return 1
    }

    _agensic_apply_runtime_capture_env
    exec > >(command tee -a -- "$stdout_path" >&$stdout_fd) 2> >(command tee -a -- "$stderr_path" >&$stderr_fd)

    AGENSIC_RUNTIME_CAPTURE_STDOUT_PATH="$stdout_path"
    AGENSIC_RUNTIME_CAPTURE_STDERR_PATH="$stderr_path"
    AGENSIC_RUNTIME_CAPTURE_ORIG_STDOUT_FD="$stdout_fd"
    AGENSIC_RUNTIME_CAPTURE_ORIG_STDERR_FD="$stderr_fd"
    return 0
}

_agensic_build_log_command_json() {
    local command="$1"
    local exit_code="$2"
    local source="${3:-runtime}"
    local duration_ms="${4:-}"
    local log_cwd="$PWD"
    local log_shell_pid="$$"
    local log_last_action="$AGENSIC_PENDING_LAST_ACTION"
    local log_accept_origin="$AGENSIC_PENDING_ACCEPTED_ORIGIN"
    local log_accept_mode="$AGENSIC_PENDING_ACCEPTED_MODE"
    local log_suggestion_kind="$AGENSIC_PENDING_ACCEPTED_KIND"
    local log_manual_after_accept="$AGENSIC_PENDING_MANUAL_EDIT_AFTER_ACCEPT"
    local log_ai_agent="$AGENSIC_PENDING_AI_AGENT"
    local log_ai_provider="$AGENSIC_PENDING_AI_PROVIDER"
    local log_ai_model="$AGENSIC_PENDING_AI_MODEL"
    local log_agent_name="$AGENSIC_PENDING_AGENT_NAME"
    local log_agent_hint="$AGENSIC_PENDING_AGENT_HINT"
    local log_model_raw="$AGENSIC_PENDING_MODEL_RAW"
    local log_wrapper_id="$AGENSIC_PENDING_WRAPPER_ID"
    local log_proof_label="$AGENSIC_PENDING_PROOF_LABEL"
    local log_proof_agent="$AGENSIC_PENDING_PROOF_AGENT"
    local log_proof_model="$AGENSIC_PENDING_PROOF_MODEL"
    local log_proof_trace="$AGENSIC_PENDING_PROOF_TRACE"
    local log_proof_timestamp="$AGENSIC_PENDING_PROOF_TIMESTAMP"
    local log_proof_signature="$AGENSIC_PENDING_PROOF_SIGNATURE"
    local log_proof_signer_scope="$AGENSIC_PENDING_PROOF_SIGNER_SCOPE"
    local log_proof_key_fingerprint="$AGENSIC_PENDING_PROOF_KEY_FINGERPRINT"
    local log_proof_host_fingerprint="$AGENSIC_PENDING_PROOF_HOST_FINGERPRINT"

    AGENSIC_LOG_COMMAND="$command" \
    AGENSIC_LOG_EXIT="$exit_code" \
    AGENSIC_LOG_DURATION_MS="$duration_ms" \
    AGENSIC_LOG_SOURCE="$source" \
    AGENSIC_LOG_CWD="$log_cwd" \
    AGENSIC_LOG_SHELL_PID="$log_shell_pid" \
    AGENSIC_LOG_LAST_ACTION="$log_last_action" \
    AGENSIC_LOG_ACCEPT_ORIGIN="$log_accept_origin" \
    AGENSIC_LOG_ACCEPT_MODE="$log_accept_mode" \
    AGENSIC_LOG_SUGGESTION_KIND="$log_suggestion_kind" \
    AGENSIC_LOG_MANUAL_AFTER_ACCEPT="$log_manual_after_accept" \
    AGENSIC_LOG_AI_AGENT="$log_ai_agent" \
    AGENSIC_LOG_AI_PROVIDER="$log_ai_provider" \
    AGENSIC_LOG_AI_MODEL="$log_ai_model" \
    AGENSIC_LOG_AGENT_NAME="$log_agent_name" \
    AGENSIC_LOG_AGENT_HINT="$log_agent_hint" \
    AGENSIC_LOG_MODEL_RAW="$log_model_raw" \
    AGENSIC_LOG_WRAPPER_ID="$log_wrapper_id" \
    AGENSIC_LOG_PROOF_LABEL="$log_proof_label" \
    AGENSIC_LOG_PROOF_AGENT="$log_proof_agent" \
    AGENSIC_LOG_PROOF_MODEL="$log_proof_model" \
    AGENSIC_LOG_PROOF_TRACE="$log_proof_trace" \
    AGENSIC_LOG_PROOF_TIMESTAMP="$log_proof_timestamp" \
    AGENSIC_LOG_PROOF_SIGNATURE="$log_proof_signature" \
    AGENSIC_LOG_PROOF_SIGNER_SCOPE="$log_proof_signer_scope" \
    AGENSIC_LOG_PROOF_KEY_FINGERPRINT="$log_proof_key_fingerprint" \
    AGENSIC_LOG_PROOF_HOST_FINGERPRINT="$log_proof_host_fingerprint" \
    python3 - <<'PY' 2>/dev/null
import json
import os

MAX_COMMAND_DURATION_MS = 86400000

def as_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

command = str(os.environ.get("AGENSIC_LOG_COMMAND", "") or "")
exit_code = as_int(os.environ.get("AGENSIC_LOG_EXIT", None), None)
duration_ms = as_int(os.environ.get("AGENSIC_LOG_DURATION_MS", None), None)
if duration_ms is not None:
    duration_ms = min(MAX_COMMAND_DURATION_MS, max(0, duration_ms))
manual_after_accept = str(
    os.environ.get("AGENSIC_LOG_MANUAL_AFTER_ACCEPT", "0") or "0"
).strip() in {"1", "true", "True"}

payload = {
    "command": command,
    "exit_code": exit_code,
    "duration_ms": duration_ms,
    "source": str(os.environ.get("AGENSIC_LOG_SOURCE", "runtime") or "runtime"),
    "working_directory": str(os.environ.get("AGENSIC_LOG_CWD", "") or ""),
    "shell_pid": as_int(os.environ.get("AGENSIC_LOG_SHELL_PID", None), None),
    "provenance_last_action": str(os.environ.get("AGENSIC_LOG_LAST_ACTION", "") or ""),
    "provenance_accept_origin": str(os.environ.get("AGENSIC_LOG_ACCEPT_ORIGIN", "") or ""),
    "provenance_accept_mode": str(os.environ.get("AGENSIC_LOG_ACCEPT_MODE", "") or ""),
    "provenance_suggestion_kind": str(os.environ.get("AGENSIC_LOG_SUGGESTION_KIND", "") or ""),
    "provenance_manual_edit_after_accept": manual_after_accept,
    "provenance_ai_agent": str(os.environ.get("AGENSIC_LOG_AI_AGENT", "") or ""),
    "provenance_ai_provider": str(os.environ.get("AGENSIC_LOG_AI_PROVIDER", "") or ""),
    "provenance_ai_model": str(os.environ.get("AGENSIC_LOG_AI_MODEL", "") or ""),
    "provenance_agent_name": str(os.environ.get("AGENSIC_LOG_AGENT_NAME", "") or ""),
    "provenance_agent_hint": str(os.environ.get("AGENSIC_LOG_AGENT_HINT", "") or ""),
    "provenance_model_raw": str(os.environ.get("AGENSIC_LOG_MODEL_RAW", "") or ""),
    "provenance_wrapper_id": str(os.environ.get("AGENSIC_LOG_WRAPPER_ID", "") or ""),
    "proof_label": str(os.environ.get("AGENSIC_LOG_PROOF_LABEL", "") or ""),
    "proof_agent": str(os.environ.get("AGENSIC_LOG_PROOF_AGENT", "") or ""),
    "proof_model": str(os.environ.get("AGENSIC_LOG_PROOF_MODEL", "") or ""),
    "proof_trace": str(os.environ.get("AGENSIC_LOG_PROOF_TRACE", "") or ""),
    "proof_timestamp": as_int(os.environ.get("AGENSIC_LOG_PROOF_TIMESTAMP", None), None),
    "proof_signature": str(os.environ.get("AGENSIC_LOG_PROOF_SIGNATURE", "") or ""),
    "proof_signer_scope": str(os.environ.get("AGENSIC_LOG_PROOF_SIGNER_SCOPE", "") or ""),
    "proof_key_fingerprint": str(os.environ.get("AGENSIC_LOG_PROOF_KEY_FINGERPRINT", "") or ""),
    "proof_host_fingerprint": str(os.environ.get("AGENSIC_LOG_PROOF_HOST_FINGERPRINT", "") or ""),
}
print(json.dumps(payload, separators=(",", ":")))
PY
}

_agensic_log_command() {
    local command="$1"
    local exit_code="$2"
    local source="${3:-runtime}"
    local duration_ms="${4:-}"
    local json_data
    json_data="$(_agensic_build_log_command_json "$command" "$exit_code" "$source" "$duration_ms")"
    if [[ -z "$json_data" ]]; then
        return
    fi
    _agensic_reload_auth_token_if_needed
    (
        local -a auth_headers
        auth_headers=()
        if [[ -n "$AGENSIC_AUTH_TOKEN" ]]; then
            auth_headers=(-H "Authorization: Bearer $AGENSIC_AUTH_TOKEN" -H "X-Agensic-Auth: $AGENSIC_AUTH_TOKEN")
        fi
        curl -s -X POST "http://127.0.0.1:22000/log_command" \
             "${auth_headers[@]}" \
             -H "Content-Type: application/json" \
             -d "$json_data" > /dev/null 2>&1
    ) &!
}

_agensic_is_blocked_runtime_command() {
    local command="$1"
    local -a tokens
    local token=""
    local exe=""
    local exe_index=0
    local i=1

    tokens=(${(z)command})
    if (( ${#tokens[@]} == 0 )); then
        return 1
    fi

    while (( i <= ${#tokens[@]} )); do
        token="${tokens[$i]}"
        if [[ -z "$token" ]]; then
            (( i++ ))
            continue
        fi
        case "$token" in
            sudo|command)
                (( i++ ))
                continue
                ;;
            env|/usr/bin/env)
                (( i++ ))
                while (( i <= ${#tokens[@]} )); do
                    token="${tokens[$i]}"
                    if [[ -z "$token" || "$token" == -* || "$token" == *=* ]]; then
                        (( i++ ))
                        continue
                    fi
                    break
                done
                continue
                ;;
            -*|*=*)
                (( i++ ))
                continue
                ;;
            *)
                exe="${(L)${token:t}}"
                exe_index=$i
                break
                ;;
        esac
    done

    if [[ -z "$exe" ]]; then
        return 1
    fi

    if _agensic_value_in_array "$exe" "${AGENSIC_BLOCKED_EXECUTABLES[@]}"; then
        return 0
    fi

    local prefix
    for prefix in "${AGENSIC_BLOCKED_EXECUTABLE_PREFIXES[@]}"; do
        if [[ "$exe" == "$prefix"* ]]; then
            return 0
        fi
    done

    if [[ "$exe" == "history" ]]; then
        for (( i = exe_index + 1; i <= ${#tokens[@]}; i++ )); do
            token="${(L)${tokens[$i]}}"
            if [[ -z "$token" ]]; then
                continue
            fi
            if [[ "$token" == "--clear" ]]; then
                return 0
            fi
            if _agensic_token_has_short_flag "$token" "c"; then
                return 0
            fi
        done
        return 1
    fi

    if [[ "$exe" == "git" ]]; then
        local j=$(( exe_index + 1 ))
        local subcmd=""
        while (( j <= ${#tokens[@]} )); do
            token="${tokens[$j]}"
            if [[ -z "$token" ]]; then
                (( j++ ))
                continue
            fi
            case "$token" in
                --)
                    (( j++ ))
                    break
                    ;;
                -C|-c|--exec-path|--git-dir|--work-tree|--namespace|--super-prefix|--config-env)
                    (( j += 2 ))
                    continue
                    ;;
                --exec-path=*|--git-dir=*|--work-tree=*|--namespace=*|--super-prefix=*|--config-env=*|-C*|-c*)
                    (( j++ ))
                    continue
                    ;;
                -*)
                    (( j++ ))
                    continue
                    ;;
                *)
                    subcmd="${(L)token}"
                    (( j++ ))
                    break
                    ;;
            esac
        done

        if [[ "$subcmd" == "reset" ]]; then
            for (( ; j <= ${#tokens[@]}; j++ )); do
                token="${(L)${tokens[$j]}}"
                if [[ "$token" == "--hard" ]]; then
                    return 0
                fi
            done
            return 1
        fi

        if [[ "$subcmd" == "clean" ]]; then
            for (( ; j <= ${#tokens[@]}; j++ )); do
                token="${(L)${tokens[$j]}}"
                if [[ "$token" == "--force" || "$token" == "--force="* ]]; then
                    return 0
                fi
                if _agensic_token_has_short_flag "$token" "f"; then
                    return 0
                fi
            done
        fi
    fi

    return 1
}

_agensic_command_forces_human_provenance() {
    local command="$1"
    local -a tokens
    local token=""
    local idx=1
    local subcmd=""

    tokens=("${(z)command}")
    if (( ${#tokens[@]} == 0 )); then
        return 1
    fi

    while (( idx <= ${#tokens[@]} )); do
        token="${tokens[$idx]}"
        if [[ "$token" == [A-Za-z_][A-Za-z0-9_]*=* ]]; then
            (( idx += 1 ))
            continue
        fi
        break
    done

    if (( idx > ${#tokens[@]} )); then
        return 1
    fi
    if [[ "${tokens[$idx]##*/}" != "agensic" ]]; then
        return 1
    fi

    (( idx += 1 ))
    if (( idx > ${#tokens[@]} )); then
        return 1
    fi

    subcmd="${tokens[$idx]}"
    [[ "$subcmd" == "run" || "$subcmd" == "provenance" ]]
}

_agensic_ai_session_agent_matches_executable() {
    local executable="${1:l}"
    local session_agent="${2:l}"
    if [[ -z "$executable" || -z "$session_agent" ]]; then
        return 1
    fi

    case "$session_agent" in
        codex) [[ "$executable" == "codex" || "$executable" == "codex-agent" ]] ;;
        qwen_code) [[ "$executable" == "qwen" ]] ;;
        mini_agent) [[ "$executable" == "mini-agent" ]] ;;
        kimi_code) [[ "$executable" == "kimi" ]] ;;
        claude|claude_code) [[ "$executable" == "claude" ]] ;;
        gemini|gemini_cli) [[ "$executable" == "gemini" ]] ;;
        cursor) [[ "$executable" == "agent" ]] ;;
        kiro) [[ "$executable" == "kiro-cli" || "$executable" == "kiro" ]] ;;
        openclaw|opencode|aider|continue|ollama|nanoclaw) [[ "$executable" == "$session_agent" ]] ;;
        *) [[ "$executable" == "$session_agent" ]] ;;
    esac
}

_agensic_command_matches_ai_session_agent_inner() {
    local command="$1"
    local session_agent="$2"
    local depth="${3:-0}"
    local -a tokens
    local token=""
    local executable=""
    local idx=1
    local shell_script=""
    local shell_token=""

    if [[ -z "$session_agent" || "$depth" != <-> || "$depth" -gt 4 ]]; then
        return 1
    fi

    tokens=("${(z)command}")
    if (( ${#tokens[@]} == 0 )); then
        return 1
    fi

    while (( idx <= ${#tokens[@]} )); do
        token="${tokens[$idx]}"
        if [[ "$token" == [A-Za-z_][A-Za-z0-9_]*=* ]]; then
            (( idx += 1 ))
            continue
        fi
        if [[ "$token" == "env" || "$token" == "command" || "$token" == "builtin" || "$token" == "noglob" || "$token" == "nocorrect" || "$token" == "nohup" ]]; then
            (( idx += 1 ))
            continue
        fi
        if [[ "$token" == "setsid" ]]; then
            (( idx += 1 ))
            while (( idx <= ${#tokens[@]} )) && [[ "${tokens[$idx]}" == -* ]]; do
                (( idx += 1 ))
            done
            continue
        fi
        break
    done

    if (( idx > ${#tokens[@]} )); then
        return 1
    fi

    executable="${tokens[$idx]##*/}"
    if _agensic_ai_session_agent_matches_executable "$executable" "$session_agent"; then
        return 0
    fi

    case "${executable:l}" in
        sh|bash|zsh|fish)
            (( idx += 1 ))
            while (( idx <= ${#tokens[@]} )); do
                shell_token="${tokens[$idx]}"
                if [[ "$shell_token" == "-c" || "$shell_token" == "-lc" || "$shell_token" == "-ic" || "$shell_token" == "-lic" || "$shell_token" == "-ci" ]]; then
                    shell_script="${tokens[$(( idx + 1 ))]:-}"
                    break
                fi
                (( idx += 1 ))
            done
            if [[ -n "$shell_script" ]]; then
                shell_script="${(Q)shell_script}"
                _agensic_command_matches_ai_session_agent_inner "$shell_script" "$session_agent" "$(( depth + 1 ))"
                return $?
            fi
            ;;
    esac
    return 1
}

_agensic_command_matches_ai_session_agent() {
    local command="$1"
    local session_agent="${AGENSIC_AI_SESSION_AGENT:-}"

    if [[ "${AGENSIC_AI_SESSION_ACTIVE:-0}" != "1" || -z "$session_agent" ]]; then
        return 1
    fi
    _agensic_command_matches_ai_session_agent_inner "$command" "$session_agent" 0
}

_agensic_force_pending_human_typed_command() {
    _agensic_clear_pending_execution
    AGENSIC_PENDING_LAST_ACTION="human_typed"
    AGENSIC_PENDING_MANUAL_EDIT_AFTER_ACCEPT=0
    _agensic_clear_next_proof_fields
}

_agensic_preexec_hook() {
    if _agensic_session_is_disabled; then
        return
    fi
    AGENSIC_AI_SESSION_AUTO_STOP_ARMED=0
    if _agensic_command_forces_human_provenance "$1"; then
        _agensic_force_pending_human_typed_command
    else
        _agensic_session_sign_if_active
        if _agensic_command_matches_ai_session_agent "$1"; then
            AGENSIC_AI_SESSION_AUTO_STOP_ARMED=1
        fi
        if _agensic_pending_execution_has_provenance; then
            _agensic_refresh_pending_proof_fields
        else
            _agensic_snapshot_pending_execution
        fi
    fi
    AGENSIC_LAST_EXECUTED_CMD="$1"
    AGENSIC_LAST_EXECUTED_STARTED_AT_MS="$(_agensic_now_epoch_ms)"
}

_agensic_precmd_hook() {
    if _agensic_session_is_disabled; then
        return
    fi
    local exit_code="$?"
    _agensic_disable_mouse_reporting
    local cmd="$AGENSIC_LAST_EXECUTED_CMD"
    local started_at_ms="$AGENSIC_LAST_EXECUTED_STARTED_AT_MS"
    local finished_at_ms=""
    local duration_ms=""
    local auto_stop_armed="${AGENSIC_AI_SESSION_AUTO_STOP_ARMED:-0}"
    AGENSIC_LAST_EXECUTED_CMD=""
    AGENSIC_LAST_EXECUTED_STARTED_AT_MS=0
    AGENSIC_AI_SESSION_AUTO_STOP_ARMED=0
    _agensic_ensure_ai_session_timer
    _agensic_reload_disabled_patterns_if_needed
    _agensic_refresh_auto_session_wrappers_if_needed

    if [[ -z "$cmd" ]]; then
        return
    fi

    if [[ "$started_at_ms" == <-> ]]; then
        finished_at_ms="$(_agensic_now_epoch_ms)"
        if [[ "$finished_at_ms" == <-> ]]; then
            duration_ms=$(( finished_at_ms - started_at_ms ))
            if (( duration_ms < 0 )); then
                duration_ms=0
            elif (( duration_ms > 86400000 )); then
                duration_ms=86400000
            fi
        fi
    fi

    if _agensic_is_blocked_runtime_command "$cmd"; then
        _agensic_clear_pending_execution
        _agensic_reset_provenance_line_state
        if [[ "$auto_stop_armed" == "1" ]]; then
            _agensic_clear_ai_session_env
        fi
        return
    fi
    if _agensic_matches_disabled_pattern "$cmd"; then
        _agensic_clear_pending_execution
        _agensic_reset_provenance_line_state
        if [[ "$auto_stop_armed" == "1" ]]; then
            _agensic_clear_ai_session_env
        fi
        return
    fi

    _agensic_log_command "$cmd" "$exit_code" "runtime" "$duration_ms"
    if [[ "$auto_stop_armed" == "1" ]]; then
        _agensic_clear_ai_session_env
    fi
    _agensic_clear_pending_execution
    _agensic_reset_provenance_line_state
}

_agensic_reset_line_state() {
    AGENSIC_LINE_LLM_CALLS_USED=0
    AGENSIC_LINE_HAS_SPACE=0
    AGENSIC_SHOW_CTRL_SPACE_HINT=0
    AGENSIC_LAST_FETCH_USED_AI=0
    AGENSIC_LAST_FETCH_AI_AGENT=""
    AGENSIC_LAST_FETCH_AI_PROVIDER=""
    AGENSIC_LAST_FETCH_AI_MODEL=""
    _agensic_reset_provenance_line_state
}

_agensic_maybe_reset_line_state_for_empty_buffer() {
    if [[ -z "$BUFFER" ]]; then
        _agensic_reset_line_state
    fi
}

_agensic_try_fetch_on_space() {
    if _agensic_session_is_disabled; then
        _agensic_clear_suggestions
        return
    fi
    if _agensic_autocomplete_is_disabled; then
        _agensic_clear_suggestions
        return
    fi
    if _agensic_buffer_has_hash; then
        _agensic_clear_suggestions
        return
    fi
    if _agensic_should_skip_agensic_for_buffer; then
        _agensic_clear_suggestions
        return
    fi

    local allow_ai=1
    local is_manual="${1:-0}"
    local budget_blocked=0
    local trigger_source="space_auto"

    if [[ "$is_manual" != "1" ]] && _agensic_should_preserve_native_tab; then
        _agensic_clear_suggestions
        AGENSIC_SHOW_CTRL_SPACE_HINT=0
        AGENSIC_LAST_FETCH_USED_AI=0
        return
    fi

    if [[ "$is_manual" == "1" ]]; then
        trigger_source="manual_ctrl_space"
    fi
    if (( AGENSIC_LLM_BUDGET_UNLIMITED == 0 && AGENSIC_LINE_LLM_CALLS_USED >= AGENSIC_MAX_LLM_CALLS_PER_LINE )); then
        allow_ai=0
        budget_blocked=1
    fi

    AGENSIC_LAST_BUFFER="$BUFFER"
    _agensic_fetch_suggestions "$allow_ai" "$trigger_source"

    if (( AGENSIC_LAST_FETCH_USED_AI == 1 )); then
        AGENSIC_LINE_LLM_CALLS_USED=$((AGENSIC_LINE_LLM_CALLS_USED + 1))
    fi
    if (( allow_ai == 0 && budget_blocked == 1 && ${#AGENSIC_SUGGESTIONS[@]} == 0 )); then
        AGENSIC_SHOW_CTRL_SPACE_HINT=1
        _agensic_set_status_message "$AGENSIC_LLM_BUDGET_REACHED_HINT"
    elif [[ "${AGENSIC_SUGGESTIONS[1]}" != "${AGENSIC_STATUS_PREFIX}${AGENSIC_LLM_BUDGET_REACHED_HINT}" ]]; then
        AGENSIC_SHOW_CTRL_SPACE_HINT=0
    fi
}

_agensic_update_display() {
    local current="${AGENSIC_SUGGESTIONS[$AGENSIC_SUGGESTION_INDEX]}"
    local mode="${AGENSIC_ACCEPT_MODES[$AGENSIC_SUGGESTION_INDEX]}"
    local display_text="${AGENSIC_DISPLAY_TEXTS[$AGENSIC_SUGGESTION_INDEX]}"
    if _agensic_is_status_suggestion "$current"; then
        local status_msg="${current#$AGENSIC_STATUS_PREFIX}"
        status_msg="${status_msg//$'\n'/}"
        status_msg="${status_msg//$'\r'/}"
        POSTDISPLAY="$status_msg"
        region_highlight=("${#BUFFER} $((${#BUFFER} + ${#POSTDISPLAY})) fg=242")
        return
    fi
    
    local display_sugg=""
    if [[ "$mode" == "replace_full" ]]; then
        if [[ "$current" != "$BUFFER" ]]; then
            display_sugg=" ${display_text}"
        fi
    else
        # If filtering happened, we might need a part of the suggestion
        # The suggestions are suffixes relative to AGENSIC_LAST_BUFFER
        local typed_since_fetch="${BUFFER#$AGENSIC_LAST_BUFFER}"
        display_sugg="${current#$typed_since_fetch}"
        display_sugg="$(_agensic_merge_suffix "$BUFFER" "$display_sugg")"
    fi
    
    # Ensure no newlines break the display
    display_sugg="${display_sugg//$'\n'/}" 
    display_sugg="${display_sugg//$'\r'/}"
    
    if [[ -n "$display_sugg" ]]; then
        local count=${#AGENSIC_SUGGESTIONS[@]}
        if [[ $count -gt 1 ]]; then
            POSTDISPLAY="${display_sugg}  ($AGENSIC_SUGGESTION_INDEX/$count, Ctrl+P/N)"
        else
            POSTDISPLAY="$display_sugg"
        fi
        # Highlight BOTH the suggestion and the hint in grey (ghost text style)
        region_highlight=("${#BUFFER} $((${#BUFFER} + ${#POSTDISPLAY})) fg=242")
    else
        POSTDISPLAY=""
        region_highlight=()
    fi
}

_agensic_has_visible_suggestion() {
    local current="${AGENSIC_SUGGESTIONS[$AGENSIC_SUGGESTION_INDEX]}"
    local mode="${AGENSIC_ACCEPT_MODES[$AGENSIC_SUGGESTION_INDEX]}"
    local display_text="${AGENSIC_DISPLAY_TEXTS[$AGENSIC_SUGGESTION_INDEX]}"

    if _agensic_is_status_suggestion "$current"; then
        return 0
    fi

    if [[ -z "$current" ]]; then
        return 1
    fi

    local display_sugg=""
    if [[ "$mode" == "replace_full" ]]; then
        [[ "$current" == "$BUFFER" ]] && return 1
        display_sugg="$display_text"
    else
        local typed_since_fetch="${BUFFER#$AGENSIC_LAST_BUFFER}"
        display_sugg="${current#$typed_since_fetch}"
        display_sugg="$(_agensic_merge_suffix "$BUFFER" "$display_sugg")"
    fi
    display_sugg="${display_sugg//$'\n'/}"
    display_sugg="${display_sugg//$'\r'/}"

    [[ -n "$display_sugg" ]]
}

# ======================================================
# 2. PAUSE DETECTION (0.15s timer)
# ======================================================

_agensic_start_timer() {
    # Stop any existing timer callback
    _agensic_stop_timer

    # Create a one-shot readable fd after 0.15s and hook it into ZLE.
    local timer_fd=""
    exec {timer_fd}< <(
        sleep 0.15
        print -r -- "1"
    ) || return

    AGENSIC_TIMER_FD="$timer_fd"
    AGENSIC_TIMER_PID=""
    zle -F "$timer_fd" _agensic_on_timer_fd_ready 2>/dev/null
}

_agensic_stop_timer() {
    if [[ -n "$AGENSIC_TIMER_FD" && "$AGENSIC_TIMER_FD" == <-> ]]; then
        zle -F "$AGENSIC_TIMER_FD" 2>/dev/null
        local fd_to_close="$AGENSIC_TIMER_FD"
        exec {fd_to_close}<&-
        AGENSIC_TIMER_FD=""
    fi
    AGENSIC_TIMER_PID=""
}

_agensic_on_timer_fd_ready() {
    local fd="$1"
    if [[ -n "$fd" && "$fd" == <-> ]]; then
        zle -F "$fd" 2>/dev/null
        local discard=""
        read -ru "$fd" discard 2>/dev/null || true
        exec {fd}<&-
    fi
    if [[ "$AGENSIC_TIMER_FD" == "$fd" ]]; then
        AGENSIC_TIMER_FD=""
    fi
    AGENSIC_TIMER_PID=""
    if zle; then
        zle _agensic_on_timer_trigger
    fi
}

_agensic_on_timer_trigger() {
    if _agensic_session_is_disabled; then
        _agensic_clear_suggestions
        _agensic_update_display
        zle -R
        return
    fi
    if _agensic_autocomplete_is_disabled; then
        _agensic_clear_suggestions
        _agensic_update_display
        zle -R
        return
    fi
    # This is called when the 0.15s timer expires
    AGENSIC_TIMER_PID=""

    if _agensic_buffer_has_hash; then
        _agensic_clear_suggestions
        _agensic_update_display
        zle -R
        return
    fi
    if _agensic_should_skip_agensic_for_buffer; then
        _agensic_clear_suggestions
        _agensic_update_display
        zle -R
        return
    fi
    if _agensic_should_preserve_native_tab; then
        _agensic_clear_suggestions
        _agensic_update_display
        zle -R
        return
    fi
    
    # Only fetch if buffer has changed and is long enough
    if [[ "$BUFFER" != "$AGENSIC_LAST_BUFFER" && ${#BUFFER} -ge 2 ]]; then
        AGENSIC_LAST_BUFFER="$BUFFER"
        # Timer-based fetch is vector-store only (no LLM).
        _agensic_fetch_suggestions 0 "pause_timer"
        _agensic_update_display
        zle -R
    fi
}

# Register the trigger function as a widget so it can access POSTDISPLAY
zle -N _agensic_on_timer_trigger

TRAPUSR2() {
    AGENSIC_AI_SESSION_TIMER_PID=""
    _agensic_enforce_ai_session_expiry
}

# ======================================================
# 3. WIDGET DEFINITIONS
# ======================================================

_agensic_clear_suggestions() {
    if zle; then
        POSTDISPLAY=""
        region_highlight=()
    fi
    AGENSIC_SUGGESTIONS=()
    AGENSIC_DISPLAY_TEXTS=()
    AGENSIC_ACCEPT_MODES=()
    AGENSIC_SUGGESTION_KINDS=()
    _agensic_reset_intent_state
    _agensic_stop_timer
}

_agensic_self_insert() {
    local inserted_key="$KEYS"
    zle .self-insert
    _agensic_mark_manual_line_edit "human_typed"

    if _agensic_session_is_disabled; then
        _agensic_clear_suggestions
        _agensic_update_display
        zle -R
        return
    fi
    if _agensic_autocomplete_is_disabled; then
        _agensic_clear_suggestions
        _agensic_update_display
        zle -R
        return
    fi

    _agensic_maybe_reset_line_state_for_empty_buffer
    _agensic_reset_intent_state

    if _agensic_buffer_has_hash; then
        _agensic_clear_suggestions
        _agensic_update_display
        zle -R
        return
    fi
    if _agensic_should_skip_agensic_for_buffer; then
        _agensic_clear_suggestions
        _agensic_update_display
        zle -R
        return
    fi
    if _agensic_should_preserve_native_tab; then
        _agensic_clear_suggestions
        AGENSIC_SHOW_CTRL_SPACE_HINT=0
        _agensic_update_display
        zle -R
        return
    fi

    # Filter existing pool if we have one
    if [[ ${#AGENSIC_SUGGESTIONS[@]} -gt 0 ]]; then
        _agensic_filter_pool
        _agensic_update_display
        zle -R
    fi

    # Auto fetch only when user presses space (new command segment boundary).
    if [[ "$inserted_key" == " " && ${#BUFFER} -ge 2 ]]; then
        _agensic_stop_timer
        AGENSIC_LINE_HAS_SPACE=1
        _agensic_try_fetch_on_space 0
        _agensic_update_display
        zle -R
        return
    fi

    # Non-space typing uses 0.2s pause detection; fetch stays vector-only there.
    if [[ ${#BUFFER} -ge 2 ]]; then
        _agensic_start_timer
    else
        _agensic_stop_timer
    fi
}

_agensic_backward_delete_char() {
    zle .backward-delete-char
    _agensic_mark_manual_line_edit "human_edit"

    # Clear suggestions and pool on delete
    _agensic_reset_intent_state
    _agensic_clear_suggestions
    _agensic_maybe_reset_line_state_for_empty_buffer
}

_agensic_interrupt() {
    _agensic_clear_suggestions
    _agensic_reset_line_state
    zle .send-break
}

_agensic_escape() {
    if _agensic_has_visible_suggestion; then
        _agensic_clear_suggestions
        _agensic_update_display
        zle -R
        return
    fi

    local keymap="${KEYMAP:-emacs}"
    case "$keymap" in
        main) keymap="emacs" ;;
        viopp|visual) keymap="vicmd" ;;
    esac

    local native_widget="${AGENSIC_NATIVE_ESC_WIDGET[$keymap]}"
    if [[ -n "$native_widget" && "$native_widget" != "_agensic_escape" ]]; then
        zle "$native_widget"
    else
        zle .undefined-key
    fi
}

# --- Paste Handling ---
autoload -Uz bracketed-paste-magic
_agensic_paste() {
    _agensic_reset_intent_state
    _agensic_clear_suggestions
    zle .bracketed-paste
    _agensic_mark_manual_line_edit "human_paste"
    _agensic_maybe_reset_line_state_for_empty_buffer
}

# --- Accept Suggestion ---
_agensic_accept_widget() {
    if _agensic_is_single_hash_intent; then
        _agensic_clear_suggestions
        _agensic_resolve_intent_command "$BUFFER"
        zle -R
        return
    fi

    if _agensic_is_double_hash_assist; then
        zle expand-or-complete
        return
    fi
    if _agensic_should_skip_agensic_for_buffer || _agensic_should_preserve_native_tab; then
        _agensic_clear_suggestions
        zle expand-or-complete
        return
    fi

    local current="${AGENSIC_SUGGESTIONS[$AGENSIC_SUGGESTION_INDEX]}"
    local mode="${AGENSIC_ACCEPT_MODES[$AGENSIC_SUGGESTION_INDEX]}"
    local kind="${AGENSIC_SUGGESTION_KINDS[$AGENSIC_SUGGESTION_INDEX]}"
    if _agensic_is_status_suggestion "$current"; then
        zle expand-or-complete
    elif [[ -n "$current" ]]; then
        local origin="ag"
        local ai_agent=""
        local ai_provider=""
        local ai_model=""
        if (( AGENSIC_LAST_FETCH_USED_AI == 1 )); then
            origin="ai"
            ai_agent="$AGENSIC_LAST_FETCH_AI_AGENT"
            ai_provider="$AGENSIC_LAST_FETCH_AI_PROVIDER"
            ai_model="$AGENSIC_LAST_FETCH_AI_MODEL"
        fi
        if [[ "$mode" == "replace_full" ]]; then
            local normalized_buffer="$(_agensic_canonicalize_buffer_spacing "$BUFFER")"
            local replacement="$(_agensic_canonicalize_buffer_spacing "$current")"
            _agensic_send_feedback "$normalized_buffer" "$replacement" "replace_full"
            BUFFER="$replacement"
            _agensic_set_suggestion_accept_state "$origin" "replace_full" "${kind:-normal}" "$ai_agent" "$ai_provider" "$ai_model"
        else
            local typed_since_fetch="${BUFFER#$AGENSIC_LAST_BUFFER}"
            local to_add="${current#$typed_since_fetch}"
            to_add="$(_agensic_merge_suffix "$BUFFER" "$to_add")"
            local merged="${BUFFER}${to_add}"
            local normalized_merged="$(_agensic_canonicalize_buffer_spacing "$merged")"
            local normalized_buffer="$(_agensic_canonicalize_buffer_spacing "$BUFFER")"
            local normalized_to_add="$to_add"
            if [[ "$normalized_merged" == "$normalized_buffer"* ]]; then
                normalized_to_add="${normalized_merged#$normalized_buffer}"
            fi
            _agensic_send_feedback "$normalized_buffer" "$normalized_to_add" "suffix_append"
            BUFFER="$normalized_merged"
            _agensic_set_suggestion_accept_state "$origin" "suffix_append" "${kind:-normal}" "$ai_agent" "$ai_provider" "$ai_model"
        fi
        CURSOR=${#BUFFER}
        _agensic_clear_suggestions
        zle -R
    else
        zle expand-or-complete
    fi
}

# --- Partial Accept ---
_agensic_partial_accept() {
    local current="${AGENSIC_SUGGESTIONS[$AGENSIC_SUGGESTION_INDEX]}"
    local mode="${AGENSIC_ACCEPT_MODES[$AGENSIC_SUGGESTION_INDEX]}"
    local kind="${AGENSIC_SUGGESTION_KINDS[$AGENSIC_SUGGESTION_INDEX]}"
    local origin="ag"
    local ai_agent=""
    local ai_provider=""
    local ai_model=""
    if (( AGENSIC_LAST_FETCH_USED_AI == 1 )); then
        origin="ai"
        ai_agent="$AGENSIC_LAST_FETCH_AI_AGENT"
        ai_provider="$AGENSIC_LAST_FETCH_AI_PROVIDER"
        ai_model="$AGENSIC_LAST_FETCH_AI_MODEL"
    fi
    
    if _agensic_is_status_suggestion "$current"; then
        zle forward-word
    elif [[ "$mode" == "replace_full" ]]; then
        BUFFER="$(_agensic_canonicalize_buffer_spacing "$current")"
        _agensic_set_suggestion_accept_state "$origin" "replace_full" "${kind:-normal}" "$ai_agent" "$ai_provider" "$ai_model"
        CURSOR=${#BUFFER}
        _agensic_clear_suggestions
        zle -R
    elif [[ -n "$current" ]]; then
        local typed_since_fetch="${BUFFER#$AGENSIC_LAST_BUFFER}"
        local remaining="${current#$typed_since_fetch}"
        remaining="$(_agensic_merge_suffix "$BUFFER" "$remaining")"
        local first_word="${remaining%% *}"
        if [[ "$first_word" == "$remaining" ]]; then
             BUFFER="${BUFFER}${remaining}"
        else
             BUFFER="${BUFFER}${first_word} "
        fi
        BUFFER="$(_agensic_canonicalize_buffer_spacing "$BUFFER")"
        _agensic_set_suggestion_accept_state "$origin" "suffix_append" "${kind:-normal}" "$ai_agent" "$ai_provider" "$ai_model"
        CURSOR=${#BUFFER}
        _agensic_clear_suggestions
        zle -R
    else
        zle forward-word
    fi
}

# --- Cycle Suggestions ---
_agensic_cycle_next() {
    if (( AGENSIC_INTENT_ACTIVE == 1 && ${#AGENSIC_INTENT_OPTIONS[@]} > 0 )); then
        local count=${#AGENSIC_INTENT_OPTIONS[@]}
        AGENSIC_INTENT_OPTION_INDEX=$(( AGENSIC_INTENT_OPTION_INDEX % count + 1 ))
        BUFFER="${AGENSIC_INTENT_OPTIONS[$AGENSIC_INTENT_OPTION_INDEX]}"
        CURSOR=${#BUFFER}
        _agensic_update_intent_hint
        zle -R
        return
    fi

    local count=${#AGENSIC_SUGGESTIONS[@]}
    if [[ $count -gt 0 ]]; then
        AGENSIC_SUGGESTION_INDEX=$(( AGENSIC_SUGGESTION_INDEX % count + 1 ))
        _agensic_update_display
        zle -R
    else
        zle down-line-or-history
    fi
}

_agensic_cycle_prev() {
    if (( AGENSIC_INTENT_ACTIVE == 1 && ${#AGENSIC_INTENT_OPTIONS[@]} > 0 )); then
        local count=${#AGENSIC_INTENT_OPTIONS[@]}
        AGENSIC_INTENT_OPTION_INDEX=$(( (AGENSIC_INTENT_OPTION_INDEX + count - 2) % count + 1 ))
        BUFFER="${AGENSIC_INTENT_OPTIONS[$AGENSIC_INTENT_OPTION_INDEX]}"
        CURSOR=${#BUFFER}
        _agensic_update_intent_hint
        zle -R
        return
    fi

    local count=${#AGENSIC_SUGGESTIONS[@]}
    if [[ $count -gt 0 ]]; then
        AGENSIC_SUGGESTION_INDEX=$(( (AGENSIC_SUGGESTION_INDEX + count - 2) % count + 1 ))
        _agensic_update_display
        zle -R
    else
        zle up-line-or-history
    fi
}

_agensic_down_line_or_history() {
    _agensic_clear_suggestions
    zle down-line-or-history
    _agensic_mark_manual_line_edit "human_edit"
    zle -R
}

_agensic_up_line_or_history() {
    _agensic_clear_suggestions
    zle up-line-or-history
    _agensic_mark_manual_line_edit "human_edit"
    zle -R
}

# --- Manual Trigger (Ctrl+Space) ---
_agensic_manual_trigger() {
    if [[ ${#BUFFER} -ge 2 ]]; then
        if _agensic_autocomplete_is_disabled; then
            _agensic_clear_suggestions
            _agensic_update_display
            zle -R
            return
        fi
        if _agensic_should_skip_agensic_for_buffer; then
            _agensic_clear_suggestions
            _agensic_update_display
            zle -R
            return
        fi
        _agensic_try_fetch_on_space 1
        _agensic_update_display
        zle -R
    fi
}

# --- Accept Line (Execute Command) ---
_agensic_accept_line() {
    if _agensic_is_double_hash_assist; then
        _agensic_clear_suggestions
        _agensic_resolve_general_assist "$BUFFER"
        zle -R
        return
    fi

    if _agensic_is_single_hash_intent; then
        _agensic_clear_suggestions
        _agensic_resolve_intent_command "$BUFFER"
        zle -R
        return
    fi

    if [[ -n "${BUFFER//[[:space:]]/}" ]]; then
        _agensic_snapshot_pending_execution
    else
        _agensic_clear_pending_execution
    fi
    _agensic_clear_suggestions
    _agensic_reset_line_state
    zle .accept-line
}

# ======================================================
# 4. ZLE REGISTRATION (Must occur before binding)
# ======================================================

zle -N _agensic_update_display
zle -N _agensic_accept_widget
zle -N _agensic_cycle_next
zle -N _agensic_cycle_prev
zle -N _agensic_partial_accept
zle -N _agensic_manual_trigger
zle -N _agensic_accept_line
zle -N _agensic_down_line_or_history
zle -N _agensic_up_line_or_history
zle -N self-insert _agensic_self_insert
zle -N backward-delete-char _agensic_backward_delete_char
zle -N _agensic_interrupt
zle -N _agensic_escape
zle -N bracketed-paste _agensic_paste

# ======================================================
# 5. KEY BINDINGS
# ======================================================

_agensic_bind_widget() {
    local key="$1"
    local widget="$2"
    # Protect against empty widget args causing errors
    if [[ -n "$widget" ]]; then
        bindkey -M emacs "$key" "$widget"
        bindkey -M viins "$key" "$widget"
        bindkey -M vicmd "$key" "$widget"
    fi
}

_agensic_default_escape_widget() {
    local keymap="$1"
    case "$keymap" in
        viins) print -r -- "vi-cmd-mode" ;;
        *) print -r -- "undefined-key" ;;
    esac
}

_agensic_capture_native_escape_binding() {
    local keymap="$1"
    local binding
    local widget

    binding="$(bindkey -M "$keymap" '^[' 2>/dev/null)"
    widget="${binding##* }"

    if [[ -z "$widget" || "$widget" == "$binding" || "$widget" == "_agensic_escape" ]]; then
        widget="$(_agensic_default_escape_widget "$keymap")"
    fi

    if [[ -n "$widget" && "$widget" != "undefined-key" ]]; then
        AGENSIC_NATIVE_ESC_WIDGET[$keymap]="$widget"
    else
        AGENSIC_NATIVE_ESC_WIDGET[$keymap]=""
    fi
}

_agensic_capture_native_escape_binding emacs
_agensic_capture_native_escape_binding viins
_agensic_capture_native_escape_binding vicmd
_agensic_reload_disabled_patterns_if_needed
_agensic_reload_auth_token_if_needed
_agensic_refresh_auto_session_wrappers_if_needed
_agensic_ensure_ai_session_timer
_agensic_disable_mouse_reporting

# --- Core Controls ---
_agensic_bind_widget '^@' _agensic_manual_trigger    # Ctrl+Space (manual trigger)
_agensic_bind_widget '^I' _agensic_accept_widget     # Tab
_agensic_bind_widget '^P' _agensic_cycle_prev
_agensic_bind_widget '^N' _agensic_cycle_next
_agensic_bind_widget '^C' _agensic_interrupt
_agensic_bind_widget '^[' _agensic_escape
_agensic_bind_widget '^M' _agensic_accept_line       # Enter
_agensic_bind_widget '^[[A' _agensic_up_line_or_history
_agensic_bind_widget '^[[B' _agensic_down_line_or_history
_agensic_bind_widget '^OA' _agensic_up_line_or_history
_agensic_bind_widget '^OB' _agensic_down_line_or_history

# --- Partial Accept (Option+Right) ---
_agensic_bind_widget '^[[1;3C' _agensic_partial_accept
_agensic_bind_widget '^[[1;9C' _agensic_partial_accept
_agensic_bind_widget '^[f' _agensic_partial_accept

# ======================================================
# 6. SHELL LIFECYCLE HOOKS (Success-only learning)
# ======================================================
if (( AGENSIC_HOOKS_REGISTERED == 0 )); then
    autoload -Uz add-zsh-hook
    add-zsh-hook preexec _agensic_preexec_hook
    add-zsh-hook precmd _agensic_precmd_hook
    AGENSIC_HOOKS_REGISTERED=1
fi
