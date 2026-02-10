# TermiMind Zsh Plugin - Ghost Text Autocomplete with Debug Logging
typeset -g TERMIMIND_SUGGESTION=""
typeset -g TERMIMIND_DEBUG=""

_termimind_fetch_suggestion() {
    local buffer_content="$BUFFER"
    local cwd="$PWD"
    
    # Python script to call the daemon (with error output)
    local response=$(python3 -c "
import urllib.request, json, sys

data = {
    'command_buffer': '''$buffer_content''', 
    'cursor_position': ${CURSOR},
    'working_directory': '''$cwd''',
    'shell': 'zsh'
}

try:
    req = urllib.request.Request(
        'http://127.0.0.1:22000/predict', 
        data=json.dumps(data).encode('utf-8'),
        headers={'Content-Type': 'application/json'}
    )
    with urllib.request.urlopen(req, timeout=2) as r:
        resp = json.load(r)
        suggestion = resp.get('suggestion', '')
        # Output in format: SUGGESTION|||DEBUG_INFO
        print(f\"{suggestion}|||Response: '{suggestion}' (len={len(suggestion)})\")
except Exception as e:
    print(f\"|||ERROR: {e}\")
" 2>&1)
    
    # Split response into suggestion and debug info
    local suggestion_part="${response%%|||*}"
    local debug_part="${response#*|||}"
    
    TERMIMIND_SUGGESTION="$suggestion_part"
    TERMIMIND_DEBUG="$debug_part"
}

_termimind_suggest_widget() {
    # Only trigger if buffer has at least 2 chars
    if [[ ${#BUFFER} -ge 2 ]]; then
        _termimind_fetch_suggestion
        
        # Display debug info below the line
        if [[ -n "$TERMIMIND_DEBUG" ]]; then
            echo ""
            echo -e "\e[33m[DEBUG] $TERMIMIND_DEBUG\e[0m"
            
            # Schedule clearing debug after 5 seconds
            (sleep 5 && zle && zle reset-prompt) &!
        fi
        
        # Display Ghost Text in gray
        if [[ -n "$TERMIMIND_SUGGESTION" ]]; then
            # Use zsh's color system properly
            # Color 244 is gray in 256-color palette
            POSTDISPLAY=$'\e[90m'"${TERMIMIND_SUGGESTION}"$'\e[0m'
            echo -e "\e[32m[GHOST TEXT SET] '$TERMIMIND_SUGGESTION'\e[0m"
        else
            POSTDISPLAY=""
            echo -e "\e[31m[NO SUGGESTION] Empty response\e[0m"
        fi
    else
        POSTDISPLAY=""
        TERMIMIND_SUGGESTION=""
    fi
    
    # CRITICAL: Force ZLE to redraw the line
    zle -R
}

_termimind_accept_widget() {
    if [[ -n "$TERMIMIND_SUGGESTION" ]]; then
        # Accept the suggestion
        BUFFER="${BUFFER}${TERMIMIND_SUGGESTION}"
        CURSOR=${#BUFFER}
        TERMIMIND_SUGGESTION=""
        POSTDISPLAY=""
        zle -R
    else
        # Fallback to normal Tab completion
        zle expand-or-complete
    fi
}

# Space key handler: insert space THEN fetch suggestion
_termimind_space_trigger() {
    # First insert the space character
    zle self-insert
    
    # Show that we're fetching
    echo ""
    echo -e "\e[36m[FETCHING] Buffer: '$BUFFER'\e[0m"
    
    # Then fetch and display suggestion
    _termimind_suggest_widget
}

# Register widgets
zle -N _termimind_suggest_widget
zle -N _termimind_accept_widget
zle -N _termimind_space_trigger

# Bind space to trigger suggestions
bindkey ' ' _termimind_space_trigger

# Bind Tab to accept suggestions
bindkey '^I' _termimind_accept_widget