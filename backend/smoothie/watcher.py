"""Polling folder ingestion with stable-file detection and durable copy claims.

No native filesystem watcher is required: startup and periodic scans also catch
files added while Smoothie was stopped or filesystem notifications were lost.
"""
from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import os
import time
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from . import db
from .artifacts import export_recording, flush_pending_exports
from .config import get_settings
from .logging_config import get_logger
from .models import FolderImport, Job, JobStage, Recording
from .storage import audio_filename, job_output_dir, supported_audio

logger = get_logger(__name__)


class SourceChanged(OSError):
    pass


def _signature(path: Path, info: os.stat_result) -> str:
    value = f"{path}\0{info.st_size}\0{info.st_mtime_ns}"
    return hashlib.sha256(value.encode()).hexdigest()


def _candidates(root: Path, output: Path):
    result = []
    for directory, subdirs, filenames in os.walk(root, followlinks=False):
        parent = Path(directory)
        subdirs[:] = [name for name in subdirs if not name.startswith(".")
                      and not (parent / name).is_symlink()
                      and not (parent / name).resolve().is_relative_to(output)]
        for name in filenames:
            path = parent / name
            if name.startswith(".") or not supported_audio(path) or path.is_symlink():
                continue
            try:
                info = path.stat()
                if path.is_file() and info.st_size:
                    result.append((path, info, _signature(path, info)))
            except OSError:
                continue  # A producer can remove/rename files during a scan.
    return result


def _copy(source: str, destination: Path, size: int, mtime_ns: int) -> str:
    """Stream a frozen source into an atomic managed copy; never modify the source."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    # A complete final file means a previous process finished copying before
    # it stopped. Recover that copy even if the original is no longer present.
    if destination.is_file() and destination.stat().st_size == size:
        with destination.open("rb") as stream:
            digest = hashlib.sha256()
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
    path = Path(source)
    temporary = destination.with_name(f".{destination.name}.part")
    try:
        if path.is_symlink():
            raise SourceChanged("Source became a symbolic link")
        with path.open("rb") as src, temporary.open("wb") as dst:
            before = os.fstat(src.fileno())
            if (before.st_size, before.st_mtime_ns) != (size, mtime_ns):
                raise SourceChanged("Source changed before copying")
            digest = hashlib.sha256()
            for block in iter(lambda: src.read(1024 * 1024), b""):
                digest.update(block)
                dst.write(block)
            dst.flush()
            os.fsync(dst.fileno())
            after = os.fstat(src.fileno())
            current = path.stat()
            identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
            if identity(before) != identity(after) or identity(before) != identity(current):
                raise SourceChanged("Source changed while copying")
        os.replace(temporary, destination)
        return digest.hexdigest()
    finally:
        temporary.unlink(missing_ok=True)


class FolderWatcher:
    def __init__(self):
        self._observed: dict[str, tuple[str, float]] = {}
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def start(self):
        if self._task is not None:
            return
        settings = get_settings()
        settings.output_dir.mkdir(parents=True, exist_ok=True)
        if settings.watch_enabled:
            settings.watch_dir.mkdir(parents=True, exist_ok=True)
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="smoothie-folder-watch")

    async def stop(self):
        self._stop.set()
        if self._task is not None:
            # Finish any active copy/DB publication before shutdown.
            await self._task
            self._task = None

    async def _run(self):
        while not self._stop.is_set():
            try:
                await flush_pending_exports()
                if get_settings().watch_enabled:
                    await self.scan_once()
            except Exception:
                logger.exception("Folder scan failed; retrying next poll")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=get_settings().watch_poll_interval)
            except asyncio.TimeoutError:
                pass

    async def scan_once(self, now: float | None = None):
        settings = get_settings()
        settings.watch_dir.mkdir(parents=True, exist_ok=True)
        settings.output_dir.mkdir(parents=True, exist_ok=True)
        # Resume durable claims left between copy and queue publication.
        async with db.async_session() as session:
            copying = (await session.execute(select(FolderImport.id).where(
                FolderImport.state == "copying"
            ))).scalars().all()
            known = set((await session.execute(select(FolderImport.source_key))).scalars())
        for import_id in copying:
            await self._finish(import_id)
        now = time.monotonic() if now is None else now
        observed = {}
        for path, info, key in await asyncio.to_thread(_candidates, settings.watch_dir, settings.output_dir):
            source = str(path)
            previous = self._observed.get(source)
            since = previous[1] if previous and previous[0] == key else now
            observed[source] = (key, since)
            if key in known or now - since < settings.watch_stable_seconds:
                continue
            import_id = uuid.uuid4().hex
            directory = job_output_dir(import_id, path.name)
            async with db.async_session() as session:
                claim = FolderImport(
                    id=import_id, source_key=key, source_path=source,
                    source_size=info.st_size, source_mtime_ns=info.st_mtime_ns,
                    output_path=str(directory), filename=audio_filename(path.name, None),
                    state="copying",
                )
                session.add(claim)
                try:
                    await session.commit()
                except IntegrityError:
                    # A unique database claim prevents duplicate queue entries.
                    await session.rollback()
                    continue
            await self._finish(import_id)
            if self._stop.is_set():
                break
        self._observed = observed

    async def _finish(self, import_id: str):
        async with db.async_session() as session:
            claim = await session.get(FolderImport, import_id)
            if claim is None or claim.state != "copying":
                return
            try:
                digest = await asyncio.to_thread(
                    _copy, claim.source_path, Path(claim.output_path) / claim.filename,
                    claim.source_size, claim.source_mtime_ns,
                )
            except SourceChanged as exc:
                # A later scan observes the new source version after it settles.
                claim.state = "changed"
                claim.error = str(exc)
                await session.commit()
                try:
                    Path(claim.output_path).rmdir()
                except OSError:
                    pass
                return
            except OSError as exc:
                # Keep the durable claim for recovery from a temporary disk or
                # permission failure. Do not enqueue a missing/partial copy.
                if claim.error != str(exc):
                    logger.warning("Could not copy %s: %s", claim.source_path, exc)
                claim.error = str(exc)
                await session.commit()
                return
            recording = Recording(
                id=claim.id, title=Path(claim.source_path).name,
                original_filename=Path(claim.source_path).name, filename=claim.filename,
                output_path=claim.output_path,
                content_type=mimetypes.guess_type(claim.source_path)[0] or "application/octet-stream",
                status=JobStage.PENDING,
            )
            session.add(recording)
            await session.flush()
            session.add(Job(recording_id=recording.id, stage=JobStage.PENDING))
            claim.sha256, claim.recording_id, claim.state, claim.error = digest, recording.id, "queued", None
            await session.commit()
        await export_recording(import_id)
        logger.info("Imported %s as recording %s", claim.source_path, import_id)


watcher = FolderWatcher()
