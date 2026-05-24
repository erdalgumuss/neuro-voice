"""NQAI Voice — FastAPI TTS server.

Endpoints:
    POST /v1/tts            non-streaming WAV synthesis
    POST /v1/tts/stream     sentence-chunked streaming WAV
    GET  /v1/voices         list voice catalog
    POST /v1/voices         enroll new voice (reference audio upload)
    DELETE /v1/voices/{id}  remove voice
    GET  /health            liveness probe
"""

from .config import settings

__all__ = ["settings"]
