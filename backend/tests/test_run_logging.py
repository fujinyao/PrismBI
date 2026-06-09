from __future__ import annotations

import importlib


def test_build_log_config_suppresses_websocket_frame_debug_in_prod(monkeypatch):
    monkeypatch.delenv("PRISMBI_WS_FRAME_DEBUG", raising=False)

    run_module = importlib.import_module("Run")
    config = run_module.build_log_config(prod=True)

    assert config["loggers"]["uvicorn.error"]["level"] == "INFO"
    assert config["loggers"]["websockets"]["level"] == "WARNING"
    assert config["loggers"]["uvicorn.protocols.websockets.websockets_impl"]["level"] == "WARNING"


def test_build_log_config_keeps_websocket_frame_debug_when_env_enabled(monkeypatch):
    monkeypatch.setenv("PRISMBI_WS_FRAME_DEBUG", "1")

    run_module = importlib.import_module("Run")
    config = run_module.build_log_config(prod=True)

    assert config["loggers"]["websockets"]["level"] == "DEBUG"
    assert config["loggers"]["uvicorn.protocols.websockets.websockets_impl"]["level"] == "DEBUG"
