"""End-to-end API smoke — registry CRUD + auth, model stubbed.

This test runs without a GPU and without the real VoxCPM2 install by
injecting a fake model into `sys.modules['voxcpm']`. The FastAPI
dependency graph and the streaming/WAV code paths still get exercised
on CI-class machines.
"""

from __future__ import annotations

import io
import sys
import wave
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

# Stub voxcpm before importing the server, so loading does not pull torch.
_fake_voxcpm = type(sys)("voxcpm")


class _StubInner:
    sample_rate = 48000


class _StubModel:
    tts_model = _StubInner()

    def generate(
        self,
        text,
        *,
        reference_wav_path,
        cfg_value=2.0,
        inference_timesteps=10,
        normalize=False,
        denoise=False,
        retry_badcase=True,
        **_kwargs,
    ):
        sr = self.tts_model.sample_rate
        duration = 0.5 + 0.05 * len(text)
        return np.zeros(int(duration * sr), dtype=np.float32)


class _StubFactory:
    @staticmethod
    def from_pretrained(model_id, load_denoiser=False):
        return _StubModel()


_fake_voxcpm.VoxCPM = _StubFactory
sys.modules["voxcpm"] = _fake_voxcpm


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    voices_dir = tmp_path / "voices"
    ref_dir = tmp_path / "ref"
    voices_dir.mkdir()
    ref_dir.mkdir()

    sr = 16000  # VoxCPM2 reference audio rate
    seed = np.zeros(sr, dtype=np.float32)
    sf.write(ref_dir / "seed.wav", seed, sr)

    monkeypatch.setenv("NQAI_VOICES_DIR", str(voices_dir))
    monkeypatch.setenv("NQAI_REFERENCE_DIR", str(ref_dir))
    monkeypatch.setenv("NQAI_API_KEYS", "test-key-1,test-key-2")
    monkeypatch.setenv("NQAI_REQUIRE_AUTH", "true")

    # Force reload to pick up env
    for mod_name in list(sys.modules):
        if mod_name.startswith(("server", "registry", "frontend")):
            del sys.modules[mod_name]

    from fastapi.testclient import TestClient
    from server.main import app

    with TestClient(app) as c:
        yield c


HEADERS = {"Authorization": "Bearer test-key-1"}


def _make_wav_bytes(duration_s: float = 2.0, sr: int = 16000) -> bytes:
    audio = (np.random.randn(int(duration_s * sr)) * 0.1).astype(np.float32)
    buf = io.BytesIO()
    sf.write(buf, audio, sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in {"ok", "warming"}
    assert body["version"] == "0.1.0"


def test_auth_rejects_missing_key(client):
    r = client.get("/v1/voices")
    assert r.status_code == 401


def test_auth_rejects_bad_key(client):
    r = client.get("/v1/voices", headers={"Authorization": "Bearer bogus"})
    assert r.status_code == 401


def test_list_voices_empty(client):
    r = client.get("/v1/voices", headers=HEADERS)
    assert r.status_code == 200
    assert r.json() == {"voices": [], "count": 0}


def test_enroll_and_list_and_delete(client):
    wav = _make_wav_bytes()
    r = client.post(
        "/v1/voices",
        headers=HEADERS,
        data={
            "voice_id": "alice-warm-01",
            "display_name": "Alice (warm)",
            "language": "tr",
            "gender": "female",
            "style_tags": "warm,clear",
        },
        files={"reference_audio": ("alice.wav", wav, "audio/wav")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["voice"]["voice_id"] == "alice-warm-01"
    assert body["voice"]["style_tags"] == ["warm", "clear"]

    listing = client.get("/v1/voices", headers=HEADERS).json()
    assert listing["count"] == 1

    r2 = client.delete("/v1/voices/alice-warm-01", headers=HEADERS)
    assert r2.status_code == 200

    listing = client.get("/v1/voices", headers=HEADERS).json()
    assert listing["count"] == 0


def test_enroll_rejects_bad_id(client):
    wav = _make_wav_bytes()
    r = client.post(
        "/v1/voices",
        headers=HEADERS,
        data={"voice_id": "Bad Id!", "display_name": "x"},
        files={"reference_audio": ("x.wav", wav, "audio/wav")},
    )
    assert r.status_code == 400


def test_enroll_rejects_duplicate(client):
    wav = _make_wav_bytes()
    fields = {"voice_id": "dup-01", "display_name": "Dup"}
    files = lambda: {"reference_audio": ("d.wav", _make_wav_bytes(), "audio/wav")}
    r1 = client.post("/v1/voices", headers=HEADERS, data=fields, files=files())
    assert r1.status_code == 200
    r2 = client.post("/v1/voices", headers=HEADERS, data=fields, files=files())
    assert r2.status_code == 409


def test_tts_with_stub_engine(client):
    wav = _make_wav_bytes()
    client.post(
        "/v1/voices",
        headers=HEADERS,
        data={"voice_id": "bob-01", "display_name": "Bob"},
        files={"reference_audio": ("b.wav", wav, "audio/wav")},
    )
    r = client.post(
        "/v1/tts",
        headers=HEADERS,
        json={"text": "Merhaba dünya. Bu bir test cümlesidir.", "voice_id": "bob-01"},
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("audio/wav")
    assert int(r.headers["X-NQAI-Sentences"]) >= 1
    assert int(r.headers["X-NQAI-Sample-Rate"]) == 48000
    # Parse it as WAV to confirm the bytes are valid
    with wave.open(io.BytesIO(r.content), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 48000
        assert w.getnframes() > 0


def test_tts_voice_404(client):
    r = client.post(
        "/v1/tts",
        headers=HEADERS,
        json={"text": "merhaba", "voice_id": "nobody-here"},
    )
    assert r.status_code == 404


def test_loads_manifest_with_unquoted_iso_datetime(client, tmp_path: Path):
    """YAML auto-parses ISO 8601 timestamps as `datetime`; the registry must
    still hand the API layer a plain string, or `GET /v1/voices/{id}` 500s."""
    import yaml
    from registry.catalog import VoiceRegistry

    voices_dir = tmp_path / "vdir"
    ref_dir = tmp_path / "rdir"
    voices_dir.mkdir()
    ref_dir.mkdir()
    (ref_dir / "ref.wav").write_bytes(_make_wav_bytes())
    (voices_dir / "yaml-ts.yaml").write_text(
        yaml.safe_dump({
            "voice_id": "yaml-ts",
            "display_name": "YAML timestamp test",
            "language": "tr",
            "gender": "neutral",
            "style_tags": ["a", "b"],
            "reference_audio": "ref.wav",
            "reference_seconds": 1.0,
            "source": "test",
            "license": "internal-bridge",
            "created_at": "2026-05-19T20:17:18+00:00",
            "created_by": "system",
        }),
        encoding="utf-8",
    )
    reg = VoiceRegistry(voices_dir=voices_dir, reference_dir=ref_dir)
    v = reg.get("yaml-ts")
    assert isinstance(v.created_at, str)
    # Round-trip the public dict (this is what the API endpoint does)
    public = v.to_public()
    assert isinstance(public["created_at"], str)


def test_tts_stream(client):
    wav = _make_wav_bytes()
    client.post(
        "/v1/voices",
        headers=HEADERS,
        data={"voice_id": "carol-01", "display_name": "Carol"},
        files={"reference_audio": ("c.wav", wav, "audio/wav")},
    )
    with client.stream(
        "POST",
        "/v1/tts/stream",
        headers=HEADERS,
        json={"text": "Birinci cümle. İkinci cümle burada.", "voice_id": "carol-01"},
    ) as r:
        chunks = b"".join(r.iter_bytes())
    assert chunks.startswith(b"RIFF")
    assert b"WAVE" in chunks[:20]
    assert len(chunks) > 44  # header + at least some PCM
