# NeuroVoice

Multilingual TTS API platform. VoxCPM2 (Apache 2.0) base with per-language
and per-character LoRA adapters, voice cloning, and chunked streaming.

Status: **v0.x** — surface stabilising, not yet semver. Production use
behind your own staging gate.

## Positioning

NeuroVoice is built for the gap that ElevenLabs, MiniMax, Deepgram TTS,
and PlayHT leave open: high-fidelity TTS in **underserved languages**.
The base model speaks 30 languages; the LoRA + text-frontend stack adds
language-specific normalisation, pronunciation, and prosody. Turkish is
the first LoRA line. Polish, Indonesian, Persian, and Vietnamese are
next.

API parity with established vendors stays intentional — you swap a base
URL and keep your client code.

## Quickstart

```bash
# 1. Install
pip install -e ".[dev]"

# 2. Configure (copy and edit secrets)
cp .env.example .env

# 3. Bring up postgres + redis + gateway + worker
docker compose -f docker-compose.dev.yaml up

# 4. Health check
curl http://localhost:8000/health
```

Synthesise:

```bash
curl -X POST http://localhost:8000/v1/tts \
  -H "Authorization: Bearer $NEUROVOICE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello, world.", "voice_id": "<your-voice>"}' \
  --output out.wav
```

Streaming and async job endpoints are at `/v1/tts/stream` and
`/v1/tts/jobs` respectively. The full surface is published via OpenAPI at
`/docs` when the server is running.

## Architecture

Gateway / worker split, Redis stream queue, object storage (R2 /
S3-compatible) for all artefacts, Postgres for catalog + audit. Inference
never runs in the gateway process. See
[`CLAUDE.md`](CLAUDE.md) for the ten architecture anchor points and
the deferred ADR list.

## License

Proprietary. The base model (VoxCPM2) is Apache 2.0; LoRA adapters and
the platform code are not.

## Roadmap

See [`CLAUDE.md`](CLAUDE.md#bilinçli-ertelenmiş-kararlar-adrleri-sırada)
for the pending design decisions (multi-language frontend, voice
manifest schema v2, billing, multi-region deployment).
