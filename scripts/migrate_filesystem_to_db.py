"""Migrate the legacy filesystem voice catalog to the new DB + R2 model.

Reads configs/voices/*.yaml + data/reference-audio/* and writes:
    - tenant row (one, slug from --tenant-slug)
    - voice rows (one per YAML)
    - reference audio uploaded to R2 (optional — pass --skip-upload to
      record a local file:// URI instead, useful for dev)

Idempotent: re-running is a no-op when (tenant_slug, voice_id) already
exists. Use --dry-run to print the plan without writing.

Pre-requisites:
    NEUROVOICE_DATABASE_URL points at a migrated Postgres
    R2 creds (boto3 conventions): NEUROVOICE_R2_ACCOUNT_ID, NEUROVOICE_R2_ACCESS_KEY_ID,
    NEUROVOICE_R2_SECRET_ACCESS_KEY, NEUROVOICE_R2_BUCKET (or pass --skip-upload)
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import sys
from pathlib import Path

import yaml

from db import AsyncSessionLocal
from repos import AuditRepo, TenantRepo, VoiceRepo


_LICENSE_KIND_VALUES = {
    "example", "synthetic", "user-owned",
    "talent-contract", "public-figure", "partner-licensed",
}


def _legacy_license_to_kind(raw: dict) -> str:
    """Map a legacy YAML manifest's license field onto ADR-10's closed
    list. New manifests already carry `license_kind` directly; this is
    the migration-time bridge for old `license: <freeform>` shapes.
    """
    if "license_kind" in raw and raw["license_kind"] in _LICENSE_KIND_VALUES:
        return raw["license_kind"]
    legacy = raw.get("license")
    if isinstance(legacy, str):
        if legacy in {"internal-bridge", "internal-placeholder", "example"}:
            return "example"
        if legacy.startswith("talent-contract"):
            return "talent-contract"
        if legacy in _LICENSE_KIND_VALUES:
            return legacy
    return "user-owned"  # safest fallback for an unknown manifest


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _audio_duration_seconds(path: Path) -> float:
    """Cheap probe — soundfile reads the header without decoding the whole file."""
    import soundfile as sf

    info = sf.info(str(path))
    return float(info.duration)


def _audio_sample_rate(path: Path) -> int:
    import soundfile as sf

    info = sf.info(str(path))
    return int(info.samplerate)


def _upload_to_r2(path: Path, bucket: str, key: str) -> str:
    """Upload + return s3:// URI. boto3 reads creds from env."""
    import boto3
    from botocore.client import Config

    account_id = os.environ["NEUROVOICE_R2_ACCOUNT_ID"]
    endpoint = f"https://{account_id}.r2.cloudflarestorage.com"
    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=os.environ["NEUROVOICE_R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["NEUROVOICE_R2_SECRET_ACCESS_KEY"],
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )
    s3.upload_file(str(path), bucket, key)
    return f"s3://{bucket}/{key}"


async def _migrate(args) -> int:
    voices_dir = args.voices_dir
    ref_dir = args.reference_dir
    if not voices_dir.is_dir():
        print(f"voices_dir not found: {voices_dir}", file=sys.stderr)
        return 2

    yaml_files = sorted(voices_dir.glob("*.yaml"))
    if not yaml_files:
        print(f"no voice manifests in {voices_dir}", file=sys.stderr)
        return 2

    async with AsyncSessionLocal() as session:
        tr = TenantRepo(session)
        tenant = await tr.get_by_slug(args.tenant_slug)
        if tenant is None:
            if args.dry_run:
                print(f"[dry-run] would create tenant slug={args.tenant_slug!r}")
                return 0
            tenant = await tr.create(
                slug=args.tenant_slug,
                display_name=args.tenant_display_name or args.tenant_slug,
            )
            await AuditRepo(session).record(
                actor_type="system",
                action="tenant.create",
                result="success",
                tenant_id=tenant.id,
                payload={"slug": args.tenant_slug, "source": "filesystem_migration"},
            )
            await session.commit()
            print(f"created tenant {tenant.slug} (id={tenant.id})")
        else:
            print(f"tenant {tenant.slug} already exists (id={tenant.id})")

        vr = VoiceRepo(session, tenant.id)
        for yaml_path in yaml_files:
            raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
            voice_id = raw.get("voice_id")
            if not voice_id:
                print(f"  skip {yaml_path.name}: no voice_id", file=sys.stderr)
                continue
            existing = await vr.get_accessible(voice_id)
            if existing is not None:
                print(f"  skip {voice_id} (already in DB)")
                continue

            ref_filename = raw.get("reference_audio")
            if not ref_filename:
                print(f"  skip {voice_id}: no reference_audio", file=sys.stderr)
                continue
            ref_path = ref_dir / ref_filename
            if not ref_path.is_file():
                print(f"  skip {voice_id}: reference {ref_path} missing",
                      file=sys.stderr)
                continue

            sha256 = _sha256_file(ref_path)
            try:
                duration = _audio_duration_seconds(ref_path)
                sample_rate = _audio_sample_rate(ref_path)
            except Exception as e:
                print(f"  skip {voice_id}: cannot probe audio: {e}",
                      file=sys.stderr)
                continue

            if args.dry_run:
                print(f"  [dry-run] would enroll {voice_id} "
                      f"({duration:.2f}s @ {sample_rate}Hz, sha256={sha256[:8]}…)")
                continue

            if args.skip_upload:
                ref_uri = f"file://{ref_path.resolve()}"
            else:
                bucket = os.environ["NEUROVOICE_R2_BUCKET"]
                key = f"voices/{tenant.slug}/{ref_filename}"
                ref_uri = _upload_to_r2(ref_path, bucket, key)

            v = await vr.create(
                voice_id=voice_id,
                display_name=raw.get("display_name", voice_id),
                language=raw.get("language", "tr"),
                gender=raw.get("gender", "neutral"),
                style_tags=list(raw.get("style_tags") or []),
                reference_uri=ref_uri,
                reference_sha256=sha256,
                reference_seconds=duration,
                reference_sample_rate=sample_rate,
                source=raw.get("source", "bootstrap"),
                # ADR-10 — closed-list license_kind. Legacy YAML may
                # carry a freeform `license` key; map known values onto
                # the new vocabulary, else default to user-owned
                # (safest fallback for unknown manifests).
                license_kind=_legacy_license_to_kind(raw),
                license_ref=raw.get("license_ref"),
            )
            await AuditRepo(session).record(
                actor_type="system",
                action="voice.create",
                result="success",
                tenant_id=tenant.id,
                target_type="voice",
                target_id=str(v.id),
                payload={
                    "voice_id": voice_id, "source": "filesystem_migration",
                    "reference_sha256": sha256,
                },
            )
            await session.commit()
            print(f"  enrolled {voice_id} → {ref_uri}")

    print("migration complete.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Move configs/voices/*.yaml + data/reference-audio/* into Postgres + R2"
    )
    p.add_argument("--tenant-slug", required=True,
                   help="target tenant slug (will be created if absent)")
    p.add_argument("--tenant-display-name", default=None)
    p.add_argument("--voices-dir", type=Path, default=Path("configs/voices"))
    p.add_argument("--reference-dir", type=Path, default=Path("data/reference-audio"))
    p.add_argument("--skip-upload", action="store_true",
                   help="record file:// URIs instead of uploading to R2 (dev only)")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    return asyncio.run(_migrate(args))


if __name__ == "__main__":
    raise SystemExit(main())
