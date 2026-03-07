import math
import re
from dataclasses import dataclass


class PrivacyGuardError(RuntimeError):
    """Raised when sanitization cannot guarantee safe LLM egress."""


@dataclass
class SanitizeResult:
    text: str
    redaction_count: int
    flags: list[str]
    had_high_entropy: bool
    had_known_secret_pattern: bool


class PrivacyGuard:
    REDACTED_SECRET = "<REDACTED_SECRET>"
    REDACTED_CREDENTIALS = "<REDACTED_CREDENTIALS>"
    _REDACTED_PREFIX = "<REDACTED_"

    # Common secret env names + wildcard-like families.
    _KNOWN_SECRET_NAME_RE = re.compile(
        r"(?i)\b("
        r"(?:[A-Z][A-Z0-9_]*_(?:KEY|TOKEN|SECRET|PASSWORD))|"
        r"(?:AWS_[A-Z0-9_]+)|"
        r"(?:OPENAI_API_KEY)|"
        r"(?:ANTHROPIC_API_KEY)|"
        r"(?:GROQ_API_KEY)|"
        r"(?:GEMINI_API_KEY)"
        r")\b"
    )

    _URL_CREDENTIAL_RE = re.compile(
        r"(?P<scheme>\b[a-zA-Z][a-zA-Z0-9+.-]*://)(?P<creds>[^/\s@]+)@"
    )
    _EXPORT_ASSIGN_RE = re.compile(
        r"(?P<prefix>\bexport\s+)"
        r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
        r"\s*=\s*(?![=])(?P<value>(?:\"[^\"]*\"|'[^']*'|[^\s;]+))"
    )
    _INLINE_ASSIGN_RE = re.compile(
        r"(?<![A-Za-z0-9_])"
        r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
        r"\s*=\s*(?![=])(?P<value>(?:\"[^\"]*\"|'[^']*'|[^\s;]+))"
    )
    _DOTENV_LINE_RE = re.compile(
        r"^\s*(?P<export>export\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>.+?)\s*$"
    )
    _SECRET_KEY_VALUE_RE = re.compile(
        r"(?P<name>"
        r"(?:[A-Za-z_][A-Za-z0-9_]*_(?:KEY|TOKEN|SECRET|PASSWORD))|"
        r"(?:AWS_[A-Za-z0-9_]+)|"
        r"(?:OPENAI_API_KEY)|"
        r"(?:ANTHROPIC_API_KEY)|"
        r"(?:GROQ_API_KEY)|"
        r"(?:GEMINI_API_KEY)"
        r")"
        r"(?P<mid>\s*(?:=|:)\s*)"
        r"(?P<quote>['\"]?)"
        r"(?P<value>[^'\"\s,}]+)"
        r"(?P=quote)",
        flags=re.IGNORECASE,
    )
    _JWT_RE = re.compile(
        r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"
    )
    _HEX_RE = re.compile(r"\b[a-fA-F0-9]{32,}\b")
    _BASE64_CANDIDATE_RE = re.compile(r"\b[A-Za-z0-9+/_=-]{32,}\b")

    def __init__(
        self,
        history_max_lines: int = 12,
        history_line_max_chars: int = 240,
        history_total_max_chars: int = 2400,
        log_max_chars: int = 320,
    ):
        self.history_max_lines = max(1, int(history_max_lines))
        self.history_line_max_chars = max(20, int(history_line_max_chars))
        self.history_total_max_chars = max(100, int(history_total_max_chars))
        self.log_max_chars = max(80, int(log_max_chars))

    @classmethod
    def _is_redacted_marker(cls, value: str) -> bool:
        return str(value or "").startswith(cls._REDACTED_PREFIX)

    @staticmethod
    def _shannon_entropy(value: str) -> float:
        if not value:
            return 0.0
        counts: dict[str, int] = {}
        for ch in value:
            counts[ch] = counts.get(ch, 0) + 1
        total = len(value)
        entropy = 0.0
        for count in counts.values():
            p = count / total
            entropy -= p * math.log2(p)
        return entropy

    def _redact_url_credentials(self, text: str) -> tuple[str, int]:
        count = 0

        def repl(match: re.Match) -> str:
            nonlocal count
            creds = match.group("creds")
            if self._is_redacted_marker(creds):
                return match.group(0)
            count += 1
            return f"{match.group('scheme')}{self.REDACTED_CREDENTIALS}@"

        return (self._URL_CREDENTIAL_RE.sub(repl, text), count)

    def _redact_env_assignments(self, text: str) -> tuple[str, int]:
        count = 0

        def is_env_var_name(name: str) -> bool:
            raw = str(name or "")
            if not raw:
                return False
            if self._KNOWN_SECRET_NAME_RE.fullmatch(raw):
                return True
            if raw.upper() == raw:
                return True
            return raw.lower() in {"http_proxy", "https_proxy", "no_proxy", "all_proxy"}

        def export_repl(match: re.Match) -> str:
            nonlocal count
            value = match.group("value").strip()
            if self._is_redacted_marker(value.strip("\"'")):
                return match.group(0)
            count += 1
            return f"{match.group('prefix')}{match.group('name')}={self.REDACTED_SECRET}"

        text = self._EXPORT_ASSIGN_RE.sub(export_repl, text)

        def inline_repl(match: re.Match) -> str:
            nonlocal count
            name = match.group("name")
            if not is_env_var_name(name):
                return match.group(0)
            value = match.group("value").strip()
            if self._is_redacted_marker(value.strip("\"'")):
                return match.group(0)
            count += 1
            return f"{name}={self.REDACTED_SECRET}"

        text = self._INLINE_ASSIGN_RE.sub(inline_repl, text)
        return (text, count)

    def _redact_dotenv_lines(self, text: str) -> tuple[str, int]:
        count = 0
        out_lines: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                out_lines.append(line)
                continue
            match = self._DOTENV_LINE_RE.match(line)
            if not match:
                out_lines.append(line)
                continue
            value = (match.group("value") or "").strip().strip("\"'")
            if self._is_redacted_marker(value):
                out_lines.append(line)
                continue
            count += 1
            export_prefix = match.group("export") or ""
            out_lines.append(f"{export_prefix}{match.group('name')}={self.REDACTED_SECRET}")
        if not out_lines:
            return ("", 0)
        return ("\n".join(out_lines), count)

    def _redact_known_secret_keys(self, text: str) -> tuple[str, int]:
        count = 0

        def repl(match: re.Match) -> str:
            nonlocal count
            value = (match.group("value") or "").strip()
            if self._is_redacted_marker(value):
                return match.group(0)
            count += 1
            quote = match.group("quote") or ""
            return f"{match.group('name')}{match.group('mid')}{quote}{self.REDACTED_SECRET}{quote}"

        return (self._SECRET_KEY_VALUE_RE.sub(repl, text), count)

    def _redact_high_entropy(self, text: str) -> tuple[str, int, bool]:
        count = 0
        had_entropy = False

        def jwt_repl(match: re.Match) -> str:
            nonlocal count, had_entropy
            token = match.group(0)
            if self._is_redacted_marker(token):
                return token
            count += 1
            had_entropy = True
            return self.REDACTED_SECRET

        text = self._JWT_RE.sub(jwt_repl, text)

        def hex_repl(match: re.Match) -> str:
            nonlocal count, had_entropy
            token = match.group(0)
            if self._is_redacted_marker(token):
                return token
            count += 1
            had_entropy = True
            return self.REDACTED_SECRET

        text = self._HEX_RE.sub(hex_repl, text)

        def base64_repl(match: re.Match) -> str:
            nonlocal count, had_entropy
            token = match.group(0)
            if self._is_redacted_marker(token):
                return token
            if len(token) < 32:
                return token
            # Avoid redacting common filesystem-like paths.
            if token.startswith("/") or token.count("/") > 1 or "." in token:
                return token
            if token.isdigit():
                return token
            entropy = self._shannon_entropy(token)
            looks_tokenish = bool(re.search(r"[0-9]", token) and re.search(r"[A-Za-z]", token))
            if looks_tokenish and entropy >= 3.6:
                count += 1
                had_entropy = True
                return self.REDACTED_SECRET
            return token

        text = self._BASE64_CANDIDATE_RE.sub(base64_repl, text)
        return (text, count, had_entropy)

    def sanitize_text(self, text: str, context: str = "generic") -> SanitizeResult:
        source = str(text or "")
        flags: list[str] = []
        redaction_count = 0
        had_known_secret_pattern = bool(self._KNOWN_SECRET_NAME_RE.search(source))
        had_high_entropy = False
        cleaned = source

        cleaned, url_count = self._redact_url_credentials(cleaned)
        if url_count:
            redaction_count += url_count
            flags.append("url_credentials")

        cleaned, env_count = self._redact_env_assignments(cleaned)
        if env_count:
            redaction_count += env_count
            flags.append("env_assignment")

        cleaned, dotenv_count = self._redact_dotenv_lines(cleaned)
        if dotenv_count:
            redaction_count += dotenv_count
            flags.append("dotenv_line")

        cleaned, named_count = self._redact_known_secret_keys(cleaned)
        if named_count:
            redaction_count += named_count
            flags.append("known_secret_name")
            had_known_secret_pattern = True

        cleaned, entropy_count, entropy_flag = self._redact_high_entropy(cleaned)
        if entropy_count:
            redaction_count += entropy_count
            flags.append("high_entropy")
        had_high_entropy = entropy_flag

        if context:
            cleaned = cleaned.replace("\r\n", "\n")

        # Deterministic output ordering for flags.
        flags = sorted(set(flags))
        return SanitizeResult(
            text=cleaned,
            redaction_count=redaction_count,
            flags=flags,
            had_high_entropy=had_high_entropy,
            had_known_secret_pattern=had_known_secret_pattern,
        )

    def sanitize_messages(self, messages: list[dict]) -> list[dict]:
        sanitized: list[dict] = []
        for msg in messages or []:
            if not isinstance(msg, dict):
                sanitized.append(msg)
                continue
            clean_msg = dict(msg)
            content = clean_msg.get("content")
            if isinstance(content, str):
                clean_msg["content"] = self.sanitize_text(content, context="message").text
            elif isinstance(content, list):
                clean_parts = []
                for part in content:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        part_copy = dict(part)
                        part_copy["text"] = self.sanitize_text(part_copy["text"], context="message").text
                        clean_parts.append(part_copy)
                    else:
                        clean_parts.append(part)
                clean_msg["content"] = clean_parts
            sanitized.append(clean_msg)
        return sanitized

    def sanitize_history_lines(self, lines: list[str]) -> tuple[list[str], int]:
        selected = list(lines or [])[-self.history_max_lines :]
        sanitized_lines: list[str] = []
        total_chars = 0
        total_redactions = 0
        for line in selected:
            result = self.sanitize_text(line, context="history")
            clean = result.text
            total_redactions += result.redaction_count
            if len(clean) > self.history_line_max_chars:
                clean = clean[: self.history_line_max_chars].rstrip() + "..."
            projected = total_chars + len(clean)
            if projected > self.history_total_max_chars:
                break
            sanitized_lines.append(clean)
            total_chars = projected
        return (sanitized_lines, total_redactions)

    def sanitize_for_log(self, text: str) -> str:
        result = self.sanitize_text(text, context="log")
        clean = result.text.replace("\n", " ").replace("\r", " ")
        if len(clean) > self.log_max_chars:
            clean = clean[: self.log_max_chars].rstrip() + "..."
        return clean

    def assert_safe_or_raise(self, text: str):
        value = str(text or "")
        for match in self._URL_CREDENTIAL_RE.finditer(value):
            creds = (match.group("creds") or "").strip()
            if self._is_redacted_marker(creds):
                continue
            raise PrivacyGuardError("Unredacted URL credentials detected")

        def has_unredacted_assignment(match: re.Match) -> bool:
            val = (match.group("value") or "").strip().strip("\"'")
            return not self._is_redacted_marker(val)

        if any(has_unredacted_assignment(m) for m in self._SECRET_KEY_VALUE_RE.finditer(value)):
            raise PrivacyGuardError("Unredacted known secret assignment detected")

        for match in self._INLINE_ASSIGN_RE.finditer(value):
            name = (match.group("name") or "").strip()
            if not (
                self._KNOWN_SECRET_NAME_RE.fullmatch(name)
                or name.upper() == name
                or name.lower() in {"http_proxy", "https_proxy", "no_proxy", "all_proxy"}
            ):
                continue
            val = (match.group("value") or "").strip().strip("\"'")
            if not self._is_redacted_marker(val):
                raise PrivacyGuardError("Unredacted env assignment detected")

        if self._JWT_RE.search(value):
            raise PrivacyGuardError("Unredacted JWT-like token detected")
        if self._HEX_RE.search(value):
            raise PrivacyGuardError("Unredacted high-entropy hex token detected")
