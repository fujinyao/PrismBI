from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from typing import Optional

from db import connection_lock, get_connection, DATA_DIR

BACKUP_DIR = os.path.join(DATA_DIR, "backups")

_backup_lock = threading.Lock()
_restore_lock = threading.Lock()
_restore_in_progress = False


def is_restore_in_progress() -> bool:
    return _restore_in_progress

ALLOWED_BACKUP_NAMES = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.")


def _validate_backup_name(name: str) -> str:
    if not name:
        raise ValueError("Backup name must not be empty")
    for ch in name:
        if ch not in ALLOWED_BACKUP_NAMES:
            raise ValueError(f"Invalid character in backup name: {ch!r}")
    if ".." in name:
        raise ValueError("Backup name must not contain '..'")
    real_path = os.path.realpath(os.path.join(BACKUP_DIR, f"{name}.zip"))
    if not real_path.startswith(os.path.realpath(BACKUP_DIR) + os.sep) and real_path != os.path.realpath(BACKUP_DIR):
        raise ValueError("Backup name resolves outside backup directory")
    return name


def _ensure_backup_dir():
    os.makedirs(BACKUP_DIR, exist_ok=True)


def _backup_meta_path(name: str) -> str:
    _validate_backup_name(name)
    return os.path.join(BACKUP_DIR, f"{name}.meta.json")


def _backup_zip_path(name: str) -> str:
    _validate_backup_name(name)
    return os.path.join(BACKUP_DIR, f"{name}.zip")


def _validate_zip_members(zf: zipfile.ZipFile, dest_dir: str) -> None:
    dest_real = os.path.realpath(dest_dir)
    for info in zf.infolist():
        member_path = os.path.realpath(os.path.join(dest_dir, info.filename))
        if not member_path.startswith(dest_real + os.sep) and member_path != dest_real:
            raise ValueError(f"Zip member escapes target directory: {info.filename}")


def create_backup() -> dict:
    with _backup_lock:
        return _create_backup_inner()


def _create_backup_inner() -> dict:
    _ensure_backup_dir()
    timestamp = datetime.now(timezone.utc)
    name = timestamp.strftime("prismbi_backup_%Y%m%d_%H%M%S") + f"_{uuid.uuid4().hex[:8]}"
    zip_path = _backup_zip_path(name)
    meta_path = _backup_meta_path(name)

    db_path = os.path.join(DATA_DIR, "prismbi.duckdb")
    wal_path = db_path + ".wal"
    projects_dir = os.path.join(DATA_DIR, "projects")
    master_key_path = os.path.join(DATA_DIR, "master.key")

    db_bytes = b""
    wal_bytes = b""
    project_files: list[dict] = []
    key_bytes = b""

    with connection_lock():
        con = get_connection()
        con.execute("CHECKPOINT")

        if os.path.exists(db_path):
            with open(db_path, "rb") as f:
                db_bytes = f.read()

        if os.path.exists(wal_path):
            with open(wal_path, "rb") as f:
                wal_bytes = f.read()

        if os.path.exists(master_key_path):
            with open(master_key_path, "rb") as f:
                key_bytes = f.read()

    if os.path.isdir(projects_dir):
        for root, _dirs, files in os.walk(projects_dir, followlinks=False):
            for fn in files:
                fpath = os.path.join(root, fn)
                rel = os.path.relpath(fpath, DATA_DIR)
                with open(fpath, "rb") as f:
                    content = f.read()
                project_files.append({"path": rel, "content": content})

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if db_bytes:
            zf.writestr("prismbi.duckdb", db_bytes)
        if wal_bytes:
            zf.writestr("prismbi.duckdb.wal", wal_bytes)
        for pf in project_files:
            zf.writestr(pf["path"], pf["content"])
        if key_bytes:
            zf.writestr("master.key", key_bytes)
    zip_data = buf.getvalue()

    with open(zip_path, "wb") as f:
        f.write(zip_data)

    size = len(zip_data)
    meta = {
        "name": name,
        "created_at": timestamp.isoformat(),
        "size": size,
        "db_size": len(db_bytes),
        "project_files": len(project_files),
        "has_wal": bool(wal_bytes),
        "has_key": bool(key_bytes),
    }
    with open(meta_path, "w") as f:
        json.dump(meta, f)

    with connection_lock():
        con = get_connection()
        con.execute(
            "INSERT OR REPLACE INTO metadata.settings (key, value) VALUES (?, ?::JSON)",
            ["last_backup_at", json.dumps(timestamp.isoformat())],
        )

    return _backup_to_dict(name, meta)


def list_backups() -> list[dict]:
    _ensure_backup_dir()
    results = []
    if not os.path.isdir(BACKUP_DIR):
        return results
    for fn in os.listdir(BACKUP_DIR):
        if fn.endswith(".meta.json"):
            name = fn[: -len(".meta.json")]
            try:
                _validate_backup_name(name)
            except ValueError:
                continue
            meta_path = os.path.join(BACKUP_DIR, fn)
            try:
                with open(meta_path, "r") as f:
                    meta = json.load(f)
                results.append(_backup_to_dict(name, meta))
            except (json.JSONDecodeError, OSError):
                zip_path = _backup_zip_path(name)
                size = os.path.getsize(zip_path) if os.path.exists(zip_path) else 0
                results.append({
                    "name": name,
                    "created_at": name.replace("prismbi_backup_", "").replace("_", ":"),
                    "size": size,
                    "valid": False,
                })
    results.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return results


def get_backup(name: str) -> Optional[dict]:
    _validate_backup_name(name)
    meta_path = _backup_meta_path(name)
    if not os.path.exists(meta_path):
        return None
    try:
        with open(meta_path, "r") as f:
            meta = json.load(f)
        return _backup_to_dict(name, meta)
    except (json.JSONDecodeError, OSError):
        return None


def download_backup(name: str) -> Optional[bytes]:
    _validate_backup_name(name)
    zip_path = _backup_zip_path(name)
    if not os.path.exists(zip_path):
        return None
    with open(zip_path, "rb") as f:
        return f.read()


def restore_backup(zip_data: bytes) -> dict:
    global _restore_in_progress
    with _restore_lock:
        _restore_in_progress = True
        try:
            return _restore_backup_inner(zip_data)
        finally:
            _restore_in_progress = False


def _restore_backup_inner(zip_data: bytes) -> dict:
    buf = BytesIO(zip_data)
    temp_dir = os.path.join(BACKUP_DIR, f".restore_{int(time.time())}_{uuid.uuid4().hex[:8]}")
    os.makedirs(temp_dir, exist_ok=True)

    try:
        with zipfile.ZipFile(buf, "r") as zf:
            _validate_zip_members(zf, temp_dir)
            zf.extractall(temp_dir)

        db_path = os.path.join(DATA_DIR, "prismbi.duckdb")
        temp_db = os.path.join(temp_dir, "prismbi.duckdb")
        temp_key = os.path.join(temp_dir, "master.key")

        if not os.path.exists(temp_db):
            shutil.rmtree(temp_dir)
            return {"success": False, "error": "Backup archive does not contain prismbi.duckdb"}

        temp_db_size = os.path.getsize(temp_db)
        if temp_db_size < 1024:
            shutil.rmtree(temp_dir)
            return {"success": False, "error": "Backup database file appears corrupted (too small)"}

        try:
            import duckdb as _duckdb
            test_con = _duckdb.connect(temp_db, read_only=True)
            test_con.execute("SELECT 1")
            test_con.close()
        except Exception as exc:
            shutil.rmtree(temp_dir)
            return {"success": False, "error": f"Backup database is not a valid DuckDB file: {exc}"}

        if os.path.exists(db_path):
            ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            pre_backup_dir = os.path.join(BACKUP_DIR, "pre_restore")
            os.makedirs(pre_backup_dir, exist_ok=True)
            shutil.copy2(db_path, os.path.join(pre_backup_dir, f"prismbi.duckdb.{ts}.bak"))
            wal_path = db_path + ".wal"
            if os.path.exists(wal_path):
                shutil.copy2(wal_path, os.path.join(pre_backup_dir, f"prismbi.duckdb.wal.{ts}.bak"))

        journal_path = os.path.join(DATA_DIR, ".restore_in_progress")
        with open(journal_path, "w") as jf:
            jf.write(datetime.now(timezone.utc).isoformat())

        from db import close_connection, init_db
        close_connection()

        temp_db_tmp = db_path + ".tmp"
        shutil.copy2(temp_db, temp_db_tmp)
        os.replace(temp_db_tmp, db_path)

        temp_wal = os.path.join(temp_dir, "prismbi.duckdb.wal")
        wal_path = db_path + ".wal"
        if os.path.exists(temp_wal):
            temp_wal_tmp = wal_path + ".tmp"
            shutil.copy2(temp_wal, temp_wal_tmp)
            os.replace(temp_wal_tmp, wal_path)
        else:
            if os.path.exists(wal_path):
                os.remove(wal_path)

        if os.path.exists(temp_key):
            key_path = os.path.join(DATA_DIR, "master.key")
            key_tmp = key_path + ".tmp"
            shutil.copy2(temp_key, key_tmp)
            os.replace(key_tmp, key_path)

        projects_dir = os.path.join(DATA_DIR, "projects")
        os.makedirs(projects_dir, exist_ok=True)
        backup_projects = os.path.join(temp_dir, "projects")
        restore_src = backup_projects if os.path.isdir(backup_projects) else temp_dir
        for item in os.listdir(restore_src):
            if item in ("prismbi.duckdb", "prismbi.duckdb.wal", "master.key"):
                continue
            if item.startswith(".") or ".." in item:
                continue
            src = os.path.join(restore_src, item)
            dst = os.path.join(projects_dir, item)
            if os.path.isdir(src):
                if os.path.exists(dst):
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)

        init_db()

        with connection_lock():
            con = get_connection()
            con.execute(
                "INSERT OR REPLACE INTO metadata.settings (key, value) VALUES (?, ?::JSON)",
                ["last_restore_at", json.dumps(datetime.now(timezone.utc).isoformat())],
            )

        if os.path.exists(journal_path):
            os.remove(journal_path)
        shutil.rmtree(temp_dir)
        return {"success": True, "error": None}

    except Exception as e:
        try:
            from db import init_db
            init_db()
        except Exception:
            pass
        shutil.rmtree(temp_dir, ignore_errors=True)
        return {"success": False, "error": "Restore failed due to an internal error. Check server logs for details."}


def delete_backup(name: str) -> bool:
    _validate_backup_name(name)
    deleted = False
    for path in [_backup_zip_path(name), _backup_meta_path(name)]:
        real = os.path.realpath(path)
        if not real.startswith(os.path.realpath(BACKUP_DIR) + os.sep):
            continue
        if os.path.exists(path):
            os.remove(path)
            deleted = True
    return deleted


def _backup_to_dict(name: str, meta: dict) -> dict:
    zip_path = _backup_zip_path(name)
    size = meta.get("size", 0)
    if not size and os.path.exists(zip_path):
        size = os.path.getsize(zip_path)
    return {
        "name": name,
        "created_at": meta.get("created_at", ""),
        "size": size,
        "db_size": meta.get("db_size"),
        "project_files": meta.get("project_files", 0),
        "has_wal": meta.get("has_wal", False),
        "has_key": meta.get("has_key", False),
        "valid": meta.get("valid", True),
    }