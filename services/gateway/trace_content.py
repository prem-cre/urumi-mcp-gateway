"""Small, bounded trace-content redaction used before optional persistence."""

import json
import re
from typing import Any

_MAX_CHARS = 4000
_MAX_DEPTH = 6
_MAX_ITEMS = 40
_SENSITIVE_KEY = re.compile(
    r"(?:email|phone|mobile|address|billing|shipping|customer|first.?name|last.?name|"
    r"password|secret|token|authorization|api.?key|card|payment|postcode|postal.?code)",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_LONG_NUMBER = re.compile(r"(?<!\w)(?:\+?\d[\d .()\-]{8,}\d)(?!\w)")
_BEARER = re.compile(r"\bBearer\s+[^\s,;]+", re.IGNORECASE)
_SECRET_TOKEN = re.compile(
    r"\b(?:sk|rk|urm|ghp|gho|xox[baprs])-?[A-Za-z0-9_-]{12,}\b", re.IGNORECASE
)
_URL_QUERY = re.compile(r"(https?://[^\s?#]+)\?[^\s#]+", re.IGNORECASE)


def _redact_long_number(match: re.Match[str]) -> str:
    return (
        "[REDACTED_NUMBER]"
        if sum(character.isdigit() for character in match.group()) >= 10
        else match.group()
    )


def _redact(value: Any, depth: int = 0) -> Any:
    if depth > _MAX_DEPTH:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        result = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= _MAX_ITEMS:
                result["…"] = "[TRUNCATED]"
                break
            name = str(key)[:100]
            result[name] = "[REDACTED]" if _SENSITIVE_KEY.search(name) else _redact(item, depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        items = [_redact(item, depth + 1) for item in value[:_MAX_ITEMS]]
        if len(value) > _MAX_ITEMS:
            items.append("[TRUNCATED]")
        return items
    if isinstance(value, str):
        text = _EMAIL.sub("[REDACTED_EMAIL]", value)
        text = _BEARER.sub("Bearer [REDACTED]", text)
        text = _SECRET_TOKEN.sub("[REDACTED_SECRET]", text)
        text = _LONG_NUMBER.sub(_redact_long_number, text)
        return _URL_QUERY.sub(r"\1?[REDACTED]", text)[:_MAX_CHARS]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:_MAX_CHARS]


def safe_trace_preview(value: Any) -> str | None:
    """Return bounded redacted JSON/text suitable for optional short-term trace storage.

    This is defense in depth, not a PII classifier. Callers still need to enable content capture
    deliberately and use synthetic/demo data where possible.
    """
    try:
        source = value
        if isinstance(value, str):
            try:
                decoded = json.loads(value)
                if isinstance(decoded, (dict, list)):
                    source = decoded
            except json.JSONDecodeError:
                pass
        cleaned = _redact(source)
        text = (
            cleaned
            if isinstance(cleaned, str)
            else json.dumps(cleaned, ensure_ascii=False, separators=(",", ":"))
        )
    except (TypeError, ValueError):
        return None
    return text[:_MAX_CHARS]
