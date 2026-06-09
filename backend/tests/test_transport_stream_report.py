from __future__ import annotations

from scripts.transport_stream_report import analyze_transport_stream_log


def test_analyze_transport_stream_log_counts_delta_and_ping_pong_lines():
    log_text = """
INFO:     connection open
DEBUG:    % sending keepalive ping
DEBUG:    > PING 4a 32 f1 19 [binary, 4 bytes]
DEBUG:    < PONG 4a 32 f1 19 [binary, 4 bytes]
DEBUG:    % received keepalive pong
DEBUG:    < TEXT '{"type":"ping"}' [15 bytes]
DEBUG:    > TEXT '{"type":"pong"}' [15 bytes]
DEBUG:    > TEXT '{"type":"delta","content_type":"step"}' [170 bytes]
DEBUG:    > TEXT '{"type":"delta","content_type":"text"}' [120 bytes]
DEBUG:    > TEXT '{"type":"delta","content_type":"text"}' [121 bytes]
DEBUG:    > TEXT '{"type":"result","data":{}}' [300 bytes]
"""

    report = analyze_transport_stream_log(log_text)

    assert report["frames"]["delta_text_frames"] == 2
    assert report["frames"]["delta_step_frames"] == 1
    assert report["frames"]["result_frames"] == 1
    assert report["frames"]["outbound_text_frames"] == 5
    assert report["frames"]["inbound_text_frames"] == 1

    assert report["ping_pong"]["keepalive_ping_logs"] == 1
    assert report["ping_pong"]["keepalive_pong_logs"] == 1
    assert report["ping_pong"]["ws_ping_frames"] == 1
    assert report["ping_pong"]["ws_pong_frames"] == 1
    assert report["ping_pong"]["app_ping_frames"] == 1
    assert report["ping_pong"]["app_pong_frames"] == 1
    assert report["ping_pong"]["ping_pong_lines_total"] == 6


def test_analyze_transport_stream_log_computes_noise_shares():
    log_text = """
DEBUG:    > TEXT '{"type":"delta","content_type":"text"}' [100 bytes]
DEBUG:    > TEXT '{"type":"delta","content_type":"text"}' [120 bytes]
DEBUG:    > TEXT '{"type":"pong"}' [15 bytes]
DEBUG:    < TEXT '{"type":"ping"}' [15 bytes]
"""

    report = analyze_transport_stream_log(log_text)

    assert report["noise"]["text_delta_share_of_outbound_text"] == 0.6667
    assert report["noise"]["frame_noise_share"] == 0.5
    assert report["line_totals"]["transport_noise_share_of_all_lines"] == 0.5
