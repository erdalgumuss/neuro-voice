# Native API spec policy

Status: in force from 2026-05-28. See [ADR-9](../decisions/2026-05-28-public-api-spec.md).

## Source of truth

The FastAPI app at [src/server/main.py](../../src/server/main.py) emits `/openapi.json` automatically from its Pydantic models and route decorators. **That output is the contract.** There is no hand-written OpenAPI YAML for the native surface.

Native routes governed by this policy:

- `GET /health`
- `GET /v1/models`
- `GET /v1/voices`, `GET /v1/voices/{voice_id}`
- `POST /v1/voices`, `PATCH /v1/voices/{voice_id}`, `DELETE /v1/voices/{voice_id}`
- `POST /v1/tts/jobs`, `GET /v1/tts/jobs/{job_id}`

Routes **not** covered here:

- ElevenLabs parity surface — see [vendor-parity.md](vendor-parity.md).
- Deprecated sync `POST /v1/tts`, `POST /v1/tts/stream` — see [versioning.md](versioning.md).
- `/admin/*` — internal surface, excluded from the published spec.

## Drift control

CI publishes `/openapi.json` to `tests/snapshots/openapi.json`. Any PR that changes the emitted schema must update the snapshot in the same commit; reviewers read the diff. A PR that drifts the snapshot without updating it fails CI.

Snapshot format: pretty-printed JSON with stable key ordering. The snapshot test is owned by the test team (Codex authoring).

## Error model

All native routes return a uniform error envelope on 4xx/5xx:

```json
{
  "error": "voice_not_found",
  "detail": "Voice 'vx_…' does not exist or is not visible to this tenant."
}
```

`ErrorResponse` is a single Pydantic model (`src/server/schemas.py`). The app-level `responses=` dict on the FastAPI app references it for the standard status codes: 401, 403, 404, 409, 422, 429, 500, 503. Per-route `responses=` may override the **example**, never the schema.

`error` is a stable kebab-case or snake_case identifier (`voice_not_found`, `quota_exceeded`, `idempotency_conflict`, …); `detail` is a human-readable message and may be `null`. Adding a new identifier is a minor version bump; renaming or removing one is a major version bump (see [versioning.md](versioning.md)).

**Planned enrichment** (separate ADR, not bound here): a `request_id` field for support correlation and a structured `code → message` split. Today's flat `{error, detail}` is the v0 contract.

## Tag taxonomy

| Tag | Routes |
| --- | --- |
| `meta` | `/health`, `/v1/models` |
| `voices` | `/v1/voices` CRUD |
| `synthesis` | `/v1/tts/jobs` async path (and deprecated sync `/v1/tts`, `/v1/tts/stream`) |
| `synthesis:parity` | ElevenLabs-compatible synthesis routes (see vendor-parity.md) |

`/admin/*` is in the same FastAPI app but excluded from the published spec via `include_in_schema=False` on each admin route. The public spec advertises only what an external integrator can rely on.

## Native vendor extensions

Native routes accept VoxCPM2-specific fields that have no counterpart in vendor SDKs:

- `lexicon_id` — override the voice's pinned pronunciation dictionary.
- `adapter_id` — override the voice's pinned LoRA adapter (e.g. for A/B comparison).
- `language_pack` — override the language pack used for text normalization.
- `seed`, `cfg`, `timesteps` — engine reproducibility controls (see ADR-7).
- `eval_pin` — request a specific evaluation profile in the response.

These fields exist on **native** routes only. They are not honored on parity routes; the request models there have `extra="forbid"`.

## Publishing

Static documentation site target: **[Scalar](https://github.com/scalar/scalar)**. Selected over ReDoc for:

- **Aesthetic fit** — Stripe/Linear/Anthropic-grade typography and layout, matching NeuroVoice positioning.
- **Interactive playground** — built-in "Try it out" against the live `/openapi.json`; ReDoc is reference-only.
- **OpenAPI 3.1** — first-class support; we ship on FastAPI 0.115+ which emits 3.1.
- **Low switch cost** — same `/openapi.json` input. If Scalar's maintenance trajectory changes, ReDoc remains a one-config-line fallback.

Distribution: standalone bundle generated in CI from the snapshot, served as static HTML from `developers.neurovoice.<tld>` (domain TBD with brand). No runtime dependency on the API server. Internal preview at `/docs` (FastAPI default Swagger UI) stays available for local development.

## Backwards-compatible additions

- **Adding** an optional request field or response field is a minor version bump and does not require coordination.
- Anything else — see [versioning.md](versioning.md).
