# GhostShell Zsh Plugin
typeset -g -a GHOSTSHELL_SUGGESTIONS
GHOSTSHELL_SUGGESTIONS=("" "" "")
typeset -g GHOSTSHELL_SUGGESTION_INDEX=1

# ======================================================
# 1. CORE LOGIC (Fetch, Display, Feedback)
# ======================================================

_ghostshell_fetch_suggestion() {
    local buffer_content="$BUFFER"
    local cwd="$PWD"
    # Escaping single quotes for python
    local escaped_buffer="${buffer_content//\'/\'\\\'\'}"
    
    local response=$(python3 -c "
import urllib.request, json, sys
data = {'command_buffer': '''$escaped_buffer''', 'cursor_position': ${CURSOR}, 'working_directory': '''$cwd''', 'shell': 'zsh'}
try:
    req = urllib.request.Request('http://127.0.0.1:22000/predict', data=json.dumps(data).encode('utf-8'), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=0.8) as r:
        s = json.load(r).get('suggestions', ['', '', ''])
        while len(s) < 3: s.append('')
        print('|'.join(s[:3]))
except Exception as e:
    print('||')
" 2>/dev/null)
    
    GHOSTSHELL_SUGGESTIONS=("${(@s:|:)response}")
    GHOSTSHELL_SUGGESTION_INDEX=1
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

_ghostshell_update_display() {
    local current="${GHOSTSHELL_SUGGESTIONS[$GHOSTSHELL_SUGGESTION_INDEX]}"
    if [[ -n "$current" ]]; then
        local others=0
        for s in "${GHOSTSHELL_SUGGESTIONS[@]}"; do
            [[ -n "$s" ]] && ((others++))
        done
        
        if [[ $others -gt 1 ]]; then
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
# 2. WIDGET DEFINITIONS
# ======================================================

# --- Trigger Suggestion ---
_ghostshell_suggest_widget() {
    if [[ ${#BUFFER} -ge 2 ]]; then
        _ghostshell_fetch_suggestion
        _ghostshell_update_display
    else
        POSTDISPLAY=""
        region_highlight=()
    fi
    zle -R
}

# --- Trigger + Insert (For ., /, space, etc) ---
_ghostshell_trigger_and_insert() {
    zle .self-insert
    _ghostshell_suggest_widget
}

# --- Clear Suggestions ---
_ghostshell_clear_suggestions() {
    POSTDISPLAY=""
    region_highlight=()
    GHOSTSHELL_SUGGESTIONS=("" "" "")
}

_ghostshell_self_insert() {
    _ghostshell_clear_suggestions
    zle .self-insert
}

_ghostshell_backward_delete_char() {
    _ghostshell_clear_suggestions
    zle .backward-delete-char
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
    if [[ -z "$current" && ${#BUFFER} -ge 2 ]]; then
        _ghostshell_fetch_suggestion
        current="${GHOSTSHELL_SUGGESTIONS[$GHOSTSHELL_SUGGESTION_INDEX]}"
    fi

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

    if [[ $has_suggestion -eq 0 && ${#BUFFER} -ge 2 ]]; then
        _ghostshell_fetch_suggestion
        for s in "${GHOSTSHELL_SUGGESTIONS[@]}"; do [[ -n "$s" ]] && has_suggestion=1 && break; done
    fi

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

    if [[ $has_suggestion -eq 0 && ${#BUFFER} -ge 2 ]]; then
        _ghostshell_fetch_suggestion
        for s in "${GHOSTSHELL_SUGGESTIONS[@]}"; do [[ -n "$s" ]] && has_suggestion=1 && break; done
    fi

    if [[ $has_suggestion -eq 1 ]]; then
        GHOSTSHELL_SUGGESTION_INDEX=$(( (GHOSTSHELL_SUGGESTION_INDEX + 1) % 3 + 1 ))
        _ghostshell_update_display
        zle -R
    else
        zle up-line-or-history
    fi
}

# ======================================================
# 3. ZLE REGISTRATION (Must occur before binding)
# ======================================================

zle -N _ghostshell_suggest_widget
zle -N _ghostshell_accept_widget
zle -N _ghostshell_cycle_next
zle -N _ghostshell_cycle_prev
zle -N _ghostshell_partial_accept
zle -N self-insert _ghostshell_self_insert
zle -N backward-delete-char _ghostshell_backward_delete_char
zle -N _ghostshell_interrupt
zle -N bracketed-paste _ghostshell_paste
zle -N _ghostshell_trigger_and_insert

# ======================================================
# 4. KEY BINDINGS
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
_ghostshell_bind_widget '^@' _ghostshell_suggest_widget    # Ctrl+Space
_ghostshell_bind_widget '^I' _ghostshell_accept_widget     # Tab
_ghostshell_bind_widget '^P' _ghostshell_cycle_prev
_ghostshell_bind_widget '^N' _ghostshell_cycle_next
_ghostshell_bind_widget '^C' _ghostshell_interrupt

# --- Partial Accept (Option+Right) ---
_ghostshell_bind_widget '^[[1;3C' _ghostshell_partial_accept
_ghostshell_bind_widget '^[[1;9C' _ghostshell_partial_accept
_ghostshell_bind_widget '^[f' _ghostshell_partial_accept

# --- Triggers ---
for c in ' ' '.' '/' '-' '(' '=' ':'; do
    bindkey -M emacs -- "$c" _ghostshell_trigger_and_insert
    # Only bind viins if the keymap exists to prevent further noise
    if bindkey -M viins >/dev/null 2>&1; then
        bindkey -M viins -- "$c" _ghostshell_trigger_and_insert
    fi
done