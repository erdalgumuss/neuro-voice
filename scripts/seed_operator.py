"""Bootstrap script: create the first admin operator (idempotent).

Usage:
    python scripts/seed_operator.py --email you@example.com --full-name "Erdal" \
        [--password-stdin]

Reads password from stdin if --password-stdin, else generates one and prints
it to stdout exactly once.

Requires NEUROVOICE_DATABASE_URL pointing at a migrated Postgres (alembic upgrade
head already run).
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
import sys

from db import AsyncSessionLocal
from repos import OperatorRepo
from server.security.passwords import hash_secret


async def _main(email: str, full_name: str | None,
                password: str | None, roles: list[str]) -> int:
    async with AsyncSessionLocal() as session:
        opr = OperatorRepo(session)
        existing = await opr.get_by_email(email)
        if existing is not None:
            print(f"operator {email!r} already exists (id={existing.id})")
            return 0

        if password is None:
            password = secrets.token_urlsafe(18)
            print("=" * 60)
            print(f"Generated password for {email}:")
            print(f"    {password}")
            print("Store it now; it will NOT be shown again.")
            print("=" * 60)

        op = await opr.create(
            email=email,
            password_hash=hash_secret(password),
            full_name=full_name,
            roles=roles,
        )
        await session.commit()
        print(f"created operator {op.email} (id={op.id})")
        return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--email", required=True)
    p.add_argument("--full-name", default=None)
    p.add_argument("--password-stdin", action="store_true",
                   help="read password from stdin instead of generating")
    p.add_argument("--role", action="append", default=["admin"],
                   help="add an extra role (default: admin)")
    args = p.parse_args(argv)
    password = sys.stdin.readline().rstrip() if args.password_stdin else None
    return asyncio.run(_main(args.email, args.full_name, password, args.role))


if __name__ == "__main__":
    raise SystemExit(main())
