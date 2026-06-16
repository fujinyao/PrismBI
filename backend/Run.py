#!/usr/bin/env python3
"""
Usage:
    poetry run python scripts/dev.py              # 默认 debug + reload
    poetry run python scripts/dev.py --prod       # 生产模式
    poetry run python scripts/dev.py --port 8080
"""

import argparse
import copy
import os
import sys
from pathlib import Path
from typing import Any

import uvicorn
from uvicorn.config import LOGGING_CONFIG as UVICORN_LOGGING_CONFIG


def _env_flag(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, *, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return float(default)
    try:
        value = float(str(raw).strip())
    except Exception:
        return float(default)
    return max(0.0, value)


def build_log_config(prod: bool) -> dict[str, Any]:
    log_config = copy.deepcopy(UVICORN_LOGGING_CONFIG)
    loggers = log_config.setdefault("loggers", {})

    runtime_level = "INFO" if prod else "DEBUG"
    loggers.setdefault("uvicorn", {})["level"] = runtime_level
    loggers.setdefault("uvicorn.error", {})["level"] = runtime_level
    loggers.setdefault("uvicorn.access", {})["level"] = "INFO"

    root_logger = log_config.setdefault("root", {})
    root_logger["level"] = runtime_level

    ws_frame_debug_enabled = _env_flag("PRISMBI_WS_FRAME_DEBUG", default=not prod)
    ws_level = "DEBUG" if ws_frame_debug_enabled else "WARNING"
    for logger_name in (
        "uvicorn.protocols.websockets.websockets_impl",
        "websockets",
        "websockets.server",
        "websockets.client",
        "websockets.protocol",
    ):
        logger_cfg = loggers.setdefault(logger_name, {})
        logger_cfg["level"] = ws_level
        if not ws_frame_debug_enabled:
            logger_cfg["propagate"] = False
            logger_cfg["handlers"] = ["default"]

    return log_config


def parse_args():
    parser = argparse.ArgumentParser(description="PrismBI Backend Launcher")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8400)
    parser.add_argument("--prod", action="store_true", help="Disable reload")
    return parser.parse_args()


def main():
    args = parse_args()
    PROJECT_ROOT = Path(__file__).parent.parent.resolve()
    sys.path.insert(0, str(PROJECT_ROOT))

    env = "production" if args.prod else "development"
    os.environ.setdefault("ENV", env)
    ws_ping_interval = _env_float("PRISMBI_WS_PING_INTERVAL", default=20.0)
    ws_ping_timeout = _env_float("PRISMBI_WS_PING_TIMEOUT", default=20.0)

    uvicorn.run(
        "main:app",
        host=args.host,
        port=args.port,
        reload=not args.prod,
        log_level="info" if args.prod else "debug",
        env_file=".env" if not args.prod else None,
        log_config=build_log_config(args.prod),
        ws_ping_interval=ws_ping_interval,
        ws_ping_timeout=ws_ping_timeout,
    )


if __name__ == "__main__":
    main()
