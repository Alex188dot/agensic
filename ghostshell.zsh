# GhostShell Zsh Plugin - Space-triggered LLM fallback model

typeset -g -a GHOSTSHELL_SUGGESTIONS
GHOSTSHELL_SUGGESTIONS=()
typeset -g GHOSTSHELL_SUGGESTION_INDEX=1
typeset -g GHOSTSHELL_STATUS_PREFIX="__GHOSTSHELL_STATUS__:"
typeset -g GHOSTSHELL_MAX_AUTO_AI_CALLS=4
typeset -g GHOSTSHELL_CTRL_SPACE_HINT="To trigger new LLM suggestions press Ctrl + Space"
typeset -g GHOSTSHELL_LINE_AUTO_AI_CALLS=0
typeset -g GHOSTSHELL_LINE_HAS_SPACE=0
typeset -g GHOSTSHELL_SHOW_CTRL_SPACE_HINT=0
typeset -g GHOSTSHELL_LAST_FETCH_USED_AI=0

# Timer for pause detection
typeset -g GHOSTSHELL_TIMER_PID=""
typeset -g GHOSTSHELL_LAST_BUFFER=""
typeset -g GHOSTSHELL_LAST_EXECUTED_CMD=""
typeset -g GHOSTSHELL_HOOKS_REGISTERED=0

# ======================================================
# 1. CORE LOGIC (Fetch, Display, Feedback)
# ======================================================

_ghostshell_fetch_suggestions() {
    local allow_ai="${1:-1}"
    local buffer_content="$BUFFER"
    local cwd="$PWD"
    GHOSTSHELL_LAST_FETCH_USED_AI=0
    
    # Don't fetch if buffer is too short
    if [[ ${#buffer_content} -lt 2 ]]; then
        GHOSTSHELL_SUGGESTIONS=()
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
}
try:
    req = urllib.request.Request('http://127.0.0.1:22000/predict', data=json.dumps(data).encode('utf-8'), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=1.5) as r:
        result = json.load(r)
        used_ai = bool(result.get('used_ai', False))
        # Get the pool of suggestions (usually 20)
        pool = result.get('pool', result.get('suggestions', []))
        bootstrap = result.get('bootstrap', {})
        # Filter out empty or duplicate strings and limit to 20
        seen = set()
        clean_pool = []
        for s in pool:
            if s and s not in seen:
                clean_pool.append(s)
                seen.add(s)
        # If bootstrap is still running and we don't yet have suggestions,
        # return a non-actionable status ghost text.
        if not clean_pool and bootstrap.get('running'):
            clean_pool = ['__GHOSTSHELL_STATUS__: ** Index is still loading, suggestions coming in a few seconds **']
        print(('1' if used_ai else '0') + '\n' + '|'.join(clean_pool[:20]))
except Exception as e:
    print('0')
" 2>/dev/null)
    
    # Parse response into GHOSTSHELL_SUGGESTIONS array and used_ai flag.
    local used_ai_line="${response%%$'\n'*}"
    local pool_line=""
    if [[ "$response" == *$'\n'* ]]; then
        pool_line="${response#*$'\n'}"
    fi

    if [[ "$used_ai_line" == "1" ]]; then
        GHOSTSHELL_LAST_FETCH_USED_AI=1
    fi

    if [[ -n "$pool_line" ]]; then
        GHOSTSHELL_SUGGESTIONS=("${(@s:|:)pool_line}")
    else
        GHOSTSHELL_SUGGESTIONS=()
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
        GHOSTSHELL_SUGGESTION_INDEX=1
        return
    fi
    GHOSTSHELL_SUGGESTIONS=("${GHOSTSHELL_STATUS_PREFIX}${message}")
    GHOSTSHELL_SUGGESTION_INDEX=1
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
    local new_suggestions=()
    for sugg in "${GHOSTSHELL_SUGGESTIONS[@]}"; do
        if [[ "$sugg" == "$typed_since_fetch"* ]]; then
            new_suggestions+=("$sugg")
        fi
    done
    
    if [[ ${#new_suggestions[@]} -gt 0 ]]; then
        GHOSTSHELL_SUGGESTIONS=("${new_suggestions[@]}")
        GHOSTSHELL_SUGGESTION_INDEX=1
        _ghostshell_update_display
    else
        # Pool exhausted; wait for the next explicit trigger.
        GHOSTSHELL_SUGGESTIONS=()
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
    # Fire and forget feedback to server for zvec feedback stats
    (
        local escaped_buf="${buffer//\'/\'\\\'\'}"
        local escaped_acc="${accepted//\'/\'\\\'\'}"
        local json_data="{\"command_buffer\": \"$escaped_buf\", \"accepted_suggestion\": \"$escaped_acc\"}"
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
    [[ "$exe" == "rm" ]]
}

_ghostshell_preexec_hook() {
    GHOSTSHELL_LAST_EXECUTED_CMD="$1"
}

_ghostshell_precmd_hook() {
    local exit_code="$?"
    local cmd="$GHOSTSHELL_LAST_EXECUTED_CMD"
    GHOSTSHELL_LAST_EXECUTED_CMD=""

    if [[ -z "$cmd" ]]; then
        return
    fi

    if [[ "$exit_code" -ne 0 ]]; then
        return
    fi

    if _ghostshell_is_blocked_runtime_command "$cmd"; then
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
    local allow_ai=1
    local is_manual="${1:-0}"
    local manual_allow_ai="${2:-1}"

    if [[ "$is_manual" == "1" ]]; then
        allow_ai="$manual_allow_ai"
    else
        if (( GHOSTSHELL_LINE_AUTO_AI_CALLS >= GHOSTSHELL_MAX_AUTO_AI_CALLS )); then
            allow_ai=0
        fi
    fi

    GHOSTSHELL_LAST_BUFFER="$BUFFER"
    _ghostshell_fetch_suggestions "$allow_ai"

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
    if _ghostshell_is_status_suggestion "$current"; then
        local status_msg="${current#$GHOSTSHELL_STATUS_PREFIX}"
        status_msg="${status_msg//$'\n'/}"
        status_msg="${status_msg//$'\r'/}"
        POSTDISPLAY="$status_msg"
        region_highlight=("${#BUFFER} $((${#BUFFER} + ${#POSTDISPLAY})) fg=242")
        return
    fi
    
    # If filtering happened, we might need a part of the suggestion
    # The suggestions are suffixes relative to GHOSTSHELL_LAST_BUFFER
    local typed_since_fetch="${BUFFER#$GHOSTSHELL_LAST_BUFFER}"
    local display_sugg="${current#$typed_since_fetch}"
    display_sugg="$(_ghostshell_merge_suffix "$BUFFER" "$display_sugg")"
    
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
    
    # Only fetch if buffer has changed and is long enough
    if [[ "$BUFFER" != "$GHOSTSHELL_LAST_BUFFER" && ${#BUFFER} -ge 2 ]]; then
        GHOSTSHELL_LAST_BUFFER="$BUFFER"
        _ghostshell_fetch_suggestions
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
    if [[ -n "$WIDGET" ]]; then
        POSTDISPLAY=""
        region_highlight=()
    fi
    GHOSTSHELL_SUGGESTIONS=()
    _ghostshell_stop_timer
}

_ghostshell_self_insert() {
    local inserted_key="$KEYS"
    zle .self-insert

    _ghostshell_maybe_reset_line_state_for_empty_buffer

    # Filter existing pool if we have one
    if [[ ${#GHOSTSHELL_SUGGESTIONS[@]} -gt 0 ]]; then
        _ghostshell_filter_pool
        _ghostshell_update_display
        zle -R
    fi

    # Auto fetch only when user presses space (new command segment boundary).
    if [[ "$inserted_key" == " " && ${#BUFFER} -ge 2 ]]; then
        GHOSTSHELL_LINE_HAS_SPACE=1
        _ghostshell_try_fetch_on_space 0
        _ghostshell_update_display
        zle -R
    fi
}

_ghostshell_backward_delete_char() {
    zle .backward-delete-char

    # Clear suggestions and pool on delete
    _ghostshell_clear_suggestions
    _ghostshell_maybe_reset_line_state_for_empty_buffer
}

_ghostshell_interrupt() {
    _ghostshell_clear_suggestions
    _ghostshell_reset_line_state
    zle .send-break
}

# --- Paste Handling ---
autoload -Uz bracketed-paste-magic
_ghostshell_paste() {
    _ghostshell_clear_suggestions
    zle .bracketed-paste
    _ghostshell_maybe_reset_line_state_for_empty_buffer
}

# --- Accept Suggestion ---
_ghostshell_accept_widget() {
    local current="${GHOSTSHELL_SUGGESTIONS[$GHOSTSHELL_SUGGESTION_INDEX]}"
    if _ghostshell_is_status_suggestion "$current"; then
        zle expand-or-complete
    elif [[ -n "$current" ]]; then
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
        _ghostshell_send_feedback "$normalized_buffer" "$normalized_to_add"
        BUFFER="$normalized_merged"
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
    
    if _ghostshell_is_status_suggestion "$current"; then
        zle forward-word
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

# --- Core Controls ---
_ghostshell_bind_widget '^@' _ghostshell_manual_trigger    # Ctrl+Space (manual trigger)
_ghostshell_bind_widget '^I' _ghostshell_accept_widget     # Tab
_ghostshell_bind_widget '^P' _ghostshell_cycle_prev
_ghostshell_bind_widget '^N' _ghostshell_cycle_next
_ghostshell_bind_widget '^C' _ghostshell_interrupt
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
