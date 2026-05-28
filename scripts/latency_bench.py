"""Real-engine latency benchmark for NeuroVoice TTS.

Drives N concurrent `/v1/tts/stream` calls against a running gateway,
captures client-observed time-to-first-byte + total wall time per
request, and (optionally) joins to ``usage_records`` to pull the
server-side waterfall (queue_wait_ms, worker_pickup_ms,
reference_resolve_ms, first_pcm_ms, first_audio_ms,
gateway_first_byte_ms, inference_ms, rtf).

The point of this script is NOT another smoke test of audio quality —
that's `scripts/smoke_test.py`. The point is a single source of truth
for "what does latency actually look like with real VoxCPM2 on real
hardware". You run this once after deploying to a new GPU shape and
the JSON/Markdown it spits out becomes the artifact you cite in
decisions like "is sticky routing worth doing?", "is our SLO budget
realistic?", and "did the L4 → L40S upgrade move the needle?".

Usage:

    python scripts/latency_bench.py \\
        --base-url http://localhost:8000 \\
        --api-key "nv_dev_..." \\
        --voice tr-warm-storyteller-v0 \\
        --requests 30 \\
        --concurrency 4 \\
        --hardware-label "L4-runpod" \\
        --out experiments/2026-XX-XX-latency-bench

Add ``--db-url postgresql+asyncpg://...`` (or set ``NEUROVOICE_DATABASE_URL``)
to also pull the worker-side waterfall from Postgres after the run.
Without it the report contains client-observed numbers only.

The report writes two artifacts into ``--out``:
* ``raw.json``     — per-request samples (one row per call) + percentiles
* ``report.md``    — human-readable summary table (paste into the
                      B.1.5 closure doc / decision log)

See docs/runbooks/latency-bench.md for full operator notes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx

# Mini eval set — same sentences as scripts/smoke_test.py so latency
# numbers and quality numbers are comparable. Repeat the set to fill
# the requested sample count.
_TEST_SENTENCES = [
    "Hadi birlikte sıradaki hayvanı bulalım!",
    "Şimdi gözlerini kapatıp derin bir nefes alalım.",
    "Yedi artı üç kaç eder, biliyor musun?",
    "Kızgın olman çok normal, önce sakinleşelim.",
    "Sence bugün ne renk bir gökyüzü gördük?",
    "Vay canına, çok güzel bir resim çizmişsin!",
    "Yirmi üç Nisan'da ne kutlarız?",
    "Doktor Ayşe, bunu bir büyüğünle birlikte yapalım dedi.",
    "Annenin iPhone'unu yerine bırakır mısın?",
    "Bir varmış, bir yokmuş, çok uzak bir ülkede küçük bir tavşan yaşarmış.",
]


@dataclass
class CallSample:
    request_id: str
    voice: str
    text_preview: str
    client_first_byte_ms: float          # POST send → first PCM byte
    client_total_ms: float               # POST send → last byte
    audio_bytes: int                     # body size at end of stream
    status_code: int
    ok: bool
    error: str | None = None
    # Waterfall populated post-run via DB join (if --db-url supplied).
    queue_wait_ms: int | None = None
    worker_pickup_ms: int | None = None
    reference_resolve_ms: int | None = None
    first_pcm_ms: int | None = None
    first_audio_ms: int | None = None
    gateway_first_byte_ms: int | None = None
    inference_ms: int | None = None
    rtf: float | None = None


@dataclass
class RunSummary:
    hardware_label: str
    base_url: str
    voice: str
    requests: int
    concurrency: int
    started_at: str
    finished_at: str
    duration_s: float
    success_rate: float
    samples: list[CallSample] = field(default_factory=list)
    percentiles: dict[str, dict[str, float | None]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


_PERCENTILE_LEVELS = (0.5, 0.9, 0.95, 0.99)
_WATERFALL_FIELDS = (
    "client_first_byte_ms",
    "client_total_ms",
    "queue_wait_ms",
    "worker_pickup_ms",
    "reference_resolve_ms",
    "first_pcm_ms",
    "first_audio_ms",
    "gateway_first_byte_ms",
    "inference_ms",
)


def _percentile(values: list[float], q: float) -> float | None:
    """Inclusive percentile (statistics.quantiles is overkill for ≤30 pts)."""
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    s = sorted(values)
    idx = q * (len(s) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(s) - 1)
    frac = idx - lo
    return s[lo] + (s[hi] - s[lo]) * frac


_WAV_HEADER_SIZE = 44  # RIFF (12) + fmt (24) + data (8). Gateway's
# `_yield_wav` emits exactly this layout before any PCM payload.


async def _one_call(
    client: httpx.AsyncClient,
    *,
    voice: str,
    text: str,
    audio_format: str,
) -> CallSample:
    """One /v1/tts/stream POST, measured client-side.

    Codex audit fix (2026-05-25): when audio_format=wav the gateway
    sends the 44-byte RIFF/WAVE header BEFORE any audio. Counting that
    header byte as "first audio" produced pembe latency numbers. We
    now skip the first ``_WAV_HEADER_SIZE`` bytes in WAV mode before
    arming the first-byte timer; in pcm16 mode the first byte IS
    audio. Default is pcm16 to keep measurements honest by construction.
    """
    rid = str(uuid.uuid4())
    payload = {
        "text": text,
        "voice_id": voice,
        "language": "tr",
        "audio_format": audio_format,
    }
    skip_remaining = _WAV_HEADER_SIZE if audio_format == "wav" else 0

    t0 = time.monotonic()
    first_byte_at: float | None = None
    audio_bytes = 0
    try:
        async with client.stream(
            "POST",
            "/v1/tts/stream",
            json=payload,
            headers={"X-Request-Id": rid},
        ) as r:
            status_code = r.status_code
            if status_code >= 400:
                body = await r.aread()
                return CallSample(
                    request_id=rid,
                    voice=voice,
                    text_preview=text[:40],
                    client_first_byte_ms=0.0,
                    client_total_ms=(time.monotonic() - t0) * 1000.0,
                    audio_bytes=0,
                    status_code=status_code,
                    ok=False,
                    error=body.decode("utf-8", errors="replace")[:200],
                )
            async for chunk in r.aiter_bytes():
                if skip_remaining > 0:
                    consumed = min(skip_remaining, len(chunk))
                    skip_remaining -= consumed
                    chunk = chunk[consumed:]
                    if not chunk:
                        continue
                if first_byte_at is None:
                    first_byte_at = time.monotonic()
                audio_bytes += len(chunk)
    except Exception as e:
        return CallSample(
            request_id=rid,
            voice=voice,
            text_preview=text[:40],
            client_first_byte_ms=0.0,
            client_total_ms=(time.monotonic() - t0) * 1000.0,
            audio_bytes=0,
            status_code=0,
            ok=False,
            error=f"{type(e).__name__}: {e}",
        )

    end_at = time.monotonic()
    first_byte_ms = (first_byte_at - t0) * 1000.0 if first_byte_at else 0.0
    total_ms = (end_at - t0) * 1000.0
    return CallSample(
        request_id=rid,
        voice=voice,
        text_preview=text[:40],
        client_first_byte_ms=first_byte_ms,
        client_total_ms=total_ms,
        audio_bytes=audio_bytes,
        status_code=status_code,
        ok=True,
    )


async def _join_usage_rows(
    samples: list[CallSample], db_url: str,
) -> int:
    """Stitch usage_records waterfall columns onto the sample list.
    Returns the number of rows successfully matched."""
    try:
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import create_async_engine

        from db.models import UsageRecord
    except ImportError as e:
        print(
            f"warning: cannot import sqlalchemy / db models ({e}); "
            "skipping DB waterfall join",
            file=sys.stderr,
        )
        return 0

    engine = create_async_engine(db_url, future=True)
    request_uuids = [uuid.UUID(s.request_id) for s in samples if s.ok]
    if not request_uuids:
        await engine.dispose()
        return 0

    matched = 0
    async with engine.connect() as conn:
        rows = (await conn.execute(
            select(
                UsageRecord.request_id,
                UsageRecord.queue_wait_ms,
                UsageRecord.worker_pickup_ms,
                UsageRecord.reference_resolve_ms,
                UsageRecord.first_pcm_ms,
                UsageRecord.first_audio_ms,
                UsageRecord.gateway_first_byte_ms,
                UsageRecord.inference_ms,
                UsageRecord.rtf,
            ).where(UsageRecord.request_id.in_(request_uuids))
        )).all()
    by_rid = {str(r.request_id): r for r in rows}
    for s in samples:
        r = by_rid.get(s.request_id)
        if r is None:
            continue
        s.queue_wait_ms = r.queue_wait_ms
        s.worker_pickup_ms = r.worker_pickup_ms
        s.reference_resolve_ms = r.reference_resolve_ms
        s.first_pcm_ms = r.first_pcm_ms
        s.first_audio_ms = r.first_audio_ms
        s.gateway_first_byte_ms = r.gateway_first_byte_ms
        s.inference_ms = r.inference_ms
        s.rtf = float(r.rtf) if r.rtf is not None else None
        matched += 1
    await engine.dispose()
    return matched


def _compute_percentiles(samples: list[CallSample]) -> dict[str, dict[str, float | None]]:
    out: dict[str, dict[str, float | None]] = {}
    ok_samples = [s for s in samples if s.ok]
    for field_name in _WATERFALL_FIELDS:
        values = [
            float(v) for v in (getattr(s, field_name) for s in ok_samples)
            if v is not None
        ]
        out[field_name] = {
            "n": float(len(values)),
            **{f"p{int(q * 100)}": _percentile(values, q) for q in _PERCENTILE_LEVELS},
            "min": min(values) if values else None,
            "max": max(values) if values else None,
            "mean": statistics.mean(values) if values else None,
        }
    return out


def _format_markdown(summary: RunSummary) -> str:
    lines: list[str] = []
    lines.append(f"# NeuroVoice latency benchmark — {summary.hardware_label}")
    lines.append("")
    lines.append(f"- **Started:** {summary.started_at}")
    lines.append(f"- **Finished:** {summary.finished_at}")
    lines.append(f"- **Wall time:** {summary.duration_s:.1f} s")
    lines.append(f"- **Base URL:** `{summary.base_url}`")
    lines.append(f"- **Voice:** `{summary.voice}`")
    lines.append(f"- **Requests:** {summary.requests}  ·  **Concurrency:** {summary.concurrency}")
    lines.append(f"- **Success rate:** {summary.success_rate * 100:.1f}%")
    lines.append("")
    lines.append("## Per-stage latency (ms unless noted)")
    lines.append("")
    lines.append("| Stage | n | p50 | p90 | p95 | p99 | mean | min | max |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for f in _WATERFALL_FIELDS:
        stats = summary.percentiles.get(f, {})
        if not stats or stats.get("n", 0) == 0:
            lines.append(f"| `{f}` | 0 | — | — | — | — | — | — | — |")
            continue
        def _fmt(v: float | None) -> str:
            return f"{v:.1f}" if v is not None else "—"
        lines.append(
            f"| `{f}` | {int(stats['n'])} | {_fmt(stats.get('p50'))} | "
            f"{_fmt(stats.get('p90'))} | {_fmt(stats.get('p95'))} | "
            f"{_fmt(stats.get('p99'))} | {_fmt(stats.get('mean'))} | "
            f"{_fmt(stats.get('min'))} | {_fmt(stats.get('max'))} |"
        )
    lines.append("")
    if summary.notes:
        lines.append("## Notes")
        for n in summary.notes:
            lines.append(f"- {n}")
        lines.append("")
    return "\n".join(lines)


async def _amain(args: argparse.Namespace) -> int:
    args.out.mkdir(parents=True, exist_ok=True)
    sentences = _TEST_SENTENCES
    if args.text_set:
        sentences = json.loads(args.text_set.read_text())
        if not isinstance(sentences, list) or not all(isinstance(s, str) for s in sentences):
            print("--text-set must be a JSON list of strings", file=sys.stderr)
            return 2

    started = datetime.now(timezone.utc)
    started_mono = time.monotonic()

    headers = {"Authorization": f"Bearer {args.api_key}"}
    timeout = httpx.Timeout(args.timeout_s, read=args.timeout_s)
    samples: list[CallSample] = []
    sem = asyncio.Semaphore(args.concurrency)

    async with httpx.AsyncClient(
        base_url=args.base_url, headers=headers, timeout=timeout,
    ) as client:
        async def _bounded(text: str) -> CallSample:
            async with sem:
                return await _one_call(
                    client,
                    voice=args.voice,
                    text=text,
                    audio_format=args.audio_format,
                )

        tasks = [
            asyncio.create_task(_bounded(sentences[i % len(sentences)]))
            for i in range(args.requests)
        ]
        for done in asyncio.as_completed(tasks):
            samples.append(await done)

    finished = datetime.now(timezone.utc)
    duration_s = time.monotonic() - started_mono
    success = sum(1 for s in samples if s.ok)

    notes: list[str] = []
    if args.db_url or os.environ.get("NEUROVOICE_DATABASE_URL"):
        # Set NEUROVOICE_DATABASE_URL via env so db.models loads cleanly.
        db_url = args.db_url or os.environ["NEUROVOICE_DATABASE_URL"]
        os.environ.setdefault("NEUROVOICE_DATABASE_URL", db_url)
        # Give the worker pipeline a moment to commit usage rows before
        # we query. The worker commits BEFORE publishing final, so by
        # the time the client sees end-of-stream the row should already
        # exist — but the gateway's UPDATE for gateway_first_byte_ms
        # fires AFTER the stream closes, so a small wait avoids races.
        await asyncio.sleep(args.db_settle_s)
        matched = await _join_usage_rows(samples, db_url)
        notes.append(
            f"DB waterfall stitch: matched {matched}/{success} successful samples"
        )
    else:
        notes.append(
            "No --db-url / NEUROVOICE_DATABASE_URL — waterfall columns are NULL. "
            "Client-side first-byte / total are still populated."
        )

    summary = RunSummary(
        hardware_label=args.hardware_label,
        base_url=args.base_url,
        voice=args.voice,
        requests=args.requests,
        concurrency=args.concurrency,
        started_at=started.isoformat(),
        finished_at=finished.isoformat(),
        duration_s=duration_s,
        success_rate=success / max(args.requests, 1),
        samples=samples,
        percentiles=_compute_percentiles(samples),
        notes=notes,
    )

    raw_path = args.out / "raw.json"
    raw_path.write_text(
        json.dumps({
            **{k: v for k, v in asdict(summary).items() if k != "samples"},
            "samples": [asdict(s) for s in summary.samples],
        }, ensure_ascii=False, indent=2),
    )
    report_path = args.out / "report.md"
    report_path.write_text(_format_markdown(summary))

    print(f"raw     -> {raw_path}")
    print(f"report  -> {report_path}")
    print()
    print(_format_markdown(summary))
    return 0 if success > 0 else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--base-url", required=True)
    p.add_argument("--api-key", required=True)
    p.add_argument("--voice", required=True, help="voice_id slug from /v1/voices")
    p.add_argument("--requests", type=int, default=20)
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--hardware-label", required=True,
                   help="free-form label for the report header, e.g. 'L4-runpod', 'A100-lambda'")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--timeout-s", type=float, default=60.0)
    p.add_argument(
        "--audio-format",
        choices=("pcm16", "wav"),
        default="pcm16",
        help="Default `pcm16` so client_first_byte_ms measures first AUDIO "
             "byte, not a WAV header byte. `wav` is supported and skips "
             "the 44-byte RIFF/WAVE header before arming the timer.",
    )
    p.add_argument(
        "--db-url",
        default=None,
        help="SQLAlchemy async DB URL. Falls back to NEUROVOICE_DATABASE_URL env var.",
    )
    p.add_argument(
        "--db-settle-s",
        type=float,
        default=1.0,
        help="Wait this long after all requests finish before querying "
             "usage_records (lets gateway_first_byte_ms UPDATEs commit).",
    )
    p.add_argument(
        "--text-set",
        type=Path,
        default=None,
        help="JSON list of strings to cycle through; defaults to the built-in mini set.",
    )
    args = p.parse_args(argv)
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
