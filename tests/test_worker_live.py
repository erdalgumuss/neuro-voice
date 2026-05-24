from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from worker.live import (
    InMemoryLiveMediaSink,
    iter_live_audio_frames,
    run_live_synthesis,
)
from worker.pipeline import VoiceView


@dataclass
class _Chunk:
    pcm_int16: bytes
    sample_rate: int = 48_000
    sentence_index: int = 0
    sentence_text: str = "Merhaba."
    elapsed_ms: float = 1.0


class _SlowSentenceEngine:
    sample_rate = 48_000

    def __init__(self) -> None:
        self.completed = False

    def warmup(self) -> None:
        pass

    def synthesize_stream(self, *, text, voice, reference_path, language_id="tr"):
        yield _Chunk(b"\x00\x00" * 1_920, sentence_index=0, sentence_text="İlk.")
        time.sleep(0.2)
        yield _Chunk(b"\x01\x00" * 960, sentence_index=1, sentence_text="İkinci.")
        self.completed = True

    def synthesize(self, *, text, voice, reference_path, language_id="tr"):
        raise NotImplementedError


async def test_live_bridge_yields_first_frame_before_generator_completes(tmp_path):
    engine = _SlowSentenceEngine()
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"x")
    frames = iter_live_audio_frames(
        engine,
        text="Merhaba. Devam.",
        voice=VoiceView(voice_id="live-voice"),
        reference_path=ref,
    )

    first = await asyncio.wait_for(anext(frames), timeout=0.1)
    assert first.seq == 0
    assert first.pcm_int16
    assert engine.completed is False
    await frames.aclose()


async def test_run_live_synthesis_records_first_audio_and_done(tmp_path):
    engine = _SlowSentenceEngine()
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"x")
    sink = InMemoryLiveMediaSink()

    trace = await run_live_synthesis(
        engine=engine,
        sink=sink,
        text="Merhaba.",
        voice=VoiceView(voice_id="live-voice"),
        reference_path=ref,
        request_id="rid-live",
    )

    event_names = [name for name, _payload in sink.control_events]
    assert "accepted" in event_names
    assert "first_audio" in event_names
    assert "done" in event_names
    assert sink.audio_frames
    assert sink.closed is True
    assert trace.as_dict()["model_ttfa_ms"] is not None
