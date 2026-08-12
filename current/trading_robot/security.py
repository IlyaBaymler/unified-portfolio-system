from __future__ import annotations

"""Secret redaction and canary scanning for release/support artifacts."""

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping
import zipfile

from .logging_setup import redact_sensitive_text

_SECRET_KEY_RE = re.compile(
    r"(?i)(?:^|[_-])(token|api[_-]?key|authorization|password|secret|credential)(?:$|[_-])"
)
_BEARER_RE = re.compile(r"(?i)Bearer\s+[A-Za-z0-9._~+\-/=]{12,}")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{8,}\b")
_TBANK_ASSIGNMENT_RE = re.compile(
    r"(?i)TBANK_(?:SANDBOX_)?TOKEN\s*[=:]\s*([^\s,;]+)"
)


@dataclass(frozen=True, slots=True)
class SecretFinding:
    source: str
    kind: str
    excerpt: str

    def to_dict(self) -> dict[str, str]:
        return {"source": self.source, "kind": self.kind, "excerpt": self.excerpt}


def redact_object(value: Any) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, nested in value.items():
            key = str(raw_key)
            if _SECRET_KEY_RE.search(key) or key.lower() in {
                "token", "api_token", "tbank_sandbox_token", "authorization"
            }:
                result[key] = "<REDACTED>"
            else:
                result[key] = redact_object(nested)
        return result
    if isinstance(value, list):
        return [redact_object(item) for item in value]
    if isinstance(value, tuple):
        return [redact_object(item) for item in value]
    if isinstance(value, str):
        return redact_sensitive_text(value)
    return value


def redact_json_text(text: str) -> str:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return redact_sensitive_text(text)
    return json.dumps(redact_object(value), ensure_ascii=False, indent=2, default=str)


def scan_text(text: str, *, source: str = "text", canaries: Iterable[str] = ()) -> list[SecretFinding]:
    findings: list[SecretFinding] = []
    for canary in canaries:
        candidate = str(canary)
        if candidate and candidate in text:
            findings.append(SecretFinding(source, "CANARY", "<canary found>"))
    if _BEARER_RE.search(text):
        findings.append(SecretFinding(source, "BEARER_TOKEN", "Bearer <redacted>"))
    if _JWT_RE.search(text):
        findings.append(SecretFinding(source, "JWT", "eyJ…"))
    match = _TBANK_ASSIGNMENT_RE.search(text)
    if match and match.group(1) not in {"", "<REDACTED>", "''", '\"\"'}:
        findings.append(SecretFinding(source, "TBANK_TOKEN_ASSIGNMENT", "TBANK_*_TOKEN=<redacted>"))
    return findings


def scan_zip(path: str | Path, *, canaries: Iterable[str] = (), max_member_bytes: int = 20_000_000) -> list[SecretFinding]:
    target = Path(path)
    findings: list[SecretFinding] = []
    try:
        with zipfile.ZipFile(target) as archive:
            for info in archive.infolist():
                if info.is_dir() or info.file_size > max_member_bytes:
                    continue
                try:
                    text = archive.read(info.filename).decode("utf-8")
                except (KeyError, UnicodeDecodeError, OSError):
                    continue
                findings.extend(scan_text(text, source=f"{target.name}!/{info.filename}", canaries=canaries))
    except (OSError, zipfile.BadZipFile) as exc:
        findings.append(SecretFinding(str(target), "INVALID_ZIP", str(exc)[:160]))
    return findings


__all__ = [
    "SecretFinding", "redact_sensitive_text", "redact_object", "redact_json_text",
    "scan_text", "scan_zip",
]
