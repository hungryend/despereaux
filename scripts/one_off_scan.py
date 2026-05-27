"""Run a one-shot ingest scan from the CLI. Useful in dev:
    uv run python scripts/one_off_scan.py
"""

from __future__ import annotations

import asyncio
import logging

from despereaux.db import apply_sqlite_pragmas
from despereaux.services.scanner import scanner


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    await apply_sqlite_pragmas()
    result = await scanner.run_once()
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
