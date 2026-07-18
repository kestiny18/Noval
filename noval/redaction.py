"""Sensitive output redaction shared by tool execution.

Tools are the model's senses, but their raw outputs may contain credentials
from config files, command output, MCP responses, or error messages.  Redaction
belongs in the execution boundary so every tool benefits from the same safety
net before content is sent to the model or persisted in the session.
"""
from __future__ import annotations

import re
from typing import Any


REDACTION = "<redacted>"

_SENSITIVE_KEY = (
    r"password|passwd|pwd|"
    r"secret|secretkey|appsecret|"
    r"token|accesstoken|access_token|"
    r"apikey|api_key|appkey|accesskey|access_key|"
    r"privatekey|private_key|"
    r"webhook|roboturl"
)

_SENSITIVE_NAME_RE = re.compile(
    rf"(?i)^[A-Za-z0-9_.-]*(?:{_SENSITIVE_KEY}|authorization|signature)[A-Za-z0-9_.-]*$"
)

_KEY_VALUE_RE = re.compile(
    rf"(?im)^(\s*(?:Error:\s*)?[A-Za-z0-9_.-]*(?:{_SENSITIVE_KEY})[A-Za-z0-9_.-]*\s*[:=]\s*)([^\r\n#]+)"
)
_JSON_VALUE_RE = re.compile(
    rf'(?i)("[A-Za-z0-9_.-]*(?:{_SENSITIVE_KEY})[A-Za-z0-9_.-]*"\s*:\s*)("([^"\\]|\\.)*"|[^,\r\n}}]+)'
)
_URL_QUERY_RE = re.compile(
    r"(?i)([?&](?:key|token|access_token|secret|password|signature)=)[^&\s\"']+"
)
_AUTH_RE = re.compile(r"(?im)^(\s*authorization\s*:\s*(?:bearer|basic)\s+)\S+")
_PEM_PRIVATE_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)


def redact_sensitive_text(text: str) -> str:
    """Redact common credential shapes from model-visible tool output."""
    if not text:
        return text
    redacted = _PEM_PRIVATE_RE.sub("-----BEGIN PRIVATE KEY-----\n<redacted>\n-----END PRIVATE KEY-----", text)
    redacted = _AUTH_RE.sub(lambda m: m.group(1) + REDACTION, redacted)
    redacted = _URL_QUERY_RE.sub(lambda m: m.group(1) + REDACTION, redacted)
    redacted = _KEY_VALUE_RE.sub(_redact_key_value, redacted)
    redacted = _JSON_VALUE_RE.sub(_redact_json_value, redacted)
    return redacted


def redact_sensitive_data(value: Any) -> Any:
    """Recursively redact credentials while preserving JSON-safe structure."""
    if isinstance(value, dict):
        return {
            key: _redact_sensitive_field(key, item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive_data(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive_data(item) for item in value)
    if isinstance(value, str):
        return redact_sensitive_text(value)
    return value


def _redact_sensitive_field(key: object, value: Any) -> Any:
    if not isinstance(key, str) or not _SENSITIVE_NAME_RE.fullmatch(key):
        return redact_sensitive_data(value)
    if isinstance(value, (dict, list, tuple)):
        return redact_sensitive_data(value)
    if value is None:
        return None
    if isinstance(value, str) and _looks_like_code_reference(value):
        return value
    return REDACTION


def _redact_key_value(match: re.Match[str]) -> str:
    value = match.group(2).strip()
    if _looks_like_code_reference(value):
        return match.group(0)
    return match.group(1) + REDACTION


def _redact_json_value(match: re.Match[str]) -> str:
    value = match.group(2).strip()
    if value.startswith('"'):
        return match.group(1) + f'"{REDACTION}"'
    return match.group(1) + REDACTION


def _looks_like_code_reference(value: str) -> bool:
    """Avoid blinding the model when reading source types or env references."""
    v = value.strip().rstrip(";")
    if not v:
        return False
    if v in {"...", "None", "null", "undefined"}:
        return True
    if re.fullmatch(r"\$[{(]?[A-Za-z_][A-Za-z0-9_]*[})]?", v):
        return True
    if re.fullmatch(r"%[A-Za-z_][A-Za-z0-9_]*%", v):
        return True
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*\([^)]*\)", v):
        return True

    # Type annotations and declarations such as:
    # access_token: string
    # token: TokenType
    # const access_token: string = ...
    type_atom = (
        r"(?:string|str|int|float|bool|boolean|bytes|dict|list|set|tuple|"
        r"Any|Optional|Union|Literal|"
        r"[A-Z][A-Za-z0-9]*(?:\.[A-Z][A-Za-z0-9]*)?)"
    )
    type_name = rf"{type_atom}(?:\[[^\]]+\])?"
    type_expr = rf"{type_name}(?:\s*[|,]\s*{type_name})*"
    if re.fullmatch(type_expr, v):
        return True
    if re.fullmatch(rf"{type_expr}\s*=\s*(?:\.\.\.|None|null|undefined)", v):
        return True
    return False
