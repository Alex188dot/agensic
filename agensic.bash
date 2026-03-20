#!/usr/bin/env bash

# Agensic Bash adapter entrypoint.
# Bash uses a readline-only integration for inline autosuggestions.

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
AGENSIC_BASH_ADAPTER_READY=0
AGENSIC_BASH_READLINE_AVAILABLE=0
AGENSIC_BASH_BACKEND="none"
AGENSIC_BASH_WIDGETS_REGISTERED=0
AGENSIC_BASH_LAST_INFO_MESSAGE=""
AGENSIC_BASH_PROMPT_PREPARED=0
AGENSIC_STATUS_PREFIX="__AGENSIC_STATUS__:"
AGENSIC_BASH_LABEL_TEXT="[Agensic]"
AGENSIC_BASH_LABEL_COLOR=$'\033[32m'
AGENSIC_BASH_SUGGESTION_COLOR=$'\033[38;5;245m'
AGENSIC_BASH_RESET_COLOR=$'\033[0m'
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
AGENSIC_PATH_HEAVY_EXECUTABLES=(cd ls cat less more head tail vi vim nvim nano code source open cp mv mkdir rmdir touch find grep rg sed awk bat)
AGENSIC_SCRIPT_EXECUTABLES=(python python3 python3.11 python3.12 node bash sh zsh ruby perl php lua)
AGENSIC_BASH_AT_PROMPT=1
AGENSIC_BASH_IN_PROMPT_HOOK=0
AGENSIC_BASH_ORIGINAL_PS1="${PS1-}"
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

_agensic_bash_last_history_entry() {
    local last=""
    last="$(builtin fc -ln -1 2>/dev/null)" || return 1
    last="${last#"${last%%[![:space:]]*}"}"
    last="${last%"${last##*[![:space:]]}"}"
    if [[ -z "$last" ]]; then
        return 1
    fi
    printf '%s\n' "$last"
}

_agensic_bash_should_ignore_debug_command() {
    local command="${1:-}"
    case "$command" in
        _agensic_*|__vte_prompt_command|history*|fc*|curl*|disown*|local\ *|return\ *|'[['*|']]'*)
            return 0
            ;;
    esac
    return 1
}

_agensic_bash_extract_executable_token() {
    local command="${1:-}"
    local -a tokens=()
    local token=""
    local i=0
    local lower=""

    read -r -a tokens <<< "$command"
    while (( i < ${#tokens[@]} )); do
        token="${tokens[$i]}"
        if [[ -z "$token" ]]; then
            ((i++))
            continue
        fi
        case "$token" in
            sudo|command)
                ((i++))
                continue
                ;;
            env|/usr/bin/env)
                ((i++))
                while (( i < ${#tokens[@]} )); do
                    token="${tokens[$i]}"
                    if [[ -z "$token" || "$token" == -* || "$token" == *=* ]]; then
                        ((i++))
                        continue
                    fi
                    break
                done
                continue
                ;;
            -*|*=*)
                ((i++))
                continue
                ;;
            *)
                lower="${token##*/}"
                printf '%s\n' "${lower,,}"
                return 0
                ;;
        esac
    done

    return 1
}

_agensic_bash_should_preserve_native_tab() {
    local buffer="$1"
    local exe=""
    local -a tokens=()
    local token=""

    exe="$(_agensic_bash_extract_executable_token "$buffer")" || return 1
    if [[ -z "$exe" ]]; then
        return 1
    fi

    if _agensic_value_in_array "$exe" "${AGENSIC_PATH_HEAVY_EXECUTABLES[@]}"; then
        return 0
    fi

    read -r -a tokens <<< "$buffer"
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
    printf '%s\n' "${READLINE_LINE:-}"
}

_agensic_bash_current_cursor() {
    printf '%s\n' "${READLINE_POINT:-0}"
}

_agensic_bash_set_buffer() {
    local value="$1"
    READLINE_LINE="$value"
    READLINE_POINT=${#READLINE_LINE}
}

_agensic_bash_prepare_prompt() {
    if [[ "${AGENSIC_BASH_PROMPT_PREPARED:-0}" == "1" ]]; then
        return 0
    fi
    if [[ "${PS1-}" != $'\n'* ]]; then
        PS1=$'\n'"${PS1-}"
    fi
    AGENSIC_BASH_PROMPT_PREPARED=1
}

_agensic_bash_overlay_supported() {
    [[ "${AGENSIC_BASH_READLINE_AVAILABLE:-0}" == "1" && "${AGENSIC_BASH_PROMPT_PREPARED:-0}" == "1" && -t 2 && "${TERM:-}" != "dumb" ]]
}

_agensic_bash_print_readline_message() {
    local message="$1"
    local hint="${2:-}"
    local rendered=""
    if [[ "${AGENSIC_BASH_READLINE_AVAILABLE:-0}" != "1" ]]; then
        return 0
    fi
    message="${message//$'\r'/ }"
    message="${message//$'\n'/ }"
    if [[ "$message" == "${AGENSIC_BASH_LAST_INFO_MESSAGE:-}" ]]; then
        return 0
    fi
    AGENSIC_BASH_LAST_INFO_MESSAGE="$message"
    if ! _agensic_bash_overlay_supported; then
        return 0
    fi
    rendered="$(_agensic_bash_render_overlay_message "$message" "$hint")"
    printf '\0337\r\033[1A\033[2K' >&2
    if [[ -n "$message" ]]; then
        printf '%s' "$rendered" >&2
    fi
    printf '\0338' >&2
}

_agensic_bash_terminal_columns() {
    local cols="${COLUMNS:-}"
    if [[ "$cols" =~ ^[0-9]+$ ]] && (( cols > 0 )); then
        printf '%s\n' "$cols"
        return
    fi
    cols="$(tput cols 2>/dev/null)"
    if [[ "$cols" =~ ^[0-9]+$ ]] && (( cols > 0 )); then
        printf '%s\n' "$cols"
        return
    fi
    printf '%s\n' "80"
}

_agensic_bash_crop_plain_text() {
    local text="$1"
    local max_cols="$2"
    local ellipsis="..."
    if [[ ! "$max_cols" =~ ^[0-9]+$ ]] || (( max_cols <= 0 )); then
        printf '%s\n' ""
        return
    fi
    if (( ${#text} <= max_cols )); then
        printf '%s\n' "$text"
        return
    fi
    if (( max_cols <= ${#ellipsis} )); then
        printf '%.*s\n' "$max_cols" "$ellipsis"
        return
    fi
    printf '%s%s\n' "${text:0:$((max_cols - ${#ellipsis}))}" "$ellipsis"
}

_agensic_bash_render_overlay_message() {
    local message="$1"
    local hint="$2"
    local cols=0
    local plain_prefix="${AGENSIC_BASH_LABEL_TEXT}"
    local hint_segment=""
    local prefix=""
    local message_cols=0
    cols="$(_agensic_bash_terminal_columns)"
    if [[ -n "$hint" ]]; then
        hint_segment=" ${hint}"
    fi
    prefix="${plain_prefix}${hint_segment} "
    message_cols=$(( cols - ${#prefix} ))
    if (( message_cols <= 0 )); then
        message=""
    else
        message="$(_agensic_bash_crop_plain_text "$message" "$message_cols")"
    fi
    printf '%s%s%s%s %s%s%s\n' \
        "$AGENSIC_BASH_LABEL_COLOR" \
        "$AGENSIC_BASH_LABEL_TEXT" \
        "$AGENSIC_BASH_RESET_COLOR" \
        "$hint_segment" \
        "$AGENSIC_BASH_SUGGESTION_COLOR" \
        "$message" \
        "$AGENSIC_BASH_RESET_COLOR"
}

_agensic_bash_render_info() {
    local message="$1"
    local hint="${2:-}"
    _agensic_bash_print_readline_message "$message" "$hint"
}

_agensic_bash_clear_info() {
    _agensic_bash_print_readline_message ""
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
    AGENSIC_SUGGESTIONS=()
    AGENSIC_DISPLAY_TEXTS=()
    AGENSIC_ACCEPT_MODES=()
    AGENSIC_SUGGESTION_KINDS=()
    AGENSIC_SUGGESTION_INDEX=0
    _agensic_bash_clear_info
}

_agensic_bash_has_visible_suggestion() {
    if (( ${#AGENSIC_SUGGESTIONS[@]} == 0 || AGENSIC_SUGGESTION_INDEX <= 0 )); then
        return 1
    fi

    local idx=$((AGENSIC_SUGGESTION_INDEX - 1))
    local current="${AGENSIC_SUGGESTIONS[$idx]}"
    local display_text="${AGENSIC_DISPLAY_TEXTS[$idx]}"
    local mode="${AGENSIC_ACCEPT_MODES[$idx]:-suffix_append}"
    local buffer=""
    local visible_len=0

    buffer="$(_agensic_bash_current_buffer)"
    visible_len="$(_agensic_bash_candidate_visible_length "$current" "$mode" "$buffer" "$display_text")"
    (( visible_len > 0 ))
}

_agensic_bash_candidate_visible_length() {
    local suggestion="$1"
    local mode="$2"
    local buffer="$3"
    local display_text="$4"
    local visible=""

    if _agensic_bash_is_status_suggestion "$suggestion"; then
        printf '%s\n' "1"
        return
    fi
    if [[ "$mode" == "replace_full" ]]; then
        if [[ "$suggestion" == "$buffer" ]]; then
            printf '%s\n' "0"
        else
            printf '%s\n' "${#display_text}"
        fi
        return
    fi

    local typed_since_fetch="${buffer#"$AGENSIC_LAST_BUFFER"}"
    visible="${suggestion#"$typed_since_fetch"}"
    visible="$(_agensic_merge_suffix "$buffer" "$visible")"
    printf '%s\n' "${#visible}"
}

_agensic_bash_select_best_suggestion_index() {
    local previous_index="${1:-0}"
    local buffer=""
    local count=0
    local best_index=0
    local best_length=-1
    local i=0

    count=${#AGENSIC_SUGGESTIONS[@]}
    if (( count == 0 )); then
        AGENSIC_SUGGESTION_INDEX=0
        return
    fi

    buffer="$(_agensic_bash_current_buffer)"
    if (( previous_index > 0 && previous_index <= count )); then
        local prev_len=0
        prev_len="$(_agensic_bash_candidate_visible_length \
            "${AGENSIC_SUGGESTIONS[$((previous_index - 1))]}" \
            "${AGENSIC_ACCEPT_MODES[$((previous_index - 1))]}" \
            "$buffer" \
            "${AGENSIC_DISPLAY_TEXTS[$((previous_index - 1))]}")"
        if (( prev_len > 0 )); then
            AGENSIC_SUGGESTION_INDEX=$previous_index
            return
        fi
    fi

    for (( i = 0; i < count; i++ )); do
        local visible_len=0
        visible_len="$(_agensic_bash_candidate_visible_length \
            "${AGENSIC_SUGGESTIONS[$i]}" \
            "${AGENSIC_ACCEPT_MODES[$i]}" \
            "$buffer" \
            "${AGENSIC_DISPLAY_TEXTS[$i]}")"
        if (( visible_len > best_length )); then
            best_length=$visible_len
            best_index=$((i + 1))
        fi
    done

    if (( best_length <= 0 )); then
        AGENSIC_SUGGESTION_INDEX=0
    else
        AGENSIC_SUGGESTION_INDEX=$best_index
    fi
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
    local inline_suffix=""
    local count=0
    local hint=""

    if _agensic_bash_is_status_suggestion "$current"; then
        message="${current#${AGENSIC_STATUS_PREFIX}}"
        _agensic_bash_render_info "$message"
        return
    fi

    buffer="$(_agensic_bash_current_buffer)"
    if _agensic_bash_should_preserve_native_tab "$buffer"; then
        _agensic_bash_clear_info
        return
    fi
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

    if [[ -z "$inline_suffix" ]]; then
        _agensic_bash_clear_info
        return
    fi

    count=${#AGENSIC_SUGGESTIONS[@]}
    if [[ "$mode" == "replace_full" ]]; then
        message="$display_text"
    else
        message="${buffer}${inline_suffix}"
    fi
    if (( count > 1 )); then
        hint="(${AGENSIC_SUGGESTION_INDEX}/${count}, Ctrl+P/N)"
    fi
    _agensic_bash_render_info "$message" "$hint"
}

_agensic_bash_fetch_error_message() {
    case "${1:-}" in
        daemon_unreachable) printf '%s\n' "Agensic daemon is not running. Run: agensic start" ;;
        auth_failed) printf '%s\n' "Agensic auth failed. Run: agensic setup" ;;
        predict_timeout) printf '%s\n' "Agensic timed out while fetching suggestions." ;;
        predict_http_error) printf '%s\n' "Agensic suggestion request failed." ;;
        *) printf '%s\n' "" ;;
    esac
}

_agensic_bash_filter_pool() {
    local buffer=""
    local previous_index="${AGENSIC_SUGGESTION_INDEX:-0}"
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
    _agensic_bash_select_best_suggestion_index "$previous_index"
    _agensic_bash_update_display
}

_agensic_bash_fetch_suggestions() {
    local allow_ai="${1:-1}"
    local trigger_source="${2:-manual}"
    local preserve_existing="${3:-0}"
    local buffer=""
    local cursor=""
    local request_json=""
    local response_json=""
    local parsed=""
    local sep=$'\x1f'
    local previous_index="${AGENSIC_SUGGESTION_INDEX:-0}"
    local -a old_suggestions=("${AGENSIC_SUGGESTIONS[@]}")
    local -a old_display_texts=("${AGENSIC_DISPLAY_TEXTS[@]}")
    local -a old_accept_modes=("${AGENSIC_ACCEPT_MODES[@]}")
    local -a old_suggestion_kinds=("${AGENSIC_SUGGESTION_KINDS[@]}")

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
    if [[ "$trigger_source" != manual_* ]] && _agensic_bash_should_preserve_native_tab "$buffer"; then
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
    print('error_code=bad_response_json')
    raise SystemExit(0)

if not bool(data.get('ok', False)):
    print('ok=0')
    print('error_code=' + str(data.get('error_code', '') or ''))
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
        if (( preserve_existing == 1 && ${#old_suggestions[@]} > 0 )); then
            AGENSIC_SUGGESTIONS=("${old_suggestions[@]}")
            AGENSIC_DISPLAY_TEXTS=("${old_display_texts[@]}")
            AGENSIC_ACCEPT_MODES=("${old_accept_modes[@]}")
            AGENSIC_SUGGESTION_KINDS=("${old_suggestion_kinds[@]}")
            _agensic_bash_select_best_suggestion_index "$previous_index"
            _agensic_bash_update_display
        else
            _agensic_bash_clear_suggestions
        fi
        AGENSIC_LAST_FETCH_ERROR_CODE="${lines[1]#error_code=}"
        local fetch_error_message=""
        fetch_error_message="$(_agensic_bash_fetch_error_message "$AGENSIC_LAST_FETCH_ERROR_CODE")"
        if [[ -n "$fetch_error_message" && ${#AGENSIC_SUGGESTIONS[@]} == 0 ]]; then
            _agensic_bash_render_info "$fetch_error_message"
        fi
        return
    fi

    AGENSIC_LAST_FETCH_ERROR_CODE=""
    AGENSIC_FETCH_SUCCESS_COUNT=$((AGENSIC_FETCH_SUCCESS_COUNT + 1))
    AGENSIC_LAST_FETCH_USED_AI=$([[ "${lines[2]#used_ai=}" == "1" ]] && printf '1' || printf '0')
    AGENSIC_LAST_FETCH_AI_AGENT="${lines[3]#ai_agent=}"
    AGENSIC_LAST_FETCH_AI_PROVIDER="${lines[4]#ai_provider=}"
    AGENSIC_LAST_FETCH_AI_MODEL="${lines[5]#ai_model=}"

    local pool_line="${lines[6]#pool=}"
    local display_line="${lines[7]#display=}"
    local mode_line="${lines[8]#modes=}"
    local kind_line="${lines[9]#kinds=}"

    IFS="$sep" read -r -a AGENSIC_SUGGESTIONS <<< "$pool_line"
    IFS="$sep" read -r -a AGENSIC_DISPLAY_TEXTS <<< "$display_line"
    IFS="$sep" read -r -a AGENSIC_ACCEPT_MODES <<< "$mode_line"
    IFS="$sep" read -r -a AGENSIC_SUGGESTION_KINDS <<< "$kind_line"
    if (( ${#AGENSIC_SUGGESTIONS[@]} > 0 )); then
        _agensic_bash_select_best_suggestion_index "$previous_index"
    else
        if (( preserve_existing == 1 && ${#old_suggestions[@]} > 0 )); then
            AGENSIC_SUGGESTIONS=("${old_suggestions[@]}")
            AGENSIC_DISPLAY_TEXTS=("${old_display_texts[@]}")
            AGENSIC_ACCEPT_MODES=("${old_accept_modes[@]}")
            AGENSIC_SUGGESTION_KINDS=("${old_suggestion_kinds[@]}")
            _agensic_bash_select_best_suggestion_index "$previous_index"
        else
            AGENSIC_SUGGESTION_INDEX=0
        fi
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

_agensic_bash_partial_accept_current_suggestion() {
    if (( ${#AGENSIC_SUGGESTIONS[@]} == 0 || AGENSIC_SUGGESTION_INDEX <= 0 )); then
        return 1
    fi

    local idx=$((AGENSIC_SUGGESTION_INDEX - 1))
    local current="${AGENSIC_SUGGESTIONS[$idx]}"
    local mode="${AGENSIC_ACCEPT_MODES[$idx]:-suffix_append}"
    local buffer=""
    local typed_since_fetch=""
    local remaining=""
    local first_word=""

    buffer="$(_agensic_bash_current_buffer)"
    if _agensic_bash_is_status_suggestion "$current"; then
        return 1
    fi
    if [[ "$mode" == "replace_full" ]]; then
        _agensic_bash_set_buffer "$(_agensic_canonicalize_buffer_spacing "$current")"
        _agensic_bash_clear_suggestions
        return 0
    fi

    typed_since_fetch="${buffer#"$AGENSIC_LAST_BUFFER"}"
    remaining="${current#"$typed_since_fetch"}"
    remaining="$(_agensic_merge_suffix "$buffer" "$remaining")"
    first_word="${remaining%% *}"
    if [[ "$first_word" == "$remaining" ]]; then
        _agensic_bash_set_buffer "$(_agensic_canonicalize_buffer_spacing "${buffer}${remaining}")"
    else
        _agensic_bash_set_buffer "$(_agensic_canonicalize_buffer_spacing "${buffer}${first_word} ")"
    fi
    _agensic_bash_clear_suggestions
    return 0
}

_agensic_bash_cycle_next() {
    local count=${#AGENSIC_SUGGESTIONS[@]}
    if (( count == 0 )); then
        return 0
    fi
    AGENSIC_SUGGESTION_INDEX=$(( AGENSIC_SUGGESTION_INDEX % count + 1 ))
    _agensic_bash_update_display
}

_agensic_bash_cycle_prev() {
    local count=${#AGENSIC_SUGGESTIONS[@]}
    if (( count == 0 )); then
        return 0
    fi
    AGENSIC_SUGGESTION_INDEX=$(( (AGENSIC_SUGGESTION_INDEX + count - 2) % count + 1 ))
    _agensic_bash_update_display
}

_agensic_bash_handle_enter() {
    local buffer=""
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
    return 0
}

_agensic_bash_after_self_insert() {
    local buffer=""
    buffer="$(_agensic_bash_current_buffer)"
    if [[ "$buffer" == '#'* ]]; then
        _agensic_bash_clear_suggestions
        return 0
    fi
    if (( ${#AGENSIC_SUGGESTIONS[@]} > 0 )); then
        _agensic_bash_filter_pool
    fi
    if _agensic_bash_should_preserve_native_tab "$buffer"; then
        _agensic_bash_clear_suggestions
        return 0
    fi
    if [[ ${#buffer} -lt 2 ]]; then
        return 0
    fi
    if [[ "$buffer" == *" " ]]; then
        AGENSIC_LAST_BUFFER="$buffer"
        _agensic_bash_fetch_suggestions 1 "space_auto" 1
        return 0
    fi
    AGENSIC_LAST_BUFFER="$buffer"
    _agensic_bash_fetch_suggestions 1 "typing_auto" 1
}

_agensic_bash_after_delete() {
    _agensic_bash_clear_suggestions
    return 0
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
    disown "$!" >/dev/null 2>&1 || true
}

_agensic_bash_preexec_trap() {
    if [[ "${AGENSIC_BASH_IN_PROMPT_HOOK:-0}" == "1" ]]; then
        return 0
    fi
    if [[ "${AGENSIC_BASH_AT_PROMPT:-0}" != "1" ]]; then
        return 0
    fi
    local command=""
    command="$(_agensic_bash_last_history_entry)"
    if [[ -z "$command" ]]; then
        command="${BASH_COMMAND:-}"
    fi
    if [[ -z "$command" ]]; then
        return 0
    fi
    if _agensic_bash_should_ignore_debug_command "$command"; then
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
    _agensic_bash_clear_suggestions
    AGENSIC_LAST_EXECUTED_CMD=""
    AGENSIC_LAST_EXECUTED_STARTED_AT_MS=0
    AGENSIC_BASH_AT_PROMPT=1
    AGENSIC_BASH_IN_PROMPT_HOOK=0
}

_agensic_readline_insert_text() {
    local text="$1"
    local point="${READLINE_POINT:-0}"
    local left="${READLINE_LINE:0:$point}"
    local right="${READLINE_LINE:$point}"
    READLINE_LINE="${left}${text}${right}"
    READLINE_POINT=$(( point + ${#text} ))
}

_agensic_readline_self_insert_char() {
    local text="${1:-}"
    _agensic_readline_insert_text "$text"
    _agensic_bash_after_self_insert
}

_agensic_readline_manual_trigger() {
    local buffer="${READLINE_LINE:-}"
    if [[ ${#buffer} -lt 2 ]]; then
        _agensic_bash_clear_suggestions
        return 0
    fi
    AGENSIC_LAST_BUFFER="$buffer"
    _agensic_bash_fetch_suggestions 1 "manual_ctrl_space"
}

_agensic_readline_accept() {
    local buffer="${READLINE_LINE:-}"
    if (( ${#AGENSIC_SUGGESTIONS[@]} == 0 )); then
        if [[ ${#buffer} -ge 2 ]]; then
            AGENSIC_LAST_BUFFER="$buffer"
            _agensic_bash_fetch_suggestions 1 "manual_tab"
        fi
    fi
    if ! _agensic_bash_accept_current_suggestion; then
        printf '\a' >&2
    fi
}

_agensic_readline_cycle_next() {
    local buffer="${READLINE_LINE:-}"
    if (( ${#AGENSIC_SUGGESTIONS[@]} == 0 )) && [[ ${#buffer} -ge 2 ]]; then
        AGENSIC_LAST_BUFFER="$buffer"
        _agensic_bash_fetch_suggestions 1 "manual_ctrl_n"
        return 0
    fi
    _agensic_bash_cycle_next
}

_agensic_readline_cycle_prev() {
    local buffer="${READLINE_LINE:-}"
    if (( ${#AGENSIC_SUGGESTIONS[@]} == 0 )) && [[ ${#buffer} -ge 2 ]]; then
        AGENSIC_LAST_BUFFER="$buffer"
        _agensic_bash_fetch_suggestions 1 "manual_ctrl_p"
        return 0
    fi
    _agensic_bash_cycle_prev
}

_agensic_readline_partial_accept() {
    if ! _agensic_bash_partial_accept_current_suggestion; then
        printf '\a' >&2
    fi
}

_agensic_readline_insert_space() {
    _agensic_readline_insert_text " "
    if [[ ${#READLINE_LINE} -lt 2 ]]; then
        _agensic_bash_clear_suggestions
        return 0
    fi
    AGENSIC_LAST_BUFFER="$READLINE_LINE"
    _agensic_bash_fetch_suggestions 1 "space_auto"
}

_agensic_readline_delete_backward_char() {
    local point="${READLINE_POINT:-0}"
    if (( point <= 0 )); then
        _agensic_bash_clear_suggestions
        return 0
    fi
    local left="${READLINE_LINE:0:$((point - 1))}"
    local right="${READLINE_LINE:$point}"
    READLINE_LINE="${left}${right}"
    READLINE_POINT=$((point - 1))
    _agensic_bash_after_delete
}

_agensic_readline_delete_char() {
    local point="${READLINE_POINT:-0}"
    local length=${#READLINE_LINE}
    if (( point >= length )); then
        _agensic_bash_clear_suggestions
        return 0
    fi
    local left="${READLINE_LINE:0:$point}"
    local right="${READLINE_LINE:$((point + 1))}"
    READLINE_LINE="${left}${right}"
    _agensic_bash_after_delete
}

_agensic_readline_escape() {
    if _agensic_bash_has_visible_suggestion; then
        _agensic_bash_clear_suggestions
    fi
    return 0
}

_agensic_register_readline_command() {
    local keyseq="$1"
    local command="$2"

    bind -m emacs-standard -x "\"${keyseq}\":${command}" >/dev/null 2>&1 || return 1
    bind -m vi-insert -x "\"${keyseq}\":${command}" >/dev/null 2>&1 || return 1
}

_agensic_register_readline_emacs_command() {
    local keyseq="$1"
    local command="$2"

    bind -m emacs-standard -x "\"${keyseq}\":${command}" >/dev/null 2>&1 || return 1
}

_agensic_bash_keyseq_from_bytes() {
    local raw="$1"
    local hex=""
    local keyseq=""
    local chunk=""

    if [[ -z "$raw" ]]; then
        return 1
    fi

    while IFS= read -r chunk; do
        for hex in $chunk; do
            [[ -n "$hex" ]] || continue
            keyseq+="\\x${hex}"
        done
    done < <(LC_ALL=C printf '%s' "$raw" | od -An -tx1 -v 2>/dev/null)

    if [[ -z "$keyseq" ]]; then
        return 1
    fi
    printf '%s\n' "$keyseq"
}

_agensic_register_readline_raw_command() {
    local raw="$1"
    local command="$2"
    local keyseq=""

    keyseq="$(_agensic_bash_keyseq_from_bytes "$raw")" || return 1
    _agensic_register_readline_command "$keyseq" "$command"
}

_agensic_register_readline_raw_emacs_command() {
    local raw="$1"
    local command="$2"
    local keyseq=""

    keyseq="$(_agensic_bash_keyseq_from_bytes "$raw")" || return 1
    _agensic_register_readline_emacs_command "$keyseq" "$command"
}

_agensic_register_readline_terminfo_command() {
    local capability="$1"
    local command="$2"
    local raw=""

    raw="$(tput "$capability" 2>/dev/null || true)"
    [[ -n "$raw" ]] || return 1
    _agensic_register_readline_raw_command "$raw" "$command"
}

_agensic_register_readline_terminfo_emacs_command() {
    local capability="$1"
    local command="$2"
    local raw=""

    raw="$(tput "$capability" 2>/dev/null || true)"
    [[ -n "$raw" ]] || return 1
    _agensic_register_readline_raw_emacs_command "$raw" "$command"
}

_agensic_register_readline_common_delete_bindings() {
    local keyseq=""
    local -a backward_delete_sequences=(
        '\x7f'
        '\177'
        '\C-?'
        '\C-h'
    )
    local -a forward_delete_sequences=(
        '\e[3~'
        '\e[3;2~'
        '\e[3;3~'
        '\e[3;4~'
        '\e[3;5~'
        '\e[3;6~'
        '\e[3;7~'
        '\e[3;8~'
        '\e[3$~'
        '\e[3^~'
        '\e[3@~'
    )

    for keyseq in "${backward_delete_sequences[@]}"; do
        _agensic_register_readline_command "$keyseq" "_agensic_readline_delete_backward_char" || true
    done
    for keyseq in "${forward_delete_sequences[@]}"; do
        _agensic_register_readline_command "$keyseq" "_agensic_readline_delete_char" || true
    done
}

_agensic_register_readline_self_insert_bindings() {
    local code=0
    local char=""
    local keyseq=""
    local shell_char=""

    for code in {33..126}; do
        printf -v char "\\$(printf '%03o' "$code")"
        printf -v keyseq '\\x%02x' "$code"
        printf -v shell_char '%q' "$char"
        _agensic_register_readline_command "$keyseq" "_agensic_readline_self_insert_char ${shell_char}" || return 1
    done

    _agensic_register_readline_command " " "_agensic_readline_insert_space" || return 1
    return 0
}

_agensic_register_readline_widgets() {
    if [[ "${AGENSIC_BASH_WIDGETS_REGISTERED:-0}" == "1" ]]; then
        return 0
    fi

    _agensic_register_readline_self_insert_bindings || return 1
    _agensic_register_readline_command '\C-@' "_agensic_readline_manual_trigger" || return 1
    _agensic_register_readline_command '\C-n' "_agensic_readline_cycle_next" || return 1
    _agensic_register_readline_command '\C-p' "_agensic_readline_cycle_prev" || return 1
    _agensic_register_readline_command '\ef' "_agensic_readline_partial_accept" || return 1
    _agensic_register_readline_terminfo_command kbs "_agensic_readline_delete_backward_char" || true
    _agensic_register_readline_terminfo_command kdch1 "_agensic_readline_delete_char" || true
    _agensic_register_readline_terminfo_command kDC "_agensic_readline_delete_char" || true
    _agensic_register_readline_common_delete_bindings
    _agensic_register_readline_emacs_command '\e' "_agensic_readline_escape" || true
    _agensic_register_readline_command '\t' "_agensic_readline_accept" || return 1

    AGENSIC_BASH_READLINE_AVAILABLE=1
    AGENSIC_BASH_BACKEND="readline"
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
    _agensic_bash_prepare_prompt >/dev/null 2>&1 || true
    if _agensic_register_readline_widgets >/dev/null 2>&1; then
        AGENSIC_BASH_ADAPTER_READY=1
        _agensic_register_bash_runtime_hooks >/dev/null 2>&1 || true
        _agensic_bash_log "readline_ready"
        return 0
    fi
    AGENSIC_BASH_ADAPTER_READY=0
    AGENSIC_BASH_BACKEND="none"
    _agensic_bash_log "readline_unavailable"
    return 1
}

_agensic_initialize_bash_adapter >/dev/null 2>&1 || true
