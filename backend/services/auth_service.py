from __future__ import annotations

import logging
import os
import secrets
import stat
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import uuid4

import bcrypt
from jose import JWTError, jwt

logger = logging.getLogger(__name__)

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.getenv("PRISMBI_DATA_DIR") or os.path.join(BACKEND_DIR, "data")

_IS_DEFAULT_SECRET = True


def _jwt_key_file_path() -> str:
    return os.getenv("PRISMBI_JWT_SECRET_KEY_FILE") or os.path.join(DATA_DIR, "jwt_secret.key")


def _read_dev_secret(key_path: str) -> str | None:
    try:
        with open(key_path, "r", encoding="utf-8") as handle:
            persisted = handle.read().strip()
            return persisted or None
    except FileNotFoundError:
        return None


def _wait_for_dev_secret(key_path: str, attempts: int = 25, delay_s: float = 0.02) -> str | None:
    for _ in range(max(1, attempts)):
        persisted = _read_dev_secret(key_path)
        if persisted:
            return persisted
        time.sleep(max(0.0, delay_s))
    return None


def _load_or_create_dev_secret() -> tuple[str, bool]:
    key_path = _jwt_key_file_path()
    key_dir = os.path.dirname(key_path) or "."
    os.makedirs(key_dir, exist_ok=True)

    persisted = _read_dev_secret(key_path)
    if persisted:
        return persisted, False

    generated = secrets.token_hex(64)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(key_path, flags, stat.S_IRUSR | stat.S_IWUSR)
    except FileExistsError:
        persisted = _wait_for_dev_secret(key_path)
        if persisted:
            return persisted, False
        raise RuntimeError("JWT dev secret key file exists but is empty")

    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(generated)
        handle.write("\n")
    os.chmod(key_path, stat.S_IRUSR | stat.S_IWUSR)
    return generated, True


def _init_secret_key() -> str:
    global _IS_DEFAULT_SECRET
    key = os.environ.get("JWT_SECRET_KEY")
    if key:
        _IS_DEFAULT_SECRET = False
        return key
    env = os.environ.get("PRISMBI_ENV", os.environ.get("ENV", "")).strip().lower()
    if env in ("prod", "production"):
        raise RuntimeError(
            "JWT_SECRET_KEY environment variable is required in production. "
            "Generate a secure key (e.g., openssl rand -hex 64) and set it."
        )

    _IS_DEFAULT_SECRET = True
    try:
        key, created = _load_or_create_dev_secret()
    except Exception:
        generated = secrets.token_hex(64)
        logger.warning(
            "JWT_SECRET_KEY not set and persistent development key could not be loaded. "
            "Falling back to ephemeral key; sessions will be invalidated on restart.",
            exc_info=True,
        )
        return generated

    if created:
        logger.warning(
            "JWT_SECRET_KEY not set — generated a persistent development key at %s. "
            "Set JWT_SECRET_KEY for production.",
            _jwt_key_file_path(),
        )
    else:
        logger.info(
            "JWT_SECRET_KEY not set — using persistent development key from %s. "
            "Set JWT_SECRET_KEY for production.",
            _jwt_key_file_path(),
        )
    return key


def is_default_secret() -> bool:
    return _IS_DEFAULT_SECRET


SECRET_KEY = _init_secret_key()


class AuthService:
    def __init__(self, secret_key: str, algorithm: str = "HS256"):
        self.secret_key = secret_key
        self.algorithm = algorithm

    def hash_password(self, password: str) -> str:
        salt = bcrypt.gensalt()
        return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")

    def verify_password(self, plain: str, hashed: str) -> bool:
        try:
            return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
        except (ValueError, TypeError):
            return False

    def create_token(self, user_id: int, username: str, expires_delta_hours: int = 24, session_id: Optional[str] = None, extra_claims: Optional[dict] = None) -> str:
        now = datetime.now(timezone.utc)
        payload = {
            "sub": str(user_id),
            "username": username,
            "sid": session_id,
            "jti": str(uuid4()),
            "iat": now,
            "exp": now + timedelta(hours=expires_delta_hours),
        }
        if extra_claims:
            payload.update(extra_claims)
        return jwt.encode(payload, self.secret_key, algorithm=self.algorithm)

    def decode_token(self, token: str) -> Optional[dict]:
        try:
            return jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
        except JWTError:
            return None

    def create_ws_token(self, user_id: int, username: str) -> str:
        now = datetime.now(timezone.utc)
        payload = {
            "sub": str(user_id),
            "username": username,
            "scope": "ws",
            "exp": now + timedelta(minutes=5),
        }
        return jwt.encode(payload, self.secret_key, algorithm=self.algorithm)


auth_service = AuthService(secret_key=SECRET_KEY)
