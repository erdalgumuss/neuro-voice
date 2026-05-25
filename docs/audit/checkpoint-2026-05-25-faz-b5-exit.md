# Faz B.5 (Vendor Parity) — Exit Checkpoint

**Date:** 2026-05-25
**Branch:** `main`
**Suite:** 463/463 pass, ruff clean
**Range:** `8e165ca..HEAD` (Dalga 2.5 → closure), full Faz B.5 from
            `df... ↑` (Dalga 1)

## What Faz B.5 was for

A direct response to the operator directive: NQAI Voice must be a
drop-in target for NIVA / NEEKO / NeuroCourse SDKs that already speak
ElevenLabs or MiniMax shapes, with no compromise on premium audio
quality. "Ucuz teknoloji kabul edilemez" — every feature accepted on
the wire either actually works or is forward-compatible and labelled
as such in the docstring.

Faz B.5 is explicitly an **intermediate phase** between Faz B (worker
omurgası + reliability hardening) and the planned Faz C v2 (operator
SLO + production hardening) — added because real-product readiness
needed vendor-shaped surface area, not more infra.

## What shipped

### Dalga 1 — Cost + premium signature
* **1.1 Output codecs** — `src/audio/encoders.py` (`Pcm16PassthroughEncoder`,
  `Mp3Encoder`, `OpusEncoder` via ffmpeg subprocess pipe with
  `-fflags +nobuffer -flush_packets 1`). Schema accepts
  `wav / pcm16 / mp3 / opus`.
* **1.2 model_id registry** — `src/server/models.py` exposes
  `nqai-voxcpm2-tr-{turbo,hd,character}` presets. `GET /v1/models`
  returns the catalog (public, no auth). Single VoxCPM2 base with
  different `cfg_value` / `inference_timesteps` knobs — naming is
  honest about that.
* **1.3 Worker warmup** — per-voice cold-load metric
  (`WORKER_COLD_LOAD_SECONDS{voice}`) + `NQAI_WORKER_WARMUP_VOICES`
  env list. First-request latency for warm voices is now bounded.

### Dalga 2 — Vendor parity + developer trust
* **2.1 voice_settings** — `{stability, similarity_boost, style,
  use_speaker_boost, speed, pitch}` on every TTS request. Active
  fields map to engine knobs (similarity_boost → cfg_value offset,
  stability → inference_timesteps offset, speed → PCM resample).
  Forward-compat fields documented per-field.
* **2.2 ElevenLabs URL aliases** — `POST /v1/text-to-speech/{voice_id}`
  + `/stream` delegate to the canonical handlers; SDKs swap base URL
  and keep working.
* **2.3 Response headers + extended metrics** — `X-NQAI-Model-Id`,
  `X-NQAI-Character-Count`, `X-NQAI-Output-Format` on every response;
  `TTSJobMetrics` now includes `first_audio_ms`, `character_count`,
  `model_id`.
* **2.4 Voice catalog enrichment** — `description`, `labels`,
  `preview_url`, `voice_settings_defaults` columns on `voices` +
  `PATCH /v1/voices/{voice_id}` (owner-only, 404-on-not-owned).
  Catalog pagination (`?limit=&offset=`).
* **2.5 First-class voice clone API** — `POST /v1/voices` accepts
  `description`, `labels`, `visibility`, `remove_background_noise`,
  `voice_talent_consent`. `POST /v1/voices/add` ElevenLabs-shape
  alias with name-derived voice_id. `EnrollResponse.requires_verification`
  flips based on consent. `NQAI_ENROLL_MIN_SECONDS` runtime floor.
* **2.6 seed + context + pronunciation_dict** — `seed` (best-effort
  determinism via `torch.manual_seed`), `previous_text` / `next_text`
  (forward-compat prosody hints), `pronunciation_dict` (per-request
  whole-word case-insensitive override, applied BEFORE the built-in
  code-mix lexicon). Worker engine signature gains `request_meta`
  bundle so future fields don't churn stubs.

### Dalga 3 — Long-form + WebSocket
* **3.1 WebSocket input streaming** — `WS /v1/text-to-speech/{voice_id}
  /stream-input` (`src/server/ws.py`). Bearer auth via query param
  OR `Authorization` header; protocol: config / append-text / flush
  / close; sentence-boundary + MAX_BUFFER_CHARS + explicit flush
  triggers; each flush enqueues one job through the existing Redis
  Streams queue + forwards result chunks back as base64 PCM frames
  with alignment metadata. Existence-leak rule preserved (voice not
  found → close 1008).
* **3.2 Async long-form + alignment** — `TTSJobCreate.text` max
  raised to 250 000 chars; runtime ceiling `NQAI_ASYNC_MAX_CHARS`
  (default 100 000) gates submission. Worker computes per-sentence
  alignment (`seq, start_ms, end_ms, text`) during the chunk loop;
  persisted on `job_idempotency.sentence_alignment` (migration 0008).
  `TTSJobStatusResponse.alignment` surfaces it.

## DoD against the original plan

| Item | Status | Notes |
| --- | --- | --- |
| Output codec layer (wav/pcm16/mp3/opus) | ✅ | ffmpeg subprocess; tested |
| model_id registry + `/v1/models` | ✅ | 3 presets; vendor-shape response |
| voice_settings (vendor superset) | ✅ | active + forward-compat per-field documented |
| ElevenLabs URL aliases (sync + stream) | ✅ | delegate to canonical handlers |
| Response header parity | ✅ | character_count, model_id, output_format |
| Voice catalog enrichment (description/labels/preview) | ✅ | + PATCH + pagination |
| First-class voice clone API + alias | ✅ | consent flag + min_seconds gate |
| seed / previous_text / next_text / pronunciation_dict | ✅ | seed + pron_dict active; context forward-compat |
| WebSocket input streaming | ✅ | sentence-boundary + flush + close semantics |
| Async long-form text + alignment metadata | ✅ | 100 000 char default ceiling; per-sentence alignment |

## What was deferred — and why

* **Real RNNoise / DeepFilterNet denoise** — the
  `remove_background_noise` flag is captured for audit but the active
  pass is deferred. Shipping a half-measure that degrades premium
  audio would violate the directive; we'd rather honestly tell the
  client "captured for governance, not applied yet".
* **previous_text / next_text engine action** — the engine does not
  yet thread these into a prosody sliding-window. They're persisted
  on the job payload + audit so once VoxCPM2 exposes a context-window
  hook the wire shape is right.
* **Subtitle SRT export endpoint** — alignment is already in the JSON
  status response; a dedicated `?format=srt` query is trivial to add
  on top of it but wasn't required for the closure.
* **WebSocket alignment timing emission** — frames carry
  `alignment.text + seq`, but per-sentence playback timestamps are
  only on the async path right now. Adding `start_ms` to WS frames
  is a follow-up.
* **MiniMax two-step file-upload clone** — the one-shot
  `POST /v1/voices` + `/v1/voices/add` covers both vendor mental
  models; the file-upload-then-clone split adds an extra endpoint
  without enabling a new use case for our current consumers.

## Test surface

Starting baseline: 442 pass (after Dalga 2.4 closure).
Final: **463 pass**, ruff clean.

| Wave | +tests | New / extended files |
| --- | --- | --- |
| 2.5 | +6 | `test_api_smoke.py` (clone metadata, alias, consent, min_seconds) |
| 2.6 | +9 | `test_normalize.py`, `test_worker_pipeline.py`, `test_api_smoke.py` |
| 3.1 | +5 | new `test_ws_stream_input.py` |
| 3.2 | +2 | `test_async_jobs.py`, `test_async_e2e.py` |
| (re-targeted) | 0 | `test_create_job_rejects_oversize_text` adjusted to actual new ceiling |

## Next phase

User-facing testing begins now. The product surface that the SDK
integrators (NIVA, NEEKO, NeuroCourse) will exercise is:

* `POST /v1/text-to-speech/{voice_id}` and `/stream` (ElevenLabs)
* `POST /v1/tts` and `/v1/tts/stream` (canonical, deprecated)
* `POST /v1/tts/jobs` + `GET /v1/tts/jobs/{id}` (async long-form)
* `WS /v1/text-to-speech/{voice_id}/stream-input` (partial text)
* `POST /v1/voices` and `/v1/voices/add` (clone)
* `GET /v1/models`, `GET /v1/voices`, `PATCH /v1/voices/{id}`

Anything found during user testing lands as Faz B.5 hotfix commits,
not a new dalga.
