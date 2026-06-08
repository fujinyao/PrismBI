from __future__ import annotations

import base64
import json
import os
import stat
import threading
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.getenv("PRISMBI_DATA_DIR") or os.path.join(BACKEND_DIR, "data")
ENCRYPTED_PREFIX = "enc:v1:"

_fernet: Fernet | None = None
_fernet_lock = threading.Lock()


def _key_file_path() -> str:
    return os.getenv("PRISMBI_ENCRYPTION_KEY_FILE") or os.path.join(DATA_DIR, "master.key")


def _validate_key(raw_key: str) -> bytes:
    key = raw_key.strip().encode("utf-8")
    try:
        decoded = base64.urlsafe_b64decode(key)
    except Exception as exc:
        raise RuntimeError("PRISMBI_ENCRYPTION_KEY must be a valid Fernet key") from exc
    if len(decoded) != 32:
        raise RuntimeError("PRISMBI_ENCRYPTION_KEY must decode to 32 bytes")
    return key


def _load_or_create_key() -> bytes:
    env_key = os.getenv("PRISMBI_ENCRYPTION_KEY")
    if env_key:
        return _validate_key(env_key)

    key_path = _key_file_path()
    os.makedirs(os.path.dirname(key_path), exist_ok=True)

    try:
        with open(key_path, "rb") as handle:
            return _validate_key(handle.read().decode("utf-8"))
    except FileNotFoundError:
        pass

    key = Fernet.generate_key()
    tmp_path = key_path + ".tmp"
    with open(tmp_path, "wb") as handle:
        handle.write(key)
    os.chmod(tmp_path, stat.S_IRUSR | stat.S_IWUSR)
    os.replace(tmp_path, key_path)
    return key


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        with _fernet_lock:
            if _fernet is None:
                _fernet = Fernet(_load_or_create_key())
    return _fernet


def is_encrypted_value(value: Any) -> bool:
    parsed = _json_value(value)
    return isinstance(parsed, str) and parsed.startswith(ENCRYPTED_PREFIX)


def encrypt_text(value: str) -> str:
    token = _get_fernet().encrypt(value.encode("utf-8")).decode("utf-8")
    return f"{ENCRYPTED_PREFIX}{token}"


def decrypt_text(value: str) -> str:
    if not value.startswith(ENCRYPTED_PREFIX):
        return value
    token = value[len(ENCRYPTED_PREFIX) :].encode("utf-8")
    try:
        return _get_fernet().decrypt(token).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError("Encrypted secret cannot be decrypted with the configured key") from exc


def encrypt_json(value: Any) -> str:
    return encrypt_text(json.dumps(value, default=str))


def decrypt_json(value: Any, fallback: Any = None) -> Any:
    parsed = _json_value(value)
    if isinstance(parsed, str) and parsed.startswith(ENCRYPTED_PREFIX):
        parsed = decrypt_text(parsed)
        try:
            return json.loads(parsed)
        except Exception:
            return fallback
    if parsed is None:
        return fallback
    return parsed


def _json_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list, int, float, bool)):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.startswith(ENCRYPTED_PREFIX):
            return text
        try:
            return json.loads(text)
        except Exception:
            return value
    return value
