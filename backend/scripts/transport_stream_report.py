#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


_FRAME_BYTES_RE = re.compile(r"\[(?P<bytes>\d+)\s+bytes\]")


def _extract_frame_bytes(line: str) -> int:
    match = _FRAME_BYTES_RE.search(line)
    if not match:
        return 0
    try:
        return max(0, int(match.group("bytes")))
    except Exception:
        return 0


def _safe_ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(numerator) / float(denominator), 4)


def analyze_transport_stream_log(text: str) -> dict[str, Any]:
    line_totals = {
        "all_lines": 0,
        "debug_lines": 0,
        "websocket_debug_lines": 0,
        "transport_noise_lines": 0,
    }
    frames = {
        "text_frames_total": 0,
        "outbound_text_frames": 0,
        "inbound_text_frames": 0,
        "delta_frames_total": 0,
        "delta_text_frames": 0,
        "delta_step_frames": 0,
        "delta_state_frames": 0,
        "delta_sql_frames": 0,
        "result_frames": 0,
        "ask_frames": 0,
        "outbound_text_bytes_total": 0,
        "inbound_text_bytes_total": 0,
        "max_outbound_text_bytes": 0,
        "max_inbound_text_bytes": 0,
    }
    ping_pong = {
        "keepalive_ping_logs": 0,
        "keepalive_pong_logs": 0,
        "ws_ping_frames": 0,
        "ws_pong_frames": 0,
        "app_ping_frames": 0,
        "app_pong_frames": 0,
    }

    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line_totals["all_lines"] = int(line_totals["all_lines"] or 0) + 1

        is_debug = line.startswith("DEBUG:")
        if is_debug:
            line_totals["debug_lines"] = int(line_totals["debug_lines"] or 0) + 1

        has_ws_marker = (
            "> TEXT" in line
            or "< TEXT" in line
            or "> PING" in line
            or "< PONG" in line
            or "% sending keepalive ping" in line
            or "% received keepalive pong" in line
        )
        if is_debug and has_ws_marker:
            line_totals["websocket_debug_lines"] = int(line_totals["websocket_debug_lines"] or 0) + 1

        line_is_transport_noise = False
        if is_debug and "% sending keepalive ping" in line:
            ping_pong["keepalive_ping_logs"] = int(ping_pong["keepalive_ping_logs"] or 0) + 1
            line_is_transport_noise = True
        if is_debug and "% received keepalive pong" in line:
            ping_pong["keepalive_pong_logs"] = int(ping_pong["keepalive_pong_logs"] or 0) + 1
            line_is_transport_noise = True
        if is_debug and "> PING" in line:
            ping_pong["ws_ping_frames"] = int(ping_pong["ws_ping_frames"] or 0) + 1
            line_is_transport_noise = True
        if is_debug and "< PONG" in line:
            ping_pong["ws_pong_frames"] = int(ping_pong["ws_pong_frames"] or 0) + 1
            line_is_transport_noise = True
        if is_debug and "< TEXT" in line and '"type":"ping"' in line:
            ping_pong["app_ping_frames"] = int(ping_pong["app_ping_frames"] or 0) + 1
            line_is_transport_noise = True
        if is_debug and "> TEXT" in line and '"type":"pong"' in line:
            ping_pong["app_pong_frames"] = int(ping_pong["app_pong_frames"] or 0) + 1
            line_is_transport_noise = True
        if line_is_transport_noise:
            line_totals["transport_noise_lines"] = int(line_totals["transport_noise_lines"] or 0) + 1

        is_outbound_text = is_debug and "> TEXT" in line
        is_inbound_text = is_debug and "< TEXT" in line
        if is_outbound_text or is_inbound_text:
            frame_bytes = _extract_frame_bytes(line)
            frames["text_frames_total"] = int(frames["text_frames_total"] or 0) + 1
            if is_outbound_text:
                frames["outbound_text_frames"] = int(frames["outbound_text_frames"] or 0) + 1
                frames["outbound_text_bytes_total"] = int(frames["outbound_text_bytes_total"] or 0) + frame_bytes
                frames["max_outbound_text_bytes"] = max(int(frames["max_outbound_text_bytes"] or 0), frame_bytes)
            if is_inbound_text:
                frames["inbound_text_frames"] = int(frames["inbound_text_frames"] or 0) + 1
                frames["inbound_text_bytes_total"] = int(frames["inbound_text_bytes_total"] or 0) + frame_bytes
                frames["max_inbound_text_bytes"] = max(int(frames["max_inbound_text_bytes"] or 0), frame_bytes)

        if is_outbound_text and '"type":"delta"' in line:
            frames["delta_frames_total"] = int(frames["delta_frames_total"] or 0) + 1
            if '"content_type":"text"' in line:
                frames["delta_text_frames"] = int(frames["delta_text_frames"] or 0) + 1
            if '"content_type":"step"' in line:
                frames["delta_step_frames"] = int(frames["delta_step_frames"] or 0) + 1
            if '"content_type":"state"' in line:
                frames["delta_state_frames"] = int(frames["delta_state_frames"] or 0) + 1
            if '"content_type":"sql"' in line:
                frames["delta_sql_frames"] = int(frames["delta_sql_frames"] or 0) + 1
        if is_outbound_text and '"type":"result"' in line:
            frames["result_frames"] = int(frames["result_frames"] or 0) + 1
        if is_inbound_text and '"type":"ask"' in line:
            frames["ask_frames"] = int(frames["ask_frames"] or 0) + 1

    outbound_count = int(frames.get("outbound_text_frames") or 0)
    inbound_count = int(frames.get("inbound_text_frames") or 0)
    ws_debug_count = int(line_totals.get("websocket_debug_lines") or 0)
    all_lines = int(line_totals.get("all_lines") or 0)
    noise_lines = int(line_totals.get("transport_noise_lines") or 0)

    ping_pong_lines_total = (
        int(ping_pong.get("keepalive_ping_logs") or 0)
        + int(ping_pong.get("keepalive_pong_logs") or 0)
        + int(ping_pong.get("ws_ping_frames") or 0)
        + int(ping_pong.get("ws_pong_frames") or 0)
        + int(ping_pong.get("app_ping_frames") or 0)
        + int(ping_pong.get("app_pong_frames") or 0)
    )

    frames["avg_outbound_text_bytes"] = round(
        float(frames.get("outbound_text_bytes_total") or 0) / float(outbound_count),
        2,
    ) if outbound_count > 0 else 0.0
    frames["avg_inbound_text_bytes"] = round(
        float(frames.get("inbound_text_bytes_total") or 0) / float(inbound_count),
        2,
    ) if inbound_count > 0 else 0.0

    line_totals["business_lines"] = max(0, all_lines - noise_lines)
    line_totals["transport_noise_share_of_all_lines"] = _safe_ratio(noise_lines, all_lines)

    ping_pong["ping_pong_lines_total"] = ping_pong_lines_total
    ping_pong["ping_pong_share_of_ws_debug"] = _safe_ratio(ping_pong_lines_total, ws_debug_count)

    noise = {
        "frame_noise_share": _safe_ratio(ping_pong_lines_total, ws_debug_count),
        "text_delta_share_of_outbound_text": _safe_ratio(
            int(frames.get("delta_text_frames") or 0),
            outbound_count,
        ),
    }

    return {
        "line_totals": line_totals,
        "frames": frames,
        "ping_pong": ping_pong,
        "noise": noise,
    }


def _render_human_report(report: dict[str, Any]) -> str:
    line_totals = report.get("line_totals") or {}
    frames = report.get("frames") or {}
    ping_pong = report.get("ping_pong") or {}
    noise = report.get("noise") or {}

    lines: list[str] = []
    lines.append("Transport/Stream Report")
    lines.append("=======================")
    lines.append(
        "lines: "
        f"all={line_totals.get('all_lines', 0)} "
        f"debug={line_totals.get('debug_lines', 0)} "
        f"ws_debug={line_totals.get('websocket_debug_lines', 0)} "
        f"noise={line_totals.get('transport_noise_lines', 0)} "
        f"noise_share={line_totals.get('transport_noise_share_of_all_lines', 0.0):.4f}"
    )
    lines.append(
        "frames: "
        f"text_total={frames.get('text_frames_total', 0)} "
        f"outbound={frames.get('outbound_text_frames', 0)} "
        f"inbound={frames.get('inbound_text_frames', 0)} "
        f"delta_text={frames.get('delta_text_frames', 0)} "
        f"delta_step={frames.get('delta_step_frames', 0)} "
        f"delta_state={frames.get('delta_state_frames', 0)} "
        f"delta_sql={frames.get('delta_sql_frames', 0)} "
        f"result={frames.get('result_frames', 0)}"
    )
    lines.append(
        "ping_pong: "
        f"ws_ping={ping_pong.get('ws_ping_frames', 0)} "
        f"ws_pong={ping_pong.get('ws_pong_frames', 0)} "
        f"app_ping={ping_pong.get('app_ping_frames', 0)} "
        f"app_pong={ping_pong.get('app_pong_frames', 0)} "
        f"keepalive_ping={ping_pong.get('keepalive_ping_logs', 0)} "
        f"keepalive_pong={ping_pong.get('keepalive_pong_logs', 0)} "
        f"share_of_ws_debug={ping_pong.get('ping_pong_share_of_ws_debug', 0.0):.4f}"
    )
    lines.append(
        "noise: "
        f"frame_noise_share={noise.get('frame_noise_share', 0.0):.4f} "
        f"delta_text_share_of_outbound={noise.get('text_delta_share_of_outbound_text', 0.0):.4f}"
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyze websocket transport/stream noise from backend ask logs"
    )
    parser.add_argument("logfile", type=Path, help="Path to backend ask.log")
    parser.add_argument("--json", action="store_true", help="Output JSON only")
    args = parser.parse_args()

    if not args.logfile.exists():
        raise SystemExit(f"Log file not found: {args.logfile}")

    text = args.logfile.read_text(encoding="utf-8", errors="ignore")
    report = analyze_transport_stream_log(text)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print(_render_human_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
