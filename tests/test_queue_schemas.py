"""Wire-schema round-trip tests for src/server/queue.py.

Covers:
  * TtsJobPayload encode/decode (bytes + str keyed dicts)
  * TtsResult encode/decode incl. base64 PCM round-trip + final/error flags
  * result_stream_name(rid) stability across str + UUID inputs

These tests don't touch Redis — they just verify the wire format is a
clean round-trip so worker and gateway can rely on it without surprises.
"""

from __future__ import annotations

import uuid

import pytest

from server.queue import (
    RESULTS_STREAM_PREFIX,
    TtsJobPayload,
    TtsResult,
    TtsResultStreamConfig,
    result_stream_name,
)


# --------------------------------------------------------------------------- #
# TtsJobPayload — job stream wire format
# --------------------------------------------------------------------------- #
def test_job_payload_roundtrip_str_keys():
    job = TtsJobPayload(
        request_id="01J2A3B4C5",
        tenant_id="tenant-uuid",
        api_key_id="key-uuid",
        voice_id="neeko-v01",
        text="Merhaba dünya.",
        language="tr",
        audio_format="wav",
        params={"cfg_value": 2.5, "inference_timesteps": 12},
    )
    wire = job.encode()
    decoded = TtsJobPayload.decode(wire)
    assert decoded == job


def test_job_payload_roundtrip_bytes_keys():
    """redis-py default decode_responses=False returns bytes."""
    job = TtsJobPayload(
        request_id="x",
        tenant_id="t",
        api_key_id="k",
        voice_id="v",
        text="ş ç ğ ı ö ü",  # Turkish UTF-8 survives the round-trip
    )
    wire = {k.encode(): v.encode() for k, v in job.encode().items()}
    decoded = TtsJobPayload.decode(wire)
    assert decoded.text == "ş ç ğ ı ö ü"


# --------------------------------------------------------------------------- #
# TtsResult — per-request chunk stream
# --------------------------------------------------------------------------- #
def test_result_chunk_roundtrip_pcm_preserved():
    pcm = bytes(range(256)) * 8  # 2 KB binary blob
    chunk = TtsResult(
        request_id="r1",
        seq=3,
        pcm_bytes=pcm,
        sentence_text="İlk cümle.",
        final=False,
    )
    wire = chunk.encode()
    decoded = TtsResult.decode(wire)
    assert decoded == chunk
    assert decoded.pcm_bytes == pcm  # base64 round-trip


def test_result_chunk_final_marker_no_pcm():
    chunk = TtsResult(request_id="r1", seq=42, pcm_bytes=b"", final=True)
    decoded = TtsResult.decode(chunk.encode())
    assert decoded.final is True
    assert decoded.pcm_bytes == b""
    assert decoded.sentence_text is None


def test_result_chunk_error_carries_message():
    chunk = TtsResult(
        request_id="r1", seq=0, pcm_bytes=b"",
        error="voice_not_found",
    )
    decoded = TtsResult.decode(chunk.encode())
    assert decoded.error == "voice_not_found"
    assert decoded.final is False


def test_result_chunk_decode_bytes_keys():
    """Worker XADD-ed entries come back from XREADGROUP as bytes dicts."""
    chunk = TtsResult(request_id="r1", seq=0, pcm_bytes=b"\x01\x02\x03")
    wire_str = chunk.encode()
    wire_bytes = {k.encode(): v.encode() for k, v in wire_str.items()}
    assert TtsResult.decode(wire_bytes) == chunk


def test_result_chunk_seq_is_int():
    chunk = TtsResult(request_id="r1", seq=1234567, pcm_bytes=b"")
    decoded = TtsResult.decode(chunk.encode())
    assert decoded.seq == 1234567
    assert isinstance(decoded.seq, int)


# --------------------------------------------------------------------------- #
# Stream naming — worker + gateway must agree
# --------------------------------------------------------------------------- #
def test_result_stream_name_stable_for_string_input():
    name = result_stream_name("01J2A3B4C5")
    assert name == "nqai.tts.results.01J2A3B4C5"
    assert name.startswith(RESULTS_STREAM_PREFIX)


def test_result_stream_name_handles_uuid_input():
    rid = uuid.UUID("12345678-1234-5678-1234-567812345678")
    name = result_stream_name(rid)
    assert name == f"nqai.tts.results.{rid}"


def test_result_stream_name_string_and_uuid_match_for_same_value():
    rid = uuid.uuid4()
    assert result_stream_name(rid) == result_stream_name(str(rid))


# --------------------------------------------------------------------------- #
# Config object — defaults and overrides
# --------------------------------------------------------------------------- #
def test_result_stream_config_defaults():
    cfg = TtsResultStreamConfig()
    assert cfg.ttl_seconds == 600
    assert cfg.maxlen == 1024
    assert cfg.extra_fields == {}


def test_result_stream_config_overrides():
    cfg = TtsResultStreamConfig(ttl_seconds=120, maxlen=64,
                                extra_fields={"trace_id": "abc"})
    assert cfg.ttl_seconds == 120
    assert cfg.maxlen == 64
    assert cfg.extra_fields["trace_id"] == "abc"


# --------------------------------------------------------------------------- #
# Negative cases — decode tolerance
# --------------------------------------------------------------------------- #
def test_result_decode_empty_fields_yields_defaults():
    """A malformed entry shouldn't crash decode — gateway must surface
    a clean error instead of an exception in the result-stream loop."""
    decoded = TtsResult.decode({})
    assert decoded.request_id == ""
    assert decoded.seq == 0
    assert decoded.pcm_bytes == b""
    assert decoded.final is False


@pytest.mark.parametrize("flag,expected", [("true", True), ("false", False),
                                            ("True", False), ("", False)])
def test_result_decode_final_flag_is_strict_string(flag, expected):
    """final is strictly 'true' string match; anything else is False.
    This prevents accidental 'True'/'1'/'yes' divergence between workers."""
    wire = {"request_id": "r1", "seq": "0", "pcm_b64": "", "final": flag}
    assert TtsResult.decode(wire).final is expected
