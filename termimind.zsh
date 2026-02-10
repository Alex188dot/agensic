# Termimind Zsh Plugin
typeset -g TERMIMIND_SUGGESTION=""

_termimind_fetch_suggestion() {
    local buffer_content="$BUFFER"
    local cwd="$PWD"
    local response=$(python3 -c "
import urllib.request, json, sys
data = {'command_buffer': '''$buffer_content''', 'cursor_position': ${CURSOR}, 'working_directory': '''$cwd''', 'shell': 'zsh'}
try:
    req = urllib.request.Request('http://127.0.0.1:22000/predict', data=json.dumps(data).encode('utf-8'), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=1.0) as r:
        print(json.load(r).get('suggestion', ''), end='')
except: pass" 2>/dev/null)
    TERMIMIND_SUGGESTION="$response"
}

_termimind_suggest_widget() {
    if [[ ${#BUFFER} -ge 2 ]]; then
        _termimind_fetch_suggestion
        if [[ -n "$TERMIMIND_SUGGESTION" ]]; then
            POSTDISPLAY="$TERMIMIND_SUGGESTION"
            region_highlight=("${#BUFFER} $((${#BUFFER} + ${#POSTDISPLAY})) fg=242")
        else
            POSTDISPLAY=""
            region_highlight=()
        fi
    else
        POSTDISPLAY=""
        region_highlight=()
    fi
    zle -R
}

_termimind_self_insert() {
    POSTDISPLAY=""
    region_highlight=()
    TERMIMIND_SUGGESTION=""
    zle .self-insert
}

_termimind_accept_widget() {
    if [[ -n "$TERMIMIND_SUGGESTION" ]]; then
        BUFFER="${BUFFER}${TERMIMIND_SUGGESTION}"
        CURSOR=${#BUFFER}
        POSTDISPLAY=""
        region_highlight=()
        TERMIMIND_SUGGESTION=""
        zle -R
    else
        zle expand-or-complete
    fi
}

_termimind_space_trigger() {
    zle .self-insert
    _termimind_suggest_widget
}

zle -N _termimind_suggest_widget
zle -N _termimind_accept_widget
zle -N _termimind_space_trigger
zle -N self-insert _termimind_self_insert

bindkey ' ' _termimind_space_trigger
bindkey '^I' _termimind_accept_widget