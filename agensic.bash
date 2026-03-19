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
AGENSIC_CLIENT_HELPER="${AGENSIC_SOURCE_DIR}/shell_client.py"
AGENSIC_RUNTIME_PYTHON="${AGENSIC_RUNTIME_PYTHON:-}"
AGENSIC_BLE_SH_PATH="${AGENSIC_BLE_SH_PATH:-}"
AGENSIC_BASH_ADAPTER_READY=0
AGENSIC_BASH_BLE_AVAILABLE=0
AGENSIC_BASH_BLE_LOADED_FROM=""
AGENSIC_BASH_BLE_WARNING_EMITTED=0
AGENSIC_BASH_WIDGETS_REGISTERED=0
AGENSIC_STATUS_PREFIX="__AGENSIC_STATUS__:"
AGENSIC_FETCH_ATTEMPT_COUNT=0
AGENSIC_FETCH_SUCCESS_COUNT=0
AGENSIC_LAST_FETCH_ERROR_CODE=""
AGENSIC_LAST_FETCH_USED_AI=0
AGENSIC_LAST_FETCH_AI_AGENT=""
AGENSIC_LAST_FETCH_AI_PROVIDER=""
AGENSIC_LAST_FETCH_AI_MODEL=""
AGENSIC_AUTH_MTIME=""
AGENSIC_AUTH_TOKEN=""
AGENSIC_LAST_BUFFER=""
AGENSIC_SUGGESTION_INDEX=0
AGENSIC_SUGGESTIONS=()
AGENSIC_DISPLAY_TEXTS=()
AGENSIC_ACCEPT_MODES=()
AGENSIC_SUGGESTION_KINDS=()

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

_agensic_bash_is_status_suggestion() {
    [[ "${1:-}" == "$AGENSIC_STATUS_PREFIX"* ]]
}

_agensic_bash_clear_suggestions() {
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
        return
    fi

    local current="${AGENSIC_SUGGESTIONS[$((AGENSIC_SUGGESTION_INDEX - 1))]}"
    local display_text="${AGENSIC_DISPLAY_TEXTS[$((AGENSIC_SUGGESTION_INDEX - 1))]}"
    local mode="${AGENSIC_ACCEPT_MODES[$((AGENSIC_SUGGESTION_INDEX - 1))]}"
    local buffer=""
    local message=""

    if _agensic_bash_is_status_suggestion "$current"; then
        message="${current#${AGENSIC_STATUS_PREFIX}}"
        _agensic_bash_render_info "$message"
        return
    fi

    buffer="$(_agensic_bash_current_buffer)"
    if [[ "$mode" == "replace_full" ]]; then
        if [[ "$current" == "$buffer" ]]; then
            _agensic_bash_clear_info
            return
        fi
        message="$display_text"
    else
        local typed_since_fetch="${buffer#"$AGENSIC_LAST_BUFFER"}"
        local suffix="${current#"$typed_since_fetch"}"
        suffix="$(_agensic_merge_suffix "$buffer" "$suffix")"
        message="$suffix"
    fi

    if [[ -n "$message" ]]; then
        local count=${#AGENSIC_SUGGESTIONS[@]}
        if (( count > 1 )); then
            message="${message}  (${AGENSIC_SUGGESTION_INDEX}/${count}, Ctrl+P/N)"
        fi
        _agensic_bash_render_info "$message"
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

    if [[ "$mode" == "replace_full" ]]; then
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
    buffer="$(_agensic_bash_current_buffer)"
    if [[ "$buffer" == '##'* ]]; then
        printf '\nAgensic assistant (##)\n%s\n' "Assistant mode is not wired in bash yet." >&2
        _agensic_bash_set_buffer ""
        _agensic_bash_clear_suggestions
        return 0
    fi
    if [[ "$buffer" == '#'* ]]; then
        printf '\nAgensic command mode (#)\n%s\n' "Intent mode is not wired in bash yet." >&2
        _agensic_bash_clear_suggestions
        return 0
    fi
    if declare -F ble/widget/accept-line >/dev/null 2>&1; then
        ble/widget/accept-line
    fi
}

_agensic_bash_after_self_insert() {
    local buffer=""
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
    _agensic_bash_clear_suggestions
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

_agensic_initialize_bash_adapter() {
    if ! _agensic_bash_is_interactive; then
        return 0
    fi
    if _agensic_source_ble_if_needed; then
        AGENSIC_BASH_ADAPTER_READY=1
        _agensic_register_bash_widgets >/dev/null 2>&1 || true
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
