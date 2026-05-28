"""NeuroVoice TTS system adapter for the eval harness.

Calls our own HTTP API just like an external client would. This is
on purpose — running the eval through our public surface catches
regressions in the wrapper (auth, voice resolution, codec pipeline)
that an in-process engine call would silently bypass.

Two paths:

  * **HTTP path** (default): POST /v1/tts/jobs + poll. Async-only —
    we deliberately don't use the deprecated sync /v1/tts. Records
    `engine_inputs` automatically because the worker pipeline runs
    unchanged.

  * **In-process path** (`--in-process`): for benchmarks where the
    operator wants to bypass HTTP overhead and measure pure model
    performance. NOT for cross-vendor comparison runs.

Authentication is via the same Bearer API key the operator already
uses for other clients (`NEUROVOICE_API_KEY` env or `--api-key` flag).
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass

import httpx

from . import SystemMetadata, SystemOutput

logger = logging.getLogger("neurovoice.eval.systems.neurovoice")


@dataclass
class NeuroVoiceSystem:
    """HTTP adapter — calls the NeuroVoice public API."""

    name: str = "neurovoice"
    base_url: str = "http://localhost:8000"
    api_key: str = ""
    model_id: str = "voxcpm2-tr-hd"
    audio_format: str = "wav"
    poll_timeout_s: float = 60.0
    poll_interval_s: float = 0.5

    def synthesize(
        self,
        *,
        text: str,
        voice_id: str,
    ) -> SystemOutput:
        if not self.api_key:
            raise RuntimeError(
                "NeuroVoiceSystem.api_key is empty — set NEUROVOICE_API_KEY in "
                "the environment or pass --api-key. Eval refuses to run with "
                "anonymous auth so report rows stay traceable to a tenant."
            )
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Idempotency-Key": str(uuid.uuid4()),
            "X-NV-App": "neurovoice-eval-harness",
        }
        t0 = time.monotonic()
        with httpx.Client(base_url=self.base_url, timeout=30.0) as client:
            create = client.post(
                "/v1/tts/jobs",
                headers=headers,
                json={
                    "text": text,
                    "voice_id": voice_id,
                    "model_id": self.model_id,
                    "audio_format": self.audio_format,
                },
            )
            create.raise_for_status()
            job_id = create.json()["job_id"]

            deadline = time.monotonic() + self.poll_timeout_s
            output_url: str | None = None
            while time.monotonic() < deadline:
                status = client.get(
                    f"/v1/tts/jobs/{job_id}",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                status.raise_for_status()
                body = status.json()
                if body["status"] == "complete":
                    output_url = body["output"]["audio_url"]
                    break
                if body["status"] == "failed":
                    raise RuntimeError(
                        f"NeuroVoice eval job {job_id} failed: "
                        f"{body.get('error_code')} {body.get('error_detail')}"
                    )
                time.sleep(self.poll_interval_s)
            if output_url is None:
                raise TimeoutError(
                    f"NeuroVoice eval job {job_id} did not complete in "
                    f"{self.poll_timeout_s:.0f}s"
                )

            audio = client.get(output_url)
            audio.raise_for_status()
            pcm, sr = _wav_bytes_to_pcm16(audio.content)

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        return SystemOutput(
            pcm_int16=pcm,
            sample_rate=sr,
            metadata=SystemMetadata(
                system=self.name,
                model_id=self.model_id,
                voice_id=voice_id,
                elapsed_ms=elapsed_ms,
                extra={"job_id": job_id, "base_url": self.base_url},
            ),
        )


def _wav_bytes_to_pcm16(wav_bytes: bytes) -> tuple[bytes, int]:
    """Strip the WAV header and return raw int16 PCM + the sample rate.
    soundfile is already a dep (used by the rest of the codebase) so
    this stays lightweight."""
    import io

    import numpy as np
    import soundfile as sf

    arr, sr = sf.read(io.BytesIO(wav_bytes), dtype="int16", always_2d=False)
    if arr.ndim > 1:
        arr = arr[:, 0]  # take mono channel — eval doesn't need stereo
    return arr.astype(np.int16).tobytes(), int(sr)
