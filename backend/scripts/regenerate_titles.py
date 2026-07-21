"""One-shot backfill: regenerate short titles for all recordings with a summary.

Run from the backend directory (it imports the package via PYTHONPATH):

    python scripts/regenerate_titles.py
    python scripts/regenerate_titles.py --only-missing   # skip recordings that already have a title
    python scripts/regenerate_titles.py --limit 10        # process at most N recordings
    python scripts/regenerate_titles.py --dry-run         # print what would change, write nothing

The title is derived from the existing summary only (no re-transcribe /
re-summarize), reusing the same provider the live app uses. Failures on a single
recording are logged and skipped so the rest can proceed.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from sqlalchemy import select

# Make the backend package importable when run as a script.
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from smoothie import db  # noqa: E402
from smoothie.models import Recording  # noqa: E402
from smoothie.tasks import regenerate_title  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("regenerate_titles")


async def _collect(recordings, only_missing: bool, limit: int | None) -> list[Recording]:
    stmt = select(Recording).join(Recording.summary)
    if only_missing:
        stmt = stmt.where(Recording.generated_title.is_(None))
    stmt = stmt.order_by(Recording.created_at.asc())
    if limit is not None:
        stmt = stmt.limit(limit)
    return list((await recordings.execute(stmt)).scalars().all())


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only-missing", action="store_true",
                        help="Skip recordings that already have a generated_title.")
    parser.add_argument("--limit", type=int, default=None,
                        help="Process at most N recordings.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would change without writing to the DB.")
    args = parser.parse_args()

    # init_db creates tables and runs column migrations (adds generated_title).
    await db.init_db()

    async with db.async_session() as session:
        recordings = await _collect(session, args.only_missing, args.limit)

    if not recordings:
        logger.info("No recordings to process.")
        return 0

    logger.info("Processing %d recording(s)%s...", len(recordings),
                " (dry-run)" if args.dry_run else "")
    success = 0
    for rec in recordings:
        if args.dry_run:
            logger.info("[dry-run] would regenerate title for %s (%s)",
                        rec.id, rec.filename)
            success += 1
            continue
        try:
            title = await regenerate_title(rec.id)
        except Exception as exc:  # noqa: BLE001 - keep going on per-item failures
            logger.error("Failed %s: %s", rec.id, exc)
            continue
        if title is None:
            logger.warning("Skipped %s: no summary available.", rec.id)
            continue
        logger.info("Updated %s -> %r", rec.id, title)
        success += 1

    logger.info("Done. %d/%d updated.", success, len(recordings))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
