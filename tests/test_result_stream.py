"""Unit tests for src/server/result_stream.py — gateway-side consumer."""

from __future__ import annotations

import asyncio
import sys
import types as _types
import uuid

import fakeredis.aioredis
import pytest

# Voxcpm stub for any server.* import.
_fake_voxcpm = _types.ModuleType("voxcpm")
_fake_voxcpm.VoxCPM = type("SF", (), {
    "from_pretrained": staticmethod(lambda *a, **kw: None),
})
sys.modules.setdefault("voxcpm", _fake_voxcpm)
sys.modules.setdefault("voxcpm.model", _types.ModuleType("voxcpm.model"))
m = _types.ModuleType("voxcpm.model.voxcpm")
m.LoRAConfig = object
sys.modules.setdefault("voxcpm.model.voxcpm", m)


async def _publish_chunks(redis, rid, chunks):
    """Helper: XADD a series of TtsResult-shaped dicts (just the fields
    that decode() needs)."""
    from server.queue import result_stream_name
    stream = result_stream_name(rid)
    for c in chunks:
        await redis.xadd(stream, c.encode())


async def test_consume_yields_chunks_then_final(monkeypatch):
    """Happy path: 3 chunks + final → consumer yields 4 then stops."""
    from server.queue import TtsResult
    from server.result_stream import consume_result_stream

    redis = fakeredis.aioredis.FakeRedis()
    rid = str(uuid.uuid4())

    await _publish_chunks(redis, rid, [
        TtsResult(request_id=rid, seq=0, pcm_bytes=b"\x01\x02",
                   sentence_text="bir"),
        TtsResult(request_id=rid, seq=1, pcm_bytes=b"\x03\x04",
                   sentence_text="iki"),
        TtsResult(request_id=rid, seq=2, pcm_bytes=b"\x05\x06",
                   sentence_text="üç"),
        TtsResult(request_id=rid, seq=3, pcm_bytes=b"", final=True),
    ])

    collected = []
    async for chunk in consume_result_stream(redis, rid, block_ms=10):
        collected.append(chunk)
    assert len(collected) == 4
    assert [c.seq for c in collected] == [0, 1, 2, 3]
    assert collected[-1].final is True
    assert collected[-1].pcm_bytes == b""


async def test_consume_short_circuits_on_error_chunk():
    """Error chunk terminates the stream immediately — caller never
    sees later (theoretical) chunks."""
    from server.queue import TtsResult
    from server.result_stream import consume_result_stream

    redis = fakeredis.aioredis.FakeRedis()
    rid = str(uuid.uuid4())

    await _publish_chunks(redis, rid, [
        TtsResult(request_id=rid, seq=0, pcm_bytes=b"\x01\x02"),
        TtsResult(request_id=rid, seq=1, pcm_bytes=b"",
                   error="voice_not_found"),
        # final chunk after error — should never be yielded
        TtsResult(request_id=rid, seq=2, pcm_bytes=b"", final=True),
    ])

    collected = []
    async for chunk in consume_result_stream(redis, rid, block_ms=10):
        collected.append(chunk)
    assert len(collected) == 2
    assert collected[-1].error == "voice_not_found"


async def test_consume_deletes_stream_on_finish():
    """After the terminating chunk, the per-request stream key is DEL'd
    so Redis doesn't carry it indefinitely (worker also sets EXPIRE
    as a safety net, but DEL frees memory immediately)."""
    from server.queue import TtsResult, result_stream_name
    from server.result_stream import consume_result_stream

    redis = fakeredis.aioredis.FakeRedis()
    rid = str(uuid.uuid4())
    stream = result_stream_name(rid)

    await _publish_chunks(redis, rid, [
        TtsResult(request_id=rid, seq=0, pcm_bytes=b"\x01\x02"),
        TtsResult(request_id=rid, seq=1, pcm_bytes=b"", final=True),
    ])
    assert await redis.exists(stream) == 1

    async for _ in consume_result_stream(redis, rid, block_ms=10):
        pass
    assert await redis.exists(stream) == 0


async def test_consume_keeps_stream_when_delete_on_finish_false():
    """Opt-out: WebSocket / multi-reader scenarios may want to leave
    the stream for another consumer."""
    from server.queue import TtsResult, result_stream_name
    from server.result_stream import consume_result_stream

    redis = fakeredis.aioredis.FakeRedis()
    rid = str(uuid.uuid4())
    stream = result_stream_name(rid)

    await _publish_chunks(redis, rid, [
        TtsResult(request_id=rid, seq=0, pcm_bytes=b"x"),
        TtsResult(request_id=rid, seq=1, pcm_bytes=b"", final=True),
    ])
    async for _ in consume_result_stream(redis, rid, block_ms=10,
                                          delete_on_finish=False):
        pass
    assert await redis.exists(stream) == 1


async def test_consume_raises_timeout_when_no_final():
    """If the worker dies mid-job (no final chunk ever arrives), the
    consumer must surface a timeout so the gateway can map it to 504."""
    from server.result_stream import ResultStreamTimeout, consume_result_stream

    redis = fakeredis.aioredis.FakeRedis()
    rid = str(uuid.uuid4())
    with pytest.raises(ResultStreamTimeout):
        async for _ in consume_result_stream(
            redis, rid, block_ms=10, overall_timeout_s=0.2,
        ):
            pass


async def test_collect_pcm_concatenates_until_final():
    """The sync-proxy convenience — drain into one buffer."""
    from server.queue import TtsResult
    from server.result_stream import collect_pcm_until_final

    redis = fakeredis.aioredis.FakeRedis()
    rid = str(uuid.uuid4())
    await _publish_chunks(redis, rid, [
        TtsResult(request_id=rid, seq=0, pcm_bytes=b"\x01\x02\x03"),
        TtsResult(request_id=rid, seq=1, pcm_bytes=b"\x04\x05\x06"),
        TtsResult(request_id=rid, seq=2, pcm_bytes=b"", final=True),
    ])
    pcm, n, err = await collect_pcm_until_final(redis, rid, block_ms=10)
    assert pcm == b"\x01\x02\x03\x04\x05\x06"
    assert n == 2
    assert err is None


async def test_collect_pcm_ignores_duplicate_seq_within_same_attempt():
    """Within a single attempt, a duplicate seq is a re-XADD by the
    worker (shouldn't happen but defence-in-depth) and must be dropped
    so the sync buffer doesn't double the sentence."""
    from server.queue import TtsResult
    from server.result_stream import collect_pcm_until_final

    redis = fakeredis.aioredis.FakeRedis()
    rid = str(uuid.uuid4())
    await _publish_chunks(redis, rid, [
        TtsResult(request_id=rid, seq=0, pcm_bytes=b"first", attempt=1),
        TtsResult(request_id=rid, seq=0, pcm_bytes=b"dup", attempt=1),
        TtsResult(request_id=rid, seq=1, pcm_bytes=b"", final=True, attempt=1),
    ])
    pcm, n, err = await collect_pcm_until_final(redis, rid, block_ms=10)
    assert pcm == b"first"
    assert n == 1
    assert err is None


async def test_collect_pcm_accepts_retry_seq_after_attempt_advance():
    """B3 fix (audit L3 2026-05-25): a worker retry resets seq=0 and
    advances `attempt`. The retry's audio MUST reach the client — the
    pre-fix behaviour treated retry as a duplicate and dropped it,
    losing the audio entirely.

    Scenario: attempt 1 publishes seq=0 then dies. Worker retry runs
    with attempt 2, deletes the stream, republishes seq=0,1,final.
    Gateway must reset dedupe on the attempt advance.
    """
    from server.queue import TtsResult
    from server.result_stream import collect_pcm_until_final

    redis = fakeredis.aioredis.FakeRedis()
    rid = str(uuid.uuid4())
    # Simulate the retry: attempt 1's stale chunk then attempt 2 from
    # scratch (worker would have DELed before republishing in reality;
    # the gateway dedupe must handle the case regardless).
    await _publish_chunks(redis, rid, [
        TtsResult(request_id=rid, seq=0, pcm_bytes=b"attempt1", attempt=1),
        # Worker retry: attempt advances, seq resets.
        TtsResult(request_id=rid, seq=0, pcm_bytes=b"attempt2-0", attempt=2),
        TtsResult(request_id=rid, seq=1, pcm_bytes=b"attempt2-1", attempt=2),
        TtsResult(
            request_id=rid, seq=2, pcm_bytes=b"", final=True, attempt=2,
        ),
    ])
    pcm, n, err = await collect_pcm_until_final(redis, rid, block_ms=10)
    # The first attempt's audio is correctly superseded by the retry —
    # only attempt 2's chunks reach the client.
    assert pcm == b"attempt2-0attempt2-1"
    assert n == 2
    assert err is None


async def test_consume_drops_stale_attempt_chunk():
    """Inverse of the above: once the gateway has seen a chunk with
    attempt=N, any straggler from attempt < N must be dropped (a
    crashed worker may finish publishing its old attempt's chunks
    AFTER the retry started)."""
    from server.queue import TtsResult
    from server.result_stream import collect_pcm_until_final

    redis = fakeredis.aioredis.FakeRedis()
    rid = str(uuid.uuid4())
    await _publish_chunks(redis, rid, [
        TtsResult(request_id=rid, seq=0, pcm_bytes=b"new", attempt=2),
        # Late chunk from the dying attempt 1 — must be ignored.
        TtsResult(request_id=rid, seq=1, pcm_bytes=b"stale", attempt=1),
        TtsResult(
            request_id=rid, seq=1, pcm_bytes=b"", final=True, attempt=2,
        ),
    ])
    pcm, n, err = await collect_pcm_until_final(redis, rid, block_ms=10)
    assert pcm == b"new"
    assert n == 1
    assert err is None


async def test_duplicate_seq_terminal_error_is_not_ignored():
    """A retry may fail terminally after an earlier attempt already
    emitted seq=0. Audio duplicate is ignored; terminal error must still
    reach the caller so sync clients do not hang until timeout."""
    from server.queue import TtsResult
    from server.result_stream import collect_pcm_until_final

    redis = fakeredis.aioredis.FakeRedis()
    rid = str(uuid.uuid4())
    await _publish_chunks(redis, rid, [
        TtsResult(request_id=rid, seq=0, pcm_bytes=b"audio"),
        TtsResult(request_id=rid, seq=0, pcm_bytes=b"", error="dlq"),
    ])

    pcm, n, err = await collect_pcm_until_final(redis, rid, block_ms=10)
    assert pcm == b"audio"
    assert n == 1
    assert err == "dlq"


async def test_consume_deletes_stream_on_generator_close():
    """Client disconnect/cancellation should not leave result streams
    around until TTL when delete_on_finish is enabled."""
    from server.queue import TtsResult, result_stream_name
    from server.result_stream import consume_result_stream

    redis = fakeredis.aioredis.FakeRedis()
    rid = str(uuid.uuid4())
    stream = result_stream_name(rid)
    await _publish_chunks(redis, rid, [
        TtsResult(request_id=rid, seq=0, pcm_bytes=b"x"),
        TtsResult(request_id=rid, seq=1, pcm_bytes=b"", final=True),
    ])
    gen = consume_result_stream(redis, rid, block_ms=10)
    first = await anext(gen)
    assert first.seq == 0
    await gen.aclose()
    assert await redis.exists(stream) == 0


async def test_collect_pcm_surfaces_error():
    from server.queue import TtsResult
    from server.result_stream import collect_pcm_until_final

    redis = fakeredis.aioredis.FakeRedis()
    rid = str(uuid.uuid4())
    await _publish_chunks(redis, rid, [
        TtsResult(request_id=rid, seq=0, pcm_bytes=b"\x01\x02"),
        TtsResult(request_id=rid, seq=1, pcm_bytes=b"", error="boom"),
    ])
    pcm, n, err = await collect_pcm_until_final(redis, rid, block_ms=10)
    assert pcm == b"\x01\x02"  # whatever arrived before the error
    assert n == 1
    assert err == "boom"


async def test_consume_handles_chunks_arriving_during_consumption():
    """Late-arriving chunks (worker writes after consumer is already
    polling) must be picked up via the BLOCK wait — not lost."""
    from server.queue import TtsResult
    from server.result_stream import consume_result_stream

    redis = fakeredis.aioredis.FakeRedis()
    rid = str(uuid.uuid4())

    async def write_chunks_after_delay():
        await asyncio.sleep(0.05)
        await _publish_chunks(redis, rid, [
            TtsResult(request_id=rid, seq=0, pcm_bytes=b"x"),
            TtsResult(request_id=rid, seq=1, pcm_bytes=b"", final=True),
        ])

    writer = asyncio.create_task(write_chunks_after_delay())
    collected = []
    async for chunk in consume_result_stream(
        redis, rid, block_ms=20, overall_timeout_s=2.0,
    ):
        collected.append(chunk)
    await writer
    assert [c.seq for c in collected] == [0, 1]
    assert collected[-1].final is True
