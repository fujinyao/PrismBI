#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


_FRAME_BYTES_RE = re.compile(r"\[(?P<bytes>\d+)\s+bytes\]")
_REQUEST_ID_RE = re.compile(r'"request_id"\s*:\s*"(?P<request_id>[^"\\]+)"')
_SEQ_RE = re.compile(r'"seq"\s*:\s*(?P<seq>\d+)')
_ELAPSED_MS_RE = re.compile(r'"elapsed_ms"\s*:\s*(?P<elapsed_ms>\d+)')


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


def _extract_json_int(line: str, regex: re.Pattern[str], group_name: str) -> int | None:
    match = regex.search(line)
    if not match:
        return None
    try:
        return int(match.group(group_name))
    except Exception:
        return None


def _extract_request_id(line: str) -> str | None:
    match = _REQUEST_ID_RE.search(line)
    if not match:
        return None
    request_id = str(match.group("request_id") or "").strip()
    return request_id or None


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
    timing = {
        "frames_with_seq": 0,
        "frames_with_elapsed_ms": 0,
        "requests_with_meta": 0,
        "seq_non_monotonic_count": 0,
        "elapsed_non_monotonic_count": 0,
        "avg_first_delta_elapsed_ms": 0.0,
        "avg_result_elapsed_ms": 0.0,
        "max_result_elapsed_ms": 0,
        "min_result_elapsed_ms": 0,
    }
    per_request_timing: dict[str, dict[str, int | None]] = {}

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

        if is_outbound_text and (
            '"type":"delta"' in line
            or '"type":"result"' in line
            or '"type":"error"' in line
        ):
            request_id = _extract_request_id(line)
            seq_value = _extract_json_int(line, _SEQ_RE, "seq")
            elapsed_ms_value = _extract_json_int(line, _ELAPSED_MS_RE, "elapsed_ms")

            if seq_value is not None:
                timing["frames_with_seq"] = int(timing["frames_with_seq"] or 0) + 1
            if elapsed_ms_value is not None:
                timing["frames_with_elapsed_ms"] = int(timing["frames_with_elapsed_ms"] or 0) + 1

            if not request_id:
                continue

            state = per_request_timing.setdefault(
                request_id,
                {
                    "last_seq": None,
                    "last_elapsed_ms": None,
                    "first_delta_elapsed_ms": None,
                    "result_elapsed_ms": None,
                },
            )

            last_seq = state.get("last_seq")
            if seq_value is not None:
                if isinstance(last_seq, int) and seq_value <= last_seq:
                    timing["seq_non_monotonic_count"] = int(timing["seq_non_monotonic_count"] or 0) + 1
                state["last_seq"] = seq_value

            last_elapsed = state.get("last_elapsed_ms")
            if elapsed_ms_value is not None:
                if isinstance(last_elapsed, int) and elapsed_ms_value < last_elapsed:
                    timing["elapsed_non_monotonic_count"] = int(timing["elapsed_non_monotonic_count"] or 0) + 1
                state["last_elapsed_ms"] = elapsed_ms_value
                if state.get("first_delta_elapsed_ms") is None and '"type":"delta"' in line:
                    state["first_delta_elapsed_ms"] = elapsed_ms_value
                if '"type":"result"' in line:
                    state["result_elapsed_ms"] = elapsed_ms_value

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

    timing["requests_with_meta"] = len(per_request_timing)
    first_delta_samples: list[int] = []
    result_samples: list[int] = []
    for state in per_request_timing.values():
        first_delta_elapsed = state.get("first_delta_elapsed_ms")
        result_elapsed = state.get("result_elapsed_ms")
        if isinstance(first_delta_elapsed, int):
            first_delta_samples.append(first_delta_elapsed)
        if isinstance(result_elapsed, int):
            result_samples.append(result_elapsed)

    if first_delta_samples:
        timing["avg_first_delta_elapsed_ms"] = round(
            float(sum(first_delta_samples)) / float(len(first_delta_samples)),
            2,
        )
    if result_samples:
        timing["avg_result_elapsed_ms"] = round(
            float(sum(result_samples)) / float(len(result_samples)),
            2,
        )
        timing["max_result_elapsed_ms"] = max(result_samples)
        timing["min_result_elapsed_ms"] = min(result_samples)

    return {
        "line_totals": line_totals,
        "frames": frames,
        "ping_pong": ping_pong,
        "noise": noise,
        "timing": timing,
    }


def _render_human_report(report: dict[str, Any]) -> str:
    line_totals = report.get("line_totals") or {}
    frames = report.get("frames") or {}
    ping_pong = report.get("ping_pong") or {}
    noise = report.get("noise") or {}
    timing = report.get("timing") or {}

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
    lines.append(
        "timing: "
        f"requests_with_meta={timing.get('requests_with_meta', 0)} "
        f"frames_with_seq={timing.get('frames_with_seq', 0)} "
        f"frames_with_elapsed_ms={timing.get('frames_with_elapsed_ms', 0)} "
        f"avg_first_delta_elapsed_ms={timing.get('avg_first_delta_elapsed_ms', 0.0)} "
        f"avg_result_elapsed_ms={timing.get('avg_result_elapsed_ms', 0.0)} "
        f"result_elapsed_range=[{timing.get('min_result_elapsed_ms', 0)},{timing.get('max_result_elapsed_ms', 0)}] "
        f"seq_non_monotonic={timing.get('seq_non_monotonic_count', 0)} "
        f"elapsed_non_monotonic={timing.get('elapsed_non_monotonic_count', 0)}"
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
