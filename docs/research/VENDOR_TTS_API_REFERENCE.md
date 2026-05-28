# Vendor TTS API Reference - ElevenLabs + MiniMax

Last updated: 2026-05-25

Purpose: collect the public TTS-focused API surface of ElevenLabs and MiniMax
as a product/API reference for NQAI Voice. This is not a mirror of vendor
documentation; it is an engineering summary with links to the official docs.

## Executive Takeaway

For our current product direction, the relevant market shape is **API-first
TTS infrastructure**, not agent orchestration.

ElevenLabs and MiniMax both expose a broad TTS product surface:

- account/API-key authentication;
- voice catalog and voice metadata;
- text-to-speech generation;
- low-latency streaming;
- optional WebSocket input streaming;
- voice cloning / voice design;
- output-format controls;
- model choice and quality/latency tradeoffs;
- usage/billing metadata;
- long-form async generation;
- SDK examples and playback recipes.

That breadth explains why their documentation is large: the TTS product is not
just "POST text, get wav". It is a developer platform with voice lifecycle,
streaming semantics, latency modes, cloning governance, usage attribution,
audio-format compatibility, and client integration patterns.

For NQAI, the closest near-term target is:

1. Keep `POST /v1/tts/stream` as our primary low-latency HTTP streaming TTS.
2. Add ElevenLabs-like aliases later for developer familiarity:
   `POST /v1/text-to-speech/{voice_id}` and
   `POST /v1/text-to-speech/{voice_id}/stream`.
3. Keep async jobs for long text / durable artifacts, closer to MiniMax async.
4. Build voice clone/enroll workflow as a first-class API surface before any
   voice-agent orchestration.
5. Treat WebSocket as optional input-streaming for partial text, not WebRTC.

## Source Index

ElevenLabs official docs:

- API reference intro: https://elevenlabs.io/docs/api-reference/
- Text to Speech capability: https://elevenlabs.io/docs/capabilities/text-to-speech
- Create speech: https://elevenlabs.io/docs/api-reference/text-to-speech
- Stream speech: https://elevenlabs.io/docs/api-reference/text-to-speech/stream
- Streaming guide: https://elevenlabs.io/docs/api-reference/streaming
- WebSocket TTS: https://elevenlabs.io/docs/api-reference/websocket
- WebSocket guide: https://elevenlabs.io/docs/eleven-api/websockets
- List voices v2: https://elevenlabs.io/docs/api-reference/voices/get-all
- Get voice: https://elevenlabs.io/docs/api-reference/voices/get
- Create IVC voice: https://elevenlabs.io/docs/api-reference/add-voice
- Voices overview: https://elevenlabs.io/docs/overview/capabilities/voices

MiniMax official docs:

- API overview: https://platform.minimax.io/docs/api-reference/api-overview
- HTTP T2A: https://platform.minimax.io/docs/api-reference/speech-t2a-http
- WebSocket T2A: https://platform.minimax.io/docs/api-reference/speech-t2a-websocket
- Async long TTS guide: https://platform.minimax.io/docs/guides/speech-t2a-async
- Upload clone audio: https://platform.minimax.io/docs/api-reference/voice-cloning-uploadcloneaudio
- Upload prompt audio: https://platform.minimax.io/docs/api-reference/voice-cloning-uploadprompt
- Voice clone: https://platform.minimax.io/docs/api-reference/voice-cloning-clone
- Voice cloning overview: https://platform.minimax.io/document/Voice_Cloning_api_intro
- Voice design overview: https://platform.minimax.io/document/voice_design_api_intro
- Voice management delete: https://platform.minimax.io/docs/api-reference/voice-management-delete

## ElevenLabs TTS API Surface

### Product Shape

ElevenLabs positions TTS as a multi-model, multi-voice API. The public docs
separate:

- Text to Speech;
- Streaming TTS over HTTP chunked transfer;
- input streaming over WebSocket;
- voice catalog / voice library;
- instant/professional voice cloning;
- generated voices / voice design;
- SDK usage and response metadata.

The important design signal: ElevenLabs does **not** require a voice-agent
surface for TTS. Voice agents are separate. TTS itself is exposed as HTTP and
WebSocket endpoints.

### Authentication and Metadata

Authentication commonly uses `xi-api-key`.

The API reference also documents raw response header access for generation
metadata, including character cost and request ID. For us, the comparable
surface should be:

- `request-id`;
- `x-nqai-character-count`;
- `x-nqai-usage-characters`;
- `x-neurovoice-id`;
- `x-nqai-sample-rate`;
- `x-nqai-duration-seconds`;
- `x-nqai-rtf` when known;
- idempotency key / request id.

### Core Non-Streaming TTS

Official shape:

```text
POST /v1/text-to-speech/{voice_id}
```

Important request fields:

- `text` - required;
- `model_id` - optional, defaults in docs to `eleven_multilingual_v2`;
- `language_code` - optional ISO language hint;
- `voice_settings` - per-request override;
- `pronunciation_dictionary_locators` - optional dictionaries;
- `seed` - best-effort determinism;
- contextual fields such as previous/next text are part of the broader TTS
  surface and should be checked before exact compatibility work.

Important query/header concepts:

- `output_format` in query string;
- `enable_logging=false` for zero-retention style enterprise flows;
- API key in header.

Response: audio bytes in the requested format.

NQAI implication: our current `POST /v1/tts` is a sync compatibility endpoint,
but an ElevenLabs-compatible product API should eventually expose a
path-voice-id version:

```text
POST /v1/text-to-speech/{voice_id}
```

That endpoint can internally map to our queue-proxied path.

### HTTP Streaming TTS

Official shape:

```text
POST /v1/text-to-speech/{voice_id}/stream
```

Behavior:

- returns audio bytes incrementally using HTTP chunked transfer;
- focuses on full-text request streaming output;
- supports `output_format`;
- supports the same core TTS request controls as create speech.

ElevenLabs docs distinguish this from WebSocket input streaming. For a normal
request where the full text is already known, HTTP streaming is the simpler
and often preferred developer surface.

NQAI implication: this maps directly to our existing:

```text
POST /v1/tts/stream
```

Recommended future alias:

```text
POST /v1/text-to-speech/{voice_id}/stream
```

with body:

```json
{
  "text": "Merhaba.",
  "model_id": "nqai-voxcpm2-turkish",
  "voice_settings": {
    "stability": 0.5,
    "similarity_boost": 0.8,
    "speed": 1.0
  },
  "audio_format": "pcm16"
}
```

### WebSocket TTS Input Streaming

Official shape:

```text
GET wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input
```

Purpose:

- send partial text over a long-lived connection;
- receive audio chunks as the model has enough text to generate;
- optionally receive alignment timing;
- useful when text itself is being generated incrementally.

Important query/options:

- `model_id`;
- `language_code`;
- `output_format`;
- `inactivity_timeout`;
- `sync_alignment`;
- `auto_mode`;
- text normalization mode;
- `seed`.

Message pattern:

- initial message carries a space/text seed, voice settings, and auth;
- subsequent messages append text;
- an empty text message closes/finalizes the generation;
- received messages include base64 audio and alignment metadata.

ElevenLabs explicitly notes WebSocket is not a universal replacement for HTTP:
when full input text is already available, HTTP can be simpler and sometimes
lower-latency due to less buffering/complexity.

NQAI implication: WebSocket should be a **later compatibility/control surface**
for partial text. It should not pull us into WebRTC or agent orchestration.

### Models and Latency/Quality Modes

ElevenLabs documents model tradeoffs:

- Flash/Turbo style models for low latency;
- Multilingual models for higher quality/stability;
- expressive newer models for richer performance, sometimes with different
  endpoint support.

Docs mention Flash v2.5 as an ultra-low-latency real-time option and
Multilingual v2 as a quality/stability option. The exact numbers and support
matrix should be treated as vendor-specific and rechecked before comparison.

NQAI implication: we should expose our own model/quality modes even if they
initially map to one backend:

```text
nqai-tts-turbo-tr
nqai-tts-hd-tr
nqai-tts-character-tr
```

Even if v0 maps all of them to VoxCPM2 with different runtime parameters, the
API surface should anticipate quality/latency choices.

### Voice Catalog and Voice Lifecycle

Key official endpoints:

```text
GET  /v2/voices
GET  /v1/voices/{voice_id}
POST /v1/voices/add
POST /v1/voices/add/{public_user_id}/{voice_id}
```

Important voice metadata in docs:

- `voice_id`;
- `name`;
- samples;
- category such as premade/cloned/generated/professional;
- fine-tuning state;
- labels;
- description;
- preview URL;
- settings: stability, speaker boost, similarity, style, speed;
- sharing/library metadata;
- verified languages / available models.

Create IVC voice:

- multipart form;
- `name`;
- `files[]`;
- `remove_background_noise`;
- optional description;
- optional labels;
- response includes `voice_id` and `requires_verification`.

NQAI implication: voice enrollment should not remain a hidden admin-only
operation. A TTS API service needs public/developer voice lifecycle endpoints:

```text
GET    /v1/voices
GET    /v1/voices/{voice_id}
POST   /v1/voices/add
PATCH  /v1/voices/{voice_id}
DELETE /v1/voices/{voice_id}
```

For our internal products, voice visibility/tenant access stays important, but
the API mental model should be account -> API key -> voice_id.

## MiniMax TTS API Surface

### Product Shape

MiniMax exposes a slightly different but very instructive split:

- synchronous HTTP T2A;
- WebSocket T2A;
- async long-text T2A;
- voice cloning;
- prompt audio upload;
- voice design;
- voice management.

MiniMax explicitly supports Turkish in its speech model language list. The
docs describe the T2A interface as stateless and suitable for short text,
voice chat, and online social interactions, while async is for books/long text.

### Authentication

MiniMax uses Bearer auth:

```text
Authorization: Bearer <token>
```

NQAI currently uses:

```text
Authorization: Bearer <api_key>
```

That is already aligned with MiniMax/OpenAI-style developer expectations.

### HTTP T2A

Official shape:

```text
POST https://api.minimax.io/v1/t2a_v2
```

Docs also mention an alternative endpoint aimed at reduced time to first audio:

```text
POST https://api-uw.minimax.io/v1/t2a_v2
```

Important request fields:

- `model` - e.g. `speech-2.8-hd`, `speech-2.8-turbo`;
- `text`;
- `stream` boolean;
- `language_boost`, including `Turkish` and `auto`;
- `voice_setting`:
  - `voice_id`;
  - `speed`;
  - `vol`;
  - `pitch`;
- `audio_setting`:
  - `sample_rate`;
  - `bitrate`;
  - `format`;
  - `channel`;
- `pronunciation_dict`;
- `timbre_weights` legacy field;
- `voice_modify`:
  - pitch/intensity/timbre/effects;
- `subtitle_enable`;
- `subtitle_type`: sentence, word, word_streaming;
- `output_format`: `url` or `hex` in non-streaming.

Output:

- non-streaming can return hex-encoded audio or URL;
- response includes `extra_info` such as audio length, sample rate, size,
  bitrate, word count, invisible-character ratio, usage characters, format,
  channel;
- `trace_id` and `base_resp`.

Streaming constraints from docs:

- streaming output supported;
- HTTP streaming supports `mp3`;
- `wav` is non-streaming only;
- in streaming, output is effectively chunked/hex-style rather than URL.

NQAI implication:

- We should expose `language_boost`/`language` in a Turkish-friendly way.
- We should return `extra_info`-like metadata in JSON status endpoints and
  headers for streaming.
- We should not pretend WAV is optimal for low latency; `pcm16` is easiest for
  internal measurement, but product playback needs MP3/Opus/WebM soon.

### WebSocket T2A

Official shape:

```text
WSS /ws/v1/t2a_v2
```

Purpose:

- synchronous T2A over WebSocket;
- stream and play audio in real time;
- useful for long-lived low-latency playback clients.

NQAI implication: similar to ElevenLabs, WebSocket is a useful TTS transport
for partial/control-style integration. It is still not WebRTC. It should be
considered after HTTP streaming is stable and codec support is clear.

### Async Long Text T2A

MiniMax async supports:

- up to 1 million characters per request;
- task creation -> task status -> file download flow;
- direct string input or uploaded text file;
- duration and file-size metadata;
- sentence-level subtitles;
- illegal-character ratio behavior;
- returned audio URL validity window.

Official shape, per docs:

```text
POST /v1/t2a_async_v2
GET  task status by task_id
download by file_id through File API
```

NQAI implication: our `/v1/tts/jobs` path is directionally correct. It should
be positioned as:

- long text;
- durable artifact;
- retryable generation;
- presigned URL result;
- status polling;
- subtitles/timestamps later.

### Voice Cloning

MiniMax voice clone flow:

1. Upload source audio:

```text
POST /v1/files/upload
purpose=voice_clone
```

2. Optional prompt audio:

```text
POST /v1/files/upload
purpose=prompt_audio
```

3. Clone:

```text
POST /v1/voice_clone
```

Important clone constraints:

- source formats: `mp3`, `m4a`, `wav`;
- source duration: 10 seconds to 5 minutes;
- source file size: <= 20 MB;
- prompt audio can improve similarity/stability;
- custom `voice_id` length/rules;
- optional preview text up to 1000 chars;
- optional noise reduction and volume normalization;
- supports language boost;
- cloned voice may be temporary unless used in T2A within 168 hours.

NQAI implication: the first serious product addition after stable TTS should be
a developer-facing voice clone/enroll workflow:

```text
POST /v1/files/upload               # purpose=voice_clone | prompt_audio
POST /v1/voices/clone               # file_id + optional prompt + requested voice_id
GET  /v1/voices/{voice_id}
POST /v1/text-to-speech/{voice_id}
```

This is more aligned with our commercial pressure than agent orchestration.

### Voice Design and Voice Management

MiniMax also has:

- voice design from text description;
- generated `voice_id` usable by T2A and async T2A;
- generated/designed voices can be deleted through voice management endpoints;
- temporary voice persistence rules similar to cloned voice behavior.

NQAI implication: voice design is useful later, but our near-term differentiator
is not "random generated voices"; it is owned Turkish character voices. Treat
voice design as lower priority than clone/enroll/governance.

## Side-by-Side Product Surface

| Surface | ElevenLabs | MiniMax | NQAI current | NQAI target |
|---|---|---|---|---|
| Auth | `xi-api-key` | `Authorization: Bearer` | Bearer API key | Keep Bearer; maybe accept `xi-api-key` alias |
| Sync TTS | `/v1/text-to-speech/{voice_id}` | `/v1/t2a_v2` | `/v1/tts` | Add vendor-like alias |
| HTTP stream | `/v1/text-to-speech/{voice_id}/stream` | `/v1/t2a_v2` with `stream=true` | `/v1/tts/stream` | Keep + add alias |
| WebSocket TTS | `/stream-input` | `/ws/v1/t2a_v2` | Not primary | Later, for partial text |
| Long async | history/longer flows in product; direct TTS is sync/stream | `/v1/t2a_async_v2` | `/v1/tts/jobs` | Keep and harden |
| Voice list | `/v2/voices` | Get Voice API / voice list | `/v1/voices` | Expand metadata/settings |
| Voice clone | `/v1/voices/add` multipart | upload -> `/v1/voice_clone` | enroll exists | Make clone API first-class |
| Voice settings | stability, similarity, style, speed, boost | speed, vol, pitch, timbre, effects | limited | Add normalized settings schema |
| Pronunciation | dictionaries | pronunciation_dict + inline syntax | Turkish frontend | Add user dictionaries |
| Output formats | many `output_format` variants | mp3/wav/flac/pcm constraints | wav/pcm mainly | Add mp3/opus/webm |
| Usage metadata | response headers | `extra_info`, trace_id | usage rows + headers | Standardize headers + status JSON |
| Docs/SDK | broad examples | broad examples | internal docs | Public API docs + examples |

## What We Should Build Before Agent Orchestration

### P0 - API Product Cleanup

- Keep `/v1/tts/stream` as canonical NQAI path.
- Add compatibility aliases:
  - `POST /v1/text-to-speech/{voice_id}`
  - `POST /v1/text-to-speech/{voice_id}/stream`
- Normalize request body:
  - `text`;
  - `model_id`;
  - `language_code`;
  - `voice_settings`;
  - `audio_format`;
  - `seed`;
  - `previous_text` / `next_text` if supported;
  - `pronunciation_dictionary_locators` later.
- Add response headers:
  - request id;
  - character count;
  - sample rate;
  - duration;
  - voice id;
  - model id;
  - RTF/latency where applicable.

### P1 - Voice Clone / Enroll API

- Developer-facing multipart upload.
- Voice sample validation:
  - duration;
  - mime/type;
  - sample rate/channel normalization;
  - file size;
  - noise reduction option;
  - consent metadata.
- Create clone/enroll:
  - `name`;
  - requested `voice_id`;
  - labels;
  - description;
  - language;
  - visibility.
- Return:
  - `voice_id`;
  - status;
  - preview URL or demo generation;
  - verification/consent flags.

### P2 - Output Codec Layer

Market APIs are not WAV-only. For web/mobile/product use:

- `pcm16` for low-level clients and measurement;
- `mp3` for compatibility;
- `opus`/`webm` for streaming bandwidth;
- `wav` for download/debug.

This should be a codec adapter layer after worker PCM, not model-specific code.

### P3 - WebSocket Input Streaming

Add after HTTP streaming is stable:

```text
GET /v1/text-to-speech/{voice_id}/stream-input
```

Use for:

- partial text from LLMs;
- alignment metadata;
- interactive playback clients;
- future NIVA/agent bridges.

Do not make this WebRTC.

### P4 - Public Docs and SDK Examples

We need wide docs because vendors win developer trust through examples.

Minimum docs set:

- authentication;
- quickstart;
- sync TTS;
- HTTP streaming TTS;
- async jobs;
- voice clone;
- voice management;
- output formats;
- error codes;
- rate limits/backpressure;
- usage/cost headers;
- Node/Python curl examples;
- browser playback guide;
- security/consent policy.

## Why Vendor Docs Are So Broad

Large TTS API docs are not bloat. They cover real platform concerns:

- **Developer onboarding:** quickstarts, SDKs, curl, raw HTTP.
- **Transport choice:** sync, HTTP stream, WebSocket, async long text.
- **Voice lifecycle:** list, clone, share, delete, settings, labels.
- **Model choice:** low latency vs quality vs expressive models.
- **Audio compatibility:** MP3/WAV/PCM/Opus/u-law/Twilio/browser playback.
- **Latency controls:** streaming modes, endpoints, buffering behavior.
- **Billing and observability:** request IDs, character count, trace IDs.
- **Safety:** verification, consent, retention/logging, moderation.
- **Product integration:** examples for web/mobile/server/S3.

For NQAI, this means our next strong move is not another infra phase. It is a
clear, polished API product surface that NEEKO, NIVA, NeuroCourse, and external
customers can all consume.

## Recommended NQAI API Compatibility Direction

Keep current native endpoints:

```text
POST /v1/tts/stream
POST /v1/tts/jobs
GET  /v1/tts/jobs/{request_id}
GET  /v1/voices
POST /v1/voices
```

Add vendor-familiar aliases:

```text
POST /v1/text-to-speech/{voice_id}
POST /v1/text-to-speech/{voice_id}/stream
GET  /v1/text-to-speech/{voice_id}/stream-input   # later websocket
POST /v1/voices/add
POST /v1/voices/clone
POST /v1/files/upload
```

This lets our internal apps migrate cheaply from ElevenLabs/MiniMax mental
models while keeping our own gateway/worker architecture intact.

## Non-Goals For Now

- Full duplex voice-agent orchestration.
- STT.
- LLM routing.
- WebRTC media rooms.
- LiveKit.
- Multi-party calls.

Those belong to NIVA/agent product surfaces later. The TTS service should first
become a clean, reliable, developer-friendly voice API.

