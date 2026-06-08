from __future__ import annotations

SENSITIVE_SETTING_PARTS = ("api_key", "password", "secret")

_EXACT_SENSITIVE_KEYS = frozenset({"token"})


def is_sensitive_key(key: str) -> bool:
    normalized = key.lower()
    for part in _EXACT_SENSITIVE_KEYS:
        if part == normalized or normalized.endswith("_" + part) or normalized.startswith(part + "_") or ("_" + part + "_") in normalized:
            return True
    for part in SENSITIVE_SETTING_PARTS:
        if part in normalized:
            return True
    return False