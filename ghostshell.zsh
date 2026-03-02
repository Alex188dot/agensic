# GhostShell Zsh Plugin - Space-triggered LLM fallback model

typeset -g -a GHOSTSHELL_SUGGESTIONS
GHOSTSHELL_SUGGESTIONS=()
typeset -g -a GHOSTSHELL_DISPLAY_TEXTS
GHOSTSHELL_DISPLAY_TEXTS=()
typeset -g -a GHOSTSHELL_ACCEPT_MODES
GHOSTSHELL_ACCEPT_MODES=()
typeset -g -a GHOSTSHELL_SUGGESTION_KINDS
GHOSTSHELL_SUGGESTION_KINDS=()
typeset -g GHOSTSHELL_SUGGESTION_INDEX=1
typeset -g GHOSTSHELL_STATUS_PREFIX="__GHOSTSHELL_STATUS__:"
typeset -g GHOSTSHELL_MAX_LLM_CALLS_PER_LINE=4
typeset -g GHOSTSHELL_LLM_BUDGET_UNLIMITED=0
typeset -g GHOSTSHELL_LLM_BUDGET_REACHED_HINT="LLM budget reached for this command line"
typeset -g GHOSTSHELL_LINE_LLM_CALLS_USED=0
typeset -g GHOSTSHELL_LINE_HAS_SPACE=0
typeset -g GHOSTSHELL_SHOW_CTRL_SPACE_HINT=0
typeset -g GHOSTSHELL_LAST_FETCH_USED_AI=0
typeset -g GHOSTSHELL_LAST_NL_INPUT=""
typeset -g GHOSTSHELL_LAST_NL_KIND=""
typeset -g GHOSTSHELL_LAST_NL_COMMAND=""
typeset -g GHOSTSHELL_LAST_NL_EXPLANATION=""
typeset -g GHOSTSHELL_LAST_NL_ALTERNATIVES=""
typeset -g GHOSTSHELL_LAST_NL_ASSIST=""
typeset -g GHOSTSHELL_LAST_NL_QUESTION=""
typeset -g GHOSTSHELL_LAST_NL_AI_AGENT=""
typeset -g GHOSTSHELL_LAST_NL_AI_PROVIDER=""
typeset -g GHOSTSHELL_LAST_NL_AI_MODEL=""
typeset -g -a GHOSTSHELL_INTENT_OPTIONS
GHOSTSHELL_INTENT_OPTIONS=()
typeset -g GHOSTSHELL_INTENT_OPTION_INDEX=1
typeset -g GHOSTSHELL_INTENT_ACTIVE=0

# Timer for pause detection
typeset -g GHOSTSHELL_TIMER_PID=""
typeset -g GHOSTSHELL_LAST_BUFFER=""
typeset -g GHOSTSHELL_LAST_EXECUTED_CMD=""
typeset -g GHOSTSHELL_LINE_LAST_ACTION=""
typeset -g GHOSTSHELL_LINE_ACCEPTED_ORIGIN=""
typeset -g GHOSTSHELL_LINE_ACCEPTED_MODE=""
typeset -g GHOSTSHELL_LINE_ACCEPTED_KIND=""
typeset -g GHOSTSHELL_LINE_MANUAL_EDIT_AFTER_ACCEPT=0
typeset -g GHOSTSHELL_LINE_ACCEPTED_AI_AGENT=""
typeset -g GHOSTSHELL_LINE_ACCEPTED_AI_PROVIDER=""
typeset -g GHOSTSHELL_LINE_ACCEPTED_AI_MODEL=""
typeset -g GHOSTSHELL_LAST_FETCH_AI_AGENT=""
typeset -g GHOSTSHELL_LAST_FETCH_AI_PROVIDER=""
typeset -g GHOSTSHELL_LAST_FETCH_AI_MODEL=""
typeset -g GHOSTSHELL_PENDING_LAST_ACTION=""
typeset -g GHOSTSHELL_PENDING_ACCEPTED_ORIGIN=""
typeset -g GHOSTSHELL_PENDING_ACCEPTED_MODE=""
typeset -g GHOSTSHELL_PENDING_ACCEPTED_KIND=""
typeset -g GHOSTSHELL_PENDING_MANUAL_EDIT_AFTER_ACCEPT=0
typeset -g GHOSTSHELL_PENDING_AI_AGENT=""
typeset -g GHOSTSHELL_PENDING_AI_PROVIDER=""
typeset -g GHOSTSHELL_PENDING_AI_MODEL=""
typeset -g GHOSTSHELL_PENDING_AGENT_NAME=""
typeset -g GHOSTSHELL_PENDING_PROOF_LABEL=""
typeset -g GHOSTSHELL_PENDING_PROOF_AGENT=""
typeset -g GHOSTSHELL_PENDING_PROOF_MODEL=""
typeset -g GHOSTSHELL_PENDING_PROOF_TRACE=""
typeset -g GHOSTSHELL_PENDING_PROOF_TIMESTAMP=0
typeset -g GHOSTSHELL_PENDING_PROOF_SIGNATURE=""
typeset -g GHOSTSHELL_PENDING_PROOF_SIGNER_SCOPE=""
typeset -g GHOSTSHELL_PENDING_PROOF_KEY_FINGERPRINT=""
typeset -g GHOSTSHELL_PENDING_PROOF_HOST_FINGERPRINT=""
typeset -g GHOSTSHELL_NEXT_PROOF_LABEL=""
typeset -g GHOSTSHELL_NEXT_PROOF_AGENT=""
typeset -g GHOSTSHELL_NEXT_PROOF_MODEL=""
typeset -g GHOSTSHELL_NEXT_PROOF_TRACE=""
typeset -g GHOSTSHELL_NEXT_PROOF_TIMESTAMP=0
typeset -g GHOSTSHELL_NEXT_PROOF_SIGNATURE=""
typeset -g GHOSTSHELL_NEXT_PROOF_SIGNER_SCOPE=""
typeset -g GHOSTSHELL_NEXT_PROOF_KEY_FINGERPRINT=""
typeset -g GHOSTSHELL_NEXT_PROOF_HOST_FINGERPRINT=""
typeset -g GHOSTSHELL_AI_SESSION_COUNTER=0
typeset -g GHOSTSHELL_AI_SESSION_TIMER_PID=""
typeset -g GHOSTSHELL_HOOKS_REGISTERED=0
typeset -gA GHOSTSHELL_NATIVE_ESC_WIDGET
GHOSTSHELL_NATIVE_ESC_WIDGET=()
typeset -g -a GHOSTSHELL_DISABLED_PATTERNS
GHOSTSHELL_DISABLED_PATTERNS=()
typeset -g GHOSTSHELL_SOURCE_PATH="${(%):-%N}"
if [[ -z "$GHOSTSHELL_SOURCE_PATH" || "$GHOSTSHELL_SOURCE_PATH" == "zsh" ]]; then
    GHOSTSHELL_SOURCE_PATH="${HOME}/.ghostshell/ghostshell.zsh"
fi
typeset -g GHOSTSHELL_SOURCE_DIR="${GHOSTSHELL_SOURCE_PATH:A:h}"
typeset -g GHOSTSHELL_HOME="${HOME}/.ghostshell"
typeset -g GHOSTSHELL_CONFIG_PATH="${HOME}/.ghostshell/config.json"
typeset -g GHOSTSHELL_AUTH_PATH="${HOME}/.ghostshell/auth.json"
typeset -g GHOSTSHELL_CLIENT_HELPER="${GHOSTSHELL_SOURCE_DIR}/shell_client.py"
typeset -g GHOSTSHELL_PLUGIN_LOG="${GHOSTSHELL_HOME}/plugin.log"
typeset -g GHOSTSHELL_CONFIG_MTIME=""
typeset -g GHOSTSHELL_AUTH_MTIME=""
typeset -g GHOSTSHELL_AUTH_TOKEN=""
typeset -g GHOSTSHELL_FETCH_ATTEMPT_COUNT=0
typeset -g GHOSTSHELL_FETCH_SUCCESS_COUNT=0
typeset -g GHOSTSHELL_LAST_FETCH_ERROR_CODE=""
typeset -g GHOSTSHELL_FETCH_LOG_THROTTLE_SECONDS=10
typeset -g GHOSTSHELL_FETCH_LOG_LAST_KEY=""
typeset -g GHOSTSHELL_FETCH_LOG_LAST_TS=0
typeset -g -a GHOSTSHELL_PATH_HEAVY_EXECUTABLES
GHOSTSHELL_PATH_HEAVY_EXECUTABLES=(cd ls cat less more head tail vi vim nvim nano code source open cp mv mkdir rmdir touch find grep rg sed awk bat)
typeset -g -a GHOSTSHELL_SCRIPT_EXECUTABLES
GHOSTSHELL_SCRIPT_EXECUTABLES=(python python3 python3.11 python3.12 node bash sh zsh ruby perl php lua)

_ghostshell_value_in_array() {
    local needle="$1"
    shift
    local item
    for item in "$@"; do
        if [[ "$needle" == "$item" ]]; then
            return 0
        fi
    done
    return 1
}

_ghostshell_extract_executable_token() {
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

_ghostshell_normalize_pattern_token() {
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

_ghostshell_get_config_mtime() {
    if [[ ! -f "$GHOSTSHELL_CONFIG_PATH" ]]; then
        print -r -- ""
        return
    fi
    local mtime
    mtime="$(stat -f '%m' "$GHOSTSHELL_CONFIG_PATH" 2>/dev/null)"
    print -r -- "$mtime"
}

_ghostshell_get_auth_mtime() {
    if [[ ! -f "$GHOSTSHELL_AUTH_PATH" ]]; then
        print -r -- ""
        return
    fi
    local mtime
    mtime="$(stat -f '%m' "$GHOSTSHELL_AUTH_PATH" 2>/dev/null)"
    print -r -- "$mtime"
}

_ghostshell_reload_auth_token_if_needed() {
    local current_mtime
    current_mtime="$(_ghostshell_get_auth_mtime)"
    if [[ "$current_mtime" == "$GHOSTSHELL_AUTH_MTIME" ]]; then
        return
    fi
    GHOSTSHELL_AUTH_MTIME="$current_mtime"
    GHOSTSHELL_AUTH_TOKEN=""
    if [[ -z "$current_mtime" ]]; then
        return
    fi

    local escaped_path="${GHOSTSHELL_AUTH_PATH//\'/\'\\\'\'}"
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
    GHOSTSHELL_AUTH_TOKEN="$token"
}

_ghostshell_reload_disabled_patterns_if_needed() {
    local current_mtime
    current_mtime="$(_ghostshell_get_config_mtime)"
    if [[ "$current_mtime" == "$GHOSTSHELL_CONFIG_MTIME" ]]; then
        return
    fi
    GHOSTSHELL_CONFIG_MTIME="$current_mtime"
    GHOSTSHELL_DISABLED_PATTERNS=()
    GHOSTSHELL_MAX_LLM_CALLS_PER_LINE=4
    GHOSTSHELL_LLM_BUDGET_UNLIMITED=0

    if [[ -z "$current_mtime" ]]; then
        return
    fi

    local escaped_path="${GHOSTSHELL_CONFIG_PATH//\'/\'\\\'\'}"
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

print(str(budget))
print('1' if unlimited else '0')
print('\x1f'.join(patterns))
" 2>/dev/null)

    if [[ -n "$response" ]]; then
        local -a response_lines
        response_lines=("${(@f)response}")
        if [[ "${response_lines[1]}" == <-> ]]; then
            GHOSTSHELL_MAX_LLM_CALLS_PER_LINE="${response_lines[1]}"
        fi
        if [[ "${response_lines[2]}" == "1" ]]; then
            GHOSTSHELL_LLM_BUDGET_UNLIMITED=1
        fi
        local patterns_line="${response_lines[3]}"
        if [[ -n "$patterns_line" ]]; then
            GHOSTSHELL_DISABLED_PATTERNS=("${(ps:$sep:)patterns_line}")
        fi
    fi
}

_ghostshell_matches_disabled_pattern() {
    local command="$1"
    local exe=""
    local pattern

    _ghostshell_reload_disabled_patterns_if_needed
    if (( ${#GHOSTSHELL_DISABLED_PATTERNS[@]} == 0 )); then
        return 1
    fi

    exe="$(_ghostshell_extract_executable_token "$command")"
    if [[ -z "$exe" ]]; then
        return 1
    fi

    for pattern in "${GHOSTSHELL_DISABLED_PATTERNS[@]}"; do
        if [[ "$exe" == "$pattern"* || "$pattern" == "$exe"* ]]; then
            return 0
        fi
    done
    return 1
}

_ghostshell_token_looks_path_or_file() {
    local token="$1"
    if [[ -z "$token" ]]; then
        return 1
    fi
    if [[ "$token" == "~"* || "$token" == "./"* || "$token" == "../"* || "$token" == *"/"* ]]; then
        return 0
    fi
    # Treat dotted tokens as likely file names (e.g. foo.txt) without
    # relying on regex escaping that can overmatch plain words in zsh.
    if [[ "$token" == *.* && "$token" != "." && "$token" != ".." ]]; then
        return 0
    fi
    return 1
}

_ghostshell_should_preserve_native_tab() {
    local buffer="$BUFFER"
    local exe=""
    local -a tokens
    local token=""

    exe="$(_ghostshell_extract_executable_token "$buffer")"
    if [[ -z "$exe" ]]; then
        return 1
    fi

    if _ghostshell_value_in_array "$exe" "${GHOSTSHELL_PATH_HEAVY_EXECUTABLES[@]}"; then
        return 0
    fi

    tokens=(${(z)buffer})
    for token in "${tokens[@]}"; do
        if _ghostshell_token_looks_path_or_file "$token"; then
            return 0
        fi
    done

    if _ghostshell_value_in_array "$exe" "${GHOSTSHELL_SCRIPT_EXECUTABLES[@]}"; then
        if [[ "$buffer" == *[[:space:]] || ${#tokens[@]} -ge 2 ]]; then
            return 0
        fi
    fi

    return 1
}

_ghostshell_should_skip_ghostshell_for_buffer() {
    _ghostshell_matches_disabled_pattern "$BUFFER"
}

# ======================================================
# 1. CORE LOGIC (Fetch, Display, Feedback)
# ======================================================

_ghostshell_now_epoch() {
    local now
    now="$(date +%s 2>/dev/null)"
    if [[ -z "$now" ]]; then
        now="0"
    fi
    print -r -- "$now"
}

_ghostshell_log_fetch_error() {
    local error_code="$1"
    local trigger_source="$2"
    local buffer_len="$3"
    if [[ -z "$error_code" ]]; then
        return
    fi

    local key="${error_code}|${trigger_source}"
    local now="$(_ghostshell_now_epoch)"
    local throttle="${GHOSTSHELL_FETCH_LOG_THROTTLE_SECONDS:-10}"

    if [[ "$key" == "$GHOSTSHELL_FETCH_LOG_LAST_KEY" ]]; then
        if [[ "$now" == <-> && "$GHOSTSHELL_FETCH_LOG_LAST_TS" == <-> ]]; then
            local delta=$(( now - GHOSTSHELL_FETCH_LOG_LAST_TS ))
            if (( delta >= 0 && delta < throttle )); then
                return
            fi
        fi
    fi

    GHOSTSHELL_FETCH_LOG_LAST_KEY="$key"
    GHOSTSHELL_FETCH_LOG_LAST_TS="$now"

    mkdir -p "$GHOSTSHELL_HOME" 2>/dev/null
    {
        printf "%s error=%s trigger=%s buffer_len=%s\n" \
            "$(date '+%Y-%m-%d %H:%M:%S' 2>/dev/null)" \
            "$error_code" \
            "$trigger_source" \
            "$buffer_len"
    } >> "$GHOSTSHELL_PLUGIN_LOG" 2>/dev/null
}

_ghostshell_fetch_suggestions() {
    local allow_ai="${1:-1}"
    local trigger_source="${2:-unknown}"
    local buffer_content="$BUFFER"
    local sep=$'\x1f'
    GHOSTSHELL_LAST_FETCH_USED_AI=0
    GHOSTSHELL_LAST_FETCH_AI_AGENT=""
    GHOSTSHELL_LAST_FETCH_AI_PROVIDER=""
    GHOSTSHELL_LAST_FETCH_AI_MODEL=""
    GHOSTSHELL_FETCH_ATTEMPT_COUNT=$((GHOSTSHELL_FETCH_ATTEMPT_COUNT + 1))
    
    # Don't fetch if buffer is too short
    if [[ ${#buffer_content} -lt 2 ]]; then
        GHOSTSHELL_SUGGESTIONS=()
        GHOSTSHELL_DISPLAY_TEXTS=()
        GHOSTSHELL_ACCEPT_MODES=()
        GHOSTSHELL_SUGGESTION_KINDS=()
        return
    fi

    local request_json
    request_json="$(
        GHOSTSHELL_REQ_BUFFER="$buffer_content" \
        GHOSTSHELL_REQ_CURSOR="$CURSOR" \
        GHOSTSHELL_REQ_CWD="$PWD" \
        GHOSTSHELL_REQ_ALLOW_AI="$allow_ai" \
        GHOSTSHELL_REQ_TRIGGER_SOURCE="$trigger_source" \
        python3 -c "
import json, os
payload = {
    'command_buffer': os.environ.get('GHOSTSHELL_REQ_BUFFER', ''),
    'cursor_position': int(os.environ.get('GHOSTSHELL_REQ_CURSOR', '0') or '0'),
    'working_directory': os.environ.get('GHOSTSHELL_REQ_CWD', ''),
    'shell': 'zsh',
    'allow_ai': bool(int(os.environ.get('GHOSTSHELL_REQ_ALLOW_AI', '1') or '1')),
    'trigger_source': os.environ.get('GHOSTSHELL_REQ_TRIGGER_SOURCE', 'unknown'),
}
print(json.dumps(payload, separators=(',', ':')))
" 2>/dev/null
    )"

    if [[ -z "$request_json" ]]; then
        GHOSTSHELL_LAST_FETCH_ERROR_CODE="payload_build_error"
        _ghostshell_log_fetch_error "$GHOSTSHELL_LAST_FETCH_ERROR_CODE" "$trigger_source" "${#buffer_content}"
        GHOSTSHELL_SUGGESTIONS=()
        GHOSTSHELL_DISPLAY_TEXTS=()
        GHOSTSHELL_ACCEPT_MODES=()
        GHOSTSHELL_SUGGESTION_KINDS=()
        GHOSTSHELL_SUGGESTION_INDEX=1
        return
    fi

    if [[ ! -f "$GHOSTSHELL_CLIENT_HELPER" ]]; then
        GHOSTSHELL_LAST_FETCH_ERROR_CODE="helper_missing"
        _ghostshell_log_fetch_error "$GHOSTSHELL_LAST_FETCH_ERROR_CODE" "$trigger_source" "${#buffer_content}"
        GHOSTSHELL_SUGGESTIONS=()
        GHOSTSHELL_DISPLAY_TEXTS=()
        GHOSTSHELL_ACCEPT_MODES=()
        GHOSTSHELL_SUGGESTION_KINDS=()
        GHOSTSHELL_SUGGESTION_INDEX=1
        return
    fi

    _ghostshell_reload_auth_token_if_needed
    local response_json
    local -a helper_cmd
    helper_cmd=(python3 "$GHOSTSHELL_CLIENT_HELPER" --timeout 3.0)
    if [[ -n "$GHOSTSHELL_AUTH_TOKEN" ]]; then
        helper_cmd+=(--auth-token "$GHOSTSHELL_AUTH_TOKEN")
    fi
    response_json="$(printf '%s' "$request_json" | "${helper_cmd[@]}" 2>/dev/null)"

    local parsed
    parsed="$(
        GHOSTSHELL_CLIENT_RESPONSE="$response_json" \
        python3 -c "
import json, os
sep = '\x1f'
raw = os.environ.get('GHOSTSHELL_CLIENT_RESPONSE', '')
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
        GHOSTSHELL_LAST_FETCH_ERROR_CODE="${error_code:-client_fetch_failed}"
        _ghostshell_log_fetch_error "$GHOSTSHELL_LAST_FETCH_ERROR_CODE" "$trigger_source" "${#buffer_content}"
        GHOSTSHELL_SUGGESTIONS=()
        GHOSTSHELL_DISPLAY_TEXTS=()
        GHOSTSHELL_ACCEPT_MODES=()
        GHOSTSHELL_SUGGESTION_KINDS=()
        GHOSTSHELL_SUGGESTION_INDEX=1
        return
    fi

    GHOSTSHELL_FETCH_SUCCESS_COUNT=$((GHOSTSHELL_FETCH_SUCCESS_COUNT + 1))
    GHOSTSHELL_LAST_FETCH_ERROR_CODE=""
    GHOSTSHELL_LAST_FETCH_AI_AGENT="$ai_agent_line"
    GHOSTSHELL_LAST_FETCH_AI_PROVIDER="$ai_provider_line"
    GHOSTSHELL_LAST_FETCH_AI_MODEL="$ai_model_line"

    if [[ "$used_ai_line" == "1" ]]; then
        GHOSTSHELL_LAST_FETCH_USED_AI=1
    fi

    if [[ -n "$pool_line" ]]; then
        GHOSTSHELL_SUGGESTIONS=("${(ps:$sep:)pool_line}")
        if (( ${#GHOSTSHELL_SUGGESTIONS[@]} > 20 )); then
            GHOSTSHELL_SUGGESTIONS=("${GHOSTSHELL_SUGGESTIONS[@][1,20]}")
        fi
        if [[ -n "$display_line" ]]; then
            GHOSTSHELL_DISPLAY_TEXTS=("${(ps:$sep:)display_line}")
        else
            GHOSTSHELL_DISPLAY_TEXTS=("${GHOSTSHELL_SUGGESTIONS[@]}")
        fi
        if [[ -n "$mode_line" ]]; then
            GHOSTSHELL_ACCEPT_MODES=("${(ps:$sep:)mode_line}")
        else
            GHOSTSHELL_ACCEPT_MODES=()
        fi
        if [[ -n "$kind_line" ]]; then
            GHOSTSHELL_SUGGESTION_KINDS=("${(ps:$sep:)kind_line}")
        else
            GHOSTSHELL_SUGGESTION_KINDS=()
        fi
        local i
        for (( i=1; i<=${#GHOSTSHELL_SUGGESTIONS[@]}; i++ )); do
            [[ -z "${GHOSTSHELL_DISPLAY_TEXTS[$i]}" ]] && GHOSTSHELL_DISPLAY_TEXTS[$i]="${GHOSTSHELL_SUGGESTIONS[$i]}"
            [[ -z "${GHOSTSHELL_ACCEPT_MODES[$i]}" ]] && GHOSTSHELL_ACCEPT_MODES[$i]="suffix_append"
            [[ -z "${GHOSTSHELL_SUGGESTION_KINDS[$i]}" ]] && GHOSTSHELL_SUGGESTION_KINDS[$i]="normal"
        done
    else
        GHOSTSHELL_SUGGESTIONS=()
        GHOSTSHELL_DISPLAY_TEXTS=()
        GHOSTSHELL_ACCEPT_MODES=()
        GHOSTSHELL_SUGGESTION_KINDS=()
    fi
    
    GHOSTSHELL_SUGGESTION_INDEX=1
}

_ghostshell_is_status_suggestion() {
    local value="$1"
    [[ "$value" == "$GHOSTSHELL_STATUS_PREFIX"* ]]
}

_ghostshell_set_status_message() {
    local message="$1"
    if [[ -z "$message" ]]; then
        GHOSTSHELL_SUGGESTIONS=()
        GHOSTSHELL_DISPLAY_TEXTS=()
        GHOSTSHELL_ACCEPT_MODES=()
        GHOSTSHELL_SUGGESTION_KINDS=()
        GHOSTSHELL_SUGGESTION_INDEX=1
        return
    fi
    GHOSTSHELL_SUGGESTIONS=("${GHOSTSHELL_STATUS_PREFIX}${message}")
    GHOSTSHELL_DISPLAY_TEXTS=("${GHOSTSHELL_STATUS_PREFIX}${message}")
    GHOSTSHELL_ACCEPT_MODES=("suffix_append")
    GHOSTSHELL_SUGGESTION_KINDS=("status")
    GHOSTSHELL_SUGGESTION_INDEX=1
}

_ghostshell_is_double_hash_assist() {
    [[ "$BUFFER" == '##'* ]]
}

_ghostshell_is_single_hash_intent() {
    [[ "$BUFFER" == '#'* && "$BUFFER" != '##'* ]]
}

_ghostshell_buffer_has_hash() {
    [[ "$BUFFER" == *"#"* ]]
}

_ghostshell_print_intent_preview() {
    local question="$1"
    local command="$2"
    local explanation="$3"
    local alternatives="$4"
    local copy_block="$5"
    local green=$'\033[32m'
    local light_blue=$'\033[94m'
    local reset=$'\033[0m'

    zle -I
    print -r -- ""
    print -r -- "GhostShell command mode (#)"
    print -r -- "Question: $question"
    print -r -- ""
    print -r -- "Copy-ready command:"
    print -r -- '```bash'
    print -r -- "${green}${copy_block}${reset}"
    print -r -- '```'
    print -r -- ""
    if [[ -n "$explanation" ]]; then
        print -r -- "$explanation"
    fi
    if [[ -n "$alternatives" ]]; then
        local -a alt_items
        alt_items=("${(@s:|||:)alternatives}")
        if [[ ${#alt_items[@]} -gt 0 ]]; then
            print -r -- ""
            print -r -- "Alternatives:"
            local alt
            for alt in "${alt_items[@]}"; do
                [[ -n "$alt" ]] && print -r -- "${light_blue}- $alt${reset}"
            done
        fi
    fi
}

_ghostshell_print_intent_refusal() {
    local question="$1"
    local explanation="$2"
    zle -I
    print -r -- ""
    print -r -- "GhostShell command mode (#)"
    print -r -- "Question: $question"
    print -r -- ""
    print -r -- "${explanation:-I can only help with terminal commands. Use '##' for general questions.}"
}

_ghostshell_reset_intent_state() {
    GHOSTSHELL_INTENT_ACTIVE=0
    GHOSTSHELL_INTENT_OPTIONS=()
    GHOSTSHELL_INTENT_OPTION_INDEX=1
}

_ghostshell_activate_intent_options() {
    local primary="$1"
    local alternatives="$2"
    _ghostshell_reset_intent_state
    if [[ -n "$primary" ]]; then
        GHOSTSHELL_INTENT_OPTIONS+=("$primary")
    fi
    if [[ -n "$alternatives" ]]; then
        local -a alt_items
        alt_items=("${(@s:|||:)alternatives}")
        local alt
        for alt in "${alt_items[@]}"; do
            if [[ -n "$alt" ]]; then
                GHOSTSHELL_INTENT_OPTIONS+=("$alt")
            fi
        done
    fi
    if [[ ${#GHOSTSHELL_INTENT_OPTIONS[@]} -gt 0 ]]; then
        GHOSTSHELL_INTENT_ACTIVE=1
        GHOSTSHELL_INTENT_OPTION_INDEX=1
    fi
}

_ghostshell_update_intent_hint() {
    if (( GHOSTSHELL_INTENT_ACTIVE == 1 )); then
        local count=${#GHOSTSHELL_INTENT_OPTIONS[@]}
        if (( count > 1 )); then
            POSTDISPLAY="  (Option $GHOSTSHELL_INTENT_OPTION_INDEX/$count, Ctrl+P/N)"
            region_highlight=("${#BUFFER} $((${#BUFFER} + ${#POSTDISPLAY})) fg=242")
            return
        fi
    fi
    POSTDISPLAY=""
    region_highlight=()
}

_ghostshell_print_assist_reply() {
    local answer="$1"
    zle -I
    print -r -- ""
    print -r -- "GhostShell assistant (##)"
    print -r -- "$answer"
}

_ghostshell_resolve_intent_command() {
    local raw="$1"
    local body="${raw#\#}"
    while [[ "$body" == [[:space:]]* ]]; do
        body="${body# }"
    done

    if [[ -z "$body" ]]; then
        _ghostshell_print_intent_refusal "" "Add a terminal request after '#'."
        _ghostshell_reset_intent_state
        zle -R
        return 1
    fi

    if [[ "$GHOSTSHELL_LAST_NL_KIND" == "intent" && "$GHOSTSHELL_LAST_NL_INPUT" == "$raw" && -n "$GHOSTSHELL_LAST_NL_COMMAND" ]]; then
        BUFFER="$GHOSTSHELL_LAST_NL_COMMAND"
        CURSOR=${#BUFFER}
        _ghostshell_set_suggestion_accept_state "ai" "replace_full" "intent_command" "$GHOSTSHELL_LAST_NL_AI_AGENT" "$GHOSTSHELL_LAST_NL_AI_PROVIDER" "$GHOSTSHELL_LAST_NL_AI_MODEL"
        _ghostshell_activate_intent_options "$GHOSTSHELL_LAST_NL_COMMAND" "$GHOSTSHELL_LAST_NL_ALTERNATIVES"
        _ghostshell_print_intent_preview "$GHOSTSHELL_LAST_NL_QUESTION" "$GHOSTSHELL_LAST_NL_COMMAND" "$GHOSTSHELL_LAST_NL_EXPLANATION" "$GHOSTSHELL_LAST_NL_ALTERNATIVES" "$GHOSTSHELL_LAST_NL_COMMAND"
        _ghostshell_update_intent_hint
        return 0
    fi

    local escaped_body="${body//\'/\'\\\'\'}"
    local escaped_pwd="${PWD//\'/\'\\\'\'}"
    local escaped_term="${TERM//\'/\'\\\'\'}"
    local platform_name="$(uname -s 2>/dev/null || echo unknown)"
    local escaped_platform="${platform_name//\'/\'\\\'\'}"
    _ghostshell_reload_auth_token_if_needed
    local response
    response=$(
        GHOSTSHELL_AUTH_TOKEN="$GHOSTSHELL_AUTH_TOKEN" \
        python3 -c "
import urllib.request, json, shlex
import os

def safe_line(v):
    return str(v or '').replace('\\r', ' ').replace('\\n', ' ').strip()

payload = {
    'intent_text': '''$escaped_body''',
    'working_directory': '''$escaped_pwd''',
    'shell': 'zsh',
    'terminal': '''$escaped_term''',
    'platform': '''$escaped_platform''',
}
try:
    token = str(os.environ.get('GHOSTSHELL_AUTH_TOKEN', '') or '').strip()
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
        headers['X-GhostShell-Auth'] = token
    req = urllib.request.Request('http://127.0.0.1:22000/intent', data=json.dumps(payload).encode('utf-8'), headers=headers)
    with urllib.request.urlopen(req, timeout=3.0) as r:
        result = json.load(r)
    status = safe_line(result.get('status', 'error'))
    primary = safe_line(result.get('primary_command', ''))
    explanation = safe_line(result.get('explanation', ''))
    alternatives = result.get('alternatives', [])
    if not isinstance(alternatives, list):
        alternatives = []
    alternatives = [safe_line(item) for item in alternatives if safe_line(item)]
    alternatives_blob = '|||'.join(alternatives[:2])
    copy_block = safe_line(result.get('copy_block', primary))
    ai_agent = safe_line(result.get('ai_agent', ''))
    ai_provider = safe_line(result.get('ai_provider', ''))
    ai_model = safe_line(result.get('ai_model', ''))
except Exception:
    status = 'error'
    primary = ''
    explanation = 'Could not resolve command mode right now.'
    alternatives_blob = ''
    copy_block = ''
    ai_agent = ''
    ai_provider = ''
    ai_model = ''
print('status=' + shlex.quote(status))
print('primary=' + shlex.quote(primary))
print('explanation=' + shlex.quote(explanation))
print('alternatives=' + shlex.quote(alternatives_blob))
print('copy_block=' + shlex.quote(copy_block))
print('ai_agent=' + shlex.quote(ai_agent))
print('ai_provider=' + shlex.quote(ai_provider))
print('ai_model=' + shlex.quote(ai_model))
" 2>/dev/null
    )

    local nl_status=""
    local nl_primary=""
    local nl_explanation=""
    local nl_alternatives=""
    local nl_copy_block=""
    local nl_ai_agent=""
    local nl_ai_provider=""
    local nl_ai_model=""
    response="${response//status=/nl_status=}"
    response="${response//primary=/nl_primary=}"
    response="${response//explanation=/nl_explanation=}"
    response="${response//alternatives=/nl_alternatives=}"
    response="${response//copy_block=/nl_copy_block=}"
    response="${response//ai_agent=/nl_ai_agent=}"
    response="${response//ai_provider=/nl_ai_provider=}"
    response="${response//ai_model=/nl_ai_model=}"
    eval "$response"

    if [[ "$nl_status" != "ok" || -z "$nl_primary" ]]; then
        _ghostshell_print_intent_refusal "$body" "${nl_explanation:-No command generated.}"
        _ghostshell_reset_intent_state
        zle -R
        return 1
    fi

    BUFFER="$nl_primary"
    CURSOR=${#BUFFER}
    _ghostshell_set_suggestion_accept_state "ai" "replace_full" "intent_command" "$nl_ai_agent" "$nl_ai_provider" "$nl_ai_model"
    GHOSTSHELL_LAST_NL_INPUT="$raw"
    GHOSTSHELL_LAST_NL_KIND="intent"
    GHOSTSHELL_LAST_NL_QUESTION="$body"
    GHOSTSHELL_LAST_NL_COMMAND="$nl_primary"
    GHOSTSHELL_LAST_NL_EXPLANATION="$nl_explanation"
    GHOSTSHELL_LAST_NL_ALTERNATIVES="$nl_alternatives"
    GHOSTSHELL_LAST_NL_AI_AGENT="$nl_ai_agent"
    GHOSTSHELL_LAST_NL_AI_PROVIDER="$nl_ai_provider"
    GHOSTSHELL_LAST_NL_AI_MODEL="$nl_ai_model"
    _ghostshell_activate_intent_options "$nl_primary" "$nl_alternatives"
    _ghostshell_print_intent_preview "$body" "$nl_primary" "$nl_explanation" "$nl_alternatives" "${nl_copy_block:-$nl_primary}"
    _ghostshell_update_intent_hint
    return 0
}

_ghostshell_resolve_general_assist() {
    local raw="$1"
    local body="${raw#\#\#}"
    while [[ "$body" == [[:space:]]* ]]; do
        body="${body# }"
    done

    if [[ -z "$body" ]]; then
        _ghostshell_set_status_message "Add a question after '##'."
        _ghostshell_update_display
        zle -R
        return 1
    fi

    if [[ "$GHOSTSHELL_LAST_NL_KIND" == "assist" && "$GHOSTSHELL_LAST_NL_INPUT" == "$raw" && -n "$GHOSTSHELL_LAST_NL_ASSIST" ]]; then
        _ghostshell_print_assist_reply "$GHOSTSHELL_LAST_NL_ASSIST"
        BUFFER=""
        CURSOR=0
        return 0
    fi

    local escaped_body="${body//\'/\'\\\'\'}"
    local escaped_pwd="${PWD//\'/\'\\\'\'}"
    local escaped_term="${TERM//\'/\'\\\'\'}"
    local platform_name="$(uname -s 2>/dev/null || echo unknown)"
    local escaped_platform="${platform_name//\'/\'\\\'\'}"
    _ghostshell_reload_auth_token_if_needed
    local answer
    answer=$(
        GHOSTSHELL_AUTH_TOKEN="$GHOSTSHELL_AUTH_TOKEN" \
        python3 -c "
import urllib.request, json
import os
payload = {
    'prompt_text': '''$escaped_body''',
    'working_directory': '''$escaped_pwd''',
    'shell': 'zsh',
    'terminal': '''$escaped_term''',
    'platform': '''$escaped_platform''',
}
try:
    token = str(os.environ.get('GHOSTSHELL_AUTH_TOKEN', '') or '').strip()
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
        headers['X-GhostShell-Auth'] = token
    req = urllib.request.Request('http://127.0.0.1:22000/assist', data=json.dumps(payload).encode('utf-8'), headers=headers)
    with urllib.request.urlopen(req, timeout=4.0) as r:
        result = json.load(r)
    answer = str(result.get('answer', '') or '').replace('\\r', ' ').strip()
except Exception:
    answer = 'Could not fetch assistant reply right now.'
print(answer)
" 2>/dev/null
    )

    if [[ -z "$answer" ]]; then
        answer="No response."
    fi

    GHOSTSHELL_LAST_NL_INPUT="$raw"
    GHOSTSHELL_LAST_NL_KIND="assist"
    GHOSTSHELL_LAST_NL_ASSIST="$answer"
    _ghostshell_print_assist_reply "$answer"
    BUFFER=""
    CURSOR=0
    return 0
}

_ghostshell_filter_pool() {
    # Filter the suggestion pool based on current buffer (typed since last fetch)
    local buffer="$BUFFER"
    
    # If buffer is shorter than last fetch, we can't reliably filter the suffixes
    if [[ ${#buffer} -lt ${#GHOSTSHELL_LAST_BUFFER} ]]; then
        GHOSTSHELL_SUGGESTIONS=()
        return
    fi
    
    local typed_since_fetch="${buffer#$GHOSTSHELL_LAST_BUFFER}"
    
    # Filter suggestions that still match what the user typed
    local -a new_suggestions=()
    local -a new_displays=()
    local -a new_modes=()
    local -a new_kinds=()
    local i
    for (( i=1; i<=${#GHOSTSHELL_SUGGESTIONS[@]}; i++ )); do
        local sugg="${GHOSTSHELL_SUGGESTIONS[$i]}"
        local display="${GHOSTSHELL_DISPLAY_TEXTS[$i]}"
        local mode="${GHOSTSHELL_ACCEPT_MODES[$i]}"
        local kind="${GHOSTSHELL_SUGGESTION_KINDS[$i]}"
        if _ghostshell_is_status_suggestion "$sugg"; then
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
        GHOSTSHELL_SUGGESTIONS=("${new_suggestions[@]}")
        GHOSTSHELL_DISPLAY_TEXTS=("${new_displays[@]}")
        GHOSTSHELL_ACCEPT_MODES=("${new_modes[@]}")
        GHOSTSHELL_SUGGESTION_KINDS=("${new_kinds[@]}")
        GHOSTSHELL_SUGGESTION_INDEX=1
        _ghostshell_update_display
    else
        # Pool exhausted; wait for the next explicit trigger.
        GHOSTSHELL_SUGGESTIONS=()
        GHOSTSHELL_DISPLAY_TEXTS=()
        GHOSTSHELL_ACCEPT_MODES=()
        GHOSTSHELL_SUGGESTION_KINDS=()
        _ghostshell_update_display
    fi
}

_ghostshell_merge_suffix() {
    local base="$1"
    local suffix="$2"
    if [[ -n "$base" && -n "$suffix" && "$base" == *[[:space:]] && "$suffix" == [[:space:]]* ]]; then
        # Deduplicate separator whitespace only at join boundary.
        local leading_ws="${suffix%%[![:space:]]*}"
        suffix="${suffix#$leading_ws}"
    fi
    print -r -- "$suffix"
}

_ghostshell_canonicalize_buffer_spacing() {
    local value="$1"
    # Skip aggressive normalization when command likely needs literal spacing semantics.
    if [[ "$value" == *"'"* || "$value" == *'"'* || "$value" == *"\\ "* ]]; then
        print -r -- "$value"
        return
    fi

    value="${value//$'\t'/ }"
    while [[ "$value" == *"  "* ]]; do
        value="${value//  / }"
    done

    # Trim edges to avoid storing accidental surrounding spaces.
    while [[ -n "$value" && "${value:0:1}" == " " ]]; do
        value="${value:1}"
    done
    while [[ -n "$value" && "${value: -1}" == " " ]]; do
        value="${value:0:${#value}-1}"
    done

    print -r -- "$value"
}

_ghostshell_reset_provenance_line_state() {
    GHOSTSHELL_LINE_LAST_ACTION=""
    GHOSTSHELL_LINE_ACCEPTED_ORIGIN=""
    GHOSTSHELL_LINE_ACCEPTED_MODE=""
    GHOSTSHELL_LINE_ACCEPTED_KIND=""
    GHOSTSHELL_LINE_MANUAL_EDIT_AFTER_ACCEPT=0
    GHOSTSHELL_LINE_ACCEPTED_AI_AGENT=""
    GHOSTSHELL_LINE_ACCEPTED_AI_PROVIDER=""
    GHOSTSHELL_LINE_ACCEPTED_AI_MODEL=""
}

_ghostshell_clear_pending_execution() {
    GHOSTSHELL_PENDING_LAST_ACTION=""
    GHOSTSHELL_PENDING_ACCEPTED_ORIGIN=""
    GHOSTSHELL_PENDING_ACCEPTED_MODE=""
    GHOSTSHELL_PENDING_ACCEPTED_KIND=""
    GHOSTSHELL_PENDING_MANUAL_EDIT_AFTER_ACCEPT=0
    GHOSTSHELL_PENDING_AI_AGENT=""
    GHOSTSHELL_PENDING_AI_PROVIDER=""
    GHOSTSHELL_PENDING_AI_MODEL=""
    GHOSTSHELL_PENDING_AGENT_NAME=""
    GHOSTSHELL_PENDING_AGENT_HINT=""
    GHOSTSHELL_PENDING_MODEL_RAW=""
    GHOSTSHELL_PENDING_WRAPPER_ID=""
    GHOSTSHELL_PENDING_PROOF_LABEL=""
    GHOSTSHELL_PENDING_PROOF_AGENT=""
    GHOSTSHELL_PENDING_PROOF_MODEL=""
    GHOSTSHELL_PENDING_PROOF_TRACE=""
    GHOSTSHELL_PENDING_PROOF_TIMESTAMP=0
    GHOSTSHELL_PENDING_PROOF_SIGNATURE=""
    GHOSTSHELL_PENDING_PROOF_SIGNER_SCOPE=""
    GHOSTSHELL_PENDING_PROOF_KEY_FINGERPRINT=""
    GHOSTSHELL_PENDING_PROOF_HOST_FINGERPRINT=""
}

_ghostshell_mark_manual_line_edit() {
    local action="$1"
    if [[ -n "$GHOSTSHELL_LINE_ACCEPTED_ORIGIN" ]]; then
        GHOSTSHELL_LINE_MANUAL_EDIT_AFTER_ACCEPT=1
    fi
    GHOSTSHELL_LINE_LAST_ACTION="$action"
}

_ghostshell_set_suggestion_accept_state() {
    local origin="$1"
    local mode="$2"
    local kind="$3"
    local ai_agent="$4"
    local ai_provider="$5"
    local ai_model="$6"
    GHOSTSHELL_LINE_ACCEPTED_ORIGIN="$origin"
    GHOSTSHELL_LINE_ACCEPTED_MODE="$mode"
    GHOSTSHELL_LINE_ACCEPTED_KIND="$kind"
    GHOSTSHELL_LINE_ACCEPTED_AI_AGENT="$ai_agent"
    GHOSTSHELL_LINE_ACCEPTED_AI_PROVIDER="$ai_provider"
    GHOSTSHELL_LINE_ACCEPTED_AI_MODEL="$ai_model"
    GHOSTSHELL_LINE_LAST_ACTION="suggestion_accept"
    GHOSTSHELL_LINE_MANUAL_EDIT_AFTER_ACCEPT=0
}

_ghostshell_snapshot_pending_execution() {
    GHOSTSHELL_PENDING_LAST_ACTION="$GHOSTSHELL_LINE_LAST_ACTION"
    GHOSTSHELL_PENDING_ACCEPTED_ORIGIN="$GHOSTSHELL_LINE_ACCEPTED_ORIGIN"
    GHOSTSHELL_PENDING_ACCEPTED_MODE="$GHOSTSHELL_LINE_ACCEPTED_MODE"
    GHOSTSHELL_PENDING_ACCEPTED_KIND="$GHOSTSHELL_LINE_ACCEPTED_KIND"
    GHOSTSHELL_PENDING_MANUAL_EDIT_AFTER_ACCEPT="$GHOSTSHELL_LINE_MANUAL_EDIT_AFTER_ACCEPT"
    GHOSTSHELL_PENDING_AI_AGENT="$GHOSTSHELL_LINE_ACCEPTED_AI_AGENT"
    GHOSTSHELL_PENDING_AI_PROVIDER="$GHOSTSHELL_LINE_ACCEPTED_AI_PROVIDER"
    GHOSTSHELL_PENDING_AI_MODEL="$GHOSTSHELL_LINE_ACCEPTED_AI_MODEL"
    GHOSTSHELL_PENDING_AGENT_NAME="${GHOSTSHELL_AI_SESSION_AGENT_NAME:-}"
    GHOSTSHELL_PENDING_AGENT_HINT="$GHOSTSHELL_LINE_ACCEPTED_AI_AGENT"
    GHOSTSHELL_PENDING_MODEL_RAW="$GHOSTSHELL_LINE_ACCEPTED_AI_MODEL"
    GHOSTSHELL_PENDING_WRAPPER_ID=""
    GHOSTSHELL_PENDING_PROOF_LABEL="$GHOSTSHELL_NEXT_PROOF_LABEL"
    GHOSTSHELL_PENDING_PROOF_AGENT="$GHOSTSHELL_NEXT_PROOF_AGENT"
    GHOSTSHELL_PENDING_PROOF_MODEL="$GHOSTSHELL_NEXT_PROOF_MODEL"
    GHOSTSHELL_PENDING_PROOF_TRACE="$GHOSTSHELL_NEXT_PROOF_TRACE"
    GHOSTSHELL_PENDING_PROOF_TIMESTAMP="$GHOSTSHELL_NEXT_PROOF_TIMESTAMP"
    GHOSTSHELL_PENDING_PROOF_SIGNATURE="$GHOSTSHELL_NEXT_PROOF_SIGNATURE"
    GHOSTSHELL_PENDING_PROOF_SIGNER_SCOPE="$GHOSTSHELL_NEXT_PROOF_SIGNER_SCOPE"
    GHOSTSHELL_PENDING_PROOF_KEY_FINGERPRINT="$GHOSTSHELL_NEXT_PROOF_KEY_FINGERPRINT"
    GHOSTSHELL_PENDING_PROOF_HOST_FINGERPRINT="$GHOSTSHELL_NEXT_PROOF_HOST_FINGERPRINT"
    if [[ -n "$GHOSTSHELL_PENDING_PROOF_TRACE" ]]; then
        GHOSTSHELL_PENDING_WRAPPER_ID="proof:${GHOSTSHELL_PENDING_PROOF_TRACE}"
    fi
    GHOSTSHELL_NEXT_PROOF_LABEL=""
    GHOSTSHELL_NEXT_PROOF_AGENT=""
    GHOSTSHELL_NEXT_PROOF_MODEL=""
    GHOSTSHELL_NEXT_PROOF_TRACE=""
    GHOSTSHELL_NEXT_PROOF_TIMESTAMP=0
    GHOSTSHELL_NEXT_PROOF_SIGNATURE=""
    GHOSTSHELL_NEXT_PROOF_SIGNER_SCOPE=""
    GHOSTSHELL_NEXT_PROOF_KEY_FINGERPRINT=""
    GHOSTSHELL_NEXT_PROOF_HOST_FINGERPRINT=""
}

_ghostshell_clear_ai_session_env() {
    _ghostshell_stop_ai_session_timer
    unset GHOSTSHELL_AI_SESSION_ACTIVE
    unset GHOSTSHELL_AI_SESSION_AGENT
    unset GHOSTSHELL_AI_SESSION_MODEL
    unset GHOSTSHELL_AI_SESSION_AGENT_NAME
    unset GHOSTSHELL_AI_SESSION_ID
    unset GHOSTSHELL_AI_SESSION_STARTED_TS
    unset GHOSTSHELL_AI_SESSION_EXPIRES_TS
    unset GHOSTSHELL_AI_SESSION_COUNTER
    unset GHOSTSHELL_AI_SESSION_TIMER_PID
}

_ghostshell_pid_is_alive() {
    local pid="$1"
    if [[ -z "$pid" || "$pid" != <-> ]]; then
        return 1
    fi
    kill -0 "$pid" 2>/dev/null
}

_ghostshell_stop_ai_session_timer() {
    if _ghostshell_pid_is_alive "${GHOSTSHELL_AI_SESSION_TIMER_PID:-}"; then
        kill "${GHOSTSHELL_AI_SESSION_TIMER_PID}" 2>/dev/null
    fi
    GHOSTSHELL_AI_SESSION_TIMER_PID=""
}

_ghostshell_now_ts() {
    local now_ts
    now_ts="$(date +%s 2>/dev/null)"
    if [[ -z "$now_ts" ]]; then
        now_ts="0"
    fi
    print -r -- "$now_ts"
}

_ghostshell_now_time_component() {
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
    _ghostshell_now_ts
}

_ghostshell_ai_session_is_expired() {
    if [[ "${GHOSTSHELL_AI_SESSION_ACTIVE:-0}" != "1" ]]; then
        return 1
    fi
    local expires_ts="${GHOSTSHELL_AI_SESSION_EXPIRES_TS:-0}"
    if [[ -z "$expires_ts" || "$expires_ts" == "0" ]]; then
        return 1
    fi
    local now_ts
    now_ts="$(_ghostshell_now_ts)"
    if [[ "$expires_ts" == <-> && "$now_ts" == <-> && "$now_ts" -ge "$expires_ts" ]]; then
        return 0
    fi
    return 1
}

_ghostshell_schedule_ai_session_expiry_timer() {
    setopt localoptions nobgnice
    _ghostshell_stop_ai_session_timer
    if [[ "${GHOSTSHELL_AI_SESSION_ACTIVE:-0}" != "1" ]]; then
        return
    fi
    local expires_ts="${GHOSTSHELL_AI_SESSION_EXPIRES_TS:-0}"
    if [[ -z "$expires_ts" || "$expires_ts" == "0" || "$expires_ts" != <-> ]]; then
        return
    fi
    local now_ts wait_seconds
    now_ts="$(_ghostshell_now_ts)"
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
    ) &!
    GHOSTSHELL_AI_SESSION_TIMER_PID="$!"
}

_ghostshell_enforce_ai_session_expiry() {
    if _ghostshell_ai_session_is_expired; then
        _ghostshell_clear_ai_session_env
        return 0
    fi
    return 1
}

_ghostshell_ensure_ai_session_timer() {
    if [[ "${GHOSTSHELL_AI_SESSION_ACTIVE:-0}" != "1" ]]; then
        _ghostshell_stop_ai_session_timer
        return
    fi
    if _ghostshell_enforce_ai_session_expiry; then
        return
    fi
    if ! _ghostshell_pid_is_alive "${GHOSTSHELL_AI_SESSION_TIMER_PID:-}"; then
        _ghostshell_schedule_ai_session_expiry_timer
    fi
}

ghostshell_session_start() {
    local agent=""
    local model=""
    local agent_name=""
    local ttl_minutes="120"
    local defaulted_identity=0

    while (( $# > 0 )); do
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
            --ttl-minutes)
                shift
                ttl_minutes="${1:-}"
                ;;
            -h|--help)
                print -r -- "usage: ghostshell_session_start [--agent <agent>] [--model <model>] [--agent-name <name>] [--ttl-minutes <1-1440>]"
                return 0
                ;;
            *)
                print -r -- "ghostshell_session_start: unknown option: $1" >&2
                return 2
                ;;
        esac
        shift || true
    done

    if [[ -z "$agent" ]]; then
        agent="unknown"
        defaulted_identity=1
    fi
    if [[ -z "$model" ]]; then
        model="unknown-model"
        defaulted_identity=1
    fi
    agent="${(L)agent}"

    if [[ "$ttl_minutes" != <-> || "$ttl_minutes" -lt 1 || "$ttl_minutes" -gt 1440 ]]; then
        print -r -- "ghostshell_session_start: --ttl-minutes must be an integer between 1 and 1440" >&2
        return 2
    fi

    if (( defaulted_identity == 1 )); then
        print -r -- "Warning: session identity missing; defaulting to agent=unknown model=unknown-model" >&2
    fi

    local now_ts expires_ts session_id
    now_ts="$(_ghostshell_now_ts)"
    expires_ts=$(( now_ts + ttl_minutes * 60 ))
    session_id="$(python3 - <<'PY' 2>/dev/null
import uuid

print(uuid.uuid4().hex[:16])
PY
)"
    if [[ -z "$session_id" ]]; then
        session_id="$now_ts"
    fi

    export GHOSTSHELL_AI_SESSION_ACTIVE="1"
    export GHOSTSHELL_AI_SESSION_AGENT="$agent"
    export GHOSTSHELL_AI_SESSION_MODEL="$model"
    export GHOSTSHELL_AI_SESSION_AGENT_NAME="$agent_name"
    export GHOSTSHELL_AI_SESSION_ID="$session_id"
    export GHOSTSHELL_AI_SESSION_STARTED_TS="$now_ts"
    export GHOSTSHELL_AI_SESSION_EXPIRES_TS="$expires_ts"
    export GHOSTSHELL_AI_SESSION_COUNTER="0"
    GHOSTSHELL_AI_SESSION_TIMER_PID=""
    _ghostshell_schedule_ai_session_expiry_timer
}

ghostshell_session_stop() {
    _ghostshell_clear_ai_session_env
}

ghostshell_session_status() {
    if [[ "${GHOSTSHELL_AI_SESSION_ACTIVE:-0}" != "1" ]]; then
        print -r -- "inactive"
        return 0
    fi

    local now_ts expires_ts remaining state
    now_ts="$(_ghostshell_now_ts)"
    expires_ts="${GHOSTSHELL_AI_SESSION_EXPIRES_TS:-0}"
    if [[ "$expires_ts" == <-> && "$expires_ts" -gt 0 ]]; then
        remaining=$(( expires_ts - now_ts ))
        if (( remaining <= 0 )); then
            state="expired"
            remaining=0
        else
            state="active"
        fi
    else
        state="active"
        remaining=0
    fi
    print -r -- "${state} agent=${GHOSTSHELL_AI_SESSION_AGENT:-} model=${GHOSTSHELL_AI_SESSION_MODEL:-} agent_name=${GHOSTSHELL_AI_SESSION_AGENT_NAME:-'-'} session_id=${GHOSTSHELL_AI_SESSION_ID:-'-'} remaining_seconds=${remaining}"
}

_ghostshell_session_sign_if_active() {
    _ghostshell_ensure_ai_session_timer
    if [[ "${GHOSTSHELL_AI_SESSION_ACTIVE:-0}" != "1" ]]; then
        return
    fi
    local session_agent="${GHOSTSHELL_AI_SESSION_AGENT:-}"
    local session_model="${GHOSTSHELL_AI_SESSION_MODEL:-}"
    local session_id="${GHOSTSHELL_AI_SESSION_ID:-}"
    local expires_ts="${GHOSTSHELL_AI_SESSION_EXPIRES_TS:-0}"
    local now_ts
    now_ts="$(_ghostshell_now_ts)"
    if [[ -n "$expires_ts" && "$expires_ts" != "0" && "$now_ts" -ge "$expires_ts" ]]; then
        _ghostshell_clear_ai_session_env
        return
    fi
    if [[ -n "$GHOSTSHELL_NEXT_PROOF_SIGNATURE" ]]; then
        return
    fi
    if [[ -z "$session_agent" || -z "$session_model" ]]; then
        return
    fi
    if [[ -z "$session_id" ]]; then
        session_id="$now_ts"
        GHOSTSHELL_AI_SESSION_ID="$session_id"
    fi
    if [[ "${GHOSTSHELL_AI_SESSION_COUNTER:-0}" != <-> ]]; then
        GHOSTSHELL_AI_SESSION_COUNTER=0
    fi
    GHOSTSHELL_AI_SESSION_COUNTER=$(( GHOSTSHELL_AI_SESSION_COUNTER + 1 ))
    local time_component
    time_component="$(_ghostshell_now_time_component)"
    local trace="session:${session_id}:${GHOSTSHELL_AI_SESSION_COUNTER}:${time_component}"
    local proof_blob signature key_fingerprint host_fingerprint
    proof_blob="$(
        GHOSTSHELL_PROOF_LABEL="AI_EXECUTED" \
        GHOSTSHELL_PROOF_AGENT="$session_agent" \
        GHOSTSHELL_PROOF_MODEL="$session_model" \
        GHOSTSHELL_PROOF_TRACE="$trace" \
        GHOSTSHELL_PROOF_TS="$now_ts" \
        python3 - <<'PY' 2>/dev/null
import hashlib
import hmac
import os
import secrets
from pathlib import Path

label = str(os.environ.get("GHOSTSHELL_PROOF_LABEL", "AI_EXECUTED") or "").strip()
agent = str(os.environ.get("GHOSTSHELL_PROOF_AGENT", "") or "").strip()
model = str(os.environ.get("GHOSTSHELL_PROOF_MODEL", "") or "").strip()
trace = str(os.environ.get("GHOSTSHELL_PROOF_TRACE", "") or "").strip()
ts = int(os.environ.get("GHOSTSHELL_PROOF_TS", "0") or "0")

secret_path = Path.home() / ".ghostshell" / "provenance_secret"
secret_path.parent.mkdir(parents=True, exist_ok=True)
if not secret_path.exists():
    secret_path.write_bytes(secrets.token_bytes(32))
    secret_path.chmod(0o600)
else:
    secret_path.chmod(0o600)
secret = secret_path.read_bytes()
msg = "\n".join([label, agent, model, trace, str(ts)])
signature = hmac.new(secret, msg.encode("utf-8"), hashlib.sha256).hexdigest()
key_fingerprint = hashlib.sha256(secret).hexdigest()[:16]
host = os.uname().nodename if hasattr(os, "uname") else os.environ.get("HOSTNAME", "")
host_material = secret + b"\n" + str(host).encode("utf-8")
host_fingerprint = hashlib.sha256(host_material).hexdigest()[:16]
print(signature)
print(key_fingerprint)
print(host_fingerprint)
PY
    )"
    local -a proof_parts
    proof_parts=("${(@f)proof_blob}")
    signature="${proof_parts[1]:-}"
    key_fingerprint="${proof_parts[2]:-}"
    host_fingerprint="${proof_parts[3]:-}"
    if [[ -z "$signature" ]]; then
        return
    fi
    GHOSTSHELL_NEXT_PROOF_LABEL="AI_EXECUTED"
    GHOSTSHELL_NEXT_PROOF_AGENT="$session_agent"
    GHOSTSHELL_NEXT_PROOF_MODEL="$session_model"
    GHOSTSHELL_NEXT_PROOF_TRACE="$trace"
    GHOSTSHELL_NEXT_PROOF_TIMESTAMP="$now_ts"
    GHOSTSHELL_NEXT_PROOF_SIGNATURE="$signature"
    GHOSTSHELL_NEXT_PROOF_SIGNER_SCOPE="local-hmac"
    GHOSTSHELL_NEXT_PROOF_KEY_FINGERPRINT="$key_fingerprint"
    GHOSTSHELL_NEXT_PROOF_HOST_FINGERPRINT="$host_fingerprint"
    GHOSTSHELL_PENDING_WRAPPER_ID="ai_session:${session_id}"
    GHOSTSHELL_PENDING_AGENT_NAME="${GHOSTSHELL_AI_SESSION_AGENT_NAME:-}"
}

ghostshell_mark_ai_executed() {
    local agent="$1"
    local model="$2"
    local trace="$3"
    if [[ -z "$agent" || -z "$model" ]]; then
        print -r -- "usage: ghostshell_mark_ai_executed <agent> <model> [trace_id]"
        return 1
    fi
    if [[ -z "$trace" ]]; then
        trace="$(date +%s 2>/dev/null)"
    fi
    local stamp
    stamp="$(date +%s 2>/dev/null)"
    if [[ -z "$stamp" ]]; then
        stamp="0"
    fi
    local proof_blob signature key_fingerprint host_fingerprint
    proof_blob="$(
        GHOSTSHELL_PROOF_LABEL="AI_EXECUTED" \
        GHOSTSHELL_PROOF_AGENT="$agent" \
        GHOSTSHELL_PROOF_MODEL="$model" \
        GHOSTSHELL_PROOF_TRACE="$trace" \
        GHOSTSHELL_PROOF_TS="$stamp" \
        python3 - <<'PY' 2>/dev/null
import hashlib
import hmac
import os
import secrets
from pathlib import Path

label = str(os.environ.get("GHOSTSHELL_PROOF_LABEL", "AI_EXECUTED") or "").strip()
agent = str(os.environ.get("GHOSTSHELL_PROOF_AGENT", "") or "").strip()
model = str(os.environ.get("GHOSTSHELL_PROOF_MODEL", "") or "").strip()
trace = str(os.environ.get("GHOSTSHELL_PROOF_TRACE", "") or "").strip()
ts = int(os.environ.get("GHOSTSHELL_PROOF_TS", "0") or "0")

secret_path = Path.home() / ".ghostshell" / "provenance_secret"
secret_path.parent.mkdir(parents=True, exist_ok=True)
if not secret_path.exists():
    secret_path.write_bytes(secrets.token_bytes(32))
    secret_path.chmod(0o600)
else:
    secret_path.chmod(0o600)
secret = secret_path.read_bytes()
msg = "\n".join([label, agent, model, trace, str(ts)])
signature = hmac.new(secret, msg.encode("utf-8"), hashlib.sha256).hexdigest()
key_fingerprint = hashlib.sha256(secret).hexdigest()[:16]
host = os.uname().nodename if hasattr(os, "uname") else os.environ.get("HOSTNAME", "")
host_material = secret + b"\n" + str(host).encode("utf-8")
host_fingerprint = hashlib.sha256(host_material).hexdigest()[:16]
print(signature)
print(key_fingerprint)
print(host_fingerprint)
PY
    )"
    local -a proof_parts
    proof_parts=("${(@f)proof_blob}")
    signature="${proof_parts[1]:-}"
    key_fingerprint="${proof_parts[2]:-}"
    host_fingerprint="${proof_parts[3]:-}"
    if [[ -z "$signature" ]]; then
        print -r -- "Could not create AI execution proof."
        return 1
    fi
    GHOSTSHELL_NEXT_PROOF_LABEL="AI_EXECUTED"
    GHOSTSHELL_NEXT_PROOF_AGENT="$agent"
    GHOSTSHELL_NEXT_PROOF_MODEL="$model"
    GHOSTSHELL_NEXT_PROOF_TRACE="$trace"
    GHOSTSHELL_NEXT_PROOF_TIMESTAMP="$stamp"
    GHOSTSHELL_NEXT_PROOF_SIGNATURE="$signature"
    GHOSTSHELL_NEXT_PROOF_SIGNER_SCOPE="local-hmac"
    GHOSTSHELL_NEXT_PROOF_KEY_FINGERPRINT="$key_fingerprint"
    GHOSTSHELL_NEXT_PROOF_HOST_FINGERPRINT="$host_fingerprint"
    print -r -- "AI execution proof armed for next command (${agent}/${model})."
    return 0
}

_ghostshell_send_feedback() {
    local buffer="$1"
    local accepted="$2"
    local accept_mode="${3:-suffix_append}"
    if _ghostshell_matches_disabled_pattern "$buffer" || _ghostshell_matches_disabled_pattern "$accepted"; then
        return
    fi
    _ghostshell_reload_auth_token_if_needed
    # Fire and forget feedback to server for zvec feedback stats
    (
        local escaped_buf="${buffer//\'/\'\\\'\'}"
        local escaped_acc="${accepted//\'/\'\\\'\'}"
        local escaped_pwd="${PWD//\'/\'\\\'\'}"
        local json_data="{\"command_buffer\": \"$escaped_buf\", \"accepted_suggestion\": \"$escaped_acc\", \"accept_mode\": \"${accept_mode}\", \"working_directory\": \"$escaped_pwd\"}"
        local -a auth_headers
        auth_headers=()
        if [[ -n "$GHOSTSHELL_AUTH_TOKEN" ]]; then
            auth_headers=(-H "Authorization: Bearer $GHOSTSHELL_AUTH_TOKEN" -H "X-GhostShell-Auth: $GHOSTSHELL_AUTH_TOKEN")
        fi
        curl -s -X POST "http://127.0.0.1:22000/feedback" \
             "${auth_headers[@]}" \
             -H "Content-Type: application/json" \
             -d "$json_data" > /dev/null 2>&1
    ) &!
}

_ghostshell_log_command() {
    local command="$1"
    local exit_code="$2"
    local source="${3:-runtime}"
    local log_cwd="$PWD"
    local log_shell_pid="$$"
    local log_last_action="$GHOSTSHELL_PENDING_LAST_ACTION"
    local log_accept_origin="$GHOSTSHELL_PENDING_ACCEPTED_ORIGIN"
    local log_accept_mode="$GHOSTSHELL_PENDING_ACCEPTED_MODE"
    local log_suggestion_kind="$GHOSTSHELL_PENDING_ACCEPTED_KIND"
    local log_manual_after_accept="$GHOSTSHELL_PENDING_MANUAL_EDIT_AFTER_ACCEPT"
    local log_ai_agent="$GHOSTSHELL_PENDING_AI_AGENT"
    local log_ai_provider="$GHOSTSHELL_PENDING_AI_PROVIDER"
    local log_ai_model="$GHOSTSHELL_PENDING_AI_MODEL"
    local log_agent_name="$GHOSTSHELL_PENDING_AGENT_NAME"
    local log_agent_hint="$GHOSTSHELL_PENDING_AGENT_HINT"
    local log_model_raw="$GHOSTSHELL_PENDING_MODEL_RAW"
    local log_wrapper_id="$GHOSTSHELL_PENDING_WRAPPER_ID"
    local log_proof_label="$GHOSTSHELL_PENDING_PROOF_LABEL"
    local log_proof_agent="$GHOSTSHELL_PENDING_PROOF_AGENT"
    local log_proof_model="$GHOSTSHELL_PENDING_PROOF_MODEL"
    local log_proof_trace="$GHOSTSHELL_PENDING_PROOF_TRACE"
    local log_proof_timestamp="$GHOSTSHELL_PENDING_PROOF_TIMESTAMP"
    local log_proof_signature="$GHOSTSHELL_PENDING_PROOF_SIGNATURE"
    local log_proof_signer_scope="$GHOSTSHELL_PENDING_PROOF_SIGNER_SCOPE"
    local log_proof_key_fingerprint="$GHOSTSHELL_PENDING_PROOF_KEY_FINGERPRINT"
    local log_proof_host_fingerprint="$GHOSTSHELL_PENDING_PROOF_HOST_FINGERPRINT"

    local json_data
    json_data="$(
        GHOSTSHELL_LOG_COMMAND="$command" \
        GHOSTSHELL_LOG_EXIT="$exit_code" \
        GHOSTSHELL_LOG_SOURCE="$source" \
        GHOSTSHELL_LOG_CWD="$log_cwd" \
        GHOSTSHELL_LOG_SHELL_PID="$log_shell_pid" \
        GHOSTSHELL_LOG_LAST_ACTION="$log_last_action" \
        GHOSTSHELL_LOG_ACCEPT_ORIGIN="$log_accept_origin" \
        GHOSTSHELL_LOG_ACCEPT_MODE="$log_accept_mode" \
        GHOSTSHELL_LOG_SUGGESTION_KIND="$log_suggestion_kind" \
        GHOSTSHELL_LOG_MANUAL_AFTER_ACCEPT="$log_manual_after_accept" \
        GHOSTSHELL_LOG_AI_AGENT="$log_ai_agent" \
        GHOSTSHELL_LOG_AI_PROVIDER="$log_ai_provider" \
        GHOSTSHELL_LOG_AI_MODEL="$log_ai_model" \
        GHOSTSHELL_LOG_AGENT_NAME="$log_agent_name" \
        GHOSTSHELL_LOG_AGENT_HINT="$log_agent_hint" \
        GHOSTSHELL_LOG_MODEL_RAW="$log_model_raw" \
        GHOSTSHELL_LOG_WRAPPER_ID="$log_wrapper_id" \
        GHOSTSHELL_LOG_PROOF_LABEL="$log_proof_label" \
        GHOSTSHELL_LOG_PROOF_AGENT="$log_proof_agent" \
        GHOSTSHELL_LOG_PROOF_MODEL="$log_proof_model" \
        GHOSTSHELL_LOG_PROOF_TRACE="$log_proof_trace" \
        GHOSTSHELL_LOG_PROOF_TIMESTAMP="$log_proof_timestamp" \
        GHOSTSHELL_LOG_PROOF_SIGNATURE="$log_proof_signature" \
        GHOSTSHELL_LOG_PROOF_SIGNER_SCOPE="$log_proof_signer_scope" \
        GHOSTSHELL_LOG_PROOF_KEY_FINGERPRINT="$log_proof_key_fingerprint" \
        GHOSTSHELL_LOG_PROOF_HOST_FINGERPRINT="$log_proof_host_fingerprint" \
        python3 - <<'PY' 2>/dev/null
import json
import os

def as_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

command = str(os.environ.get("GHOSTSHELL_LOG_COMMAND", "") or "")
exit_code = as_int(os.environ.get("GHOSTSHELL_LOG_EXIT", None), None)
manual_after_accept = str(
    os.environ.get("GHOSTSHELL_LOG_MANUAL_AFTER_ACCEPT", "0") or "0"
).strip() in {"1", "true", "True"}

payload = {
    "command": command,
    "exit_code": exit_code,
    "source": str(os.environ.get("GHOSTSHELL_LOG_SOURCE", "runtime") or "runtime"),
    "working_directory": str(os.environ.get("GHOSTSHELL_LOG_CWD", "") or ""),
    "shell_pid": as_int(os.environ.get("GHOSTSHELL_LOG_SHELL_PID", None), None),
    "provenance_last_action": str(os.environ.get("GHOSTSHELL_LOG_LAST_ACTION", "") or ""),
    "provenance_accept_origin": str(os.environ.get("GHOSTSHELL_LOG_ACCEPT_ORIGIN", "") or ""),
    "provenance_accept_mode": str(os.environ.get("GHOSTSHELL_LOG_ACCEPT_MODE", "") or ""),
    "provenance_suggestion_kind": str(os.environ.get("GHOSTSHELL_LOG_SUGGESTION_KIND", "") or ""),
    "provenance_manual_edit_after_accept": manual_after_accept,
    "provenance_ai_agent": str(os.environ.get("GHOSTSHELL_LOG_AI_AGENT", "") or ""),
    "provenance_ai_provider": str(os.environ.get("GHOSTSHELL_LOG_AI_PROVIDER", "") or ""),
    "provenance_ai_model": str(os.environ.get("GHOSTSHELL_LOG_AI_MODEL", "") or ""),
    "provenance_agent_name": str(os.environ.get("GHOSTSHELL_LOG_AGENT_NAME", "") or ""),
    "provenance_agent_hint": str(os.environ.get("GHOSTSHELL_LOG_AGENT_HINT", "") or ""),
    "provenance_model_raw": str(os.environ.get("GHOSTSHELL_LOG_MODEL_RAW", "") or ""),
    "provenance_wrapper_id": str(os.environ.get("GHOSTSHELL_LOG_WRAPPER_ID", "") or ""),
    "proof_label": str(os.environ.get("GHOSTSHELL_LOG_PROOF_LABEL", "") or ""),
    "proof_agent": str(os.environ.get("GHOSTSHELL_LOG_PROOF_AGENT", "") or ""),
    "proof_model": str(os.environ.get("GHOSTSHELL_LOG_PROOF_MODEL", "") or ""),
    "proof_trace": str(os.environ.get("GHOSTSHELL_LOG_PROOF_TRACE", "") or ""),
    "proof_timestamp": as_int(os.environ.get("GHOSTSHELL_LOG_PROOF_TIMESTAMP", None), None),
    "proof_signature": str(os.environ.get("GHOSTSHELL_LOG_PROOF_SIGNATURE", "") or ""),
    "proof_signer_scope": str(os.environ.get("GHOSTSHELL_LOG_PROOF_SIGNER_SCOPE", "") or ""),
    "proof_key_fingerprint": str(os.environ.get("GHOSTSHELL_LOG_PROOF_KEY_FINGERPRINT", "") or ""),
    "proof_host_fingerprint": str(os.environ.get("GHOSTSHELL_LOG_PROOF_HOST_FINGERPRINT", "") or ""),
}
print(json.dumps(payload, separators=(",", ":")))
PY
    )"
    if [[ -z "$json_data" ]]; then
        return
    fi
    _ghostshell_reload_auth_token_if_needed
    (
        local -a auth_headers
        auth_headers=()
        if [[ -n "$GHOSTSHELL_AUTH_TOKEN" ]]; then
            auth_headers=(-H "Authorization: Bearer $GHOSTSHELL_AUTH_TOKEN" -H "X-GhostShell-Auth: $GHOSTSHELL_AUTH_TOKEN")
        fi
        curl -s -X POST "http://127.0.0.1:22000/log_command" \
             "${auth_headers[@]}" \
             -H "Content-Type: application/json" \
             -d "$json_data" > /dev/null 2>&1
    ) &!
}

_ghostshell_is_blocked_runtime_command() {
    local command="$1"
    local exe=""
    exe="$(_ghostshell_extract_executable_token "$command")"
    if [[ -z "$exe" ]]; then
        return 1
    fi
    [[ "$exe" == "rm" ]]
}

_ghostshell_preexec_hook() {
    _ghostshell_session_sign_if_active
    _ghostshell_snapshot_pending_execution
    GHOSTSHELL_LAST_EXECUTED_CMD="$1"
}

_ghostshell_precmd_hook() {
    local exit_code="$?"
    local cmd="$GHOSTSHELL_LAST_EXECUTED_CMD"
    GHOSTSHELL_LAST_EXECUTED_CMD=""
    _ghostshell_ensure_ai_session_timer
    _ghostshell_reload_disabled_patterns_if_needed

    if [[ -z "$cmd" ]]; then
        return
    fi

    if [[ "$exit_code" -ne 0 ]]; then
        _ghostshell_clear_pending_execution
        _ghostshell_reset_provenance_line_state
        return
    fi

    if _ghostshell_is_blocked_runtime_command "$cmd"; then
        _ghostshell_clear_pending_execution
        _ghostshell_reset_provenance_line_state
        return
    fi
    if _ghostshell_matches_disabled_pattern "$cmd"; then
        _ghostshell_clear_pending_execution
        _ghostshell_reset_provenance_line_state
        return
    fi

    _ghostshell_log_command "$cmd" "$exit_code" "runtime"
    _ghostshell_clear_pending_execution
    _ghostshell_reset_provenance_line_state
}

_ghostshell_reset_line_state() {
    GHOSTSHELL_LINE_LLM_CALLS_USED=0
    GHOSTSHELL_LINE_HAS_SPACE=0
    GHOSTSHELL_SHOW_CTRL_SPACE_HINT=0
    GHOSTSHELL_LAST_FETCH_USED_AI=0
    GHOSTSHELL_LAST_FETCH_AI_AGENT=""
    GHOSTSHELL_LAST_FETCH_AI_PROVIDER=""
    GHOSTSHELL_LAST_FETCH_AI_MODEL=""
    _ghostshell_reset_provenance_line_state
}

_ghostshell_maybe_reset_line_state_for_empty_buffer() {
    if [[ -z "$BUFFER" ]]; then
        _ghostshell_reset_line_state
    fi
}

_ghostshell_try_fetch_on_space() {
    if _ghostshell_buffer_has_hash; then
        _ghostshell_clear_suggestions
        return
    fi
    if _ghostshell_should_skip_ghostshell_for_buffer; then
        _ghostshell_clear_suggestions
        return
    fi

    local allow_ai=1
    local is_manual="${1:-0}"
    local budget_blocked=0
    local trigger_source="space_auto"

    if [[ "$is_manual" != "1" ]] && _ghostshell_should_preserve_native_tab; then
        _ghostshell_clear_suggestions
        GHOSTSHELL_SHOW_CTRL_SPACE_HINT=0
        GHOSTSHELL_LAST_FETCH_USED_AI=0
        return
    fi

    if [[ "$is_manual" == "1" ]]; then
        trigger_source="manual_ctrl_space"
    fi
    if (( GHOSTSHELL_LLM_BUDGET_UNLIMITED == 0 && GHOSTSHELL_LINE_LLM_CALLS_USED >= GHOSTSHELL_MAX_LLM_CALLS_PER_LINE )); then
        allow_ai=0
        budget_blocked=1
    fi

    GHOSTSHELL_LAST_BUFFER="$BUFFER"
    _ghostshell_fetch_suggestions "$allow_ai" "$trigger_source"

    if (( GHOSTSHELL_LAST_FETCH_USED_AI == 1 )); then
        GHOSTSHELL_LINE_LLM_CALLS_USED=$((GHOSTSHELL_LINE_LLM_CALLS_USED + 1))
    fi
    if (( allow_ai == 0 && budget_blocked == 1 && ${#GHOSTSHELL_SUGGESTIONS[@]} == 0 )); then
        GHOSTSHELL_SHOW_CTRL_SPACE_HINT=1
        _ghostshell_set_status_message "$GHOSTSHELL_LLM_BUDGET_REACHED_HINT"
    elif [[ "${GHOSTSHELL_SUGGESTIONS[1]}" != "${GHOSTSHELL_STATUS_PREFIX}${GHOSTSHELL_LLM_BUDGET_REACHED_HINT}" ]]; then
        GHOSTSHELL_SHOW_CTRL_SPACE_HINT=0
    fi
}

_ghostshell_update_display() {
    local current="${GHOSTSHELL_SUGGESTIONS[$GHOSTSHELL_SUGGESTION_INDEX]}"
    local mode="${GHOSTSHELL_ACCEPT_MODES[$GHOSTSHELL_SUGGESTION_INDEX]}"
    local display_text="${GHOSTSHELL_DISPLAY_TEXTS[$GHOSTSHELL_SUGGESTION_INDEX]}"
    if _ghostshell_is_status_suggestion "$current"; then
        local status_msg="${current#$GHOSTSHELL_STATUS_PREFIX}"
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
        # The suggestions are suffixes relative to GHOSTSHELL_LAST_BUFFER
        local typed_since_fetch="${BUFFER#$GHOSTSHELL_LAST_BUFFER}"
        display_sugg="${current#$typed_since_fetch}"
        display_sugg="$(_ghostshell_merge_suffix "$BUFFER" "$display_sugg")"
    fi
    
    # Ensure no newlines break the display
    display_sugg="${display_sugg//$'\n'/}" 
    display_sugg="${display_sugg//$'\r'/}"
    
    if [[ -n "$display_sugg" ]]; then
        local count=${#GHOSTSHELL_SUGGESTIONS[@]}
        if [[ $count -gt 1 ]]; then
            POSTDISPLAY="${display_sugg}  ($GHOSTSHELL_SUGGESTION_INDEX/$count, Ctrl+P/N)"
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

_ghostshell_has_visible_suggestion() {
    local current="${GHOSTSHELL_SUGGESTIONS[$GHOSTSHELL_SUGGESTION_INDEX]}"
    local mode="${GHOSTSHELL_ACCEPT_MODES[$GHOSTSHELL_SUGGESTION_INDEX]}"
    local display_text="${GHOSTSHELL_DISPLAY_TEXTS[$GHOSTSHELL_SUGGESTION_INDEX]}"

    if _ghostshell_is_status_suggestion "$current"; then
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
        local typed_since_fetch="${BUFFER#$GHOSTSHELL_LAST_BUFFER}"
        display_sugg="${current#$typed_since_fetch}"
        display_sugg="$(_ghostshell_merge_suffix "$BUFFER" "$display_sugg")"
    fi
    display_sugg="${display_sugg//$'\n'/}"
    display_sugg="${display_sugg//$'\r'/}"

    [[ -n "$display_sugg" ]]
}

# ======================================================
# 2. PAUSE DETECTION (0.15s timer)
# ======================================================

_ghostshell_start_timer() {
    # Kill existing timer if any
    _ghostshell_stop_timer
    
    # Start a background timer that triggers after 0.15s
    (
        sleep 0.15
        # Signal the main shell to fetch suggestions
        kill -USR1 $$ 2>/dev/null
    ) &!
    GHOSTSHELL_TIMER_PID=$!
}

_ghostshell_stop_timer() {
    if [[ -n "$GHOSTSHELL_TIMER_PID" ]]; then
        kill $GHOSTSHELL_TIMER_PID 2>/dev/null
        GHOSTSHELL_TIMER_PID=""
    fi
}

_ghostshell_on_timer_trigger() {
    # This is called when the 0.15s timer expires
    GHOSTSHELL_TIMER_PID=""

    if _ghostshell_buffer_has_hash; then
        _ghostshell_clear_suggestions
        _ghostshell_update_display
        zle -R
        return
    fi
    if _ghostshell_should_skip_ghostshell_for_buffer; then
        _ghostshell_clear_suggestions
        _ghostshell_update_display
        zle -R
        return
    fi
    if _ghostshell_should_preserve_native_tab; then
        _ghostshell_clear_suggestions
        _ghostshell_update_display
        zle -R
        return
    fi
    
    # Only fetch if buffer has changed and is long enough
    if [[ "$BUFFER" != "$GHOSTSHELL_LAST_BUFFER" && ${#BUFFER} -ge 2 ]]; then
        GHOSTSHELL_LAST_BUFFER="$BUFFER"
        # Timer-based fetch is vector-store only (no LLM).
        _ghostshell_fetch_suggestions 0 "pause_timer"
        _ghostshell_update_display
        zle -R
    fi
}

# Register the trigger function as a widget so it can access POSTDISPLAY
zle -N _ghostshell_on_timer_trigger

# Set up signal handler for timer
TRAPUSR1() {
    # Ensure we are in ZLE
    if zle; then
        # Invoke the update logic AS A WIDGET
        zle _ghostshell_on_timer_trigger
    fi
}

TRAPUSR2() {
    GHOSTSHELL_AI_SESSION_TIMER_PID=""
    _ghostshell_enforce_ai_session_expiry
}

# ======================================================
# 3. WIDGET DEFINITIONS
# ======================================================

_ghostshell_clear_suggestions() {
    if zle; then
        POSTDISPLAY=""
        region_highlight=()
    fi
    GHOSTSHELL_SUGGESTIONS=()
    GHOSTSHELL_DISPLAY_TEXTS=()
    GHOSTSHELL_ACCEPT_MODES=()
    GHOSTSHELL_SUGGESTION_KINDS=()
    _ghostshell_reset_intent_state
    _ghostshell_stop_timer
}

_ghostshell_self_insert() {
    local inserted_key="$KEYS"
    zle .self-insert
    _ghostshell_mark_manual_line_edit "human_typed"

    _ghostshell_maybe_reset_line_state_for_empty_buffer
    _ghostshell_reset_intent_state

    if _ghostshell_buffer_has_hash; then
        _ghostshell_clear_suggestions
        _ghostshell_update_display
        zle -R
        return
    fi
    if _ghostshell_should_skip_ghostshell_for_buffer; then
        _ghostshell_clear_suggestions
        _ghostshell_update_display
        zle -R
        return
    fi
    if _ghostshell_should_preserve_native_tab; then
        _ghostshell_clear_suggestions
        GHOSTSHELL_SHOW_CTRL_SPACE_HINT=0
        _ghostshell_update_display
        zle -R
        return
    fi

    # Filter existing pool if we have one
    if [[ ${#GHOSTSHELL_SUGGESTIONS[@]} -gt 0 ]]; then
        _ghostshell_filter_pool
        _ghostshell_update_display
        zle -R
    fi

    # Auto fetch only when user presses space (new command segment boundary).
    if [[ "$inserted_key" == " " && ${#BUFFER} -ge 2 ]]; then
        _ghostshell_stop_timer
        GHOSTSHELL_LINE_HAS_SPACE=1
        _ghostshell_try_fetch_on_space 0
        _ghostshell_update_display
        zle -R
        return
    fi

    # Non-space typing uses 0.2s pause detection; fetch stays vector-only there.
    if [[ ${#BUFFER} -ge 2 ]]; then
        _ghostshell_start_timer
    else
        _ghostshell_stop_timer
    fi
}

_ghostshell_backward_delete_char() {
    zle .backward-delete-char
    _ghostshell_mark_manual_line_edit "human_edit"

    # Clear suggestions and pool on delete
    _ghostshell_reset_intent_state
    _ghostshell_clear_suggestions
    _ghostshell_maybe_reset_line_state_for_empty_buffer
}

_ghostshell_interrupt() {
    _ghostshell_clear_suggestions
    _ghostshell_reset_line_state
    zle .send-break
}

_ghostshell_escape() {
    if _ghostshell_has_visible_suggestion; then
        _ghostshell_clear_suggestions
        _ghostshell_update_display
        zle -R
        return
    fi

    local keymap="${KEYMAP:-emacs}"
    case "$keymap" in
        main) keymap="emacs" ;;
        viopp|visual) keymap="vicmd" ;;
    esac

    local native_widget="${GHOSTSHELL_NATIVE_ESC_WIDGET[$keymap]}"
    if [[ -n "$native_widget" && "$native_widget" != "_ghostshell_escape" ]]; then
        zle "$native_widget"
    else
        zle .undefined-key
    fi
}

# --- Paste Handling ---
autoload -Uz bracketed-paste-magic
_ghostshell_paste() {
    _ghostshell_reset_intent_state
    _ghostshell_clear_suggestions
    zle .bracketed-paste
    _ghostshell_mark_manual_line_edit "human_paste"
    _ghostshell_maybe_reset_line_state_for_empty_buffer
}

# --- Accept Suggestion ---
_ghostshell_accept_widget() {
    if _ghostshell_is_single_hash_intent; then
        _ghostshell_clear_suggestions
        _ghostshell_resolve_intent_command "$BUFFER"
        zle -R
        return
    fi

    if _ghostshell_is_double_hash_assist; then
        zle expand-or-complete
        return
    fi
    if _ghostshell_should_skip_ghostshell_for_buffer || _ghostshell_should_preserve_native_tab; then
        _ghostshell_clear_suggestions
        zle expand-or-complete
        return
    fi

    local current="${GHOSTSHELL_SUGGESTIONS[$GHOSTSHELL_SUGGESTION_INDEX]}"
    local mode="${GHOSTSHELL_ACCEPT_MODES[$GHOSTSHELL_SUGGESTION_INDEX]}"
    local kind="${GHOSTSHELL_SUGGESTION_KINDS[$GHOSTSHELL_SUGGESTION_INDEX]}"
    if _ghostshell_is_status_suggestion "$current"; then
        zle expand-or-complete
    elif [[ -n "$current" ]]; then
        local origin="gs"
        local ai_agent=""
        local ai_provider=""
        local ai_model=""
        if (( GHOSTSHELL_LAST_FETCH_USED_AI == 1 )); then
            origin="ai"
            ai_agent="$GHOSTSHELL_LAST_FETCH_AI_AGENT"
            ai_provider="$GHOSTSHELL_LAST_FETCH_AI_PROVIDER"
            ai_model="$GHOSTSHELL_LAST_FETCH_AI_MODEL"
        fi
        if [[ "$mode" == "replace_full" ]]; then
            local normalized_buffer="$(_ghostshell_canonicalize_buffer_spacing "$BUFFER")"
            local replacement="$(_ghostshell_canonicalize_buffer_spacing "$current")"
            _ghostshell_send_feedback "$normalized_buffer" "$replacement" "replace_full"
            BUFFER="$replacement"
            _ghostshell_set_suggestion_accept_state "$origin" "replace_full" "${kind:-normal}" "$ai_agent" "$ai_provider" "$ai_model"
        else
            local typed_since_fetch="${BUFFER#$GHOSTSHELL_LAST_BUFFER}"
            local to_add="${current#$typed_since_fetch}"
            to_add="$(_ghostshell_merge_suffix "$BUFFER" "$to_add")"
            local merged="${BUFFER}${to_add}"
            local normalized_merged="$(_ghostshell_canonicalize_buffer_spacing "$merged")"
            local normalized_buffer="$(_ghostshell_canonicalize_buffer_spacing "$BUFFER")"
            local normalized_to_add="$to_add"
            if [[ "$normalized_merged" == "$normalized_buffer"* ]]; then
                normalized_to_add="${normalized_merged#$normalized_buffer}"
            fi
            _ghostshell_send_feedback "$normalized_buffer" "$normalized_to_add" "suffix_append"
            BUFFER="$normalized_merged"
            _ghostshell_set_suggestion_accept_state "$origin" "suffix_append" "${kind:-normal}" "$ai_agent" "$ai_provider" "$ai_model"
        fi
        CURSOR=${#BUFFER}
        _ghostshell_clear_suggestions
        zle -R
    else
        zle expand-or-complete
    fi
}

# --- Partial Accept ---
_ghostshell_partial_accept() {
    local current="${GHOSTSHELL_SUGGESTIONS[$GHOSTSHELL_SUGGESTION_INDEX]}"
    local mode="${GHOSTSHELL_ACCEPT_MODES[$GHOSTSHELL_SUGGESTION_INDEX]}"
    local kind="${GHOSTSHELL_SUGGESTION_KINDS[$GHOSTSHELL_SUGGESTION_INDEX]}"
    local origin="gs"
    local ai_agent=""
    local ai_provider=""
    local ai_model=""
    if (( GHOSTSHELL_LAST_FETCH_USED_AI == 1 )); then
        origin="ai"
        ai_agent="$GHOSTSHELL_LAST_FETCH_AI_AGENT"
        ai_provider="$GHOSTSHELL_LAST_FETCH_AI_PROVIDER"
        ai_model="$GHOSTSHELL_LAST_FETCH_AI_MODEL"
    fi
    
    if _ghostshell_is_status_suggestion "$current"; then
        zle forward-word
    elif [[ "$mode" == "replace_full" ]]; then
        BUFFER="$(_ghostshell_canonicalize_buffer_spacing "$current")"
        _ghostshell_set_suggestion_accept_state "$origin" "replace_full" "${kind:-normal}" "$ai_agent" "$ai_provider" "$ai_model"
        CURSOR=${#BUFFER}
        _ghostshell_clear_suggestions
        zle -R
    elif [[ -n "$current" ]]; then
        local typed_since_fetch="${BUFFER#$GHOSTSHELL_LAST_BUFFER}"
        local remaining="${current#$typed_since_fetch}"
        remaining="$(_ghostshell_merge_suffix "$BUFFER" "$remaining")"
        local first_word="${remaining%% *}"
        if [[ "$first_word" == "$remaining" ]]; then
             BUFFER="${BUFFER}${remaining}"
        else
             BUFFER="${BUFFER}${first_word} "
        fi
        BUFFER="$(_ghostshell_canonicalize_buffer_spacing "$BUFFER")"
        _ghostshell_set_suggestion_accept_state "$origin" "suffix_append" "${kind:-normal}" "$ai_agent" "$ai_provider" "$ai_model"
        CURSOR=${#BUFFER}
        _ghostshell_clear_suggestions
        zle -R
    else
        zle forward-word
    fi
}

# --- Cycle Suggestions ---
_ghostshell_cycle_next() {
    if (( GHOSTSHELL_INTENT_ACTIVE == 1 && ${#GHOSTSHELL_INTENT_OPTIONS[@]} > 0 )); then
        local count=${#GHOSTSHELL_INTENT_OPTIONS[@]}
        GHOSTSHELL_INTENT_OPTION_INDEX=$(( GHOSTSHELL_INTENT_OPTION_INDEX % count + 1 ))
        BUFFER="${GHOSTSHELL_INTENT_OPTIONS[$GHOSTSHELL_INTENT_OPTION_INDEX]}"
        CURSOR=${#BUFFER}
        _ghostshell_update_intent_hint
        zle -R
        return
    fi

    local count=${#GHOSTSHELL_SUGGESTIONS[@]}
    if [[ $count -gt 0 ]]; then
        GHOSTSHELL_SUGGESTION_INDEX=$(( GHOSTSHELL_SUGGESTION_INDEX % count + 1 ))
        _ghostshell_update_display
        zle -R
    else
        zle down-line-or-history
    fi
}

_ghostshell_cycle_prev() {
    if (( GHOSTSHELL_INTENT_ACTIVE == 1 && ${#GHOSTSHELL_INTENT_OPTIONS[@]} > 0 )); then
        local count=${#GHOSTSHELL_INTENT_OPTIONS[@]}
        GHOSTSHELL_INTENT_OPTION_INDEX=$(( (GHOSTSHELL_INTENT_OPTION_INDEX + count - 2) % count + 1 ))
        BUFFER="${GHOSTSHELL_INTENT_OPTIONS[$GHOSTSHELL_INTENT_OPTION_INDEX]}"
        CURSOR=${#BUFFER}
        _ghostshell_update_intent_hint
        zle -R
        return
    fi

    local count=${#GHOSTSHELL_SUGGESTIONS[@]}
    if [[ $count -gt 0 ]]; then
        GHOSTSHELL_SUGGESTION_INDEX=$(( (GHOSTSHELL_SUGGESTION_INDEX + count - 2) % count + 1 ))
        _ghostshell_update_display
        zle -R
    else
        zle up-line-or-history
    fi
}

_ghostshell_down_line_or_history() {
    _ghostshell_clear_suggestions
    zle down-line-or-history
    _ghostshell_mark_manual_line_edit "human_edit"
    zle -R
}

_ghostshell_up_line_or_history() {
    _ghostshell_clear_suggestions
    zle up-line-or-history
    _ghostshell_mark_manual_line_edit "human_edit"
    zle -R
}

# --- Manual Trigger (Ctrl+Space) ---
_ghostshell_manual_trigger() {
    if [[ ${#BUFFER} -ge 2 ]]; then
        if _ghostshell_should_skip_ghostshell_for_buffer; then
            _ghostshell_clear_suggestions
            _ghostshell_update_display
            zle -R
            return
        fi
        _ghostshell_try_fetch_on_space 1
        _ghostshell_update_display
        zle -R
    fi
}

# --- Accept Line (Execute Command) ---
_ghostshell_accept_line() {
    if _ghostshell_is_double_hash_assist; then
        _ghostshell_clear_suggestions
        _ghostshell_resolve_general_assist "$BUFFER"
        zle -R
        return
    fi

    if _ghostshell_is_single_hash_intent; then
        _ghostshell_clear_suggestions
        _ghostshell_resolve_intent_command "$BUFFER"
        zle -R
        return
    fi

    if [[ -n "${BUFFER//[[:space:]]/}" ]]; then
        _ghostshell_snapshot_pending_execution
    else
        _ghostshell_clear_pending_execution
    fi
    _ghostshell_clear_suggestions
    _ghostshell_reset_line_state
    zle .accept-line
}

# ======================================================
# 4. ZLE REGISTRATION (Must occur before binding)
# ======================================================

zle -N _ghostshell_update_display
zle -N _ghostshell_accept_widget
zle -N _ghostshell_cycle_next
zle -N _ghostshell_cycle_prev
zle -N _ghostshell_partial_accept
zle -N _ghostshell_manual_trigger
zle -N _ghostshell_accept_line
zle -N _ghostshell_down_line_or_history
zle -N _ghostshell_up_line_or_history
zle -N self-insert _ghostshell_self_insert
zle -N backward-delete-char _ghostshell_backward_delete_char
zle -N _ghostshell_interrupt
zle -N _ghostshell_escape
zle -N bracketed-paste _ghostshell_paste

# ======================================================
# 5. KEY BINDINGS
# ======================================================

_ghostshell_bind_widget() {
    local key="$1"
    local widget="$2"
    # Protect against empty widget args causing errors
    if [[ -n "$widget" ]]; then
        bindkey -M emacs "$key" "$widget"
        bindkey -M viins "$key" "$widget"
        bindkey -M vicmd "$key" "$widget"
    fi
}

_ghostshell_default_escape_widget() {
    local keymap="$1"
    case "$keymap" in
        viins) print -r -- "vi-cmd-mode" ;;
        *) print -r -- "undefined-key" ;;
    esac
}

_ghostshell_capture_native_escape_binding() {
    local keymap="$1"
    local binding
    local widget

    binding="$(bindkey -M "$keymap" '^[' 2>/dev/null)"
    widget="${binding##* }"

    if [[ -z "$widget" || "$widget" == "$binding" || "$widget" == "_ghostshell_escape" ]]; then
        widget="$(_ghostshell_default_escape_widget "$keymap")"
    fi

    if [[ -n "$widget" && "$widget" != "undefined-key" ]]; then
        GHOSTSHELL_NATIVE_ESC_WIDGET[$keymap]="$widget"
    else
        GHOSTSHELL_NATIVE_ESC_WIDGET[$keymap]=""
    fi
}

_ghostshell_capture_native_escape_binding emacs
_ghostshell_capture_native_escape_binding viins
_ghostshell_capture_native_escape_binding vicmd
_ghostshell_reload_disabled_patterns_if_needed
_ghostshell_reload_auth_token_if_needed
_ghostshell_ensure_ai_session_timer

# --- Core Controls ---
_ghostshell_bind_widget '^@' _ghostshell_manual_trigger    # Ctrl+Space (manual trigger)
_ghostshell_bind_widget '^I' _ghostshell_accept_widget     # Tab
_ghostshell_bind_widget '^P' _ghostshell_cycle_prev
_ghostshell_bind_widget '^N' _ghostshell_cycle_next
_ghostshell_bind_widget '^C' _ghostshell_interrupt
_ghostshell_bind_widget '^[' _ghostshell_escape
_ghostshell_bind_widget '^M' _ghostshell_accept_line       # Enter
_ghostshell_bind_widget '^[[A' _ghostshell_up_line_or_history
_ghostshell_bind_widget '^[[B' _ghostshell_down_line_or_history
_ghostshell_bind_widget '^OA' _ghostshell_up_line_or_history
_ghostshell_bind_widget '^OB' _ghostshell_down_line_or_history

# --- Partial Accept (Option+Right) ---
_ghostshell_bind_widget '^[[1;3C' _ghostshell_partial_accept
_ghostshell_bind_widget '^[[1;9C' _ghostshell_partial_accept
_ghostshell_bind_widget '^[f' _ghostshell_partial_accept

# ======================================================
# 6. SHELL LIFECYCLE HOOKS (Success-only learning)
# ======================================================
if (( GHOSTSHELL_HOOKS_REGISTERED == 0 )); then
    autoload -Uz add-zsh-hook
    add-zsh-hook preexec _ghostshell_preexec_hook
    add-zsh-hook precmd _ghostshell_precmd_hook
    GHOSTSHELL_HOOKS_REGISTERED=1
fi
