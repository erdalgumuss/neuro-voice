"""ffmpeg + Deepgram transcription for the data prep step.

For each source file in ``project.source_raw_dir``:
    1. ffmpeg -> 16 kHz mono WAV in ``project.source_audio_dir``.
    2. Deepgram nova-3 utterance-split transcription (cached as JSON).
    3. Per-utterance clip + raw manifest row + review CSV row.

Output is one JSONL row per accepted utterance plus a CSV that captures
the segmentation decisions (kept / skipped + reason) so the operator
can sanity-check before kicking off training.
"""

from __future__ import annotations

import csv
import json
import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
import soundfile as sf

from .project import ProjectLayout

logger = logging.getLogger("neurovoice.finetune.transcribe")

DEEPGRAM_API_URL = "https://api.deepgram.com/v1/listen"

SUPPORTED_SOURCE_SUFFIXES = {
    ".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac",
    ".mp4", ".mov", ".webm",
}


@dataclass(frozen=True)
class TranscribeConfig:
    deepgram_model: str = "nova-3"
    language: str = "tr"
    utt_split: str = "0.8"
    min_segment_seconds: float = 1.0
    max_segment_seconds: float = 30.0
    pad_start_seconds: float = 0.10
    pad_end_seconds: float = 0.20
    target_sample_rate: int = 16000


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def ffmpeg_to_wav(src: Path, dst: Path, *, sample_rate: int = 16000) -> None:
    """Transcode any supported source codec to mono PCM WAV.

    Raises ``CalledProcessError`` if ffmpeg fails. The operator's first
    sanity check after a fresh project is "does ffmpeg complete on every
    source_raw file?" — propagating the error here surfaces codec issues
    early.
    """
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", str(src),
            "-ac", "1",
            "-ar", str(sample_rate),
            "-vn",
            str(dst),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def transcribe_deepgram(
    wav_path: Path, api_key: str, cfg: TranscribeConfig
) -> dict[str, Any]:
    """One HTTP POST to Deepgram. The response JSON is opaque blob the
    caller persists as a per-file cache."""
    params = {
        "model": cfg.deepgram_model,
        "language": cfg.language,
        "smart_format": "true",
        "punctuate": "true",
        "utterances": "true",
        "utt_split": cfg.utt_split,
    }
    headers = {
        "Authorization": f"Token {api_key}",
        "Content-Type": "audio/wav",
    }
    with wav_path.open("rb") as f:
        resp = requests.post(
            DEEPGRAM_API_URL, params=params, headers=headers, data=f,
            timeout=1800,
        )
    resp.raise_for_status()
    return resp.json()


def _write_clip(
    source_wav: Path, dst: Path, start: float, end: float,
) -> None:
    audio, sr = sf.read(str(source_wav))
    start_frame = max(0, int(start * sr))
    end_frame = min(len(audio), int(end * sr))
    if end_frame > start_frame:
        sf.write(str(dst), audio[start_frame:end_frame], sr)


def build_raw_manifest(
    layout: ProjectLayout,
    api_key: str,
    cfg: TranscribeConfig | None = None,
) -> tuple[int, int]:
    """Walk ``layout.source_raw_dir``, transcribe + segment + write clips,
    and produce ``layout.raw_manifest_path`` + ``layout.segments_review_csv``.

    Returns ``(accepted_utterances, skipped_utterances)``.
    """
    cfg = cfg or TranscribeConfig()
    layout.ensure_dirs()

    source_files = sorted(
        path for path in layout.source_raw_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_SOURCE_SUFFIXES
    )
    if not source_files:
        raise FileNotFoundError(
            f"no source audio under {layout.source_raw_dir}"
        )

    manifest_rows: list[dict[str, Any]] = []
    review_rows: list[dict[str, Any]] = []

    for src_path in source_files:
        source_wav = layout.source_audio_dir / f"{src_path.stem}_16k.wav"
        if not source_wav.exists():
            ffmpeg_to_wav(src_path, source_wav, sample_rate=cfg.target_sample_rate)

        transcript_cache = layout.deepgram_dir / f"{src_path.stem}.deepgram.json"
        if transcript_cache.exists():
            dg = json.loads(transcript_cache.read_text(encoding="utf-8"))
        else:
            dg = transcribe_deepgram(source_wav, api_key, cfg)
            transcript_cache.write_text(
                json.dumps(dg, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        utterances = dg.get("results", {}).get("utterances", []) or []
        for utt_idx, utt in enumerate(utterances, 1):
            text = _clean_text(utt.get("transcript", ""))
            start = max(0.0, float(utt.get("start", 0.0)) - cfg.pad_start_seconds)
            end = float(utt.get("end", 0.0)) + cfg.pad_end_seconds
            duration = end - start

            row = {
                "source_file": src_path.name,
                "utterance_index": utt_idx,
                "start": round(start, 3),
                "end": round(end, 3),
                "duration": round(duration, 3),
                "text": text,
            }
            if not text:
                row["status"] = "skip:empty_text"
                review_rows.append(row)
                continue
            if duration < cfg.min_segment_seconds:
                row["status"] = "skip:too_short"
                review_rows.append(row)
                continue
            if duration > cfg.max_segment_seconds:
                row["status"] = "skip:too_long"
                review_rows.append(row)
                continue

            clip_name = f"{src_path.stem}_{utt_idx:04d}.wav"
            clip_path = layout.audio_dir / clip_name
            if not clip_path.exists():
                _write_clip(source_wav, clip_path, start, end)

            row["status"] = "accepted"
            row["audio"] = str(clip_path.relative_to(layout.project_dir))
            review_rows.append(row)
            manifest_rows.append({
                "audio": row["audio"],
                "text": text,
                "duration": row["duration"],
                "speaker_id": layout.voice_id,
                "source_file": src_path.name,
            })

    # Raw manifest (training input).
    with layout.raw_manifest_path.open("w", encoding="utf-8") as f:
        for row in manifest_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # Review CSV (operator sanity check).
    csv_fields = ["source_file", "utterance_index", "start", "end",
                  "duration", "text", "status", "audio"]
    with layout.segments_review_csv.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=csv_fields)
        writer.writeheader()
        for row in review_rows:
            writer.writerow({k: row.get(k, "") for k in csv_fields})

    accepted = sum(1 for r in review_rows if r["status"] == "accepted")
    skipped = sum(1 for r in review_rows if r["status"] != "accepted")
    logger.info(
        "transcribe done: %d accepted, %d skipped (review: %s)",
        accepted, skipped, layout.segments_review_csv,
    )
    return accepted, skipped
