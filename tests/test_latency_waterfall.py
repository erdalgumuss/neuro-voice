from __future__ import annotations

from live import LiveLatencyWaterfall, split_pcm16_frames


def test_latency_waterfall_derives_core_live_metrics():
    trace = LiveLatencyWaterfall(request_id="rid")
    trace.mark("gateway_received_ms", 1_000)
    trace.mark("session_admitted_ms", 1_025)
    trace.mark("worker_selected_ms", 1_020)
    trace.mark("worker_accepted_ms", 1_040)
    trace.mark("model_start_ms", 1_070)
    trace.mark("model_first_audio_ms", 1_120)
    trace.mark("gateway_or_media_first_send_ms", 1_125)
    trace.mark("final_audio_done_ms", 1_300)

    metrics = trace.as_dict()
    assert metrics["admission_ms"] == 25
    assert metrics["worker_dispatch_ms"] == 20
    assert metrics["model_ttfa_ms"] == 50
    assert metrics["first_audio_ms"] == 125
    assert metrics["total_inference_ms"] == 230


def test_split_pcm16_frames_uses_20ms_default_at_48khz():
    frame_bytes = 48_000 // 50 * 2
    pcm = b"\x01\x02" * (48_000 // 50 * 2 + 10)
    frames = split_pcm16_frames(pcm)
    assert len(frames[0]) == frame_bytes
    assert b"".join(frames) == pcm
