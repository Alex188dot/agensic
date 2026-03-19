#!/usr/bin/env bash

# Agensic Bash adapter entrypoint.
# This currently handles shared helper loading and ble.sh discovery/attach.
# Inline suggestion rendering and keybindings will be layered on top of this.

if [[ -n "${BASH_SOURCE[0]:-}" ]]; then
    AGENSIC_SOURCE_PATH="${BASH_SOURCE[0]}"
elif [[ -z "${AGENSIC_SOURCE_PATH:-}" ]]; then
    AGENSIC_STATE_HOME="${XDG_STATE_HOME:-${HOME}/.local/state}"
    AGENSIC_SOURCE_PATH="${AGENSIC_STATE_HOME}/agensic/install/agensic.bash"
fi

AGENSIC_SOURCE_DIR="$(cd "$(dirname "${AGENSIC_SOURCE_PATH}")" && pwd)"
AGENSIC_CONFIG_HOME="${XDG_CONFIG_HOME:-${HOME}/.config}"
AGENSIC_STATE_HOME="${XDG_STATE_HOME:-${HOME}/.local/state}"
AGENSIC_HOME="${AGENSIC_STATE_HOME}/agensic"
AGENSIC_CONFIG_PATH="${AGENSIC_CONFIG_HOME}/agensic/config.json"
AGENSIC_PLUGIN_LOG="${AGENSIC_HOME}/plugin.log"
AGENSIC_SHARED_HELPERS_PATH="${AGENSIC_SOURCE_DIR}/shell/agensic_shared.sh"
AGENSIC_BLE_SH_PATH="${AGENSIC_BLE_SH_PATH:-}"
AGENSIC_BASH_ADAPTER_READY=0
AGENSIC_BASH_BLE_AVAILABLE=0
AGENSIC_BASH_BLE_LOADED_FROM=""
AGENSIC_BASH_BLE_WARNING_EMITTED=0

if [[ -f "$AGENSIC_SHARED_HELPERS_PATH" ]]; then
    # shellcheck disable=SC1090
    source "$AGENSIC_SHARED_HELPERS_PATH"
fi

_agensic_bash_is_interactive() {
    [[ "$-" == *i* ]]
}

_agensic_bash_log() {
    local message="$1"
    mkdir -p "$AGENSIC_HOME" 2>/dev/null || return
    chmod 700 "$AGENSIC_HOME" 2>/dev/null || true
    {
        printf "%s bash_adapter=%s\n" \
            "$(date '+%Y-%m-%d %H:%M:%S' 2>/dev/null)" \
            "$message"
    } >> "$AGENSIC_PLUGIN_LOG" 2>/dev/null
    chmod 600 "$AGENSIC_PLUGIN_LOG" 2>/dev/null || true
}

_agensic_find_ble_sh() {
    local candidate=""
    local -a candidates=()

    if [[ -n "$AGENSIC_BLE_SH_PATH" ]]; then
        candidates+=("$AGENSIC_BLE_SH_PATH")
    fi

    candidates+=(
        "${HOME}/.local/share/blesh/ble.sh"
        "${HOME}/.local/share/blesh/latest/ble.sh"
        "/usr/local/share/blesh/ble.sh"
        "/usr/share/blesh/ble.sh"
    )

    for candidate in "${candidates[@]}"; do
        if [[ -f "$candidate" ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done

    return 1
}

_agensic_emit_ble_missing_warning() {
    if ! _agensic_bash_is_interactive; then
        return
    fi
    if [[ "${AGENSIC_BASH_BLE_WARNING_EMITTED:-0}" == "1" ]]; then
        return
    fi
    AGENSIC_BASH_BLE_WARNING_EMITTED=1
    printf '%s\n' "Agensic bash support requires ble.sh. Install ble.sh and restart bash." >&2
}

_agensic_source_ble_if_needed() {
    if [[ -n "${BLE_VERSION:-}" ]]; then
        AGENSIC_BASH_BLE_AVAILABLE=1
        AGENSIC_BASH_BLE_LOADED_FROM="session"
        return 0
    fi

    local ble_path=""
    ble_path="$(_agensic_find_ble_sh)" || return 1
    if ! source -- "$ble_path" --attach=none >/dev/null 2>&1; then
        return 1
    fi
    if declare -F ble-attach >/dev/null 2>&1; then
        ble-attach >/dev/null 2>&1 || true
    fi
    if [[ -n "${BLE_VERSION:-}" ]]; then
        AGENSIC_BASH_BLE_AVAILABLE=1
        AGENSIC_BASH_BLE_LOADED_FROM="$ble_path"
        return 0
    fi
    return 1
}

_agensic_initialize_bash_adapter() {
    if ! _agensic_bash_is_interactive; then
        return 0
    fi
    if _agensic_source_ble_if_needed; then
        AGENSIC_BASH_ADAPTER_READY=1
        _agensic_bash_log "ble_ready source=${AGENSIC_BASH_BLE_LOADED_FROM:-unknown}"
        return 0
    fi
    AGENSIC_BASH_ADAPTER_READY=0
    AGENSIC_BASH_BLE_AVAILABLE=0
    _agensic_bash_log "ble_missing"
    _agensic_emit_ble_missing_warning
    return 1
}

_agensic_initialize_bash_adapter >/dev/null 2>&1 || true
