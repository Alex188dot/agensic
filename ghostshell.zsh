# GhostShell Zsh Plugin - New Paradigm
# Uses vector DB for suggestions, triggers on 0.2s pause, no trigger chars

typeset -g -a GHOSTSHELL_SUGGESTIONS
GHOSTSHELL_SUGGESTIONS=("" "" "")
typeset -g GHOSTSHELL_SUGGESTION_INDEX=1

# Pool of up to 20 suggestions from vector DB
typeset -g -a GHOSTSHELL_SUGGESTION_POOL
GHOSTSHELL_SUGGESTION_POOL=()

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
        GHOSTSHELL_SUGGESTION_POOL=()
        GHOSTSHELL_SUGGESTIONS=("" "" "")
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
        # Get both the top 3 suggestions and the full pool if available
        suggestions = result.get('suggestions', ['', '', ''])
        pool = result.get('pool', suggestions)  # Fallback to suggestions if no pool
        while len(suggestions) < 3: suggestions.append('')
        while len(pool) < 20: pool.append('')
        # Print suggestions|pool (separated by special delimiter)
        print('|'.join(suggestions[:3]) + '|||' + '|'.join(pool[:20]))
except Exception as e:
    print('||')
" 2>/dev/null)
    
    # Parse response: first 3 are current suggestions, rest is the pool
    if [[ "$response" == *"|||"* ]]; then
        local sugg_part="${response%%|||*}"
        local pool_part="${response##*|||}"
        GHOSTSHELL_SUGGESTIONS=("${(@s:|:)sugg_part}")
        GHOSTSHELL_SUGGESTION_POOL=("${(@s:|:)pool_part}")
    else
        GHOSTSHELL_SUGGESTIONS=("${(@s:|:)response}")
        GHOSTSHELL_SUGGESTION_POOL=("${GHOSTSHELL_SUGGESTIONS[@]}")
    fi
    
    GHOSTSHELL_SUGGESTION_INDEX=1
}

_ghostshell_filter_pool() {
    # Filter the suggestion pool based on current buffer
    # This is called as user types to narrow down options
    local buffer="$BUFFER"
    
    # If buffer is shorter than last fetch, clear pool (backspace)
    if [[ ${#buffer} -lt ${#GHOSTSHELL_LAST_BUFFER} ]]; then
        GHOSTSHELL_SUGGESTION_POOL=()
        return
    fi
    
    # Simple prefix filtering
    # We check if suggestions in pool start with the typed suffix
    # But since suggestions are suffixes themselves, we need logic.
    # Actually simpler: The pool contains suffixes valid for GHOSTSHELL_LAST_BUFFER.
    # We need to see if they are still valid for BUFFER.
    
    local typed_since_fetch="${buffer#$GHOSTSHELL_LAST_BUFFER}"
    
    # If user typed something not matching start of suggestions, filter
    local new_pool=()
    for sugg in "${GHOSTSHELL_SUGGESTION_POOL[@]}"; do
        if [[ "$sugg" == "$typed_since_fetch"* ]]; then
            # Keep it, but trim the typed part for display? 
            # No, keep full suffix, but display logic handles it?
            # actually we should update suggestions to reflect remaining part
            new_pool+=("$sugg")
        fi
    done
    
    if [[ ${#new_pool[@]} -gt 0 ]]; then
        GHOSTSHELL_SUGGESTION_POOL=("${new_pool[@]}")
        # Update current top 3 from pool
        GHOSTSHELL_SUGGESTIONS=("" "" "")
        [[ -n "${new_pool[1]}" ]] && GHOSTSHELL_SUGGESTIONS[1]="${new_pool[1]}"
        [[ -n "${new_pool[2]}" ]] && GHOSTSHELL_SUGGESTIONS[2]="${new_pool[2]}"
        [[ -n "${new_pool[3]}" ]] && GHOSTSHELL_SUGGESTIONS[3]="${new_pool[3]}"
        
        # Reset index if out of bounds
        GHOSTSHELL_SUGGESTION_INDEX=1
        
        _ghostshell_update_display
    else
        # Pool exhausted, maybe clear or fetch new?
        # For now clear
        GHOSTSHELL_SUGGESTIONS=("" "" "")
        _ghostshell_update_display
        
        # Trigger fetch immediately? 
        # Or wait for pause? Wait for pause is safer.
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
    
    if [[ -n "$current" ]]; then
        local others=0
        for s in "${GHOSTSHELL_SUGGESTIONS[@]}"; do
            [[ -n "$s" ]] && ((others++))
        done
        
        # Show pool count if we have more than 3
        local pool_count=${#GHOSTSHELL_SUGGESTION_POOL[@]}
        if [[ $pool_count -gt 3 ]]; then
            POSTDISPLAY="$current ($pool_count options, Ctrl+P/N to cycle)"
        elif [[ $others -gt 1 ]]; then
            POSTDISPLAY="$current (Ctrl+P / Ctrl+N to cycle)"
        else
            POSTDISPLAY="$current"
        fi
        region_highlight=("${#BUFFER} $((${#BUFFER} + ${#current})) fg=242")
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
        # Invalidate current display
        zle -I
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
    GHOSTSHELL_SUGGESTIONS=("" "" "")
    GHOSTSHELL_SUGGESTION_POOL=()
    _ghostshell_stop_timer
}

_ghostshell_self_insert() {
    zle .self-insert
    
    # Filter existing pool if we have one
    if [[ ${#GHOSTSHELL_SUGGESTION_POOL[@]} -gt 0 ]]; then
        _ghostshell_filter_pool
        _ghostshell_update_display
        zle -R
    fi
    
    # Check if pool is exhausted (no matches left)
    local has_matches=0
    for s in "${GHOSTSHELL_SUGGESTION_POOL[@]}"; do
        [[ -n "$s" ]] && has_matches=1 && break
    done
    
    # If pool is exhausted and buffer is long enough, start timer for AI
    if [[ $has_matches -eq 0 && ${#BUFFER} -ge 2 ]]; then
        _ghostshell_start_timer
    fi
}

_ghostshell_backward_delete_char() {
    zle .backward-delete-char
    
    # Clear suggestions and pool on delete
    _ghostshell_clear_suggestions
    
    # Start timer to re-fetch
    if [[ ${#BUFFER} -ge 2 ]]; then
        _ghostshell_start_timer
    fi
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
        _ghostshell_send_feedback "$BUFFER" "$current"
        BUFFER="${BUFFER}${current}"
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
        local first_word="${current%% *}"
        if [[ "$first_word" == "$current" ]]; then
             BUFFER="${BUFFER}${current}"
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
    local has_suggestion=0
    for s in "${GHOSTSHELL_SUGGESTIONS[@]}"; do [[ -n "$s" ]] && has_suggestion=1 && break; done

    if [[ $has_suggestion -eq 1 ]]; then
        GHOSTSHELL_SUGGESTION_INDEX=$(( GHOSTSHELL_SUGGESTION_INDEX % 3 + 1 ))
        _ghostshell_update_display
        zle -R
    else
        zle down-line-or-history
    fi
}

_ghostshell_cycle_prev() {
    local has_suggestion=0
    for s in "${GHOSTSHELL_SUGGESTIONS[@]}"; do [[ -n "$s" ]] && has_suggestion=1 && break; done

    if [[ $has_suggestion -eq 1 ]]; then
        GHOSTSHELL_SUGGESTION_INDEX=$(( (GHOSTSHELL_SUGGESTION_INDEX + 1) % 3 + 1 ))
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