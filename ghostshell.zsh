# GhostShell Zsh Plugin
typeset -g -a GHOSTSHELL_SUGGESTIONS
GHOSTSHELL_SUGGESTIONS=("" "" "")
typeset -g GHOSTSHELL_SUGGESTION_INDEX=1

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
    with urllib.request.urlopen(req, timeout=1.0) as r:
        s = json.load(r).get('suggestions', ['', '', ''])
        # Ensure we always output 3 parts
        while len(s) < 3: s.append('')
        print('|'.join(s[:3]))
except Exception as e:
    print('||')
" 2>/dev/null)
    
    # Split using (ps:|:) which is Zsh's flag for splitting and keeping empty fields
    GHOSTSHELL_SUGGESTIONS=("${(@s:|:)response}")
    GHOSTSHELL_SUGGESTION_INDEX=1
}

_ghostshell_update_display() {
    local current="${GHOSTSHELL_SUGGESTIONS[$GHOSTSHELL_SUGGESTION_INDEX]}"
    if [[ -n "$current" ]]; then
        # Check if there are other suggestions
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

_ghostshell_self_insert() {
    POSTDISPLAY=""
    region_highlight=()
    GHOSTSHELL_SUGGESTIONS=("" "" "")
    zle .self-insert
}

_ghostshell_accept_widget() {
    local current="${GHOSTSHELL_SUGGESTIONS[$GHOSTSHELL_SUGGESTION_INDEX]}"
    if [[ -n "$current" ]]; then
        BUFFER="${BUFFER}${current}"
        CURSOR=${#BUFFER}
        POSTDISPLAY=""
        region_highlight=()
        GHOSTSHELL_SUGGESTIONS=("" "" "")
        zle -R
    else
        zle expand-or-complete
    fi
}

_ghostshell_partial_accept() {
    local current="${GHOSTSHELL_SUGGESTIONS[$GHOSTSHELL_SUGGESTION_INDEX]}"
    if [[ -z "$current" && ${#BUFFER} -ge 2 ]]; then
        _ghostshell_fetch_suggestion
        current="${GHOSTSHELL_SUGGESTIONS[$GHOSTSHELL_SUGGESTION_INDEX]}"
    fi

    if [[ -n "$current" ]]; then
        # Take the first word
        local first_word="${current%% *}"
        if [[ "$first_word" == "$current" ]]; then
             BUFFER="${BUFFER}${current}"
        else
             BUFFER="${BUFFER}${first_word} "
        fi
        CURSOR=${#BUFFER}
        GHOSTSHELL_SUGGESTIONS=("" "" "")
        POSTDISPLAY=""
        region_highlight=()
        zle -R
    else
        zle forward-word
    fi
}

_ghostshell_cycle_next() {
    local has_suggestion=0
    for s in "${GHOSTSHELL_SUGGESTIONS[@]}"; do
        [[ -n "$s" ]] && has_suggestion=1 && break
    done

    if [[ $has_suggestion -eq 0 && ${#BUFFER} -ge 2 ]]; then
        _ghostshell_fetch_suggestion
        for s in "${GHOSTSHELL_SUGGESTIONS[@]}"; do
            [[ -n "$s" ]] && has_suggestion=1 && break
        done
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
    for s in "${GHOSTSHELL_SUGGESTIONS[@]}"; do
        [[ -n "$s" ]] && has_suggestion=1 && break
    done

    if [[ $has_suggestion -eq 0 && ${#BUFFER} -ge 2 ]]; then
        _ghostshell_fetch_suggestion
        for s in "${GHOSTSHELL_SUGGESTIONS[@]}"; do
            [[ -n "$s" ]] && has_suggestion=1 && break
        done
    fi

    if [[ $has_suggestion -eq 1 ]]; then
        GHOSTSHELL_SUGGESTION_INDEX=$(( (GHOSTSHELL_SUGGESTION_INDEX + 1) % 3 + 1 ))
        _ghostshell_update_display
        zle -R
    else
        zle up-line-or-history
    fi
}

_ghostshell_ai_panel() {
    if [[ ${#BUFFER} -ge 2 && -z "${GHOSTSHELL_SUGGESTIONS[1]}" && -z "${GHOSTSHELL_SUGGESTIONS[2]}" && -z "${GHOSTSHELL_SUGGESTIONS[3]}" ]]; then
        _ghostshell_fetch_suggestion
    fi

    local has_suggestion=0
    for s in "${GHOSTSHELL_SUGGESTIONS[@]}"; do
        [[ -n "$s" ]] && has_suggestion=1 && break
    done
    if [[ $has_suggestion -eq 0 ]]; then
        zle -M "No AI suggestions available for this input."
        return
    fi

    local selected=1
    local key
    
    while true; do
        local msg="--- GhostShell AI Panel ---"
        for i in 1 2 3; do
            local mark="  "
            [[ $i -eq $selected ]] && mark="> "
            local line="${GHOSTSHELL_SUGGESTIONS[$i]}"
            [[ -z "$line" ]] && line="(empty)"
            msg+="\n$mark${line}"
        done
        msg+="\n(Up/Down to toggle, Enter to choose, Esc to cancel)"
        
        zle -M "$msg"
        
        read -k 1 key
        if [[ "$key" == $'\x1b' ]]; then
            # Peek for arrow keys
            read -k 2 -t 0.1 rest
            if [[ "$rest" == "[A" ]]; then # Up
                selected=$(( (selected + 1) % 3 + 1 ))
            elif [[ "$rest" == "[B" ]]; then # Down
                selected=$(( selected % 3 + 1 ))
            else
                zle -M ""
                break
            fi
        elif [[ "$key" == $'\x0d' ]]; then # Enter
            GHOSTSHELL_SUGGESTION_INDEX=$selected
            _ghostshell_accept_widget
            zle -M ""
            break
        else
            zle -M ""
            break
        fi
    done
}

_ghostshell_space_trigger() {
    zle .self-insert
    _ghostshell_suggest_widget
}

zle -N _ghostshell_suggest_widget
zle -N _ghostshell_accept_widget
zle -N _ghostshell_space_trigger
zle -N _ghostshell_cycle_next
zle -N _ghostshell_cycle_prev
zle -N _ghostshell_partial_accept
zle -N _ghostshell_ai_panel
zle -N self-insert _ghostshell_self_insert

_ghostshell_bind_widget() {
    local key="$1"
    local widget="$2"
    bindkey -M emacs "$key" "$widget"
    bindkey -M viins "$key" "$widget"
    bindkey -M vicmd "$key" "$widget"
}

# Trigger suggestion: Ctrl+Space (^@)
_ghostshell_bind_widget '^@' _ghostshell_suggest_widget

# Partial Accept (Word-by-Word): Ctrl+Right, with Option+Right fallback
_ghostshell_bind_widget '^[[1;5C' _ghostshell_partial_accept
_ghostshell_bind_widget '^[[5C' _ghostshell_partial_accept
_ghostshell_bind_widget '^[[1;3C' _ghostshell_partial_accept
_ghostshell_bind_widget '^[f' _ghostshell_partial_accept

# Cycle suggestions (non-ambiguous): Ctrl+P / Ctrl+N
_ghostshell_bind_widget '^P' _ghostshell_cycle_prev
_ghostshell_bind_widget '^N' _ghostshell_cycle_next

# AI Panel: Ctrl+G, Ctrl+X then A, Option+J fallback
_ghostshell_bind_widget '^G' _ghostshell_ai_panel
_ghostshell_bind_widget '^Xa' _ghostshell_ai_panel
_ghostshell_bind_widget '^[j' _ghostshell_ai_panel

_ghostshell_bind_widget ' ' _ghostshell_space_trigger
_ghostshell_bind_widget '^I' _ghostshell_accept_widget
