"""Pydantic schemas for the v1 HTTP API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


AudioFormat = Literal["wav", "pcm16"]
StreamFormat = Literal["wav", "pcm16"]


class TTSRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=20000)
    voice_id: str = Field(..., min_length=3, max_length=64)
    language: Literal["tr"] = "tr"
    audio_format: AudioFormat = "wav"
    sample_rate: int | None = Field(default=None, ge=8000, le=48000)


class TTSStreamRequest(TTSRequest):
    audio_format: StreamFormat = "wav"
    chunk_format: Literal["sentence", "raw_pcm"] = "sentence"


class VoicePublic(BaseModel):
    voice_id: str
    display_name: str
    language: str
    gender: str
    style_tags: list[str]
    reference_seconds: float
    source: str
    license: str
    created_at: str
    created_by: str


class VoiceListResponse(BaseModel):
    voices: list[VoicePublic]
    count: int


class EnrollResponse(BaseModel):
    voice: VoicePublic
    detail: str = "voice enrolled"


class DeleteResponse(BaseModel):
    voice_id: str
    detail: str = "voice deleted"


class HealthResponse(BaseModel):
    status: Literal["ok", "warming", "degraded"]
    model_id: str
    device: str
    sample_rate: int
    loaded: bool
    voice_count: int
    version: str


class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None
