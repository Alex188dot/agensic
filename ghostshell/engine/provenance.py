import hashlib
import hmac
import json
import os
import secrets
import socket
import stat
import subprocess
import time
from pathlib import Path
from typing import Any

from .agent_registry import AgentRegistry, build_model_fingerprint
from ghostshell.utils import ensure_private_dir
from .registry_updater import (
    DEFAULT_REGISTRY_URL,
    DEFAULT_REMOTE_META_PATH,
    refresh_remote_registry,
    verify_cached_registry,
)


PROOF_MAX_AGE_SECONDS = 900
DEFAULT_SECRET_PATH = os.path.expanduser("~/.ghostshell/provenance_secret")

_HUMAN_ACTIONS = {
    "human_typed",
    "human_type",
    "human_edit",
    "human_delete",
    "human_paste",
}

_BASE_LABEL_CONFIDENCE = {
    "AI_EXECUTED": 0.99,
    "HUMAN_TYPED": 0.93,
    "AI_SUGGESTED_HUMAN_RAN": 0.88,
    "GS_SUGGESTED_HUMAN_RAN": 0.86,
    "UNKNOWN": 0.35,
}

_TIER_CONFIDENCE = {
    "proof": 0.99,
    "integrated": 0.92,
    "verified": 0.80,
    "heuristic": 0.60,
    "community": 0.45,
    "": 0.0,
}

_REGISTRY = AgentRegistry()


def _normalize(value: Any) -> str:
    return str(value or "").strip()


def _normalize_lower(value: Any) -> str:
    return _normalize(value).lower()


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _safe_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = _normalize_lower(value)
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return bool(value)


def _proof_message(
    label: str,
    agent: str,
    model: str,
    trace: str,
    timestamp: int,
) -> str:
    return "\n".join(
        [
            _normalize(label),
            _normalize(agent),
            _normalize(model),
            _normalize(trace),
            str(int(timestamp)),
        ]
    )


def ensure_provenance_secret(path: str = DEFAULT_SECRET_PATH) -> bytes:
    target = os.path.expanduser(path)
    ensure_private_dir(os.path.dirname(target))
    if not os.path.exists(target):
        with open(target, "wb") as f:
            f.write(secrets.token_bytes(32))
        os.chmod(target, 0o600)
    else:
        mode = stat.S_IMODE(os.stat(target).st_mode)
        if mode != 0o600:
            os.chmod(target, 0o600)
    with open(target, "rb") as f:
        return f.read()


def verify_signed_proof(
    label: str,
    agent: str,
    model: str,
    trace: str,
    timestamp: int,
    signature: str,
    now_ts: int | None = None,
) -> tuple[bool, str]:
    clean_label = _normalize(label)
    clean_signature = _normalize_lower(signature)
    ts_value = _safe_int(timestamp)
    current_ts = int(now_ts or time.time())

    if clean_label != "AI_EXECUTED":
        return (False, "proof_label_invalid")
    if ts_value <= 0:
        return (False, "proof_timestamp_missing")
    if abs(current_ts - ts_value) > PROOF_MAX_AGE_SECONDS:
        return (False, "proof_timestamp_stale")
    if not clean_signature:
        return (False, "proof_signature_missing")

    try:
        secret = ensure_provenance_secret()
    except Exception:
        return (False, "proof_secret_unavailable")

    message = _proof_message(clean_label, agent, model, trace, ts_value)
    expected = hmac.new(secret, message.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, clean_signature):
        return (False, "proof_signature_invalid")
    return (True, "proof_valid")


def sign_proof_payload(
    label: str,
    agent: str,
    model: str,
    trace: str,
    timestamp: int,
) -> str:
    secret = ensure_provenance_secret()
    message = _proof_message(label, agent, model, trace, timestamp)
    return hmac.new(secret, message.encode("utf-8"), hashlib.sha256).hexdigest()


def build_local_proof_metadata() -> dict[str, str]:
    secret: bytes | None = None
    key_fingerprint = ""
    host_fingerprint = ""

    try:
        secret = ensure_provenance_secret()
    except Exception:
        secret = None

    if secret:
        key_fingerprint = hashlib.sha256(secret).hexdigest()[:16]

    host = ""
    try:
        host = socket.gethostname()
    except Exception:
        host = _normalize(os.environ.get("HOSTNAME"))

    if host:
        material = (secret + b"\n" + host.encode("utf-8")) if secret else host.encode("utf-8")
        host_fingerprint = hashlib.sha256(material).hexdigest()[:16]

    return {
        "proof_signer_scope": "local-hmac",
        "proof_key_fingerprint": key_fingerprint,
        "proof_host_fingerprint": host_fingerprint,
    }


def get_agent_registry(force_reload: bool = False) -> AgentRegistry:
    global _REGISTRY
    if force_reload:
        _REGISTRY.reload()
    return _REGISTRY


def get_registry_summary(force_reload: bool = False) -> dict[str, Any]:
    registry = get_agent_registry(force_reload=force_reload)
    return registry.summary()


def list_registry_agents(status_filter: str = "", force_reload: bool = False) -> list[dict[str, Any]]:
    registry = get_agent_registry(force_reload=force_reload)
    return registry.list_agents(status_filter=status_filter)


def get_registry_agent(agent_id: str, force_reload: bool = False) -> dict[str, Any] | None:
    registry = get_agent_registry(force_reload=force_reload)
    return registry.get_agent(agent_id)


def _load_remote_meta(path: str = DEFAULT_REMOTE_META_PATH) -> dict[str, Any]:
    target = Path(path).expanduser()
    if not target.exists() or not target.is_file():
        return {}
    try:
        with open(target, "r", encoding="utf-8") as f:
            parsed = json.load(f)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def refresh_agent_registry(config: dict[str, Any] | None = None, force: bool = False) -> dict[str, Any]:
    cfg = config if isinstance(config, dict) else {}
    registry_url = _normalize(cfg.get("provenance_registry_url")) or DEFAULT_REGISTRY_URL
    registry_pubkey = _normalize(cfg.get("provenance_registry_pubkey"))
    refresh_hours = max(1, _safe_int(cfg.get("provenance_registry_refresh_hours", 24)))

    now_ts = int(time.time())
    meta = _load_remote_meta()
    verified_at = _safe_int(meta.get("verified_at"))

    if not force and verified_at > 0 and (now_ts - verified_at) < int(refresh_hours * 3600):
        summary = get_registry_summary(force_reload=False)
        return {
            "ok": True,
            "reason": "fresh_cache",
            "updated": False,
            "version": str(summary.get("version", "") or ""),
        }

    result = refresh_remote_registry(
        url=registry_url,
        public_key=registry_pubkey,
    )
    if bool(result.get("ok", False)):
        get_agent_registry(force_reload=True)
    return result


def verify_cached_agent_registry(config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config if isinstance(config, dict) else {}
    registry_pubkey = _normalize(cfg.get("provenance_registry_pubkey"))
    return verify_cached_registry(public_key=registry_pubkey)


def _ps_row_for_pid(pid: int) -> dict[str, Any] | None:
    try:
        probe = subprocess.run(
            ["ps", "-p", str(pid), "-o", "pid=,ppid=,comm=,command="],
            capture_output=True,
            text=True,
            check=False,
            timeout=0.2,
        )
    except Exception:
        return None

    if probe.returncode != 0:
        return None
    line = (probe.stdout or "").strip().splitlines()
    if not line:
        return None
    raw = line[0].strip()
    if not raw:
        return None

    parts = raw.split(None, 3)
    if len(parts) < 3:
        return None

    command = parts[3].strip() if len(parts) > 3 else ""
    return {
        "pid": _safe_int(parts[0]),
        "ppid": _safe_int(parts[1]),
        "comm": parts[2].strip(),
        "command": command,
    }


def inspect_process_lineage(shell_pid: int, max_depth: int = 12) -> dict[str, Any]:
    lineage: list[dict[str, Any]] = []
    current = _safe_int(shell_pid)
    seen: set[int] = set()

    for _ in range(max(1, int(max_depth))):
        if current <= 0 or current in seen:
            break
        seen.add(current)
        row = _ps_row_for_pid(current)
        if row is None:
            break
        lineage.append(row)
        next_pid = _safe_int(row.get("ppid"))
        if next_pid <= 0 or next_pid == current:
            break
        current = next_pid

    registry = get_agent_registry(force_reload=False)
    hints: list[str] = []
    for row in lineage:
        match = registry.infer_from_lineage([row])
        if match is None:
            continue
        if match.agent_id and match.agent_id not in hints:
            hints.append(match.agent_id)

    best = registry.infer_from_lineage(lineage)
    match_payload: dict[str, Any] = {}
    if best is not None:
        match_payload = {
            "agent_id": best.agent_id,
            "registry_status": best.registry_status,
            "match_kind": best.match_kind,
            "confidence": best.confidence,
            "evidence_tier": best.evidence_tier,
            "model_raw": best.model_raw,
            "model_normalized": best.model_normalized,
            "provider": best.provider,
            "evidence": list(best.evidence),
        }

    return {
        "lineage": lineage,
        "hints": hints,
        "match": match_payload,
    }


def classify_command_run(command: str, payload: dict[str, Any]) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    evidence: list[str] = []

    last_action = _normalize_lower(data.get("provenance_last_action"))
    accept_origin = _normalize_lower(data.get("provenance_accept_origin"))
    manual_after_accept = bool(data.get("provenance_manual_edit_after_accept", False))

    ai_agent = _normalize_lower(data.get("provenance_ai_agent"))
    ai_model = _normalize(data.get("provenance_ai_model"))
    ai_provider = _normalize_lower(data.get("provenance_ai_provider"))
    agent_name = _normalize(data.get("provenance_agent_name"))

    provenance_agent_hint = _normalize_lower(data.get("provenance_agent_hint"))
    provenance_model_raw = _normalize(data.get("provenance_model_raw"))
    provenance_wrapper_id = _normalize(data.get("provenance_wrapper_id"))

    proof_label = _normalize(data.get("proof_label"))
    proof_agent = _normalize_lower(data.get("proof_agent"))
    proof_model = _normalize(data.get("proof_model"))
    proof_trace = _normalize(data.get("proof_trace"))
    proof_timestamp = _safe_int(data.get("proof_timestamp"))
    proof_signature = _normalize(data.get("proof_signature"))
    proof_signer_scope = _normalize_lower(data.get("proof_signer_scope"))
    proof_key_fingerprint = _normalize_lower(data.get("proof_key_fingerprint"))
    proof_host_fingerprint = _normalize_lower(data.get("proof_host_fingerprint"))

    proof_valid, proof_reason = verify_signed_proof(
        proof_label,
        proof_agent,
        proof_model,
        proof_trace,
        proof_timestamp,
        proof_signature,
    )
    proof_signature_present = bool(proof_signature)
    evidence.append(proof_reason)

    shell_pid = _safe_int(data.get("shell_pid"))
    lineage_payload = inspect_process_lineage(shell_pid) if shell_pid > 0 else {"lineage": [], "hints": [], "match": {}}
    lineage_hints = [str(item) for item in lineage_payload.get("hints", []) if str(item)]
    if lineage_hints:
        evidence.append(f"lineage_hint={','.join(lineage_hints)}")

    registry = get_agent_registry(force_reload=False)
    registry_summary = registry.summary()
    registry_version = str(registry_summary.get("version", "") or "")

    label = "UNKNOWN"
    confidence = _BASE_LABEL_CONFIDENCE["UNKNOWN"]
    if proof_valid:
        label = "AI_EXECUTED"
        confidence = _BASE_LABEL_CONFIDENCE["AI_EXECUTED"]
        evidence.append("proof_enforced_strict")
        if proof_signer_scope:
            evidence.append(f"proof_signer_scope={proof_signer_scope}")
        if proof_key_fingerprint:
            evidence.append(f"proof_key_fingerprint={proof_key_fingerprint}")
        if proof_host_fingerprint:
            evidence.append(f"proof_host_fingerprint={proof_host_fingerprint}")
    elif proof_signature_present:
        label = "AI_EXECUTED"
        confidence = 0.97
        evidence.append("proof_signature_present_override")
        if proof_signer_scope:
            evidence.append(f"proof_signer_scope={proof_signer_scope}")
        if proof_key_fingerprint:
            evidence.append(f"proof_key_fingerprint={proof_key_fingerprint}")
        if proof_host_fingerprint:
            evidence.append(f"proof_host_fingerprint={proof_host_fingerprint}")
    elif last_action in _HUMAN_ACTIONS:
        label = "HUMAN_TYPED"
        confidence = _BASE_LABEL_CONFIDENCE["HUMAN_TYPED"]
        evidence.append(f"last_action={last_action}")
    elif accept_origin == "ai" and not manual_after_accept:
        label = "AI_SUGGESTED_HUMAN_RAN"
        confidence = _BASE_LABEL_CONFIDENCE["AI_SUGGESTED_HUMAN_RAN"]
        evidence.append("accept_origin=ai")
    elif accept_origin == "gs" and not manual_after_accept:
        label = "GS_SUGGESTED_HUMAN_RAN"
        confidence = _BASE_LABEL_CONFIDENCE["GS_SUGGESTED_HUMAN_RAN"]
        evidence.append("accept_origin=gs")
    else:
        if manual_after_accept:
            evidence.append("manual_edit_after_accept=true")

    agent = ""
    provider = ""
    raw_model = ""
    normalized_model = ""
    evidence_tier = ""
    agent_source = ""
    registry_status = ""

    if proof_valid or proof_signature_present:
        agent = proof_agent
        provider = ai_provider
        raw_model = proof_model or provenance_model_raw or ai_model
        evidence_tier = "proof"
        agent_source = "proof" if proof_valid else "proof_signature"
        if proof_trace:
            evidence.append(f"proof_trace={proof_trace}")
    elif accept_origin == "ai" and not manual_after_accept:
        agent = ai_agent or provenance_agent_hint
        provider = ai_provider
        raw_model = provenance_model_raw or ai_model
        evidence_tier = "integrated" if provenance_wrapper_id else ""
        agent_source = "payload_ai"
        if provenance_wrapper_id:
            evidence.append(f"wrapper_id={provenance_wrapper_id}")

    inferred_from_payload = registry.infer_agent_from_provider_model(provider=provider, model=raw_model)
    if not agent and inferred_from_payload.get("agent_id"):
        agent = str(inferred_from_payload.get("agent_id", "") or "")
        agent_source = agent_source or "provider_model_infer"
    if not normalized_model:
        normalized_model = str(inferred_from_payload.get("model_normalized", "") or "")
    if not registry_status and not (proof_valid or proof_signature_present):
        registry_status = str(inferred_from_payload.get("registry_status", "") or "")

    if not agent and provenance_agent_hint:
        hinted = registry.get_agent(provenance_agent_hint)
        if hinted is not None:
            agent = str(hinted.get("agent_id", "") or "")
            agent_source = agent_source or "payload_hint"
            registry_status = str(hinted.get("status", "") or "")

    lineage_match = lineage_payload.get("match", {}) if isinstance(lineage_payload.get("match"), dict) else {}
    if lineage_match:
        if not agent:
            agent = _normalize_lower(lineage_match.get("agent_id"))
            agent_source = agent_source or (
                "lineage_exact" if _normalize_lower(lineage_match.get("match_kind")) == "exact_executable" else "lineage_token"
            )
        if not provider:
            provider = _normalize_lower(lineage_match.get("provider"))
        if not raw_model:
            raw_model = _normalize(lineage_match.get("model_raw"))
        if not normalized_model:
            normalized_model = _normalize_lower(lineage_match.get("model_normalized"))
        if not registry_status and not (proof_valid or proof_signature_present):
            registry_status = _normalize_lower(lineage_match.get("registry_status"))
        if not evidence_tier:
            evidence_tier = _normalize_lower(lineage_match.get("evidence_tier"))
        for item in lineage_match.get("evidence", []) if isinstance(lineage_match.get("evidence", []), list) else []:
            text = _normalize(item)
            if text:
                evidence.append(text)

    if not agent and lineage_hints:
        agent = lineage_hints[0]
        agent_source = agent_source or "lineage_hint"

    if not raw_model:
        raw_model = provenance_model_raw or ai_model

    if agent and not normalized_model:
        normalized_model = registry.normalize_model(agent, raw_model, provider)

    agent_meta = registry.get_agent(agent) if agent else None
    if proof_valid or proof_signature_present:
        if agent and agent_meta is None:
            registry_status = "unmapped_signed"
            evidence.append("proof_agent_unmapped=true")
        elif agent_meta is not None:
            registry_status = _normalize_lower(agent_meta.get("status"))
    elif not registry_status and agent_meta is not None:
        registry_status = _normalize_lower(agent_meta.get("status"))

    model = raw_model or normalized_model
    fingerprint = build_model_fingerprint(agent, normalized_model, raw_model)
    if fingerprint:
        evidence.append(f"model_fingerprint={fingerprint}")

    if evidence_tier:
        evidence.append(f"evidence_tier={evidence_tier}")

    tier_confidence = _TIER_CONFIDENCE.get(evidence_tier, 0.0)
    if label == "UNKNOWN":
        confidence = max(confidence, tier_confidence)
    elif evidence_tier == "integrated":
        confidence = max(confidence, tier_confidence)

    confidence = float(max(0.0, min(1.0, confidence)))

    if command:
        evidence.append(f"command_len={len(command)}")

    return {
        "label": label,
        "confidence": confidence,
        "agent": agent,
        "agent_name": agent_name,
        "provider": provider,
        "model": model,
        "raw_model": raw_model,
        "normalized_model": normalized_model,
        "model_fingerprint": fingerprint,
        "proof_valid": bool(proof_valid),
        "proof_reason": proof_reason,
        "proof_signer_scope": proof_signer_scope,
        "proof_key_fingerprint": proof_key_fingerprint,
        "proof_host_fingerprint": proof_host_fingerprint,
        "evidence_tier": evidence_tier,
        "agent_source": agent_source,
        "registry_version": registry_version,
        "registry_status": registry_status,
        "lineage_hints": lineage_hints,
        "lineage": lineage_payload.get("lineage", []),
        "evidence": evidence,
    }
