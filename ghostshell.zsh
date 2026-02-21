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
typeset -g GHOSTSHELL_MAX_AUTO_AI_CALLS=4
typeset -g GHOSTSHELL_CTRL_SPACE_HINT="To trigger new LLM suggestions press Ctrl + Space"
typeset -g GHOSTSHELL_LINE_AUTO_AI_CALLS=0
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
typeset -g -a GHOSTSHELL_INTENT_OPTIONS
GHOSTSHELL_INTENT_OPTIONS=()
typeset -g GHOSTSHELL_INTENT_OPTION_INDEX=1
typeset -g GHOSTSHELL_INTENT_ACTIVE=0

# Timer for pause detection
typeset -g GHOSTSHELL_TIMER_PID=""
typeset -g GHOSTSHELL_LAST_BUFFER=""
typeset -g GHOSTSHELL_LAST_EXECUTED_CMD=""
typeset -g GHOSTSHELL_HOOKS_REGISTERED=0
typeset -gA GHOSTSHELL_NATIVE_ESC_WIDGET
GHOSTSHELL_NATIVE_ESC_WIDGET=()
typeset -g -a GHOSTSHELL_DISABLED_PATTERNS
GHOSTSHELL_DISABLED_PATTERNS=()
typeset -g GHOSTSHELL_CONFIG_PATH="${HOME}/.ghostshell/config.json"
typeset -g GHOSTSHELL_CONFIG_MTIME=""
typeset -g -a GHOSTSHELL_PATH_HEAVY_EXECUTABLES
GHOSTSHELL_PATH_HEAVY_EXECUTABLES=(cd ls cat less more head tail vi vim nvim nano code open source cp mv mkdir rmdir touch find grep rg sed awk bat)
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

_ghostshell_reload_disabled_patterns_if_needed() {
    local current_mtime
    current_mtime="$(_ghostshell_get_config_mtime)"
    if [[ "$current_mtime" == "$GHOSTSHELL_CONFIG_MTIME" ]]; then
        return
    fi
    GHOSTSHELL_CONFIG_MTIME="$current_mtime"
    GHOSTSHELL_DISABLED_PATTERNS=()

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

print('\x1f'.join(patterns))
" 2>/dev/null)

    if [[ -n "$response" ]]; then
        GHOSTSHELL_DISABLED_PATTERNS=("${(ps:$sep:)response}")
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
    if [[ "$token" =~ \.[A-Za-z0-9_-]+$ ]]; then
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

_ghostshell_fetch_suggestions() {
    local allow_ai="${1:-1}"
    local trigger_source="${2:-unknown}"
    local buffer_content="$BUFFER"
    local cwd="$PWD"
    local sep=$'\x1f'
    GHOSTSHELL_LAST_FETCH_USED_AI=0
    
    # Don't fetch if buffer is too short
    if [[ ${#buffer_content} -lt 2 ]]; then
        GHOSTSHELL_SUGGESTIONS=()
        GHOSTSHELL_DISPLAY_TEXTS=()
        GHOSTSHELL_ACCEPT_MODES=()
        GHOSTSHELL_SUGGESTION_KINDS=()
        return
    fi
    
    # Escaping single quotes for python
    local escaped_buffer="${buffer_content//\'/\'\\\'\'}"
    
    local response=$(python3 -c "
import urllib.request, json, sys
data = {
    'command_buffer': '''$escaped_buffer''',
    'cursor_position': ${CURSOR},
    'working_directory': '''$cwd''',
    'shell': 'zsh',
    'allow_ai': bool(int('${allow_ai}')),
    'trigger_source': '''$trigger_source''',
}
try:
    req = urllib.request.Request('http://127.0.0.1:22000/predict', data=json.dumps(data).encode('utf-8'), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=1.5) as r:
        result = json.load(r)
        used_ai = bool(result.get('used_ai', False))
        # Get structured suggestions when available.
        pool = result.get('pool', result.get('suggestions', []))
        pool_meta = result.get('pool_meta', [])
        bootstrap = result.get('bootstrap', {})
        # Filter out empty or duplicate strings and limit to 20
        seen = set()
        clean_pool = []
        clean_display = []
        clean_modes = []
        clean_kinds = []
        if isinstance(pool_meta, list):
            for item in pool_meta:
                if not isinstance(item, dict):
                    continue
                accept_text = str(item.get('accept_text', '') or '')
                if not accept_text or accept_text in seen:
                    continue
                seen.add(accept_text)
                clean_pool.append(accept_text)
                clean_display.append(str(item.get('display_text', accept_text) or accept_text))
                clean_modes.append(str(item.get('accept_mode', 'suffix_append') or 'suffix_append'))
                clean_kinds.append(str(item.get('kind', 'normal') or 'normal'))
                if len(clean_pool) >= 20:
                    break
        if not clean_pool:
            for s in pool:
                if s and s not in seen:
                    clean_pool.append(s)
                    clean_display.append(s)
                    clean_modes.append('suffix_append')
                    clean_kinds.append('normal')
                    seen.add(s)
                if len(clean_pool) >= 20:
                    break
        # If bootstrap is still running and we don't yet have suggestions,
        # return a non-actionable status ghost text.
        if not clean_pool and bootstrap.get('running'):
            clean_pool = ['__GHOSTSHELL_STATUS__: ** Index is still loading, suggestions coming in a few seconds **']
            clean_display = clean_pool[:]
            clean_modes = ['suffix_append']
            clean_kinds = ['status']
        print(
            ('1' if used_ai else '0') + '\n' +
            '\x1f'.join(clean_pool[:20]) + '\n' +
            '\x1f'.join(clean_display[:20]) + '\n' +
            '\x1f'.join(clean_modes[:20]) + '\n' +
            '\x1f'.join(clean_kinds[:20])
        )
except Exception as e:
    print('0')
" 2>/dev/null)
    
    # Parse response into GHOSTSHELL_SUGGESTIONS array and used_ai flag.
    local -a response_lines
    response_lines=("${(@f)response}")
    local used_ai_line="${response_lines[1]}"
    local pool_line="${response_lines[2]}"
    local display_line="${response_lines[3]}"
    local mode_line="${response_lines[4]}"
    local kind_line="${response_lines[5]}"

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
    local response
    response=$(python3 -c "
import urllib.request, json, shlex

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
    req = urllib.request.Request('http://127.0.0.1:22000/intent', data=json.dumps(payload).encode('utf-8'), headers={'Content-Type': 'application/json'})
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
except Exception:
    status = 'error'
    primary = ''
    explanation = 'Could not resolve command mode right now.'
    alternatives_blob = ''
    copy_block = ''
print('status=' + shlex.quote(status))
print('primary=' + shlex.quote(primary))
print('explanation=' + shlex.quote(explanation))
print('alternatives=' + shlex.quote(alternatives_blob))
print('copy_block=' + shlex.quote(copy_block))
" 2>/dev/null)

    local nl_status=""
    local nl_primary=""
    local nl_explanation=""
    local nl_alternatives=""
    local nl_copy_block=""
    response="${response//status=/nl_status=}"
    response="${response//primary=/nl_primary=}"
    response="${response//explanation=/nl_explanation=}"
    response="${response//alternatives=/nl_alternatives=}"
    response="${response//copy_block=/nl_copy_block=}"
    eval "$response"

    if [[ "$nl_status" != "ok" || -z "$nl_primary" ]]; then
        _ghostshell_print_intent_refusal "$body" "${nl_explanation:-No command generated.}"
        _ghostshell_reset_intent_state
        zle -R
        return 1
    fi

    BUFFER="$nl_primary"
    CURSOR=${#BUFFER}
    GHOSTSHELL_LAST_NL_INPUT="$raw"
    GHOSTSHELL_LAST_NL_KIND="intent"
    GHOSTSHELL_LAST_NL_QUESTION="$body"
    GHOSTSHELL_LAST_NL_COMMAND="$nl_primary"
    GHOSTSHELL_LAST_NL_EXPLANATION="$nl_explanation"
    GHOSTSHELL_LAST_NL_ALTERNATIVES="$nl_alternatives"
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
    local answer
    answer=$(python3 -c "
import urllib.request, json
payload = {
    'prompt_text': '''$escaped_body''',
    'working_directory': '''$escaped_pwd''',
    'shell': 'zsh',
    'terminal': '''$escaped_term''',
    'platform': '''$escaped_platform''',
}
try:
    req = urllib.request.Request('http://127.0.0.1:22000/assist', data=json.dumps(payload).encode('utf-8'), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=4.0) as r:
        result = json.load(r)
    answer = str(result.get('answer', '') or '').replace('\\r', ' ').strip()
except Exception:
    answer = 'Could not fetch assistant reply right now.'
print(answer)
" 2>/dev/null)

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

_ghostshell_send_feedback() {
    local buffer="$1"
    local accepted="$2"
    local accept_mode="${3:-suffix_append}"
    if _ghostshell_matches_disabled_pattern "$buffer" || _ghostshell_matches_disabled_pattern "$accepted"; then
        return
    fi
    # Fire and forget feedback to server for zvec feedback stats
    (
        local escaped_buf="${buffer//\'/\'\\\'\'}"
        local escaped_acc="${accepted//\'/\'\\\'\'}"
        local json_data="{\"command_buffer\": \"$escaped_buf\", \"accepted_suggestion\": \"$escaped_acc\", \"accept_mode\": \"${accept_mode}\"}"
        curl -s -X POST "http://127.0.0.1:22000/feedback" \
             -H "Content-Type: application/json" \
             -d "$json_data" > /dev/null 2>&1
    ) &!
}

_ghostshell_log_command() {
    local command="$1"
    local exit_code="$2"
    local source="${3:-runtime}"
    # Log executed command to vector DB
    (
        local escaped_cmd="${command//\'/\'\\\'\'}"
        local json_data="{\"command\": \"$escaped_cmd\", \"exit_code\": ${exit_code}, \"source\": \"${source}\"}"
        curl -s -X POST "http://127.0.0.1:22000/log_command" \
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
    GHOSTSHELL_LAST_EXECUTED_CMD="$1"
}

_ghostshell_precmd_hook() {
    local exit_code="$?"
    local cmd="$GHOSTSHELL_LAST_EXECUTED_CMD"
    GHOSTSHELL_LAST_EXECUTED_CMD=""
    _ghostshell_reload_disabled_patterns_if_needed

    if [[ -z "$cmd" ]]; then
        return
    fi

    if [[ "$exit_code" -ne 0 ]]; then
        return
    fi

    if _ghostshell_is_blocked_runtime_command "$cmd"; then
        return
    fi
    if _ghostshell_matches_disabled_pattern "$cmd"; then
        return
    fi

    _ghostshell_log_command "$cmd" "$exit_code" "runtime"
}

_ghostshell_reset_line_state() {
    GHOSTSHELL_LINE_AUTO_AI_CALLS=0
    GHOSTSHELL_LINE_HAS_SPACE=0
    GHOSTSHELL_SHOW_CTRL_SPACE_HINT=0
    GHOSTSHELL_LAST_FETCH_USED_AI=0
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
    local manual_allow_ai="${2:-1}"
    local trigger_source="space_auto"

    if [[ "$is_manual" != "1" ]] && _ghostshell_should_preserve_native_tab; then
        _ghostshell_clear_suggestions
        GHOSTSHELL_SHOW_CTRL_SPACE_HINT=0
        GHOSTSHELL_LAST_FETCH_USED_AI=0
        return
    fi

    if [[ "$is_manual" == "1" ]]; then
        allow_ai="$manual_allow_ai"
        trigger_source="manual_ctrl_space"
    else
        if (( GHOSTSHELL_LINE_AUTO_AI_CALLS >= GHOSTSHELL_MAX_AUTO_AI_CALLS )); then
            allow_ai=0
        fi
    fi

    GHOSTSHELL_LAST_BUFFER="$BUFFER"
    _ghostshell_fetch_suggestions "$allow_ai" "$trigger_source"

    if [[ "$is_manual" != "1" ]]; then
        if (( GHOSTSHELL_LAST_FETCH_USED_AI == 1 )); then
            GHOSTSHELL_LINE_AUTO_AI_CALLS=$((GHOSTSHELL_LINE_AUTO_AI_CALLS + 1))
        fi
        if (( allow_ai == 0 && ${#GHOSTSHELL_SUGGESTIONS[@]} == 0 )); then
            GHOSTSHELL_SHOW_CTRL_SPACE_HINT=1
            _ghostshell_set_status_message "$GHOSTSHELL_CTRL_SPACE_HINT"
        elif [[ "${GHOSTSHELL_SUGGESTIONS[1]}" != "${GHOSTSHELL_STATUS_PREFIX}${GHOSTSHELL_CTRL_SPACE_HINT}" ]]; then
            GHOSTSHELL_SHOW_CTRL_SPACE_HINT=0
        fi
    else
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
# 2. PAUSE DETECTION (0.2s timer)
# ======================================================

_ghostshell_start_timer() {
    # Kill existing timer if any
    _ghostshell_stop_timer
    
    # Start a background timer that triggers after 0.2s
    (
        sleep 0.2
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
    # This is called when the 0.2s timer expires
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
    if _ghostshell_is_status_suggestion "$current"; then
        zle expand-or-complete
    elif [[ -n "$current" ]]; then
        if [[ "$mode" == "replace_full" ]]; then
            local normalized_buffer="$(_ghostshell_canonicalize_buffer_spacing "$BUFFER")"
            local replacement="$(_ghostshell_canonicalize_buffer_spacing "$current")"
            _ghostshell_send_feedback "$normalized_buffer" "$replacement" "replace_full"
            BUFFER="$replacement"
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
    
    if _ghostshell_is_status_suggestion "$current"; then
        zle forward-word
    elif [[ "$mode" == "replace_full" ]]; then
        BUFFER="$(_ghostshell_canonicalize_buffer_spacing "$current")"
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

# --- Manual Trigger (Ctrl+Space) ---
_ghostshell_manual_trigger() {
    if [[ ${#BUFFER} -ge 2 ]]; then
        if _ghostshell_should_skip_ghostshell_for_buffer; then
            _ghostshell_clear_suggestions
            _ghostshell_update_display
            zle -R
            return
        fi
        local manual_allow_ai=0
        if [[ "$BUFFER" == *[[:space:]]* ]]; then
            manual_allow_ai=1
        fi
        _ghostshell_try_fetch_on_space 1 "$manual_allow_ai"
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

# --- Core Controls ---
_ghostshell_bind_widget '^@' _ghostshell_manual_trigger    # Ctrl+Space (manual trigger)
_ghostshell_bind_widget '^I' _ghostshell_accept_widget     # Tab
_ghostshell_bind_widget '^P' _ghostshell_cycle_prev
_ghostshell_bind_widget '^N' _ghostshell_cycle_next
_ghostshell_bind_widget '^C' _ghostshell_interrupt
_ghostshell_bind_widget '^[' _ghostshell_escape
_ghostshell_bind_widget '^M' _ghostshell_accept_line       # Enter

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
