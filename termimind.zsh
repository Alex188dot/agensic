# TermiMind Zsh Plugin
typeset -g TERMIMIND_SUGGESTION=""

_termimind_fetch_suggestion() {
    local buffer_content="$BUFFER"
    local cwd="$PWD"
    
    # We send request to port 22000
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
    with urllib.request.urlopen(req, timeout=2.0) as r:
        print(json.load(r)['suggestion'])
except Exception as e:
    # If connection fails, print nothing so terminal doesn't break
    pass
")
    
    TERMIMIND_SUGGESTION="$response"
}

_termimind_suggest_widget() {
    if [[ -n "$BUFFER" ]]; then
        _termimind_fetch_suggestion
        if [[ -n "$TERMIMIND_SUGGESTION" ]]; then
            # Gray text (38;5;244)
            POSTDISPLAY=$'\n\e[38;5;244m-> '"${TERMIMIND_SUGGESTION}"$'\e[0m'
        else
            POSTDISPLAY=""
        fi
    else
        POSTDISPLAY=""
    fi
}

_termimind_accept_widget() {
    if [[ -n "$TERMIMIND_SUGGESTION" ]]; then
        BUFFER="${BUFFER}${TERMIMIND_SUGGESTION}"
        CURSOR=${#BUFFER}
        TERMIMIND_SUGGESTION=""
        POSTDISPLAY=""
    else
        zle expand-or-complete
    fi
}

zle -N _termimind_suggest_widget
zle -N _termimind_accept_widget

# Trigger on Space
_termimind_space_trigger() {
    zle self-insert
    _termimind_suggest_widget
}
zle -N _termimind_space_trigger

bindkey ' ' _termimind_space_trigger
bindkey '^I' _termimind_accept_widget