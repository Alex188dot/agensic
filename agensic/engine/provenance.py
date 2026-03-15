import base64
import hashlib
import os
import socket
import subprocess
import time
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from agensic.paths import APP_PATHS
from .agent_registry import AgentRegistry, build_model_fingerprint
from agensic.utils import ensure_private_dir


PROOF_MAX_AGE_SECONDS = 900
DEFAULT_PRIVATE_KEY_PATH = APP_PATHS.provenance_private_key_path
DEFAULT_PUBLIC_KEY_PATH = APP_PATHS.provenance_public_key_path
PROOF_SIGNER_SCOPE = "local-ed25519"

_HUMAN_ACTIONS = {
    "human_typed",
    "human_type",
    "human_edit",
    "human_delete",
    "human_paste",
}

_SIGNED_PROOF_LABELS = {
    "AI_EXECUTED",
}

_BASE_LABEL_CONFIDENCE = {
    "AI_EXECUTED": 0.99,
    "INVALID_PROOF": 0.98,
    "HUMAN_TYPED": 0.93,
    "AI_SUGGESTED_HUMAN_RAN": 0.88,
    "AG_SUGGESTED_HUMAN_RAN": 0.86,
    "UNKNOWN": 0.35,
}

_TIER_CONFIDENCE = {
    "proof": 0.99,
    "proof_invalid": 0.98,
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


def _is_signed_proof_label(label: Any) -> bool:
    return _normalize(label) in _SIGNED_PROOF_LABELS


def _proof_message(
    label: str,
    agent: str,
    model: str,
    trace: str,
    timestamp: int,
    ) -> bytes:
    return "\n".join(
        [
            _normalize(label),
            _normalize(agent),
            _normalize(model),
            _normalize(trace),
            str(int(timestamp)),
        ]
    ).encode("utf-8")


def ensure_provenance_keypair(
    private_path: str = DEFAULT_PRIVATE_KEY_PATH,
    public_path: str = DEFAULT_PUBLIC_KEY_PATH,
) -> tuple[str, str]:
    private_target = os.path.expanduser(private_path)
    public_target = os.path.expanduser(public_path)
    ensure_private_dir(os.path.dirname(private_target))
    ensure_private_dir(os.path.dirname(public_target))

    private_key: Ed25519PrivateKey | None = None
    if not os.path.exists(private_target):
        private_key = Ed25519PrivateKey.generate()
        with open(private_target, "wb") as private_file:
            private_file.write(
                private_key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.NoEncryption(),
                )
            )
    elif private_key is None:
        with open(private_target, "rb") as private_file:
            loaded_private = serialization.load_pem_private_key(
                private_file.read(),
                password=None,
            )
        if not isinstance(loaded_private, Ed25519PrivateKey):
            raise RuntimeError("provenance private key is not Ed25519")
        private_key = loaded_private
    os.chmod(private_target, 0o600)

    public_key = private_key.public_key()
    expected_public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    existing_public_bytes = b""
    if os.path.exists(public_target):
        try:
            with open(public_target, "rb") as public_file:
                existing_public_bytes = public_file.read()
        except Exception:
            existing_public_bytes = b""

    if existing_public_bytes != expected_public_bytes:
        with open(public_target, "wb") as public_file:
            public_file.write(expected_public_bytes)
    os.chmod(public_target, 0o600)
    return (private_target, public_target)


def _proof_payload_present(
    label: str,
    agent: str,
    model: str,
    trace: str,
    timestamp_raw: Any,
    signature: str,
) -> bool:
    return any(
        (
            _normalize(label),
            _normalize(agent),
            _normalize(model),
            _normalize(trace),
            _normalize(timestamp_raw),
            _normalize(signature),
        )
    )


def _collect_required_proof_issues(
    label: str,
    agent: str,
    model: str,
    trace: str,
    timestamp: int,
    signature: str,
) -> list[str]:
    issues: list[str] = []
    clean_label = _normalize(label)

    if not clean_label:
        issues.append("proof_label_missing")
    elif not _is_signed_proof_label(clean_label):
        issues.append("proof_label_invalid")
    if not _normalize_lower(agent):
        issues.append("proof_agent_missing")
    if not _normalize(model):
        issues.append("proof_model_missing")
    if not _normalize(trace):
        issues.append("proof_trace_missing")
    if int(timestamp) <= 0:
        issues.append("proof_timestamp_missing")
    if not _normalize(signature):
        issues.append("proof_signature_missing")
    return issues


def verify_signed_proof(
    label: str,
    agent: str,
    model: str,
    trace: str,
    timestamp: int,
    signature: str,
    now_ts: int | None = None,
    public_path: str = DEFAULT_PUBLIC_KEY_PATH,
) -> tuple[bool, str]:
    clean_label = _normalize(label)
    clean_signature = _normalize(signature)
    ts_value = _safe_int(timestamp)
    current_ts = int(now_ts or time.time())
    public_key_path = os.path.expanduser(public_path)

    if not _is_signed_proof_label(clean_label):
        return (False, "proof_label_invalid")
    if ts_value <= 0:
        return (False, "proof_timestamp_missing")
    if abs(current_ts - ts_value) > PROOF_MAX_AGE_SECONDS:
        return (False, "proof_timestamp_stale")
    if not clean_signature:
        return (False, "proof_signature_missing")

    if not os.path.exists(public_key_path):
        return (False, "proof_public_key_unavailable")

    try:
        signature_bytes = base64.b64decode(clean_signature.encode("ascii"), validate=True)
    except Exception:
        return (False, "proof_signature_encoding_invalid")

    try:
        with open(public_key_path, "rb") as public_key_file:
            loaded_public = serialization.load_pem_public_key(public_key_file.read())
        if not isinstance(loaded_public, Ed25519PublicKey):
            return (False, "proof_public_key_unavailable")
        loaded_public.verify(signature_bytes, _proof_message(clean_label, agent, model, trace, ts_value))
    except InvalidSignature:
        return (False, "proof_signature_invalid")
    except Exception:
        return (False, "proof_verifier_unavailable")
    return (True, "proof_valid")


def sign_proof_payload(
    label: str,
    agent: str,
    model: str,
    trace: str,
    timestamp: int,
    private_path: str = DEFAULT_PRIVATE_KEY_PATH,
    public_path: str = DEFAULT_PUBLIC_KEY_PATH,
) -> str:
    private_key_path, _ = ensure_provenance_keypair(private_path=private_path, public_path=public_path)
    with open(private_key_path, "rb") as private_key_file:
        loaded_private = serialization.load_pem_private_key(
            private_key_file.read(),
            password=None,
        )
    if not isinstance(loaded_private, Ed25519PrivateKey):
        raise RuntimeError("provenance private key is not Ed25519")
    signature = loaded_private.sign(_proof_message(label, agent, model, trace, timestamp))
    return base64.b64encode(signature).decode("ascii")


def build_local_proof_metadata(
    private_path: str = DEFAULT_PRIVATE_KEY_PATH,
    public_path: str = DEFAULT_PUBLIC_KEY_PATH,
) -> dict[str, str]:
    public_key_bytes: bytes | None = None
    key_fingerprint = ""
    host_fingerprint = ""

    try:
        _, public_key_path = ensure_provenance_keypair(private_path=private_path, public_path=public_path)
        with open(public_key_path, "rb") as public_key_file:
            public_key_bytes = public_key_file.read()
    except Exception:
        public_key_bytes = None

    if public_key_bytes:
        key_fingerprint = hashlib.sha256(public_key_bytes).hexdigest()[:16]

    host = ""
    try:
        host = socket.gethostname()
    except Exception:
        host = _normalize(os.environ.get("HOSTNAME"))

    if host:
        material = (public_key_bytes + b"\n" + host.encode("utf-8")) if public_key_bytes else host.encode("utf-8")
        host_fingerprint = hashlib.sha256(material).hexdigest()[:16]

    return {
        "proof_signer_scope": PROOF_SIGNER_SCOPE,
        "proof_key_fingerprint": key_fingerprint,
        "proof_host_fingerprint": host_fingerprint,
    }


def get_agent_registry(force_reload: bool = False) -> AgentRegistry:
    global _REGISTRY
    current_override_path = str(APP_PATHS.agent_registry_local_override_path or "").strip()
    registry_override_path = str(_REGISTRY.summary().get("local_override_path", "") or "").strip()
    if current_override_path and current_override_path != registry_override_path:
        _REGISTRY = AgentRegistry(local_override_path=current_override_path)
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


def classify_command_run(
    command: str,
    payload: dict[str, Any],
    *,
    proof_public_path: str = DEFAULT_PUBLIC_KEY_PATH,
) -> dict[str, Any]:
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
    proof_timestamp_raw = data.get("proof_timestamp")
    proof_timestamp = _safe_int(proof_timestamp_raw)
    proof_signature = _normalize(data.get("proof_signature"))
    proof_signer_scope = _normalize_lower(data.get("proof_signer_scope"))
    proof_key_fingerprint = _normalize_lower(data.get("proof_key_fingerprint"))
    proof_host_fingerprint = _normalize_lower(data.get("proof_host_fingerprint"))

    proof_payload_present = _proof_payload_present(
        proof_label,
        proof_agent,
        proof_model,
        proof_trace,
        proof_timestamp_raw,
        proof_signature,
    )
    proof_field_issues = _collect_required_proof_issues(
        proof_label,
        proof_agent,
        proof_model,
        proof_trace,
        proof_timestamp,
        proof_signature,
    )
    if proof_payload_present and not proof_field_issues:
        proof_valid, proof_reason = verify_signed_proof(
            proof_label,
            proof_agent,
            proof_model,
            proof_trace,
            proof_timestamp,
            proof_signature,
            public_path=proof_public_path,
        )
    elif proof_payload_present:
        proof_valid, proof_reason = (False, proof_field_issues[0])
    else:
        proof_valid, proof_reason = (False, "proof_absent")
    for item in [proof_reason, *proof_field_issues]:
        if item and item not in evidence:
            evidence.append(item)

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
        label = proof_label if _is_signed_proof_label(proof_label) else "AI_EXECUTED"
        confidence = _BASE_LABEL_CONFIDENCE.get(label, _BASE_LABEL_CONFIDENCE["AI_EXECUTED"])
        evidence.append("proof_enforced_strict")
        if proof_signer_scope:
            evidence.append(f"proof_signer_scope={proof_signer_scope}")
        if proof_key_fingerprint:
            evidence.append(f"proof_key_fingerprint={proof_key_fingerprint}")
        if proof_host_fingerprint:
            evidence.append(f"proof_host_fingerprint={proof_host_fingerprint}")
    elif proof_payload_present:
        label = "INVALID_PROOF"
        confidence = _BASE_LABEL_CONFIDENCE["INVALID_PROOF"]
        evidence.append("proof_enforced_strict")
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
    elif accept_origin == "ag" and not manual_after_accept:
        label = "AG_SUGGESTED_HUMAN_RAN"
        confidence = _BASE_LABEL_CONFIDENCE["AG_SUGGESTED_HUMAN_RAN"]
        evidence.append(f"accept_origin={accept_origin}")
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
    proof_rejected = bool(proof_payload_present and not proof_valid)

    if proof_valid:
        agent = proof_agent
        provider = ai_provider
        raw_model = proof_model or provenance_model_raw or ai_model
        evidence_tier = "proof"
        agent_source = "proof"
        if proof_trace:
            evidence.append(f"proof_trace={proof_trace}")
    elif proof_rejected:
        agent = proof_agent
        provider = ai_provider
        raw_model = proof_model or provenance_model_raw or ai_model
        evidence_tier = "proof_invalid"
        agent_source = "proof_invalid"
        registry_status = "invalid_proof"
        evidence.append("proof_claim_unverified=true")
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

    if not proof_rejected:
        inferred_from_payload = registry.infer_agent_from_provider_model(provider=provider, model=raw_model)
        if not agent and inferred_from_payload.get("agent_id"):
            agent = str(inferred_from_payload.get("agent_id", "") or "")
            agent_source = agent_source or "provider_model_infer"
        if not normalized_model:
            normalized_model = str(inferred_from_payload.get("model_normalized", "") or "")
        if not registry_status and not proof_valid:
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
                    "lineage_exact"
                    if _normalize_lower(lineage_match.get("match_kind")) == "exact_executable"
                    else "lineage_token"
                )
            if not provider:
                provider = _normalize_lower(lineage_match.get("provider"))
            if not raw_model:
                raw_model = _normalize(lineage_match.get("model_raw"))
            if not normalized_model:
                normalized_model = _normalize_lower(lineage_match.get("model_normalized"))
            if not registry_status and not proof_valid:
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
    if proof_valid:
        if agent and agent_meta is None:
            registry_status = "unmapped_signed"
            evidence.append("proof_agent_unmapped=true")
        elif agent_meta is not None:
            registry_status = _normalize_lower(agent_meta.get("status"))
    elif proof_rejected and agent_meta is not None:
        evidence.append("proof_claim_registry_match=true")
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
