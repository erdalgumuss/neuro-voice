"""Seed the catalog with the 5 launch voices.

Usage:
    python scripts/bootstrap_voices.py --base-url http://localhost:8000 \
        --api-key dev-key-1 \
        --voices configs/seed_voices.yaml

The seed YAML lists 5 voices and the local audio file each one should be
enrolled with. Reference files must already exist on disk. If a voice with the
same id is already enrolled, it is left in place (idempotent).

For the first voice (`tr-warm-storyteller-v0`) we don't need to upload — the manifest
shipped in `configs/voices/tr-warm-storyteller-v0.yaml` already points at the on-disk
reference. The bootstrap script verifies it is reachable.
"""

from __future__ import annotations

import argparse
import mimetypes
import sys
from pathlib import Path

import httpx
import yaml


def _load_seed(path: Path) -> list[dict]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "voices" not in raw:
        raise SystemExit(f"seed file {path} must contain top-level 'voices:' list")
    return raw["voices"]


def _content_type(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Seed the NQAI voice catalog")
    p.add_argument("--base-url", default="http://localhost:8000")
    p.add_argument("--api-key", required=True)
    p.add_argument("--voices", type=Path, default=Path("configs/seed_voices.yaml"))
    p.add_argument("--reference-dir", type=Path, default=Path("data/reference-audio"))
    args = p.parse_args(argv)

    seed = _load_seed(args.voices)
    headers = {"Authorization": f"Bearer {args.api_key}"}

    with httpx.Client(base_url=args.base_url, headers=headers, timeout=120) as client:
        existing_resp = client.get("/v1/voices")
        existing_resp.raise_for_status()
        existing = existing_resp.json().get("voices", [])
        existing_ids = {v["voice_id"] for v in existing}
        print(f"already enrolled: {sorted(existing_ids) or '(none)'}")

        for voice in seed:
            vid = voice["voice_id"]
            if vid in existing_ids:
                print(f"skip {vid} (already enrolled)")
                continue
            ref_filename = voice.get("reference_audio")
            if not ref_filename:
                print(f"skip {vid}: no reference_audio listed", file=sys.stderr)
                continue
            ref_path = args.reference_dir / ref_filename
            if not ref_path.is_file():
                print(f"skip {vid}: reference {ref_path} missing", file=sys.stderr)
                continue
            with ref_path.open("rb") as fh:
                resp = client.post(
                    "/v1/voices",
                    data={
                        "voice_id": vid,
                        "display_name": voice["display_name"],
                        "language": voice.get("language", "tr"),
                        "gender": voice.get("gender", "neutral"),
                        "style_tags": ",".join(voice.get("style_tags", [])),
                        "source": voice.get("source", "bootstrap"),
                        "license": voice.get("license", "internal-bridge"),
                    },
                    files={"reference_audio": (ref_path.name, fh, _content_type(ref_path))},
                )
            if resp.status_code >= 400:
                print(f"FAIL {vid}: {resp.status_code} {resp.text}", file=sys.stderr)
                continue
            print(f"enrolled {vid}: {resp.json()['voice']['display_name']}")

        final_resp = client.get("/v1/voices")
        final_resp.raise_for_status()
        final = final_resp.json()
        print(f"\ncatalog now has {final['count']} voice(s):")
        for v in final["voices"]:
            print(f"  - {v['voice_id']:24s} {v['display_name']}  ({v['gender']}, {v['language']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
