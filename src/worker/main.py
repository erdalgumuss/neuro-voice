"""Worker process entry point — `python -m worker.main`.

Lifecycle (full implementation lands in step 4 of the Faz B.1 split):

    boot → DB pool ready → Redis ready → R2 client ready
         → model eager-load (NQAI_WARMUP_ON_BOOT=true default)
         → XGROUP CREATE MKSTREAM (idempotent)
         → XREADGROUP loop
         → SIGTERM → drain in-flight → XACK → exit

This skeleton exists so step 2 can land as a small, verifiable commit
without behaviour change. The engine + pipeline arrive in steps 3 and 4.
"""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger("nqai_voice.worker")


def run() -> int:
    """Boot a single worker process. Step 2: placeholder; real consumer
    + pipeline are wired up in step 4. Returns the process exit code."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    logger.warning(
        "worker stub — Faz B.1 step 2 placeholder. "
        "Consumer loop arrives in step 4."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover — entry point
    sys.exit(run())
