from __future__ import annotations

import json
from typing import Any, Optional

import duckdb

from db import connection_lock, get_connection
from services.crypto_service import decrypt_json, encrypt_json, is_encrypted_value


from services.sensitive_keys import is_sensitive_key as _is_sensitive_key


def _decode_setting(key: str, value: Any) -> Any:
    if _is_sensitive_key(key) or is_encrypted_value(value):
        return decrypt_json(value, None)
    return value


class SettingsService:
    def __init__(self, db: duckdb.DuckDBPyConnection):
        self.db = db

    def get_all(self) -> dict:
        with connection_lock():
            rows = self.db.execute(
                "SELECT key, value FROM metadata.settings"
            ).fetchall()
        return {r[0]: _decode_setting(r[0], r[1]) for r in rows}

    def get(self, key: str) -> Optional[Any]:
        with connection_lock():
            row = self.db.execute(
                "SELECT value FROM metadata.settings WHERE key = ?", [key]
            ).fetchone()
        return _decode_setting(key, row[0]) if row else None

    def set(self, key: str, value: Any) -> None:
        stored_value = encrypt_json(value) if _is_sensitive_key(key) else value
        with connection_lock():
            self.db.execute(
                "INSERT OR REPLACE INTO metadata.settings (key, value, updated_at) VALUES (?, ?::JSON, CURRENT_TIMESTAMP)",
                [key, json.dumps(stored_value)],
            )

    def update_group(self, prefix: str, data: dict) -> None:
        with connection_lock():
            for key, value in data.items():
                full_key = f"{prefix}_{key}" if prefix else key
                stored_value = encrypt_json(value) if _is_sensitive_key(full_key) else value
                self.db.execute(
                    "INSERT OR REPLACE INTO metadata.settings (key, value, updated_at) VALUES (?, ?::JSON, CURRENT_TIMESTAMP)",
                    [full_key, json.dumps(stored_value)],
                )