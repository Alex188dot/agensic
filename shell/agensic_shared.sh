#!/usr/bin/env sh

# Shared shell-integration helpers used by shell-specific adapters.
# Keep this file free of shell-editor-specific logic.

_agensic_value_in_array() {
    local needle="$1"
    shift
    local item
    for item in "$@"; do
        if [ "$needle" = "$item" ]; then
            return 0
        fi
    done
    return 1
}

_agensic_token_has_short_flag() {
    local token="${1:-}"
    local flag="${2:-}"
    token="$(printf '%s' "$token" | tr '[:upper:]' '[:lower:]')"
    flag="$(printf '%s' "$flag" | tr '[:upper:]' '[:lower:]')"

    if [ -z "$token" ] || [ -z "$flag" ]; then
        return 1
    fi
    case "$token" in
        --*) return 1 ;;
        "-$flag") return 0 ;;
    esac
    if [ "${token#-}" != "$token" ] && [ "${#token}" -gt 2 ]; then
        case "${token#-}" in
            *"$flag"*) return 0 ;;
        esac
    fi
    return 1
}

_agensic_token_looks_path_or_file() {
    local token="$1"
    if [ -z "$token" ]; then
        return 1
    fi
    case "$token" in
        "~"*|"./"*|"../"*|*"/"*) return 0 ;;
    esac
    case "$token" in
        *.*)
            if [ "$token" != "." ] && [ "$token" != ".." ]; then
                return 0
            fi
            ;;
    esac
    return 1
}

_agensic_merge_suffix() {
    local base="$1"
    local suffix="$2"
    local trimmed=""

    if [ -n "$base" ] && [ -n "$suffix" ]; then
        case "$base" in
            *[![:space:]]) ;;
            *)
                case "$suffix" in
                    [[:space:]]*)
                        trimmed="$suffix"
                        while [ -n "$trimmed" ] && [ "${trimmed# }" != "$trimmed" ]; do
                            trimmed="${trimmed# }"
                        done
                        while [ -n "$trimmed" ] && [ "$(printf '\t%s' "$trimmed")" != "$(printf '\t%s' "${trimmed#	}")" ]; do
                            trimmed="${trimmed#	}"
                        done
                        suffix="$trimmed"
                        ;;
                esac
                ;;
        esac
    fi
    printf '%s\n' "$suffix"
}

_agensic_canonicalize_buffer_spacing() {
    local value="$1"

    case "$value" in
        *"'"*|*\"*|*"\\ "*)
            printf '%s\n' "$value"
            return
            ;;
    esac

    value=$(printf '%s' "$value" | tr '\t' ' ')
    while :; do
        case "$value" in
            *"  "*)
                value=$(printf '%s' "$value" | sed 's/  \+/ /g')
                ;;
            *)
                break
                ;;
        esac
    done

    while [ "${value# }" != "$value" ]; do
        value="${value# }"
    done
    while [ "${value% }" != "$value" ]; do
        value="${value% }"
    done

    printf '%s\n' "$value"
}

_agensic_reset_provenance_line_state() {
    AGENSIC_LINE_LAST_ACTION=""
    AGENSIC_LINE_ACCEPTED_ORIGIN=""
    AGENSIC_LINE_ACCEPTED_MODE=""
    AGENSIC_LINE_ACCEPTED_KIND=""
    AGENSIC_LINE_MANUAL_EDIT_AFTER_ACCEPT=0
    AGENSIC_LINE_ACCEPTED_AI_AGENT=""
    AGENSIC_LINE_ACCEPTED_AI_PROVIDER=""
    AGENSIC_LINE_ACCEPTED_AI_MODEL=""
}

_agensic_clear_pending_execution() {
    AGENSIC_PENDING_LAST_ACTION=""
    AGENSIC_PENDING_ACCEPTED_ORIGIN=""
    AGENSIC_PENDING_ACCEPTED_MODE=""
    AGENSIC_PENDING_ACCEPTED_KIND=""
    AGENSIC_PENDING_MANUAL_EDIT_AFTER_ACCEPT=0
    AGENSIC_PENDING_AI_AGENT=""
    AGENSIC_PENDING_AI_PROVIDER=""
    AGENSIC_PENDING_AI_MODEL=""
    AGENSIC_PENDING_AGENT_NAME=""
    AGENSIC_PENDING_AGENT_HINT=""
    AGENSIC_PENDING_MODEL_RAW=""
    AGENSIC_PENDING_WRAPPER_ID=""
    AGENSIC_PENDING_PROOF_LABEL=""
    AGENSIC_PENDING_PROOF_AGENT=""
    AGENSIC_PENDING_PROOF_MODEL=""
    AGENSIC_PENDING_PROOF_TRACE=""
    AGENSIC_PENDING_PROOF_TIMESTAMP=0
    AGENSIC_PENDING_PROOF_SIGNATURE=""
    AGENSIC_PENDING_PROOF_SIGNER_SCOPE=""
    AGENSIC_PENDING_PROOF_KEY_FINGERPRINT=""
    AGENSIC_PENDING_PROOF_HOST_FINGERPRINT=""
}

_agensic_mark_manual_line_edit() {
    local action="$1"
    if [ -n "${AGENSIC_LINE_ACCEPTED_ORIGIN:-}" ]; then
        AGENSIC_LINE_MANUAL_EDIT_AFTER_ACCEPT=1
    fi
    AGENSIC_LINE_LAST_ACTION="$action"
}

_agensic_set_suggestion_accept_state() {
    local origin="$1"
    local mode="$2"
    local kind="$3"
    local ai_agent="$4"
    local ai_provider="$5"
    local ai_model="$6"
    AGENSIC_LINE_ACCEPTED_ORIGIN="$origin"
    AGENSIC_LINE_ACCEPTED_MODE="$mode"
    AGENSIC_LINE_ACCEPTED_KIND="$kind"
    AGENSIC_LINE_ACCEPTED_AI_AGENT="$ai_agent"
    AGENSIC_LINE_ACCEPTED_AI_PROVIDER="$ai_provider"
    AGENSIC_LINE_ACCEPTED_AI_MODEL="$ai_model"
    AGENSIC_LINE_LAST_ACTION="suggestion_accept"
    AGENSIC_LINE_MANUAL_EDIT_AFTER_ACCEPT=0
}

_agensic_refresh_pending_proof_fields() {
    AGENSIC_PENDING_PROOF_LABEL="$AGENSIC_NEXT_PROOF_LABEL"
    AGENSIC_PENDING_PROOF_AGENT="$AGENSIC_NEXT_PROOF_AGENT"
    AGENSIC_PENDING_PROOF_MODEL="$AGENSIC_NEXT_PROOF_MODEL"
    AGENSIC_PENDING_PROOF_TRACE="$AGENSIC_NEXT_PROOF_TRACE"
    AGENSIC_PENDING_PROOF_TIMESTAMP="$AGENSIC_NEXT_PROOF_TIMESTAMP"
    AGENSIC_PENDING_PROOF_SIGNATURE="$AGENSIC_NEXT_PROOF_SIGNATURE"
    AGENSIC_PENDING_PROOF_SIGNER_SCOPE="$AGENSIC_NEXT_PROOF_SIGNER_SCOPE"
    AGENSIC_PENDING_PROOF_KEY_FINGERPRINT="$AGENSIC_NEXT_PROOF_KEY_FINGERPRINT"
    AGENSIC_PENDING_PROOF_HOST_FINGERPRINT="$AGENSIC_NEXT_PROOF_HOST_FINGERPRINT"
    if [ -n "${AGENSIC_PENDING_PROOF_TRACE:-}" ]; then
        AGENSIC_PENDING_WRAPPER_ID="proof:${AGENSIC_PENDING_PROOF_TRACE}"
    fi
    AGENSIC_NEXT_PROOF_LABEL=""
    AGENSIC_NEXT_PROOF_AGENT=""
    AGENSIC_NEXT_PROOF_MODEL=""
    AGENSIC_NEXT_PROOF_TRACE=""
    AGENSIC_NEXT_PROOF_TIMESTAMP=0
    AGENSIC_NEXT_PROOF_SIGNATURE=""
    AGENSIC_NEXT_PROOF_SIGNER_SCOPE=""
    AGENSIC_NEXT_PROOF_KEY_FINGERPRINT=""
    AGENSIC_NEXT_PROOF_HOST_FINGERPRINT=""
}

_agensic_clear_next_proof_fields() {
    AGENSIC_NEXT_PROOF_LABEL=""
    AGENSIC_NEXT_PROOF_AGENT=""
    AGENSIC_NEXT_PROOF_MODEL=""
    AGENSIC_NEXT_PROOF_TRACE=""
    AGENSIC_NEXT_PROOF_TIMESTAMP=0
    AGENSIC_NEXT_PROOF_SIGNATURE=""
    AGENSIC_NEXT_PROOF_SIGNER_SCOPE=""
    AGENSIC_NEXT_PROOF_KEY_FINGERPRINT=""
    AGENSIC_NEXT_PROOF_HOST_FINGERPRINT=""
}

_agensic_snapshot_pending_execution() {
    AGENSIC_PENDING_LAST_ACTION="$AGENSIC_LINE_LAST_ACTION"
    AGENSIC_PENDING_ACCEPTED_ORIGIN="$AGENSIC_LINE_ACCEPTED_ORIGIN"
    AGENSIC_PENDING_ACCEPTED_MODE="$AGENSIC_LINE_ACCEPTED_MODE"
    AGENSIC_PENDING_ACCEPTED_KIND="$AGENSIC_LINE_ACCEPTED_KIND"
    AGENSIC_PENDING_MANUAL_EDIT_AFTER_ACCEPT="$AGENSIC_LINE_MANUAL_EDIT_AFTER_ACCEPT"
    AGENSIC_PENDING_AI_AGENT="$AGENSIC_LINE_ACCEPTED_AI_AGENT"
    AGENSIC_PENDING_AI_PROVIDER="$AGENSIC_LINE_ACCEPTED_AI_PROVIDER"
    AGENSIC_PENDING_AI_MODEL="$AGENSIC_LINE_ACCEPTED_AI_MODEL"
    AGENSIC_PENDING_AGENT_NAME="${AGENSIC_AI_SESSION_AGENT_NAME:-}"
    AGENSIC_PENDING_AGENT_HINT="$AGENSIC_LINE_ACCEPTED_AI_AGENT"
    AGENSIC_PENDING_MODEL_RAW="$AGENSIC_LINE_ACCEPTED_AI_MODEL"
    AGENSIC_PENDING_WRAPPER_ID=""
    _agensic_refresh_pending_proof_fields
}

_agensic_pending_execution_has_provenance() {
    if [ -n "${AGENSIC_PENDING_LAST_ACTION:-}" ] || [ -n "${AGENSIC_PENDING_ACCEPTED_ORIGIN:-}" ]; then
        return 0
    fi
    return 1
}
