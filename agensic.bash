#!/usr/bin/env bash

# Agensic Bash adapter entrypoint.
# This currently handles shared helper loading and ble.sh discovery/attach.
# Inline suggestion rendering and keybindings will be layered on top of this.

if [[ -n "${BASH_SOURCE[0]:-}" ]]; then
    AGENSIC_SOURCE_PATH="${BASH_SOURCE[0]}"
elif [[ -z "${AGENSIC_SOURCE_PATH:-}" ]]; then
    AGENSIC_STATE_HOME="${XDG_STATE_HOME:-${HOME}/.local/state}"
    AGENSIC_SOURCE_PATH="${AGENSIC_STATE_HOME}/agensic/install/agensic.bash"
fi

AGENSIC_SOURCE_DIR="$(cd "$(dirname "${AGENSIC_SOURCE_PATH}")" && pwd)"
AGENSIC_CONFIG_HOME="${XDG_CONFIG_HOME:-${HOME}/.config}"
AGENSIC_STATE_HOME="${XDG_STATE_HOME:-${HOME}/.local/state}"
AGENSIC_HOME="${AGENSIC_STATE_HOME}/agensic"
AGENSIC_CONFIG_PATH="${AGENSIC_CONFIG_HOME}/agensic/config.json"
AGENSIC_AUTH_PATH="${AGENSIC_CONFIG_HOME}/agensic/auth.json"
AGENSIC_PLUGIN_LOG="${AGENSIC_HOME}/plugin.log"
AGENSIC_SHARED_HELPERS_PATH="${AGENSIC_SOURCE_DIR}/shell/agensic_shared.sh"
AGENSIC_CLIENT_HELPER="${AGENSIC_CLIENT_HELPER:-${AGENSIC_SOURCE_DIR}/shell_client.py}"
AGENSIC_RUNTIME_PYTHON="${AGENSIC_RUNTIME_PYTHON:-}"
AGENSIC_BLE_SH_PATH="${AGENSIC_BLE_SH_PATH:-}"
AGENSIC_BASH_ADAPTER_READY=0
AGENSIC_BASH_BLE_AVAILABLE=0
AGENSIC_BASH_BLE_LOADED_FROM=""
AGENSIC_BASH_BLE_WARNING_EMITTED=0
AGENSIC_BASH_WIDGETS_REGISTERED=0
AGENSIC_BASH_GHOST_ACTIVE=0
AGENSIC_BASH_GHOST_SUFFIX=""
AGENSIC_STATUS_PREFIX="__AGENSIC_STATUS__:"
AGENSIC_FETCH_ATTEMPT_COUNT=0
AGENSIC_FETCH_SUCCESS_COUNT=0
AGENSIC_LAST_FETCH_ERROR_CODE=""
AGENSIC_LAST_FETCH_USED_AI=0
AGENSIC_LAST_FETCH_AI_AGENT=""
AGENSIC_LAST_FETCH_AI_PROVIDER=""
AGENSIC_LAST_FETCH_AI_MODEL=""
AGENSIC_LAST_NL_INPUT=""
AGENSIC_LAST_NL_KIND=""
AGENSIC_LAST_NL_COMMAND=""
AGENSIC_LAST_NL_ASSIST=""
AGENSIC_LAST_EXECUTED_CMD=""
AGENSIC_LAST_EXECUTED_STARTED_AT_MS=0
AGENSIC_AUTH_MTIME=""
AGENSIC_AUTH_TOKEN=""
AGENSIC_LAST_BUFFER=""
AGENSIC_SUGGESTION_INDEX=0
AGENSIC_SUGGESTIONS=()
AGENSIC_DISPLAY_TEXTS=()
AGENSIC_ACCEPT_MODES=()
AGENSIC_SUGGESTION_KINDS=()
AGENSIC_BASH_AT_PROMPT=1
AGENSIC_BASH_IN_PROMPT_HOOK=0
AGENSIC_BASH_ORIGINAL_PROMPT_COMMAND="${PROMPT_COMMAND:-}"
AGENSIC_BASH_RUNTIME_HOOKS_REGISTERED=0

if [[ -f "$AGENSIC_SHARED_HELPERS_PATH" ]]; then
    # shellcheck disable=SC1090
    source "$AGENSIC_SHARED_HELPERS_PATH"
fi

if [[ -z "$AGENSIC_RUNTIME_PYTHON" ]]; then
    AGENSIC_RUNTIME_PYTHON="${AGENSIC_HOME}/install/.venv/bin/python"
fi
if [[ ! -x "$AGENSIC_RUNTIME_PYTHON" ]]; then
    AGENSIC_RUNTIME_PYTHON="${AGENSIC_SOURCE_DIR}/.venv/bin/python"
fi
if [[ ! -x "$AGENSIC_RUNTIME_PYTHON" ]]; then
    AGENSIC_RUNTIME_PYTHON="python3"
fi

_agensic_bash_is_interactive() {
    [[ "$-" == *i* ]]
}

_agensic_bash_log() {
    local message="$1"
    mkdir -p "$AGENSIC_HOME" 2>/dev/null || return
    chmod 700 "$AGENSIC_HOME" 2>/dev/null || true
    {
        printf "%s bash_adapter=%s\n" \
            "$(date '+%Y-%m-%d %H:%M:%S' 2>/dev/null)" \
            "$message"
    } >> "$AGENSIC_PLUGIN_LOG" 2>/dev/null
    chmod 600 "$AGENSIC_PLUGIN_LOG" 2>/dev/null || true
}

_agensic_bash_get_auth_mtime() {
    if [[ ! -f "$AGENSIC_AUTH_PATH" ]]; then
        printf '%s\n' ""
        return
    fi
    stat -c '%Y' "$AGENSIC_AUTH_PATH" 2>/dev/null || printf '%s\n' ""
}

_agensic_reload_auth_token_if_needed() {
    local current_mtime=""
    current_mtime="$(_agensic_bash_get_auth_mtime)"
    if [[ "$current_mtime" == "$AGENSIC_AUTH_MTIME" ]]; then
        return
    fi
    AGENSIC_AUTH_MTIME="$current_mtime"
    AGENSIC_AUTH_TOKEN=""
    if [[ -z "$current_mtime" || ! -f "$AGENSIC_AUTH_PATH" ]]; then
        return
    fi

    local token=""
    token="$(
        python3 -c "
import json
path = '''$AGENSIC_AUTH_PATH'''
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

_agensic_bash_current_buffer() {
    printf '%s\n' "${_ble_edit_str:-}"
}

_agensic_bash_current_cursor() {
    printf '%s\n' "${_ble_edit_ind:-0}"
}

_agensic_bash_set_buffer() {
    local value="$1"
    _ble_edit_str="$value"
    _ble_edit_ind=${#_ble_edit_str}
    if declare -F ble/widget/redraw-line >/dev/null 2>&1; then
        ble/widget/redraw-line >/dev/null 2>&1 || true
    fi
}

_agensic_bash_strip_ghost_if_present() {
    if [[ "${AGENSIC_BASH_GHOST_ACTIVE:-0}" != "1" || -z "${AGENSIC_BASH_GHOST_SUFFIX:-}" ]]; then
        return 0
    fi

    local current="${_ble_edit_str:-}"
    if [[ "$current" == *"${AGENSIC_BASH_GHOST_SUFFIX}" ]]; then
        _ble_edit_str="${current%"$AGENSIC_BASH_GHOST_SUFFIX"}"
        if (( _ble_edit_ind > ${#_ble_edit_str} )); then
            _ble_edit_ind=${#_ble_edit_str}
        fi
    fi
    AGENSIC_BASH_GHOST_ACTIVE=0
    AGENSIC_BASH_GHOST_SUFFIX=""
}

_agensic_bash_apply_ghost_suffix() {
    local base="$1"
    local suffix="$2"

    _agensic_bash_strip_ghost_if_present
    if [[ -z "$suffix" ]]; then
        return 0
    fi

    _ble_edit_str="${base}${suffix}"
    _ble_edit_ind=${#base}
    AGENSIC_BASH_GHOST_ACTIVE=1
    AGENSIC_BASH_GHOST_SUFFIX="$suffix"
    if declare -F ble/widget/redraw-line >/dev/null 2>&1; then
        ble/widget/redraw-line >/dev/null 2>&1 || true
    fi
}

_agensic_bash_render_info() {
    local message="$1"
    if declare -p _ble_edit_info >/dev/null 2>&1; then
        _ble_edit_info[0]=1
        _ble_edit_info[1]=0
        _ble_edit_info[2]="$message"
        _ble_edit_info_invalidated=1
    fi
    if declare -F ble/widget/redraw-line >/dev/null 2>&1; then
        ble/widget/redraw-line >/dev/null 2>&1 || true
    fi
}

_agensic_bash_clear_info() {
    if declare -p _ble_edit_info >/dev/null 2>&1; then
        _ble_edit_info[0]=0
        _ble_edit_info[1]=0
        _ble_edit_info[2]=""
        _ble_edit_info_invalidated=1
    fi
    if declare -F ble/widget/redraw-line >/dev/null 2>&1; then
        ble/widget/redraw-line >/dev/null 2>&1 || true
    fi
}

_agensic_bash_render_markdown_or_plain() {
    local text="$1"
    if command -v python3 >/dev/null 2>&1; then
        AGENSIC_MARKDOWN_TEXT="$text" python3 -c "
import os
text = os.environ.get('AGENSIC_MARKDOWN_TEXT', '')
try:
    from rich.console import Console
    from rich.markdown import Markdown
    Console(soft_wrap=True).print(Markdown(text))
except Exception:
    print(text)
" 2>/dev/null && return 0
    fi
    printf '%s\n' "$text"
}

_agensic_bash_now_epoch_ms() {
    python3 - <<'PY' 2>/dev/null
import time
print(int(time.time() * 1000))
PY
}

_agensic_bash_is_status_suggestion() {
    [[ "${1:-}" == "$AGENSIC_STATUS_PREFIX"* ]]
}

_agensic_bash_clear_suggestions() {
    _agensic_bash_strip_ghost_if_present
    AGENSIC_SUGGESTIONS=()
    AGENSIC_DISPLAY_TEXTS=()
    AGENSIC_ACCEPT_MODES=()
    AGENSIC_SUGGESTION_KINDS=()
    AGENSIC_SUGGESTION_INDEX=0
    _agensic_bash_clear_info
}

_agensic_bash_update_display() {
    if (( ${#AGENSIC_SUGGESTIONS[@]} == 0 || AGENSIC_SUGGESTION_INDEX <= 0 )); then
        _agensic_bash_clear_info
        _agensic_bash_strip_ghost_if_present
        return
    fi

    local current="${AGENSIC_SUGGESTIONS[$((AGENSIC_SUGGESTION_INDEX - 1))]}"
    local display_text="${AGENSIC_DISPLAY_TEXTS[$((AGENSIC_SUGGESTION_INDEX - 1))]}"
    local mode="${AGENSIC_ACCEPT_MODES[$((AGENSIC_SUGGESTION_INDEX - 1))]}"
    local buffer=""
    local message=""
    local inline_suffix=""

    if _agensic_bash_is_status_suggestion "$current"; then
        _agensic_bash_strip_ghost_if_present
        message="${current#${AGENSIC_STATUS_PREFIX}}"
        _agensic_bash_render_info "$message"
        return
    fi

    _agensic_bash_strip_ghost_if_present
    buffer="$(_agensic_bash_current_buffer)"
    if [[ "$mode" == "replace_full" ]]; then
        if [[ "$current" == "$buffer" ]]; then
            _agensic_bash_clear_info
            return
        fi
        inline_suffix=" $display_text"
    else
        local typed_since_fetch="${buffer#"$AGENSIC_LAST_BUFFER"}"
        inline_suffix="${current#"$typed_since_fetch"}"
        inline_suffix="$(_agensic_merge_suffix "$buffer" "$inline_suffix")"
    fi

    if [[ -n "$inline_suffix" ]]; then
        _agensic_bash_apply_ghost_suffix "$buffer" "$inline_suffix"
        local count=${#AGENSIC_SUGGESTIONS[@]}
        if (( count > 1 )); then
            message="(${AGENSIC_SUGGESTION_INDEX}/${count}, Ctrl+P/N)"
            _agensic_bash_render_info "$message"
        else
            _agensic_bash_clear_info
        fi
    else
        _agensic_bash_clear_info
    fi
}

_agensic_bash_filter_pool() {
    local buffer=""
    buffer="$(_agensic_bash_current_buffer)"
    if [[ "${#buffer}" -lt "${#AGENSIC_LAST_BUFFER}" ]]; then
        _agensic_bash_clear_suggestions
        return
    fi

    local typed_since_fetch="${buffer#"$AGENSIC_LAST_BUFFER"}"
    local -a next_suggestions=()
    local -a next_displays=()
    local -a next_modes=()
    local -a next_kinds=()
    local i=0

    for (( i = 0; i < ${#AGENSIC_SUGGESTIONS[@]}; i++ )); do
        local sugg="${AGENSIC_SUGGESTIONS[$i]}"
        local display="${AGENSIC_DISPLAY_TEXTS[$i]}"
        local mode="${AGENSIC_ACCEPT_MODES[$i]}"
        local kind="${AGENSIC_SUGGESTION_KINDS[$i]}"
        if _agensic_bash_is_status_suggestion "$sugg"; then
            next_suggestions+=("$sugg")
            next_displays+=("$display")
            next_modes+=("$mode")
            next_kinds+=("$kind")
        elif [[ "$mode" == "replace_full" ]]; then
            next_suggestions+=("$sugg")
            next_displays+=("$display")
            next_modes+=("$mode")
            next_kinds+=("$kind")
        elif [[ "$sugg" == "$typed_since_fetch"* ]]; then
            next_suggestions+=("$sugg")
            next_displays+=("$display")
            next_modes+=("${mode:-suffix_append}")
            next_kinds+=("${kind:-normal}")
        fi
    done

    AGENSIC_SUGGESTIONS=("${next_suggestions[@]}")
    AGENSIC_DISPLAY_TEXTS=("${next_displays[@]}")
    AGENSIC_ACCEPT_MODES=("${next_modes[@]}")
    AGENSIC_SUGGESTION_KINDS=("${next_kinds[@]}")
    if (( ${#AGENSIC_SUGGESTIONS[@]} > 0 )); then
        AGENSIC_SUGGESTION_INDEX=1
    else
        AGENSIC_SUGGESTION_INDEX=0
    fi
    _agensic_bash_update_display
}

_agensic_bash_fetch_suggestions() {
    local allow_ai="${1:-1}"
    local trigger_source="${2:-manual}"
    local buffer=""
    local cursor=""
    local request_json=""
    local response_json=""
    local parsed=""
    local sep=$'\x1f'

    _agensic_bash_strip_ghost_if_present
    buffer="$(_agensic_bash_current_buffer)"
    cursor="$(_agensic_bash_current_cursor)"
    AGENSIC_LAST_FETCH_USED_AI=0
    AGENSIC_LAST_FETCH_AI_AGENT=""
    AGENSIC_LAST_FETCH_AI_PROVIDER=""
    AGENSIC_LAST_FETCH_AI_MODEL=""
    AGENSIC_FETCH_ATTEMPT_COUNT=$((AGENSIC_FETCH_ATTEMPT_COUNT + 1))

    if [[ ${#buffer} -lt 2 || ! -f "$AGENSIC_CLIENT_HELPER" ]]; then
        _agensic_bash_clear_suggestions
        return
    fi

    request_json="$(
        AGENSIC_REQ_BUFFER="$buffer" \
        AGENSIC_REQ_CURSOR="$cursor" \
        AGENSIC_REQ_CWD="$PWD" \
        AGENSIC_REQ_ALLOW_AI="$allow_ai" \
        AGENSIC_REQ_TRIGGER_SOURCE="$trigger_source" \
        python3 -c "
import json, os
payload = {
    'command_buffer': os.environ.get('AGENSIC_REQ_BUFFER', ''),
    'cursor_position': int(os.environ.get('AGENSIC_REQ_CURSOR', '0') or '0'),
    'working_directory': os.environ.get('AGENSIC_REQ_CWD', ''),
    'shell': 'bash',
    'allow_ai': bool(int(os.environ.get('AGENSIC_REQ_ALLOW_AI', '1') or '1')),
    'trigger_source': os.environ.get('AGENSIC_REQ_TRIGGER_SOURCE', 'unknown'),
}
print(json.dumps(payload, separators=(',', ':')))
" 2>/dev/null
    )"

    _agensic_reload_auth_token_if_needed
    local -a helper_cmd=()
    helper_cmd=("$AGENSIC_RUNTIME_PYTHON" "$AGENSIC_CLIENT_HELPER" --timeout 3.0)
    if [[ -n "$AGENSIC_AUTH_TOKEN" ]]; then
        helper_cmd+=("--auth-token=$AGENSIC_AUTH_TOKEN")
    fi
    response_json="$(printf '%s' "$request_json" | "${helper_cmd[@]}" 2>/dev/null)"

    parsed="$(
        AGENSIC_CLIENT_RESPONSE="$response_json" python3 -c "
import json, os
sep = '\x1f'
raw = os.environ.get('AGENSIC_CLIENT_RESPONSE', '')
try:
    data = json.loads(raw)
except Exception:
    print('ok=0')
    raise SystemExit(0)

if not bool(data.get('ok', False)):
    print('ok=0')
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
print('used_ai=' + ('1' if bool(data.get('used_ai', False)) else '0'))
print('ai_agent=' + str(data.get('ai_agent', '') or ''))
print('ai_provider=' + str(data.get('ai_provider', '') or ''))
print('ai_model=' + str(data.get('ai_model', '') or ''))
print('pool=' + sep.join(pool))
print('display=' + sep.join(display))
print('modes=' + sep.join(modes))
print('kinds=' + sep.join(kinds))
" 2>/dev/null
    )"

    local -a lines=()
    mapfile -t lines <<< "$parsed"
    if [[ "${lines[0]:-}" != "ok=1" ]]; then
        _agensic_bash_clear_suggestions
        return
    fi

    AGENSIC_FETCH_SUCCESS_COUNT=$((AGENSIC_FETCH_SUCCESS_COUNT + 1))
    AGENSIC_LAST_FETCH_USED_AI=$([[ "${lines[1]#used_ai=}" == "1" ]] && printf '1' || printf '0')
    AGENSIC_LAST_FETCH_AI_AGENT="${lines[2]#ai_agent=}"
    AGENSIC_LAST_FETCH_AI_PROVIDER="${lines[3]#ai_provider=}"
    AGENSIC_LAST_FETCH_AI_MODEL="${lines[4]#ai_model=}"

    local pool_line="${lines[5]#pool=}"
    local display_line="${lines[6]#display=}"
    local mode_line="${lines[7]#modes=}"
    local kind_line="${lines[8]#kinds=}"

    IFS="$sep" read -r -a AGENSIC_SUGGESTIONS <<< "$pool_line"
    IFS="$sep" read -r -a AGENSIC_DISPLAY_TEXTS <<< "$display_line"
    IFS="$sep" read -r -a AGENSIC_ACCEPT_MODES <<< "$mode_line"
    IFS="$sep" read -r -a AGENSIC_SUGGESTION_KINDS <<< "$kind_line"
    if (( ${#AGENSIC_SUGGESTIONS[@]} > 0 )); then
        AGENSIC_SUGGESTION_INDEX=1
    else
        AGENSIC_SUGGESTION_INDEX=0
    fi
    _agensic_bash_update_display
}

_agensic_bash_accept_current_suggestion() {
    if (( ${#AGENSIC_SUGGESTIONS[@]} == 0 || AGENSIC_SUGGESTION_INDEX <= 0 )); then
        return 1
    fi

    local idx=$((AGENSIC_SUGGESTION_INDEX - 1))
    local current="${AGENSIC_SUGGESTIONS[$idx]}"
    local mode="${AGENSIC_ACCEPT_MODES[$idx]:-suffix_append}"
    local buffer=""
    buffer="$(_agensic_bash_current_buffer)"

    if _agensic_bash_is_status_suggestion "$current"; then
        return 1
    fi

    if [[ "${AGENSIC_BASH_GHOST_ACTIVE:-0}" == "1" && "$mode" != "replace_full" ]]; then
        _ble_edit_ind=${#_ble_edit_str}
        AGENSIC_BASH_GHOST_ACTIVE=0
        AGENSIC_BASH_GHOST_SUFFIX=""
    elif [[ "$mode" == "replace_full" ]]; then
        _agensic_bash_set_buffer "$(_agensic_canonicalize_buffer_spacing "$current")"
    else
        local typed_since_fetch="${buffer#"$AGENSIC_LAST_BUFFER"}"
        local to_add="${current#"$typed_since_fetch"}"
        to_add="$(_agensic_merge_suffix "$buffer" "$to_add")"
        _agensic_bash_set_buffer "$(_agensic_canonicalize_buffer_spacing "${buffer}${to_add}")"
    fi

    _agensic_bash_clear_suggestions
    return 0
}

_agensic_bash_cycle_next() {
    local count=${#AGENSIC_SUGGESTIONS[@]}
    if (( count == 0 )); then
        if declare -F ble/widget/next-history >/dev/null 2>&1; then
            ble/widget/next-history
        fi
        return
    fi
    AGENSIC_SUGGESTION_INDEX=$(( AGENSIC_SUGGESTION_INDEX % count + 1 ))
    _agensic_bash_update_display
}

_agensic_bash_cycle_prev() {
    local count=${#AGENSIC_SUGGESTIONS[@]}
    if (( count == 0 )); then
        if declare -F ble/widget/previous-history >/dev/null 2>&1; then
            ble/widget/previous-history
        fi
        return
    fi
    AGENSIC_SUGGESTION_INDEX=$(( (AGENSIC_SUGGESTION_INDEX + count - 2) % count + 1 ))
    _agensic_bash_update_display
}

_agensic_bash_handle_enter() {
    local buffer=""
    _agensic_bash_strip_ghost_if_present
    buffer="$(_agensic_bash_current_buffer)"
    if [[ "$buffer" == '##'* ]]; then
        _agensic_bash_clear_suggestions
        _agensic_bash_resolve_general_assist "$buffer"
        return $?
    fi
    if [[ "$buffer" == '#'* ]]; then
        _agensic_bash_clear_suggestions
        _agensic_bash_resolve_intent_command "$buffer"
        return $?
    fi
    if [[ -n "${buffer//[[:space:]]/}" ]]; then
        _agensic_snapshot_pending_execution
    else
        _agensic_clear_pending_execution
    fi
    if declare -F ble/widget/accept-line >/dev/null 2>&1; then
        ble/widget/accept-line
    fi
}

_agensic_bash_after_self_insert() {
    local buffer=""
    _agensic_bash_strip_ghost_if_present
    buffer="$(_agensic_bash_current_buffer)"
    if (( ${#AGENSIC_SUGGESTIONS[@]} > 0 )); then
        _agensic_bash_filter_pool
    fi
    if [[ "$buffer" == *" " && ${#buffer} -ge 2 ]]; then
        AGENSIC_LAST_BUFFER="$buffer"
        _agensic_bash_fetch_suggestions 1 "space_auto"
    fi
}

_agensic_bash_after_delete() {
    _agensic_bash_strip_ghost_if_present
    _agensic_bash_clear_suggestions
}

_agensic_bash_resolve_intent_command() {
    local raw="$1"
    local body="${raw#\#}"
    body="${body#"${body%%[![:space:]]*}"}"
    if [[ -z "$body" ]]; then
        _agensic_bash_render_info "Add a terminal request after '#'."
        return 1
    fi

    local response=""
    local -a helper_cmd=()
    helper_cmd=(
        "$AGENSIC_RUNTIME_PYTHON" "$AGENSIC_CLIENT_HELPER"
        --op intent
        --format shell_lines_v1
        --timeout 3.0
        --intent-text "$body"
        --working-directory "$PWD"
        --shell bash
        --terminal "${TERM:-}"
        --platform "$(uname -s 2>/dev/null || printf 'unknown')"
    )
    _agensic_reload_auth_token_if_needed
    if [[ -n "$AGENSIC_AUTH_TOKEN" ]]; then
        helper_cmd+=("--auth-token=$AGENSIC_AUTH_TOKEN")
    fi
    response="$("${helper_cmd[@]}" 2>/dev/null)"

    local -a lines=()
    mapfile -t lines <<< "$response"
    if [[ "${lines[0]:-}" != "agensic_shell_lines_v1" || "${lines[1]:-}" != "intent" ]]; then
        _agensic_bash_render_info "Could not resolve command mode right now."
        return 1
    fi

    local status="${lines[4]:-error}"
    local primary="${lines[5]:-}"
    local explanation="${lines[6]:-Could not resolve command mode right now.}"
    if [[ "$status" != "ok" || -z "$primary" ]]; then
        _agensic_bash_render_info "$explanation"
        return 1
    fi

    _agensic_bash_set_buffer "$primary"
    AGENSIC_LAST_NL_INPUT="$raw"
    AGENSIC_LAST_NL_KIND="intent"
    AGENSIC_LAST_NL_COMMAND="$primary"
    printf '\nAgensic command mode (#)\nQuestion: %s\n\n%s\n' "$body" "$primary" >&2
    return 0
}

_agensic_bash_resolve_general_assist() {
    local raw="$1"
    local body="${raw#\#\#}"
    body="${body#"${body%%[![:space:]]*}"}"
    if [[ -z "$body" ]]; then
        _agensic_bash_render_info "Add a question after '##'."
        return 1
    fi

    local response=""
    local -a helper_cmd=()
    helper_cmd=(
        "$AGENSIC_RUNTIME_PYTHON" "$AGENSIC_CLIENT_HELPER"
        --op assist
        --format shell_lines_v1
        --timeout 4.0
        --prompt-text "$body"
        --working-directory "$PWD"
        --shell bash
        --terminal "${TERM:-}"
        --platform "$(uname -s 2>/dev/null || printf 'unknown')"
    )
    _agensic_reload_auth_token_if_needed
    if [[ -n "$AGENSIC_AUTH_TOKEN" ]]; then
        helper_cmd+=("--auth-token=$AGENSIC_AUTH_TOKEN")
    fi
    response="$("${helper_cmd[@]}" 2>/dev/null)"

    local -a lines=()
    mapfile -t lines <<< "$response"
    if [[ "${lines[0]:-}" != "agensic_shell_lines_v1" || "${lines[1]:-}" != "assist" ]]; then
        _agensic_bash_render_info "Could not fetch assistant reply right now."
        return 1
    fi

    local answer_count="${lines[4]:-0}"
    local answer=""
    if [[ "$answer_count" =~ ^[0-9]+$ ]] && (( answer_count > 0 )); then
        local i=0
        for (( i = 5; i < 5 + answer_count; i++ )); do
            answer+="${lines[$i]}"$'\n'
        done
        answer="${answer%$'\n'}"
    fi
    if [[ -z "$answer" ]]; then
        answer="Could not fetch assistant reply right now."
    fi

    AGENSIC_LAST_NL_INPUT="$raw"
    AGENSIC_LAST_NL_KIND="assist"
    AGENSIC_LAST_NL_ASSIST="$answer"
    printf '\nAgensic assistant (##)\n' >&2
    _agensic_bash_render_markdown_or_plain "$answer" >&2
    _agensic_bash_set_buffer ""
    return 0
}

_agensic_bash_build_log_command_json() {
    local command="$1"
    local exit_code="$2"
    local duration_ms="${3:-}"
    AGENSIC_LOG_COMMAND="$command" \
    AGENSIC_LOG_EXIT="$exit_code" \
    AGENSIC_LOG_DURATION_MS="$duration_ms" \
    AGENSIC_LOG_CWD="$PWD" \
    python3 - <<'PY' 2>/dev/null
import json
import os

def as_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

payload = {
    "command": str(os.environ.get("AGENSIC_LOG_COMMAND", "") or ""),
    "exit_code": as_int(os.environ.get("AGENSIC_LOG_EXIT", None), None),
    "duration_ms": as_int(os.environ.get("AGENSIC_LOG_DURATION_MS", None), None),
    "source": "runtime",
    "working_directory": str(os.environ.get("AGENSIC_LOG_CWD", "") or ""),
    "shell_pid": as_int(os.getpid(), None),
}
print(json.dumps(payload, separators=(",", ":")))
PY
}

_agensic_bash_log_command() {
    local command="$1"
    local exit_code="$2"
    local duration_ms="${3:-}"
    local json_data=""
    json_data="$(_agensic_bash_build_log_command_json "$command" "$exit_code" "$duration_ms")"
    if [[ -z "$json_data" ]]; then
        return
    fi
    _agensic_reload_auth_token_if_needed
    (
        local -a auth_headers=()
        if [[ -n "$AGENSIC_AUTH_TOKEN" ]]; then
            auth_headers=(-H "Authorization: Bearer $AGENSIC_AUTH_TOKEN" -H "X-Agensic-Auth: $AGENSIC_AUTH_TOKEN")
        fi
        curl -s -X POST "http://127.0.0.1:22000/log_command" \
            "${auth_headers[@]}" \
            -H "Content-Type: application/json" \
            -d "$json_data" >/dev/null 2>&1
    ) &
}

_agensic_bash_preexec_trap() {
    if [[ "${AGENSIC_BASH_IN_PROMPT_HOOK:-0}" == "1" ]]; then
        return 0
    fi
    if [[ "${AGENSIC_BASH_AT_PROMPT:-0}" != "1" ]]; then
        return 0
    fi
    local command="${BASH_COMMAND:-}"
    if [[ -z "$command" ]]; then
        return 0
    fi
    AGENSIC_BASH_AT_PROMPT=0
    AGENSIC_LAST_EXECUTED_CMD="$command"
    AGENSIC_LAST_EXECUTED_STARTED_AT_MS="$(_agensic_bash_now_epoch_ms)"
    return 0
}

_agensic_bash_precmd() {
    local exit_code="$?"
    AGENSIC_BASH_IN_PROMPT_HOOK=1
    if [[ -n "${AGENSIC_LAST_EXECUTED_CMD:-}" ]]; then
        local finished_at_ms=""
        local duration_ms=""
        finished_at_ms="$(_agensic_bash_now_epoch_ms)"
        if [[ "$AGENSIC_LAST_EXECUTED_STARTED_AT_MS" =~ ^[0-9]+$ && "$finished_at_ms" =~ ^[0-9]+$ ]]; then
            duration_ms=$(( finished_at_ms - AGENSIC_LAST_EXECUTED_STARTED_AT_MS ))
            if (( duration_ms < 0 )); then
                duration_ms=0
            fi
        fi
        _agensic_bash_log_command "$AGENSIC_LAST_EXECUTED_CMD" "$exit_code" "$duration_ms"
    fi
    AGENSIC_LAST_EXECUTED_CMD=""
    AGENSIC_LAST_EXECUTED_STARTED_AT_MS=0
    AGENSIC_BASH_AT_PROMPT=1
    AGENSIC_BASH_IN_PROMPT_HOOK=0
}

_agensic_find_ble_sh() {
    local candidate=""
    local -a candidates=()

    if [[ -n "$AGENSIC_BLE_SH_PATH" ]]; then
        candidates+=("$AGENSIC_BLE_SH_PATH")
    fi

    candidates+=(
        "${HOME}/.local/share/blesh/ble.sh"
        "${HOME}/.local/share/blesh/latest/ble.sh"
        "/usr/local/share/blesh/ble.sh"
        "/usr/share/blesh/ble.sh"
    )

    for candidate in "${candidates[@]}"; do
        if [[ -f "$candidate" ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done

    return 1
}

_agensic_emit_ble_missing_warning() {
    if ! _agensic_bash_is_interactive; then
        return
    fi
    if [[ "${AGENSIC_BASH_BLE_WARNING_EMITTED:-0}" == "1" ]]; then
        return
    fi
    AGENSIC_BASH_BLE_WARNING_EMITTED=1
    printf '%s\n' "Agensic bash support requires ble.sh. Install ble.sh and restart bash." >&2
}

_agensic_source_ble_if_needed() {
    if [[ -n "${BLE_VERSION:-}" ]]; then
        AGENSIC_BASH_BLE_AVAILABLE=1
        AGENSIC_BASH_BLE_LOADED_FROM="session"
        return 0
    fi

    local ble_path=""
    ble_path="$(_agensic_find_ble_sh)" || return 1
    if ! source -- "$ble_path" --attach=none >/dev/null 2>&1; then
        return 1
    fi
    if declare -F ble-attach >/dev/null 2>&1; then
        ble-attach >/dev/null 2>&1 || true
    fi
    if [[ -n "${BLE_VERSION:-}" ]]; then
        AGENSIC_BASH_BLE_AVAILABLE=1
        AGENSIC_BASH_BLE_LOADED_FROM="$ble_path"
        return 0
    fi
    return 1
}

function ble/widget/agensic/accept {
    if ! _agensic_bash_accept_current_suggestion; then
        if declare -F ble/widget/complete >/dev/null 2>&1; then
            ble/widget/complete
        fi
    fi
}

function ble/widget/agensic/manual-trigger {
    local buffer=""
    buffer="$(_agensic_bash_current_buffer)"
    if [[ ${#buffer} -lt 2 ]]; then
        return 0
    fi
    AGENSIC_LAST_BUFFER="$buffer"
    _agensic_bash_fetch_suggestions 1 "manual_ctrl_space"
}

function ble/widget/agensic/partial-accept {
    if (( ${#AGENSIC_SUGGESTIONS[@]} == 0 || AGENSIC_SUGGESTION_INDEX <= 0 )); then
        if declare -F ble/widget/forward-word >/dev/null 2>&1; then
            ble/widget/forward-word
        fi
        return 0
    fi

    local idx=$((AGENSIC_SUGGESTION_INDEX - 1))
    local current="${AGENSIC_SUGGESTIONS[$idx]}"
    local mode="${AGENSIC_ACCEPT_MODES[$idx]:-suffix_append}"
    local buffer=""
    buffer="$(_agensic_bash_current_buffer)"
    if _agensic_bash_is_status_suggestion "$current"; then
        return 0
    fi
    if [[ "$mode" == "replace_full" ]]; then
        _agensic_bash_set_buffer "$(_agensic_canonicalize_buffer_spacing "$current")"
        _agensic_bash_clear_suggestions
        return 0
    fi

    local typed_since_fetch="${buffer#"$AGENSIC_LAST_BUFFER"}"
    local remaining="${current#"$typed_since_fetch"}"
    remaining="$(_agensic_merge_suffix "$buffer" "$remaining")"
    local first_word="${remaining%% *}"
    if [[ "$first_word" == "$remaining" ]]; then
        _agensic_bash_set_buffer "$(_agensic_canonicalize_buffer_spacing "${buffer}${remaining}")"
    else
        _agensic_bash_set_buffer "$(_agensic_canonicalize_buffer_spacing "${buffer}${first_word} ")"
    fi
    _agensic_bash_clear_suggestions
}

function ble/widget/agensic/cycle-next {
    _agensic_bash_cycle_next
}

function ble/widget/agensic/cycle-prev {
    _agensic_bash_cycle_prev
}

function ble/widget/agensic/accept-line {
    _agensic_bash_handle_enter
}

_agensic_register_bash_widgets() {
    if [[ "${AGENSIC_BASH_WIDGETS_REGISTERED:-0}" == "1" ]]; then
        return 0
    fi
    if ! declare -F ble-bind >/dev/null 2>&1; then
        return 1
    fi

    ble-bind -f 'C-@' 'agensic/manual-trigger'
    ble-bind -f 'TAB' 'agensic/accept'
    ble-bind -f 'C-p' 'agensic/cycle-prev'
    ble-bind -f 'C-n' 'agensic/cycle-next'
    ble-bind -f 'M-f' 'agensic/partial-accept'
    ble-bind -f 'C-m' 'agensic/accept-line'
    ble-bind -f 'RET' 'agensic/accept-line'

    if declare -F ble/function#advice >/dev/null 2>&1; then
        ble/function#advice after ble/widget/default/self-insert '_agensic_bash_after_self_insert'
        ble/function#advice after ble/widget/default/backward-delete-char '_agensic_bash_after_delete'
    fi

    AGENSIC_BASH_WIDGETS_REGISTERED=1
    return 0
}

_agensic_register_bash_runtime_hooks() {
    if [[ "${AGENSIC_BASH_RUNTIME_HOOKS_REGISTERED:-0}" == "1" ]]; then
        return 0
    fi
    trap '_agensic_bash_preexec_trap' DEBUG
    if [[ -n "${PROMPT_COMMAND:-}" ]]; then
        PROMPT_COMMAND="_agensic_bash_precmd;${PROMPT_COMMAND}"
    else
        PROMPT_COMMAND="_agensic_bash_precmd"
    fi
    AGENSIC_BASH_RUNTIME_HOOKS_REGISTERED=1
    return 0
}

_agensic_initialize_bash_adapter() {
    if ! _agensic_bash_is_interactive; then
        return 0
    fi
    if _agensic_source_ble_if_needed; then
        AGENSIC_BASH_ADAPTER_READY=1
        _agensic_register_bash_widgets >/dev/null 2>&1 || true
        _agensic_register_bash_runtime_hooks >/dev/null 2>&1 || true
        _agensic_bash_log "ble_ready source=${AGENSIC_BASH_BLE_LOADED_FROM:-unknown}"
        return 0
    fi
    AGENSIC_BASH_ADAPTER_READY=0
    AGENSIC_BASH_BLE_AVAILABLE=0
    _agensic_bash_log "ble_missing"
    _agensic_emit_ble_missing_warning
    return 1
}

_agensic_initialize_bash_adapter >/dev/null 2>&1 || true
