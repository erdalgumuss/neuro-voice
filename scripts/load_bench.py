"""Sustained-load + stability benchmark for NQAI Voice (Faz C v1 item 5).

Differs from `scripts/latency_bench.py`:
* latency_bench: small N, careful per-stage timing. Answers "what does
  ONE request look like with real VoxCPM2".
* load_bench:   high concurrency, T-second duration. Answers "does the
  system stay stable under sustained traffic, what's the throughput,
  what's the 503 rate, where does it break".

The output is the artifact for the Codex-listed checklist items:
  * 20-user baseline
  * 50-user smoke
  * 200-user target simulation

Run separately for each load level; the produced raw.json + report.md
become evidence for the Faz C closure doc.

Usage:

    python scripts/load_bench.py \\
        --base-url http://localhost:8000 \\
        --api-key "nqai_dev_..." \\
        --voice neeko-v01 \\
        --concurrency 20 \\
        --duration-s 60 \\
        --hardware-label "L4-runpod" \\
        --out experiments/2026-XX-XX-load-20

See docs/runbooks/load-chaos.md for the scenario matrix (20 / 50 /
200 baseline + chaos perturbations like worker-kill, Redis-blip,
R2-slow, DB-pool-saturation).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx

_DEFAULT_SENTENCES = [
    "Merhaba dünya.",
    "Bugün hava nasıl?",
    "Bir varmış, bir yokmuş.",
    "Sence kahve mi çay mı?",
    "Yarın görüşürüz.",
    "İyi geceler, tatlı rüyalar.",
    "Şu anda ne yapıyorsun?",
    "Gel, yan yana yürüyelim.",
]


@dataclass
class Sample:
    """One client-side request observation."""
    started_at_ms: int           # monotonic-derived offset from run start
    finished_at_ms: int
    first_byte_offset_ms: float  # within-request: 0 if no byte received
    total_ms: float
    status_code: int
    audio_bytes: int
    ok: bool
    error_type: str | None = None


@dataclass
class RunReport:
    hardware_label: str
    base_url: str
    voice: str
    concurrency: int
    duration_s: float
    started_at: str
    finished_at: str
    wall_time_s: float
    total_requests: int
    # Outcome bucketing (Codex audit 2026-05-25):
    #   successes              — admitted + completed cleanly (status 2xx, full body)
    #   admission_rejections   — gateway said 503 (capacity-aware OR XLEN ceiling).
    #                            Controlled, NOT failure. Sized backpressure.
    #   uncontrolled_failures  — 5xx other than 503, client timeouts, transport
    #                            errors. Always pages the on-call.
    successes: int
    admission_rejections: int
    uncontrolled_failures: int
    error_breakdown: dict[str, int]
    throughput_rps: float
    # Two rates so the dashboard doesn't lie:
    #   accepted_success_rate    — successes / (successes + uncontrolled_failures).
    #                              Excludes 503s: backpressure is the system
    #                              working as designed, not a regression.
    #   admission_rejection_rate — admission_rejections / total_requests.
    #                              Tracks how often we refused. Too high means
    #                              undersized cluster; never zero means well-tuned.
    accepted_success_rate: float
    admission_rejection_rate: float
    latency_first_byte_ms: dict[str, float | None]
    latency_total_ms: dict[str, float | None]
    notes: list[str] = field(default_factory=list)


_PCT_LEVELS = (0.5, 0.9, 0.95, 0.99)


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    s = sorted(values)
    idx = q * (len(s) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (idx - lo)


def _stats_block(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"n": 0.0, "min": None, "max": None, "mean": None, **{
            f"p{int(q * 100)}": None for q in _PCT_LEVELS
        }}
    return {
        "n": float(len(values)),
        "min": min(values),
        "max": max(values),
        "mean": statistics.mean(values),
        **{f"p{int(q * 100)}": _percentile(values, q) for q in _PCT_LEVELS},
    }


_WAV_HEADER_SIZE = 44  # See latency_bench._WAV_HEADER_SIZE.


async def _one_call(
    client: httpx.AsyncClient,
    *,
    voice: str,
    text: str,
    run_start_mono: float,
    audio_format: str,
) -> Sample:
    """Codex audit fix (2026-05-25): in WAV mode skip the 44-byte
    RIFF/WAVE header so first_byte timing reflects actual audio. In
    pcm16 mode (the default) the first byte IS audio."""
    started_mono = time.monotonic()
    started_off_ms = int((started_mono - run_start_mono) * 1000)
    skip_remaining = _WAV_HEADER_SIZE if audio_format == "wav" else 0
    first_byte_at: float | None = None
    audio_bytes = 0
    status_code = 0
    ok = False
    error_type: str | None = None
    try:
        async with client.stream(
            "POST",
            "/v1/tts/stream",
            json={
                "text": text, "voice_id": voice,
                "language": "tr", "audio_format": audio_format,
            },
        ) as r:
            status_code = r.status_code
            if r.status_code >= 400:
                _ = await r.aread()  # drain
                error_type = f"http_{r.status_code}"
            else:
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
                ok = True
    except httpx.TimeoutException:
        error_type = "timeout"
    except httpx.HTTPError as e:
        error_type = f"httpx_{type(e).__name__}"
    except Exception as e:  # noqa: BLE001
        error_type = f"client_{type(e).__name__}"

    finished_mono = time.monotonic()
    return Sample(
        started_at_ms=started_off_ms,
        finished_at_ms=int((finished_mono - run_start_mono) * 1000),
        first_byte_offset_ms=(
            (first_byte_at - started_mono) * 1000.0
            if first_byte_at is not None else 0.0
        ),
        total_ms=(finished_mono - started_mono) * 1000.0,
        status_code=status_code,
        audio_bytes=audio_bytes,
        ok=ok,
        error_type=error_type,
    )


async def _worker_loop(
    worker_idx: int,
    client: httpx.AsyncClient,
    *,
    voice: str,
    sentences: list[str],
    run_start_mono: float,
    deadline_mono: float,
    samples: list[Sample],
    samples_lock: asyncio.Lock,
    audio_format: str,
) -> None:
    """One concurrent client. Keeps requesting until the deadline."""
    i = 0
    while time.monotonic() < deadline_mono:
        text = sentences[(worker_idx + i) % len(sentences)]
        sample = await _one_call(
            client, voice=voice, text=text,
            run_start_mono=run_start_mono,
            audio_format=audio_format,
        )
        async with samples_lock:
            samples.append(sample)
        i += 1


def _format_markdown(report: RunReport) -> str:
    lines: list[str] = []
    lines.append(f"# NQAI Voice load benchmark — {report.hardware_label}")
    lines.append("")
    lines.append(f"- **Started:** {report.started_at}")
    lines.append(f"- **Finished:** {report.finished_at}")
    lines.append(f"- **Wall time:** {report.wall_time_s:.1f} s "
                 f"(target {report.duration_s:.0f} s)")
    lines.append(f"- **Base URL:** `{report.base_url}`")
    lines.append(f"- **Voice:** `{report.voice}`")
    lines.append(f"- **Concurrency:** {report.concurrency}")
    lines.append(f"- **Total requests:** {report.total_requests}")
    lines.append(f"- **Throughput:** {report.throughput_rps:.2f} req/s")
    lines.append("")
    lines.append("## Outcome bucketing")
    lines.append("")
    lines.append("| Bucket | Count | Notes |")
    lines.append("|---|---|---|")
    lines.append(
        f"| Successes | {report.successes} | admitted + completed cleanly |"
    )
    lines.append(
        f"| Admission rejections (503) | {report.admission_rejections} | "
        f"controlled backpressure — sized capacity at work |"
    )
    lines.append(
        f"| Uncontrolled failures | {report.uncontrolled_failures} | "
        f"non-503 5xx, timeouts, transport errors — investigate |"
    )
    lines.append("")
    lines.append(
        f"- **Accepted success rate:** "
        f"{report.accepted_success_rate * 100:.1f}% "
        f"(excludes 503s — backpressure is by design)"
    )
    lines.append(
        f"- **Admission rejection rate:** "
        f"{report.admission_rejection_rate * 100:.1f}% "
        f"(non-zero with well-tuned backpressure is OK; bound it under load)"
    )
    lines.append("")
    if report.error_breakdown:
        lines.append("## Error breakdown")
        lines.append("")
        lines.append("| Error type | Count |")
        lines.append("|---|---|")
        for err, count in sorted(
            report.error_breakdown.items(), key=lambda kv: -kv[1],
        ):
            lines.append(f"| `{err}` | {count} |")
        lines.append("")

    def _row(name: str, stats: dict[str, float | None]) -> str:
        def _fmt(v: float | None) -> str:
            return f"{v:.1f}" if isinstance(v, (int, float)) else "—"
        return (
            f"| {name} | {int(stats['n'])} | {_fmt(stats.get('p50'))} | "
            f"{_fmt(stats.get('p90'))} | {_fmt(stats.get('p95'))} | "
            f"{_fmt(stats.get('p99'))} | {_fmt(stats.get('mean'))} | "
            f"{_fmt(stats.get('min'))} | {_fmt(stats.get('max'))} |"
        )

    lines.append("## Latency (client-observed, ms)")
    lines.append("")
    lines.append("| Metric | n | p50 | p90 | p95 | p99 | mean | min | max |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    lines.append(_row("client_first_byte_ms", report.latency_first_byte_ms))
    lines.append(_row("client_total_ms", report.latency_total_ms))
    lines.append("")
    if report.notes:
        lines.append("## Notes")
        for n in report.notes:
            lines.append(f"- {n}")
    return "\n".join(lines)


async def _amain(args: argparse.Namespace) -> int:
    args.out.mkdir(parents=True, exist_ok=True)

    sentences = _DEFAULT_SENTENCES
    if args.text_set:
        sentences = json.loads(args.text_set.read_text())
        if not isinstance(sentences, list) or not all(isinstance(s, str) for s in sentences):
            print("--text-set must be a JSON list of strings", file=sys.stderr)
            return 2

    headers = {"Authorization": f"Bearer {args.api_key}"}
    timeout = httpx.Timeout(args.timeout_s, read=args.timeout_s)

    samples: list[Sample] = []
    samples_lock = asyncio.Lock()
    started = datetime.now(timezone.utc)
    run_start_mono = time.monotonic()
    deadline_mono = run_start_mono + args.duration_s

    # One HTTP client shared across workers — httpx pools connections.
    limits = httpx.Limits(
        max_connections=args.concurrency * 2,
        max_keepalive_connections=args.concurrency,
    )
    async with httpx.AsyncClient(
        base_url=args.base_url, headers=headers, timeout=timeout, limits=limits,
    ) as client:
        worker_tasks = [
            asyncio.create_task(_worker_loop(
                idx, client,
                voice=args.voice, sentences=sentences,
                run_start_mono=run_start_mono, deadline_mono=deadline_mono,
                samples=samples, samples_lock=samples_lock,
                audio_format=args.audio_format,
            ))
            for idx in range(args.concurrency)
        ]
        await asyncio.gather(*worker_tasks, return_exceptions=True)

    finished = datetime.now(timezone.utc)
    wall_time_s = time.monotonic() - run_start_mono

    total = len(samples)
    successes = sum(1 for s in samples if s.ok)
    admission_rejections = sum(
        1 for s in samples if (not s.ok) and s.error_type == "http_503"
    )
    uncontrolled_failures = total - successes - admission_rejections
    error_breakdown = Counter(s.error_type or "ok" for s in samples if not s.ok)

    successful_fb = [s.first_byte_offset_ms for s in samples if s.ok and s.first_byte_offset_ms > 0]
    successful_total = [s.total_ms for s in samples if s.ok]

    # accepted_success_rate excludes 503s: backpressure is by design.
    decided = successes + uncontrolled_failures
    accepted_success_rate = (successes / decided) if decided else 0.0
    admission_rejection_rate = (
        admission_rejections / total if total else 0.0
    )

    report = RunReport(
        hardware_label=args.hardware_label,
        base_url=args.base_url,
        voice=args.voice,
        concurrency=args.concurrency,
        duration_s=args.duration_s,
        started_at=started.isoformat(),
        finished_at=finished.isoformat(),
        wall_time_s=wall_time_s,
        total_requests=total,
        successes=successes,
        admission_rejections=admission_rejections,
        uncontrolled_failures=uncontrolled_failures,
        error_breakdown=dict(error_breakdown),
        throughput_rps=total / max(wall_time_s, 1e-9),
        accepted_success_rate=accepted_success_rate,
        admission_rejection_rate=admission_rejection_rate,
        latency_first_byte_ms=_stats_block(successful_fb),
        latency_total_ms=_stats_block(successful_total),
        notes=[
            "Latency stats exclude failed samples (errors aren't latency).",
            "Throughput counts ALL requests issued (success + failure).",
            "503s are bucketed as `admission_rejections` (controlled "
            "backpressure), NOT failures. Only non-503 errors page the "
            "on-call.",
        ],
    )

    raw_path = args.out / "raw.json"
    raw_path.write_text(json.dumps({
        **{k: v for k, v in asdict(report).items()},
        "samples": [asdict(s) for s in samples],
    }, ensure_ascii=False, indent=2))
    report_path = args.out / "report.md"
    report_path.write_text(_format_markdown(report))

    print(f"raw     -> {raw_path}")
    print(f"report  -> {report_path}")
    print()
    print(_format_markdown(report))
    return 0 if successes > 0 else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--base-url", required=True)
    p.add_argument("--api-key", required=True)
    p.add_argument("--voice", required=True)
    p.add_argument("--concurrency", type=int, required=True)
    p.add_argument("--duration-s", type=float, required=True)
    p.add_argument("--hardware-label", required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--timeout-s", type=float, default=60.0)
    p.add_argument("--text-set", type=Path, default=None)
    p.add_argument(
        "--audio-format",
        choices=("pcm16", "wav"),
        default="pcm16",
        help="Default `pcm16` so first_byte timing measures audio, not "
             "the WAV header. `wav` is supported and skips the header.",
    )
    args = p.parse_args(argv)
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
