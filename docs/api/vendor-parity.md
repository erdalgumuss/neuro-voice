# Vendor-parity scope

Status: in force from 2026-05-28. See [ADR-9](../decisions/2026-05-28-public-api-spec.md).

The native NeuroVoice API ([openapi-policy.md](openapi-policy.md)) is the canonical surface. To ease migration from incumbent TTS providers, a **subset** of vendor URL+method+body shapes is supported as a compatibility layer. This document defines the scope and the rules.

## Vendors covered

| Vendor | Status | Spec source |
| --- | --- | --- |
| ElevenLabs | Supported (v0) | [vendors/elevenlabs/](../../vendors/elevenlabs/) |
| MiniMax | Out of scope for v0 | — |
| OpenAI TTS | Out of scope for v0 | — |
| PlayHT | Out of scope for v0 | — |

## ElevenLabs parity surface

| ElevenLabs route | Our route | Status |
| --- | --- | --- |
| `POST /v1/voices/add` | `POST /v1/voices/add` | Supported. Body-compatible alias for `POST /v1/voices`. |
| `POST /v1/text-to-speech/{voice_id}` | same path | Supported. Body-compatible synchronous synthesis. |
| `POST /v1/text-to-speech/{voice_id}/stream` | same path | Supported. Body-compatible chunked streaming. |
| `WS /v1/text-to-speech/{voice_id}/stream-input` | same path | Supported. Body-compatible input-streaming WebSocket. |
| `GET /v1/voices`, `GET /v1/voices/{voice_id}` | same | Native shape; field naming preserves ElevenLabs convention where it overlaps. |
| `POST /v1/text-to-speech/{voice_id}/with-timestamps` | — | Not implemented. |
| `POST /v1/speech-to-speech/*` | — | Not implemented. |
| `POST /v1/voice-generation/*` | — | Not implemented. |
| `POST /v1/dubbing/*` | — | Not implemented. |
| `POST /v1/projects/*` (audiobook flows) | — | Not implemented. |

## Compatibility guarantees

1. **SDK drop-in for the parity surface.** The official ElevenLabs Python and TypeScript SDKs target the supported routes successfully. This is enforced by contract tests against the vendored ElevenLabs OpenAPI spec, not by hand-written documentation.
2. **Auth dual-name.** Both `xi-api-key` (ElevenLabs convention) and `X-NV-API-Key` (native, see ADR-1) authenticate the same API key.
3. **Body parity, not behavioral parity.** Voice IDs, voice metadata, and synthesis output formats are returned in shapes the vendor SDK accepts. The underlying voice catalog, pricing, rendering quality, and latency curve are NeuroVoice — not cloned.

## Non-goals

- **Behavioral cloning.** We do not match ElevenLabs' rendering quality, latency curve, prosody choices, or voice IDs. Parity is structural.
- **Native extensions on parity routes.** Parity request models have `extra="forbid"`. `lexicon_id`, `adapter_id`, `language_pack`, `eval_pin`, and other VoxCPM2-specific fields are rejected with 422. An integrator who needs these must use the native route.
- **Tracking the vendor roadmap.** When ElevenLabs ships a new field or endpoint, we evaluate inclusion case-by-case. There is no automatic catch-up obligation.

## Drift control

The vendor's published OpenAPI spec is pinned under [vendors/elevenlabs/openapi.yaml](../../vendors/elevenlabs/). Contract tests (`tests/contract/test_elevenlabs_parity.py`, owned by the test team) replay vendor-schema requests against our server. A schema-incompatible response fails CI.

Refresh cadence: quarterly, plus out-of-cycle when the vendor publishes a non-breaking addition we want to support. Breaking vendor changes are handled per [versioning.md](versioning.md) "Parity routes" section.

## When to break parity

- A parity guarantee blocks a native feature — file an ADR; parity may be narrowed.
- A vendor breaking-change upstream — refresh the pin, run the contract test, decide between supporting the new shape (minor or major bump depending on impact) or sunsetting the affected route.
