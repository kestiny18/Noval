"""Sensitive output redaction shared by tool execution.

Tools are the model's senses, but their raw outputs may contain credentials
from config files, command output, MCP responses, or error messages.  Redaction
belongs in the execution boundary so every tool benefits from the same safety
net before content is sent to the model or persisted in the session.
"""
from __future__ import annotations

import re


REDACTION = "<redacted>"

_SENSITIVE_KEY = (
    r"password|passwd|pwd|"
    r"secret|secretkey|appsecret|"
    r"token|accesstoken|access_token|"
    r"apikey|api_key|appkey|accesskey|access_key|"
    r"privatekey|private_key|"
    r"webhook|roboturl"
)

_KEY_VALUE_RE = re.compile(
    rf"(?im)^(\s*[A-Za-z0-9_.-]*(?:{_SENSITIVE_KEY})[A-Za-z0-9_.-]*\s*[:=]\s*)([^\r\n#]+)"
)
_JSON_VALUE_RE = re.compile(
    rf'(?i)("?[A-Za-z0-9_.-]*(?:{_SENSITIVE_KEY})[A-Za-z0-9_.-]*"?\s*:\s*)("([^"\\]|\\.)*"|[^,\r\n}}]+)'
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
    redacted = _KEY_VALUE_RE.sub(lambda m: m.group(1) + REDACTION, redacted)
    redacted = _JSON_VALUE_RE.sub(_redact_json_value, redacted)
    return redacted


def _redact_json_value(match: re.Match[str]) -> str:
    value = match.group(2).strip()
    if value.startswith('"'):
        return match.group(1) + f'"{REDACTION}"'
    return match.group(1) + REDACTION
