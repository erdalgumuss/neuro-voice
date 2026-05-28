"""ElevenLabs adapter — for vendor baseline rows in the comparison table.

The whole point of the eval harness is the side-by-side: "NQAI VoxCPM2
vs ElevenLabs Multilingual v2 vs ElevenLabs Flash on the SAME test
set, scored by the SAME metrics." This adapter is the second column
of that table.

API key via `ELEVENLABS_API_KEY` env. The adapter is intentionally
minimal — no streaming, no SDK dependency, just `requests` over HTTPS
because the only thing we need is the audio bytes for a single
sentence. Operators rotate the key by exporting a new value.

Cost note: each call charges credits against the operator's ElevenLabs
account. The runner caches PCM by (system, voice, text) hash so an
interrupted benchmark doesn't re-bill clips that already succeeded —
see `eval.runner._cache_key`.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

from . import SystemMetadata, SystemOutput

logger = logging.getLogger("neurovoice.eval.systems.elevenlabs")


@dataclass
class ElevenLabsSystem:
    """`voice_id` here is an ElevenLabs voice id (e.g. `21m00Tcm4TlvDq8ikWAM`),
    NOT an NQAI catalog slug. The eval CLI accepts an explicit
    `--elevenlabs-voice` flag so the operator can pick the closest
    counterpart to their NQAI voice for the comparison."""

    name: str = "elevenlabs"
    api_key: str = ""
    model_id: str = "eleven_multilingual_v2"
    output_format: str = "pcm_24000"

    BASE_URL: str = "https://api.elevenlabs.io"

    def synthesize(
        self,
        *,
        text: str,
        voice_id: str,
    ) -> SystemOutput:
        if not self.api_key:
            raise RuntimeError(
                "ElevenLabsSystem.api_key is empty — set "
                "ELEVENLABS_API_KEY or pass --elevenlabs-key. The eval "
                "harness refuses to attempt anonymous vendor calls."
            )
        headers = {
            "xi-api-key": self.api_key,
            "accept": (
                "audio/pcm" if self.output_format.startswith("pcm")
                else "audio/wav"
            ),
            "content-type": "application/json",
        }
        params = {"output_format": self.output_format}
        body = {"text": text, "model_id": self.model_id}

        t0 = time.monotonic()
        with httpx.Client(timeout=60.0) as client:
            r = client.post(
                f"{self.BASE_URL}/v1/text-to-speech/{voice_id}",
                headers=headers,
                params=params,
                json=body,
            )
            r.raise_for_status()
            audio_bytes = r.content
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        # ElevenLabs `pcm_24000` returns raw little-endian int16 at
        # 24 kHz. `pcm_44100` is also available; the suffix doubles
        # as the sample rate. If the operator picked a non-pcm format
        # the runner is OK with mp3/wav but metric backends expect
        # int16 PCM, so we decode here.
        if self.output_format.startswith("pcm_"):
            try:
                sr = int(self.output_format.split("_", 1)[1])
            except (IndexError, ValueError) as e:
                raise ValueError(
                    f"unparseable ElevenLabs pcm format '{self.output_format}'"
                ) from e
            pcm = audio_bytes
        else:
            pcm, sr = _decode_to_pcm16(audio_bytes)

        return SystemOutput(
            pcm_int16=pcm,
            sample_rate=sr,
            metadata=SystemMetadata(
                system=self.name,
                model_id=self.model_id,
                voice_id=voice_id,
                elapsed_ms=elapsed_ms,
                extra={"output_format": self.output_format},
            ),
        )


def _decode_to_pcm16(audio_bytes: bytes) -> tuple[bytes, int]:
    """Decode mp3/wav blob → int16 PCM + sample rate."""
    import io

    import numpy as np
    import soundfile as sf

    arr, sr = sf.read(io.BytesIO(audio_bytes), dtype="int16", always_2d=False)
    if arr.ndim > 1:
        arr = arr[:, 0]
    return arr.astype(np.int16).tobytes(), int(sr)
