# GhostShell Zsh Plugin - New Paradigm
# Uses vector DB for suggestions, triggers on 0.2s pause, no trigger chars

typeset -g -a GHOSTSHELL_SUGGESTIONS
GHOSTSHELL_SUGGESTIONS=()
typeset -g GHOSTSHELL_SUGGESTION_INDEX=1

# Timer for pause detection
typeset -g GHOSTSHELL_TIMER_PID=""
typeset -g GHOSTSHELL_LAST_BUFFER=""

# ======================================================
# 1. CORE LOGIC (Fetch, Display, Feedback)
# ======================================================

_ghostshell_fetch_suggestions() {
    local buffer_content="$BUFFER"
    local cwd="$PWD"
    
    # Don't fetch if buffer is too short
    if [[ ${#buffer_content} -lt 2 ]]; then
        GHOSTSHELL_SUGGESTIONS=()
        return
    fi
    
    # Escaping single quotes for python
    local escaped_buffer="${buffer_content//\'/\'\\\'\'}"
    
    local response=$(python3 -c "
import urllib.request, json, sys
data = {'command_buffer': '''$escaped_buffer''', 'cursor_position': ${CURSOR}, 'working_directory': '''$cwd''', 'shell': 'zsh'}
try:
    req = urllib.request.Request('http://127.0.0.1:22000/predict', data=json.dumps(data).encode('utf-8'), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=1.5) as r:
        result = json.load(r)
        # Get the pool of suggestions (usually 20)
        pool = result.get('pool', result.get('suggestions', []))
        # Filter out empty or duplicate strings and limit to 20
        seen = set()
        clean_pool = []
        for s in pool:
            if s and s not in seen:
                clean_pool.append(s)
                seen.add(s)
        print('|'.join(clean_pool[:20]))
except Exception as e:
    print('')
" 2>/dev/null)
    
    # Parse response into GHOSTSHELL_SUGGESTIONS array
    if [[ -n "$response" ]]; then
        GHOSTSHELL_SUGGESTIONS=("${(@s:|:)response}")
    else
        GHOSTSHELL_SUGGESTIONS=()
    fi
    
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
        # Pool exhausted, clear and wait for next pause
        GHOSTSHELL_SUGGESTIONS=()
        _ghostshell_update_display
        _ghostshell_start_timer
    fi
}

_ghostshell_send_feedback() {
    local buffer="$1"
    local accepted="$2"
    # Fire and forget feedback to server for learning
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
    # Log executed command to vector DB
    (
        local escaped_cmd="${command//\'/\'\\\'\'}"
        local json_data="{\"command\": \"$escaped_cmd\"}"
        curl -s -X POST "http://127.0.0.1:22000/log_command" \
             -H "Content-Type: application/json" \
             -d "$json_data" > /dev/null 2>&1
    ) &!
}

_ghostshell_update_display() {
    local current="${GHOSTSHELL_SUGGESTIONS[$GHOSTSHELL_SUGGESTION_INDEX]}"
    
    # If filtering happened, we might need a part of the suggestion
    # The suggestions are suffixes relative to GHOSTSHELL_LAST_BUFFER
    local typed_since_fetch="${BUFFER#$GHOSTSHELL_LAST_BUFFER}"
    local display_sugg="${current#$typed_since_fetch}"
    
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
    zle .self-insert
    
    # Filter existing pool if we have one
    if [[ ${#GHOSTSHELL_SUGGESTIONS[@]} -gt 0 ]]; then
        _ghostshell_filter_pool
        _ghostshell_update_display
        zle -R
    fi
    
    # If pool is exhausted and buffer is long enough, start timer for AI
    if [[ ${#GHOSTSHELL_SUGGESTIONS[@]} -eq 0 && ${#BUFFER} -ge 2 ]]; then
        _ghostshell_start_timer
    fi
}

_ghostshell_backward_delete_char() {
    zle .backward-delete-char
    
    # Clear suggestions and pool on delete
    _ghostshell_clear_suggestions
}

_ghostshell_interrupt() {
    _ghostshell_clear_suggestions
    zle .send-break
}

# --- Paste Handling ---
autoload -Uz bracketed-paste-magic
_ghostshell_paste() {
    _ghostshell_clear_suggestions
    zle .bracketed-paste
}

# --- Accept Suggestion ---
_ghostshell_accept_widget() {
    local current="${GHOSTSHELL_SUGGESTIONS[$GHOSTSHELL_SUGGESTION_INDEX]}"
    if [[ -n "$current" ]]; then
        local typed_since_fetch="${BUFFER#$GHOSTSHELL_LAST_BUFFER}"
        local to_add="${current#$typed_since_fetch}"
        _ghostshell_send_feedback "$BUFFER" "$to_add"
        BUFFER="${BUFFER}${to_add}"
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
    
    if [[ -n "$current" ]]; then
        local typed_since_fetch="${BUFFER#$GHOSTSHELL_LAST_BUFFER}"
        local remaining="${current#$typed_since_fetch}"
        local first_word="${remaining%% *}"
        if [[ "$first_word" == "$remaining" ]]; then
             BUFFER="${BUFFER}${remaining}"
        else
             BUFFER="${BUFFER}${first_word} "
        fi
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
        GHOSTSHELL_LAST_BUFFER="$BUFFER"
        _ghostshell_fetch_suggestions
        _ghostshell_update_display
        zle -R
    fi
}

# --- Accept Line (Execute Command) ---
_ghostshell_accept_line() {
    local cmd="$BUFFER"
    _ghostshell_clear_suggestions
    
    # Log the command to vector DB
    if [[ -n "$cmd" ]]; then
        _ghostshell_log_command "$cmd"
    fi
    
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

# NO MORE TRIGGER CHARACTERS - removed the loop that bound space, dot, etc.