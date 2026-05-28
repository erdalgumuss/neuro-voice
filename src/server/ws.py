"""WebSocket input-streaming TTS —  .

Endpoint
--------
::

    WS /v1/text-to-speech/{voice_id}/stream-input

Wire shape mirrors ElevenLabs' ``/stream-input`` so SDKs that already
implement that pattern can flip base URLs and keep working:

* Auth: ``?api_key=<bearer>`` query parameter (or ``Authorization:
  Bearer`` header on the WS upgrade; some browser SDKs cannot set
  arbitrary headers on ``new WebSocket()`` so we accept both).
* Inbound messages (JSON):

    * Initial config (optional):
      ``{"voice_settings": {...}, "model_id": "...",
         "audio_format": "pcm16", "seed": 42,
         "previous_text": "...", "pronunciation_dict": {...}}``
    * Append text:
      ``{"text": "Hello "}``
    * Append + flush:
      ``{"text": "world.", "flush": true}``
    * Force flush (no new text):
      ``{"flush": true}``
    * Close:
      ``{"close": true}``  (or just close the WS)

* Outbound messages (JSON):

    * Audio chunk:
      ``{"audio": "<base64 pcm16>", "alignment": {"text": "First sentence.",
        "seq": 0}, "audio_format": "pcm16", "sample_rate": 48000}``
    * Sentence boundary:
      ``{"event": "sentence_end", "seq": 0, "text": "First sentence."}``
    * Error:
      ``{"event": "error", "code": "voice_not_found", "detail": "..."}``
    * Done:
      ``{"event": "done"}``

Flushing strategy
-----------------
We accumulate text until ONE of:

    * a sentence boundary (``.``, ``!``, ``?`` followed by space/EOL)
    * the client sets ``flush=true``
    * the buffer exceeds ``MAX_BUFFER_CHARS`` (forced flush so a client
      that never sends punctuation still gets audio)

Each flush enqueues ONE job onto the existing Redis Streams queue and
forwards the result chunks back to the client as base64 PCM frames.
This keeps the worker / engine path identical to the HTTP surface —
no separate engine entry point for WS.

Why not bypass the queue and call the engine directly here? Because
the engine lives in the worker process; the gateway is pure I/O. Going
through Redis costs ~2 ms per segment, well below the 80–100 ms latency
budget set in scale-roadmap §11.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import re
import uuid
from typing import Any

from fastapi import HTTPException, WebSocket, WebSocketDisconnect, status
from sqlalchemy.ext.asyncio import AsyncSession

from observability import TTS_REQUESTS
from repos import (
    AuditRepo,
    IdempotencyRepo,
    VoiceConsentRecordRepo,
    VoiceRepo,
    lifecycle_state,
)

from .auth import authenticate_bearer
from .config import settings
from .models import UnknownModelError, resolve_model
from .queue import TtsJobPayload, TtsJobQueue
from .result_stream import ResultStreamTimeout, consume_result_stream
from .schemas import VoiceSettings, _validate_pronunciation_dict

logger = logging.getLogger("neurovoice.server.ws")

# Buffer floor: never flush an utterance shorter than this without an
# explicit `flush=true`. Stops a noisy stream of single-char appends
# from queuing one job per keystroke.
MIN_BUFFER_CHARS = 12

# Buffer ceiling: if the client never sends punctuation or `flush=true`,
# we still emit at this length so the listener gets audio. Picked to
# match scale-roadmap §11's "one sentence is ~120 chars TR" baseline.
MAX_BUFFER_CHARS = 240

# Idle timeout: tear down the WS if the client sends nothing for this
# long. Mirrors ElevenLabs' `inactivity_timeout` default (20 s).
DEFAULT_IDLE_TIMEOUT_S = 20.0

# Per-segment synthesis ceiling. Each flushed segment goes through the
# same queue path as `/v1/tts`; if a worker dies mid-segment the WS
# surfaces an error event instead of hanging.
SEGMENT_TIMEOUT_S = 30.0

# Sentence-boundary detector. We want to flush on a real terminal
# punctuation followed by whitespace OR end of buffer, not on a decimal
# point inside "3.14" — the worker frontend will normalise that anyway.
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")


def _emit_tts_request_metric(tenant: str, voice: str, st: str) -> None:
    """ hotfix — bump `TTS_REQUESTS` with the correct labelnames.

    The  commit used the wrong label keys here (`endpoint=...`
    instead of the real `(tenant, voice, status)`) which made the
    `prometheus_client` call raise ValueError, swallowed by
    `contextlib.suppress(Exception)`. The metric never incremented on
    the WS happy path so dashboards and alerts under-counted WS traffic.
    Centralised here so every WS close path emits the same shape.

    ``st`` enum mirrors the HTTP convention: ``success``, ``error``,
    ``backpressure``, ``auth_failed``.
    """
    with contextlib.suppress(Exception):
        TTS_REQUESTS.labels(tenant=tenant, voice=voice, status=st).inc()


def _ws_audio_format(raw: str | None) -> str:
    """WebSocket clients default to pcm16 — small, browser-decodable
    via AudioContext, no MP3/Opus container framing on the wire. WAV is
    silly for streaming because each frame would carry a fresh header."""
    if raw in (None, "", "pcm16"):
        return "pcm16"
    if raw in ("mp3", "opus", "wav"):
        return raw
    raise ValueError(f"unsupported audio_format '{raw}' for WS stream-input")


def _split_at_last_boundary(buf: str) -> tuple[str, str]:
    """Return ``(segment_to_flush, remainder)``.

    Picks the LAST sentence-end match so we send as much complete text
    as possible per job (better prosody continuity within a sentence).
    """
    last_end = -1
    for m in _SENTENCE_END.finditer(buf):
        last_end = m.end()
    if last_end <= 0:
        return ("", buf)
    return (buf[:last_end].strip(), buf[last_end:].lstrip())


class _WsState:
    """Per-connection state. Kept off the WebSocket object so the
    cleanup path can null it out for the GC without touching attributes
    we don't own."""

    __slots__ = (
        "buffer",
        "voice_settings",
        "model_id",
        "audio_format",
        "seed",
        "previous_text",
        "next_text",
        "pronunciation_dict",
        "seq",
    )

    def __init__(self) -> None:
        self.buffer: str = ""
        self.voice_settings: dict[str, Any] | None = None
        self.model_id: str | None = None
        self.audio_format: str = "pcm16"
        self.seed: int | None = None
        self.previous_text: str | None = None
        self.next_text: str | None = None
        self.pronunciation_dict: dict[str, str] | None = None
        # Sentence index across the WHOLE session — each segment may
        # produce multiple sentence chunks; we keep a session-global
        # counter so the client can render captions monotonically.
        self.seq: int = 0


async def _send_error(ws: WebSocket, code: str, detail: str) -> None:
    with _suppress_disconnect():
        await ws.send_json({"event": "error", "code": code, "detail": detail})


class _suppress_disconnect:
    """Context manager that swallows ``WebSocketDisconnect`` raised
    from sends. The client may have closed mid-flight; we don't want
    that to crash the WS handler."""

    def __enter__(self) -> _suppress_disconnect:  # type: ignore[name-defined]
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:  # type: ignore[no-untyped-def]
        return exc_type is not None and issubclass(exc_type, WebSocketDisconnect)


def _apply_config(state: _WsState, msg: dict[str, Any]) -> str | None:
    """Mutate `state` from an inbound message's config fields.

    Returns an error code string if validation failed (caller emits the
    error frame + closes), or None on success. Idempotent — clients
    may re-send config in any message.
    """
    if "voice_settings" in msg and msg["voice_settings"] is not None:
        try:
            state.voice_settings = (
                VoiceSettings(**msg["voice_settings"])
                .model_dump(exclude_none=True)
            )
        except Exception as e:  # noqa: BLE001 — pydantic chains nicely
            logger.info("ws voice_settings invalid: %s", e)
            return "invalid_voice_settings"
    if "model_id" in msg and msg["model_id"] is not None:
        try:
            preset = resolve_model(msg["model_id"])
        except UnknownModelError:
            return "unknown_model_id"
        state.model_id = preset.model_id
    if "audio_format" in msg and msg["audio_format"] is not None:
        try:
            state.audio_format = _ws_audio_format(msg["audio_format"])
        except ValueError:
            return "unsupported_audio_format"
    if "seed" in msg and msg["seed"] is not None:
        try:
            seed = int(msg["seed"])
        except (TypeError, ValueError):
            return "invalid_seed"
        if seed < 0 or seed > 2147483647:
            return "invalid_seed"
        state.seed = seed
    if "previous_text" in msg and msg["previous_text"] is not None:
        pv = str(msg["previous_text"])
        if len(pv) > 4000:
            return "previous_text_too_long"
        state.previous_text = pv
    if "next_text" in msg and msg["next_text"] is not None:
        nx = str(msg["next_text"])
        if len(nx) > 4000:
            return "next_text_too_long"
        state.next_text = nx
    if "pronunciation_dict" in msg and msg["pronunciation_dict"] is not None:
        try:
            state.pronunciation_dict = _validate_pronunciation_dict(
                msg["pronunciation_dict"],
            )
        except ValueError:
            return "invalid_pronunciation_dict"
    return None


async def _flush_segment(
    *,
    ws: WebSocket,
    state: _WsState,
    segment: str,
    voice_id: str,
    db_voice_slug: str,
    tenant_id: uuid.UUID,
    api_key_id: uuid.UUID,
    api_key_prefix: str,
    redis: Any,
    queue: TtsJobQueue,
    session: AsyncSession,
    app_label: str | None,
) -> None:
    """Enqueue ONE segment and forward result chunks back to the client.

    Each flushed segment uses a fresh request_id so the existing per-
    request result stream pattern (`consume_result_stream`) drops in
    unchanged. Idempotency is reserved server-side because the client
    can't realistically supply a stable key for partial-text flushes.

     hotfix (2026-05-25 audit): three admission gates kick in
    BEFORE the XADD so the WS surface behaves identically to HTTP:

      1. **Per-segment max_chars** — the segment must not exceed
         `settings.max_chars_per_request` (default 4000). The HTTP sync
         paths enforce this; without the gate a WS client can submit a
         20 000-char segment that the sync proxy would have refused.
      2. **Capacity-aware backpressure** — defers to the HTTP helper
         `_compute_backpressure_decision`. If the cluster says it's
         saturated, we send an error frame with `queue_saturated` and
         drop the segment without enqueueing. Audit + metric writes
         mirror the HTTP path so `TTS_REQUESTS{status="backpressure"}`
         stays the single SLO denominator.
    """
    # Gate 1 — per-segment max_chars.
    if len(segment) > settings.max_chars_per_request:
        await _send_error(
            ws, "segment_too_long",
            f"segment {len(segment)} chars exceeds max_chars_per_request="
            f"{settings.max_chars_per_request}; flush more often",
        )
        _emit_tts_request_metric(
            str(tenant_id), db_voice_slug, "error",
        )
        return

    # Gate 2 — capacity-aware backpressure (shared HTTP/WS decision).
    from .main import _compute_backpressure_decision  # local — avoids circular
    admit, denied_reason, bp_payload = await _compute_backpressure_decision(
        queue,
    )
    if not admit:
        try:
            await AuditRepo(session).record(
                actor_type="api_key",
                actor_id=api_key_id,
                actor_label=api_key_prefix,
                action="tts.backpressure",
                result="denied",
                tenant_id=tenant_id,
                payload={**bp_payload, "reason": denied_reason,
                         "surface": "ws"},
            )
            await session.commit()
        except Exception:
            logger.exception("ws backpressure audit write failed; continuing")
        _emit_tts_request_metric(
            str(tenant_id), db_voice_slug, "backpressure",
        )
        await _send_error(
            ws, "queue_saturated",
            f"cluster backpressure ({denied_reason}); slow down or retry",
        )
        return

    rid = uuid.uuid4()
    idem = IdempotencyRepo(session, tenant_id)
    try:
        # Reserve unconditionally — the rid is freshly minted so a
        # conflict is impossible; the row gives us the same audit /
        # billing trail every other path uses.
        await idem.reserve(
            request_id=rid,
            api_key_id=api_key_id,
            request_hash=f"ws:{hash(segment) & 0xFFFFFFFF:x}",
        )
        await session.commit()
    except Exception:
        # Any DB hiccup → surface as a soft error frame, drop the segment,
        # but keep the WS alive so the client can retry the next chunk.
        logger.exception("ws idempotency reserve failed for rid=%s", rid)
        await _send_error(ws, "internal", "could not reserve idempotency row")
        return

    payload = TtsJobPayload(
        request_id=str(rid),
        tenant_id=str(tenant_id),
        api_key_id=str(api_key_id),
        voice_id=db_voice_slug,
        text=segment,
        language="tr",
        audio_format=state.audio_format,
        model_id=state.model_id,
        voice_settings=state.voice_settings,
        seed=state.seed,
        previous_text=state.previous_text,
        next_text=state.next_text,
        pronunciation_dict=state.pronunciation_dict,
        app_label=app_label,
    )
    try:
        await queue.submit(payload)
    except Exception:
        logger.exception("ws queue submit failed for rid=%s", rid)
        await _send_error(ws, "queue_unavailable", "could not enqueue segment")
        return

    try:
        async for chunk in consume_result_stream(
            redis, str(rid),
            overall_timeout_s=SEGMENT_TIMEOUT_S,
        ):
            if chunk.error:
                await _send_error(ws, "worker_error", chunk.error)
                return
            if chunk.final:
                # Don't emit `done` per-segment — the client wants a
                # session-level done at WS close. Sentence boundary frame
                # is the per-segment signal.
                continue
            payload_audio = base64.b64encode(chunk.pcm_bytes).decode("ascii")
            with _suppress_disconnect():
                await ws.send_json({
                    "audio": payload_audio,
                    "alignment": {
                        "text": chunk.sentence_text or "",
                        "seq": state.seq,
                    },
                    "audio_format": state.audio_format,
                    "sample_rate": 48000,
                })
                await ws.send_json({
                    "event": "sentence_end",
                    "seq": state.seq,
                    "text": chunk.sentence_text or "",
                })
            state.seq += 1
    except ResultStreamTimeout:
        await _send_error(ws, "timeout", "worker did not finish in time")
        return

    # After a clean segment finish, treat its text as `previous_text`
    # for the next segment so prosody hints carry forward without the
    # client having to re-send. Forward-compat: engine ignores it today
    # ( leaves previous_text/next_text as no-ops) but the
    # protocol shape is right when engine support lands.
    state.previous_text = segment


async def stream_input_endpoint(
    websocket: WebSocket,
    voice_id: str,
    queue: TtsJobQueue,
    session_factory,
    redis,
) -> None:
    """FastAPI WS handler. Wired from `server.main` so the FastAPI
    decorator stays next to the HTTP routes for OpenAPI discoverability.

    `session_factory` is a 0-arg callable returning an async context
    manager that yields an `AsyncSession` — same shape as
    `db.session.AsyncSessionLocal`. We don't reuse the request-scoped
    `Depends(get_session)` because WebSockets get one session for the
    whole connection (auth + voice resolve + per-segment idem-reserve)
    and Starlette's WS lifecycle isn't tied to a single session.
    """
    # ---------- Auth ----------------------------------------------------
    # ElevenLabs uses `?xi-api-key=...`; we accept `api_key` (Bearer-style
    # token) on the query string OR the standard `Authorization: Bearer`
    # header. Either path is OK; both wrong → 1008.
    api_key_full = websocket.query_params.get("api_key")
    authorization_header = websocket.headers.get("authorization")
    if api_key_full and not authorization_header:
        authorization_header = f"Bearer {api_key_full}"
    if not authorization_header:
        _emit_tts_request_metric("unknown", "unknown", "auth_failed")
        await websocket.close(code=1008, reason="missing api key")
        return

    # Open the session and run the auth + voice lookup BEFORE we accept,
    # so a bad bearer / missing voice closes with a sane reason.
    async with session_factory() as ws_session:
        # auth runs the same DB-backed pipeline as HTTP — we get the
        # tenant + scope without inventing a parallel auth path. 
        # hotfix (2026-05-25): preserve the 401-vs-403 distinction the
        # HTTP path makes — `authenticate_bearer` raises HTTPException
        # with the right status_code, we just need to map it through.
        try:
            auth_ctx = await authenticate_bearer(
                authorization_header,
                session=ws_session,
                redis=redis,
                required_scopes=("tts:write",),
            )
        except HTTPException as e:
            _emit_tts_request_metric("unknown", "unknown", "auth_failed")
            # 401 → "auth failed" (bad bearer, expired, missing).
            # 403 → "forbidden" (scope insufficient, tenant inactive).
            # 429 → "rate limited".
            # RFC 6455 1008 (policy violation) for all auth-class failures;
            # the `reason` string + an error frame BEFORE close let the
            # SDK distinguish.
            reason = {
                status.HTTP_401_UNAUTHORIZED: "auth failed",
                status.HTTP_403_FORBIDDEN: "forbidden",
                status.HTTP_429_TOO_MANY_REQUESTS: "rate limited",
            }.get(e.status_code, "auth failed")
            await websocket.close(code=1008, reason=reason)
            return
        except Exception as e:  # noqa: BLE001 — log + close
            logger.exception("ws auth pipeline crashed: %s", e)
            _emit_tts_request_metric("unknown", "unknown", "auth_failed")
            await websocket.close(code=1011, reason="internal")
            return

        # Resolve voice through the SAME VoiceRepo as HTTP — accessible
        # = owned ∪ shared ∪ public. Non-accessible → 1008 with a
        # vendor-neutral reason (don't leak existence; D-08).
        repo = VoiceRepo(ws_session, auth_ctx.tenant_id)
        db_voice = await repo.get_accessible(voice_id)
        if db_voice is None or db_voice.deleted_at is not None:
            _emit_tts_request_metric(
                str(auth_ctx.tenant_id), "unknown", "error",
            )
            await websocket.close(code=1008, reason="voice not found")
            return

        # ADR-11 — lifecycle + active-consent gate (parallel of the
        # HTTP `_ensure_voice_synthesizable` helper). Frozen / pending-
        # purge / purged voices close with 1008 + a domain-specific
        # reason string. RFC 6455 reason length cap is 123 bytes; keep
        # the reason short.
        ls = lifecycle_state(db_voice)
        if ls != "active":
            _emit_tts_request_metric(
                str(auth_ctx.tenant_id), db_voice.voice_id, "error",
            )
            await websocket.close(
                code=1008, reason=f"voice {ls}",
            )
            return
        latest_consent = await VoiceConsentRecordRepo(
            ws_session,
        ).latest_active(db_voice.id)
        if latest_consent is None:
            _emit_tts_request_metric(
                str(auth_ctx.tenant_id), db_voice.voice_id, "error",
            )
            await websocket.close(
                code=1008, reason="voice no active consent",
            )
            return

        await websocket.accept()
        # Earlier revisions called `TTS_REQUESTS.labels(endpoint=..., status=...)`
        # with the wrong label set, and `contextlib.suppress(Exception)` silenced
        # the resulting ValueError — so the WS happy-path was missing from the
        # SLO counter. Count it with the real labelnames (tenant, voice, status).
        _emit_tts_request_metric(
            str(auth_ctx.tenant_id), db_voice.voice_id, "success",
        )

        state = _WsState()
        # Most-recent app label wins; clients can set it via header on the
        # upgrade or `X-NV-App` in the initial config message.
        app_label = (
            websocket.headers.get("x-nv-app")
            or websocket.headers.get("X-NV-App")
        )

        # ---------- Inbound loop ----------------------------------------
        last_msg_at = asyncio.get_running_loop().time()
        try:
            while True:
                try:
                    raw = await asyncio.wait_for(
                        websocket.receive_text(),
                        timeout=DEFAULT_IDLE_TIMEOUT_S,
                    )
                except asyncio.TimeoutError:
                    # No-op heartbeat: tell the client we're closing on idle.
                    await _send_error(
                        websocket, "idle_timeout",
                        f"no message in {DEFAULT_IDLE_TIMEOUT_S:.0f}s",
                    )
                    break

                last_msg_at = asyncio.get_running_loop().time()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await _send_error(
                        websocket, "invalid_json", "message is not JSON",
                    )
                    continue
                if not isinstance(msg, dict):
                    await _send_error(
                        websocket, "invalid_message", "expected JSON object",
                    )
                    continue

                # 1. apply any config in the message
                cfg_err = _apply_config(state, msg)
                if cfg_err is not None:
                    await _send_error(websocket, cfg_err, "config rejected")
                    continue

                # 2. close request
                if msg.get("close") is True:
                    break

                # 3. accumulate text
                if isinstance(msg.get("text"), str):
                    appended = msg["text"]
                    # Light guard so a misbehaving client cannot push us
                    # past the worker's max request length in one shot.
                    if len(state.buffer) + len(appended) > 20000:
                        await _send_error(
                            websocket, "buffer_overflow",
                            "accumulated text exceeds 20000 chars; flush more often",
                        )
                        state.buffer = ""
                        continue
                    state.buffer += appended

                # 4. flush logic
                force_flush = bool(msg.get("flush"))
                segment, remainder = _split_at_last_boundary(state.buffer)

                if force_flush:
                    # Flush EVERYTHING the buffer has, with or without
                    # a sentence boundary — vendor-parity with the
                    # ElevenLabs ``{"flush": true}`` semantics.
                    segment = state.buffer.strip()
                    remainder = ""
                elif segment and len(segment) >= MIN_BUFFER_CHARS:
                    # Sentence boundary path — keep `remainder` as the
                    # start of the next sentence so we can append to it.
                    pass
                elif len(state.buffer) >= MAX_BUFFER_CHARS:
                    # No punctuation in sight — force-emit so the client
                    # gets audio. Hard cut at the last whitespace before
                    # the ceiling so we don't slice a word in half.
                    cut = state.buffer.rfind(" ", 0, MAX_BUFFER_CHARS)
                    if cut <= 0:
                        cut = MAX_BUFFER_CHARS
                    segment = state.buffer[:cut].strip()
                    remainder = state.buffer[cut:].lstrip()
                else:
                    # Not enough to flush yet.
                    segment = ""
                    remainder = state.buffer

                state.buffer = remainder
                if not segment:
                    continue

                await _flush_segment(
                    ws=websocket,
                    state=state,
                    segment=segment,
                    voice_id=voice_id,
                    db_voice_slug=db_voice.voice_id,
                    tenant_id=auth_ctx.tenant_id,
                    api_key_id=auth_ctx.api_key_id,
                    api_key_prefix=auth_ctx.api_key.prefix,
                    redis=redis,
                    queue=queue,
                    session=ws_session,
                    app_label=app_label,
                )

            # Connection-close drain: if the client ended the session
            # with buffered text, flush it as a final segment so no
            # audio is silently dropped.
            tail = state.buffer.strip()
            if tail:
                await _flush_segment(
                    ws=websocket,
                    state=state,
                    segment=tail,
                    voice_id=voice_id,
                    db_voice_slug=db_voice.voice_id,
                    tenant_id=auth_ctx.tenant_id,
                    api_key_id=auth_ctx.api_key_id,
                    api_key_prefix=auth_ctx.api_key.prefix,
                    redis=redis,
                    queue=queue,
                    session=ws_session,
                    app_label=app_label,
                )
                state.buffer = ""

            # Session-level done frame.
            with _suppress_disconnect():
                await websocket.send_json({"event": "done"})

            _ = last_msg_at  # quiet unused-variable warning under linters
        except WebSocketDisconnect:
            logger.info("ws client disconnected for voice=%s", voice_id)
        finally:
            with contextlib.suppress(Exception):
                await websocket.close()
