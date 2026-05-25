"""End-to-end smoke test against a running nqai-voice server.

Synthesizes the 10-sentence mini eval set with every enrolled voice, writes
WAVs into experiments/<date>-platform-smoke/output/<voice_id>/ and prints
per-call timing + RTF + a final summary.

Usage:
    python scripts/smoke_test.py --base-url http://localhost:8000 \
        --api-key dev-key-1 \
        --voices neeko-v01 \
        --out experiments/2026-05-24-platform-smoke
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

TEST_SENTENCES = [
    ("01", "oyun",        "Hadi birlikte sıradaki hayvanı bulalım!"),
    ("02", "uyku",        "Şimdi gözlerini kapatıp derin bir nefes alalım."),
    ("03", "ders",        "Yedi artı üç kaç eder, biliyor musun?"),
    ("04", "sefkat",      "Kızgın olman çok normal, önce sakinleşelim."),
    ("05", "soru",        "Sence bugün ne renk bir gökyüzü gördük?"),
    ("06", "heyecan",     "Vay canına, çok güzel bir resim çizmişsin!"),
    ("07", "sayi_tarih",  "Yirmi üç Nisan'da ne kutlarız?"),
    ("08", "kisaltma",    "Doktor Ayşe, bunu bir büyüğünle birlikte yapalım dedi."),
    ("09", "kod_karisim", "Annenin iPhone'unu yerine bırakır mısın?"),
    ("10", "hikaye",      "Bir varmış, bir yokmuş, çok uzak bir ülkede küçük bir tavşan yaşarmış."),
]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default="http://localhost:8000")
    p.add_argument("--api-key", required=True)
    p.add_argument(
        "--voices",
        nargs="+",
        default=None,
        help="space-separated voice_ids; default = every voice in the catalog",
    )
    p.add_argument("--out", type=Path, default=Path("experiments/platform-smoke"))
    p.add_argument("--streaming", action="store_true", help="use /v1/tts/stream instead of /v1/tts")
    p.add_argument(
        "--skip-warmup",
        action="store_true",
        help="skip legacy /admin/warmup call; worker boot warmup is enough for B.1+",
    )
    p.add_argument("--text-set", type=Path, default=None,
                   help="optional JSON file with [{id, cat, text}, ...]; defaults to mini set")
    args = p.parse_args(argv)

    sentences = TEST_SENTENCES
    if args.text_set:
        sentences = [(d["id"], d["cat"], d["text"]) for d in json.loads(args.text_set.read_text())]

    args.out.mkdir(parents=True, exist_ok=True)
    out_root = args.out / "output"
    out_root.mkdir(exist_ok=True)

    headers = {"Authorization": f"Bearer {args.api_key}"}
    with httpx.Client(base_url=args.base_url, headers=headers, timeout=300) as client:
        # Older single-process deployments exposed /admin/warmup. B.1+
        # workers warm themselves on boot, and some smoke targets do not
        # expose the legacy endpoint at all. Treat 401/403/404 as a
        # non-fatal skip; real synthesis below is the actual health check.
        if args.skip_warmup:
            print("warmup skipped (--skip-warmup)")
        else:
            wr = client.post("/admin/warmup")
            if wr.status_code in {401, 403, 404}:
                print(f"warmup skipped ({wr.status_code}: {wr.text[:120]})")
            else:
                wr.raise_for_status()
                print(f"warmup OK ({wr.json()})")

        catalog = client.get("/v1/voices").json()
        catalog_ids = [v["voice_id"] for v in catalog["voices"]]
        target_voices = args.voices or catalog_ids
        target_voices = [v for v in target_voices if v in catalog_ids]
        if not target_voices:
            print("no voices to test", file=sys.stderr)
            return 2
        print(f"testing voices: {target_voices}")

        all_results: dict = {
            "date_utc": datetime.now(timezone.utc).isoformat(),
            "base_url": args.base_url,
            "streaming": args.streaming,
            "voices": {},
        }

        for vid in target_voices:
            v_out = out_root / vid
            v_out.mkdir(exist_ok=True)
            per_voice: list[dict] = []
            t_voice0 = time.time()
            for sid, cat, text in sentences:
                t0 = time.time()
                endpoint = "/v1/tts/stream" if args.streaming else "/v1/tts"
                resp = client.post(
                    endpoint,
                    json={"text": text, "voice_id": vid, "language": "tr", "audio_format": "wav"},
                )
                elapsed = time.time() - t0
                if resp.status_code >= 400:
                    print(f"  [{vid}] {sid} FAIL {resp.status_code}: {resp.text}")
                    per_voice.append({"id": sid, "cat": cat, "text": text, "ok": False,
                                      "status": resp.status_code, "error": resp.text})
                    continue
                wav_bytes = resp.content
                out_path = v_out / f"{sid}_{cat}.wav"
                out_path.write_bytes(wav_bytes)
                rtf_hdr = resp.headers.get("X-NQAI-RTF", "n/a")
                dur_hdr = resp.headers.get("X-NQAI-Duration-Seconds", "n/a")
                print(f"  [{vid}] {sid} {cat:12s} {elapsed:.2f}s call · dur={dur_hdr}s · server-RTF={rtf_hdr}")
                per_voice.append({
                    "id": sid, "cat": cat, "text": text, "ok": True,
                    "client_elapsed_s": elapsed,
                    "server_duration_s": dur_hdr,
                    "server_rtf": rtf_hdr,
                    "audio_path": str(out_path.relative_to(args.out)),
                })
            voice_elapsed = time.time() - t_voice0
            all_results["voices"][vid] = {
                "sentences": per_voice,
                "total_elapsed_s": voice_elapsed,
            }

        summary_path = args.out / "summary.json"
        summary_path.write_text(json.dumps(all_results, ensure_ascii=False, indent=2))
        print(f"\nsummary -> {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
