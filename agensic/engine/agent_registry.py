import json
import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agensic.paths import APP_PATHS

DEFAULT_REMOTE_CACHE_PATH = APP_PATHS.agent_registry_remote_cache_path
DEFAULT_REMOTE_META_PATH = APP_PATHS.agent_registry_remote_meta_path
DEFAULT_LOCAL_OVERRIDE_PATH = APP_PATHS.agent_registry_local_override_path


@dataclass(frozen=True)
class ModelPattern:
    pattern: str
    normalized: str

    def matches(self, value: str) -> bool:
        if not self.pattern:
            return False
        try:
            return bool(re.search(self.pattern, value, flags=re.IGNORECASE))
        except re.error:
            return False


@dataclass(frozen=True)
class AgentDescriptor:
    agent_id: str
    display_name: str
    aliases: tuple[str, ...]
    executables: tuple[str, ...]
    process_tokens: tuple[str, ...]
    window_tokens: tuple[str, ...]
    model_patterns: tuple[ModelPattern, ...]
    provider_hints: tuple[str, ...]
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "display_name": self.display_name,
            "aliases": list(self.aliases),
            "executables": list(self.executables),
            "process_tokens": list(self.process_tokens),
            "window_tokens": list(self.window_tokens),
            "model_patterns": [
                {"pattern": item.pattern, "normalized": item.normalized}
                for item in self.model_patterns
            ],
            "provider_hints": list(self.provider_hints),
            "status": self.status,
        }


@dataclass(frozen=True)
class AgentMatch:
    agent_id: str
    registry_status: str
    match_kind: str
    confidence: float
    evidence_tier: str
    model_raw: str
    model_normalized: str
    provider: str
    evidence: tuple[str, ...]


class AgentRegistry:
    def __init__(
        self,
        builtin_path: str | None = None,
        remote_cache_path: str | None = None,
        remote_meta_path: str | None = None,
        local_override_path: str | None = None,
    ) -> None:
        here = Path(__file__).resolve().parent
        self._builtin_path = str(builtin_path or (here / "data" / "agents_builtin.json"))
        self._remote_cache_path = str(remote_cache_path or DEFAULT_REMOTE_CACHE_PATH)
        self._remote_meta_path = str(remote_meta_path or DEFAULT_REMOTE_META_PATH)
        self._local_override_path = str(local_override_path or DEFAULT_LOCAL_OVERRIDE_PATH)

        self.registry_version = ""
        self.registry_source = "builtin"
        self.remote_loaded = False
        self._agents: dict[str, AgentDescriptor] = {}
        self._aliases: dict[str, str] = {}
        self.reload()

    @staticmethod
    def _normalize(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _normalize_lower(value: Any) -> str:
        return str(value or "").strip().lower()

    @staticmethod
    def _clean_list(values: Any) -> list[str]:
        if not isinstance(values, list):
            return []
        out: list[str] = []
        seen: set[str] = set()
        for item in values:
            val = str(item or "").strip()
            if not val:
                continue
            key = val.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(val)
        return out

    def _load_json_file(self, path: str) -> dict[str, Any]:
        target = Path(path).expanduser()
        if not target.exists() or not target.is_file():
            return {}
        try:
            with open(target, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _remote_cache_is_verified(self) -> bool:
        meta = self._load_json_file(self._remote_meta_path)
        return bool(meta.get("signature_valid", False))

    def _doc_to_map(self, doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for raw in doc.get("agents", []) if isinstance(doc.get("agents", []), list) else []:
            if not isinstance(raw, dict):
                continue
            agent_id = self._normalize_lower(raw.get("agent_id"))
            if not agent_id:
                continue
            out[agent_id] = dict(raw)
            out[agent_id]["agent_id"] = agent_id
        return out

    def _build_descriptor(self, raw: dict[str, Any]) -> AgentDescriptor | None:
        agent_id = self._normalize_lower(raw.get("agent_id"))
        if not agent_id:
            return None

        raw_patterns = raw.get("model_patterns", [])
        patterns: list[ModelPattern] = []
        if isinstance(raw_patterns, list):
            for item in raw_patterns:
                if not isinstance(item, dict):
                    continue
                pattern = self._normalize(item.get("pattern"))
                normalized = self._normalize_lower(item.get("normalized"))
                if not pattern:
                    continue
                patterns.append(ModelPattern(pattern=pattern, normalized=normalized))

        status = self._normalize_lower(raw.get("status"))
        if status not in {"verified", "community"}:
            status = "community"

        aliases = self._clean_list(raw.get("aliases"))
        aliases.append(agent_id)

        return AgentDescriptor(
            agent_id=agent_id,
            display_name=self._normalize(raw.get("display_name")) or agent_id,
            aliases=tuple([x.lower() for x in aliases]),
            executables=tuple([x.lower() for x in self._clean_list(raw.get("executables"))]),
            process_tokens=tuple([x.lower() for x in self._clean_list(raw.get("process_tokens"))]),
            window_tokens=tuple([x.lower() for x in self._clean_list(raw.get("window_tokens"))]),
            model_patterns=tuple(patterns),
            provider_hints=tuple([x.lower() for x in self._clean_list(raw.get("provider_hints"))]),
            status=status,
        )

    def reload(self) -> None:
        builtin = self._load_json_file(self._builtin_path)
        remote = self._load_json_file(self._remote_cache_path) if self._remote_cache_is_verified() else {}
        local = self._load_json_file(self._local_override_path)

        merged: dict[str, dict[str, Any]] = {}
        merged.update(self._doc_to_map(builtin))

        remote_used = False
        if remote:
            merged.update(self._doc_to_map(remote))
            remote_used = True

        if local:
            merged.update(self._doc_to_map(local))

        agents: dict[str, AgentDescriptor] = {}
        alias_map: dict[str, str] = {}
        for key, raw in merged.items():
            descriptor = self._build_descriptor(raw)
            if descriptor is None:
                continue
            agents[key] = descriptor
            for alias in descriptor.aliases:
                alias_map[alias] = descriptor.agent_id

        builtin_version = self._normalize(builtin.get("version")) or "builtin"
        remote_version = self._normalize(remote.get("version"))
        local_version = self._normalize(local.get("version"))

        if local_version:
            self.registry_version = local_version
            self.registry_source = "local_override"
        elif remote_used and remote_version:
            self.registry_version = remote_version
            self.registry_source = "remote"
        else:
            self.registry_version = builtin_version
            self.registry_source = "builtin"

        self.remote_loaded = remote_used
        self._agents = agents
        self._aliases = alias_map

    def summary(self) -> dict[str, Any]:
        return {
            "version": self.registry_version,
            "source": self.registry_source,
            "agent_count": len(self._agents),
            "remote_loaded": bool(self.remote_loaded),
            "builtin_path": self._builtin_path,
            "remote_cache_path": self._remote_cache_path,
            "local_override_path": self._local_override_path,
        }

    def list_agents(self, status_filter: str = "") -> list[dict[str, Any]]:
        target_status = self._normalize_lower(status_filter)
        out: list[dict[str, Any]] = []
        for descriptor in sorted(self._agents.values(), key=lambda item: item.agent_id):
            if target_status and descriptor.status != target_status:
                continue
            out.append(descriptor.to_dict())
        return out

    def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        key = self._normalize_lower(agent_id)
        if not key:
            return None
        resolved = self._aliases.get(key, key)
        descriptor = self._agents.get(resolved)
        if descriptor is None:
            return None
        return descriptor.to_dict()

    def _infer_agent_from_alias(self, value: str) -> str:
        key = self._normalize_lower(value)
        if not key:
            return ""
        if key in self._aliases:
            return self._aliases[key]
        for alias, agent_id in self._aliases.items():
            if alias and alias in key:
                return agent_id
        return ""

    def normalize_model(self, agent_id: str, raw_model: str, provider: str = "") -> str:
        clean_raw = self._normalize(raw_model)
        if not clean_raw:
            return ""

        lowered = clean_raw.lower()
        resolved = self._aliases.get(self._normalize_lower(agent_id), self._normalize_lower(agent_id))
        descriptor = self._agents.get(resolved)
        if descriptor is not None:
            for pattern in descriptor.model_patterns:
                if pattern.matches(lowered):
                    return pattern.normalized or lowered

        clean_provider = self._normalize_lower(provider)
        if clean_provider == "openai" and lowered.startswith("openai/"):
            lowered = lowered.split("/", 1)[1]
        elif clean_provider == "anthropic" and "claude" in lowered:
            return "claude"
        elif clean_provider == "gemini" and "gemini" in lowered:
            return "gemini"
        return lowered

    def infer_agent_from_provider_model(self, provider: str, model: str) -> dict[str, str]:
        clean_provider = self._normalize_lower(provider)
        clean_model = self._normalize(model)
        lowered_model = clean_model.lower()

        best_agent = ""
        model_normalized = ""

        if lowered_model:
            candidates: list[tuple[int, AgentDescriptor, str]] = []
            for descriptor in self._agents.values():
                for pattern in descriptor.model_patterns:
                    if pattern.matches(lowered_model):
                        normalized = pattern.normalized or lowered_model
                        score = 60
                        if clean_provider and clean_provider in descriptor.provider_hints:
                            score += 30
                        if descriptor.agent_id in lowered_model:
                            score += 20
                        if descriptor.status == "verified":
                            score += 5
                        candidates.append((score, descriptor, normalized))
                        break
            if candidates:
                candidates.sort(key=lambda item: item[0], reverse=True)
                best_agent = candidates[0][1].agent_id
                model_normalized = candidates[0][2]

        if not best_agent and clean_provider:
            for descriptor in self._agents.values():
                if clean_provider in descriptor.provider_hints:
                    best_agent = descriptor.agent_id
                    break

        if not best_agent:
            best_agent = self._infer_agent_from_alias(clean_provider)

        if not model_normalized:
            model_normalized = self.normalize_model(best_agent, clean_model, clean_provider)

        descriptor = self._agents.get(best_agent)
        registry_status = descriptor.status if descriptor is not None else ""

        return {
            "agent_id": best_agent,
            "provider": clean_provider,
            "model_raw": clean_model,
            "model_normalized": model_normalized,
            "registry_status": registry_status,
            "agent_source": "provider_model_infer" if best_agent else "",
        }

    @staticmethod
    def _safe_shlex_split(value: str) -> list[str]:
        if not value:
            return []
        try:
            return shlex.split(value, posix=True)
        except Exception:
            return value.split()

    @staticmethod
    def _extract_executable_token(command: str) -> str:
        tokens = AgentRegistry._safe_shlex_split(command)
        if not tokens:
            return ""
        idx = 0
        n = len(tokens)
        while idx < n:
            token = (tokens[idx] or "").strip()
            if not token:
                idx += 1
                continue
            if token in {"sudo", "command"}:
                idx += 1
                continue
            if token in {"env", "/usr/bin/env"}:
                idx += 1
                while idx < n:
                    env_token = (tokens[idx] or "").strip()
                    if not env_token or env_token.startswith("-"):
                        idx += 1
                        continue
                    if "=" in env_token and not env_token.startswith("="):
                        idx += 1
                        continue
                    break
                continue
            if token.startswith("-"):
                idx += 1
                continue
            if "=" in token and not token.startswith("="):
                idx += 1
                continue
            return os.path.basename(token).strip().lower()
        return ""

    @staticmethod
    def extract_model_provider_from_command(command: str) -> dict[str, str]:
        tokens = AgentRegistry._safe_shlex_split(command)
        out = {"model_raw": "", "provider": "", "agent_hint": ""}
        if not tokens:
            return out

        idx = 0
        while idx < len(tokens):
            token = str(tokens[idx] or "")
            low = token.lower()
            nxt = str(tokens[idx + 1] or "") if idx + 1 < len(tokens) else ""

            if low in {"--model", "-m"} and nxt:
                out["model_raw"] = nxt.strip()
                idx += 2
                continue
            if low.startswith("--model="):
                out["model_raw"] = token.split("=", 1)[1].strip()
                idx += 1
                continue

            if low == "--provider" and nxt:
                out["provider"] = nxt.strip().lower()
                idx += 2
                continue
            if low.startswith("--provider="):
                out["provider"] = token.split("=", 1)[1].strip().lower()
                idx += 1
                continue

            if low == "--agent" and nxt:
                out["agent_hint"] = nxt.strip().lower()
                idx += 2
                continue
            if low.startswith("--agent="):
                out["agent_hint"] = token.split("=", 1)[1].strip().lower()
                idx += 1
                continue

            idx += 1

        return out

    def infer_from_lineage(self, lineage: list[dict[str, Any]]) -> AgentMatch | None:
        best: AgentMatch | None = None

        for row in lineage:
            if not isinstance(row, dict):
                continue
            comm = self._normalize_lower(row.get("comm"))
            command = self._normalize(row.get("command"))
            exe = self._extract_executable_token(command)
            model_meta = self.extract_model_provider_from_command(command)
            model_raw = self._normalize(model_meta.get("model_raw"))
            provider = self._normalize_lower(model_meta.get("provider"))
            inline_agent = self._normalize_lower(model_meta.get("agent_hint"))
            haystack = f"{comm} {command.lower()}"

            for descriptor in self._agents.values():
                exact = False
                token_match = False

                if exe and exe in descriptor.executables:
                    exact = True
                comm_base = os.path.basename(comm).strip().lower() if comm else ""
                if comm_base and comm_base in descriptor.executables:
                    exact = True

                for token in descriptor.process_tokens:
                    if token and token in haystack:
                        token_match = True
                        break

                if not exact and not token_match:
                    continue

                resolved_agent = descriptor.agent_id
                if inline_agent:
                    mapped = self._infer_agent_from_alias(inline_agent)
                    if mapped:
                        resolved_agent = mapped

                normalized_model = self.normalize_model(resolved_agent, model_raw, provider)
                if exact and model_raw:
                    tier = "verified"
                    score = 0.80
                elif exact:
                    tier = "heuristic"
                    score = 0.60
                else:
                    if descriptor.status == "community":
                        tier = "community"
                        score = 0.45
                    else:
                        tier = "heuristic"
                        score = 0.60

                candidate = AgentMatch(
                    agent_id=resolved_agent,
                    registry_status=descriptor.status,
                    match_kind=("exact_executable" if exact else "token"),
                    confidence=score,
                    evidence_tier=tier,
                    model_raw=model_raw,
                    model_normalized=normalized_model,
                    provider=provider,
                    evidence=(
                        f"lineage_comm={comm}",
                        f"lineage_exe={exe}",
                        f"lineage_match={'exact' if exact else 'token'}",
                    ),
                )

                if best is None:
                    best = candidate
                    continue

                if candidate.confidence > best.confidence:
                    best = candidate
                    continue

                if (
                    candidate.confidence == best.confidence
                    and candidate.registry_status == "verified"
                    and best.registry_status != "verified"
                ):
                    best = candidate

        return best


def build_model_fingerprint(agent: str, normalized_model: str, raw_model: str) -> str:
    clean_agent = str(agent or "").strip().lower()
    if not clean_agent:
        return ""
    model = str(normalized_model or "").strip().lower() or str(raw_model or "").strip().lower()
    if not model:
        return ""
    return f"{clean_agent}_{model}"
