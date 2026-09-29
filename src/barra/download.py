"""Fetch a model's daily files and unzip them into ``<root>/<yyyy>/``."""

from __future__ import annotations

import json
import logging
import os
import posixpath
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Iterator, Protocol

from .models import FileSpec, ModelSpec

log = logging.getLogger(__name__)

MANIFEST_DIR = ".manifest"
TMP_DIR = ".tmp"

COMPLETE = "complete"
PARTIAL = "partial"
NO_DATA = "no_data"


class FtpClient(Protocol):
    def list_dir(self, remote_dir: str) -> set[str]: ...

    def download(self, remote_path: str, local_path: Path) -> int: ...


@dataclass
class DayResult:
    date: date
    required_total: int
    downloaded: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    missing_optional: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if not self.missing:
            return COMPLETE
        if len(self.missing) < self.required_total:
            return PARTIAL
        return NO_DATA


def year_dir(root: Path, d: date) -> Path:
    return Path(root) / f"{d:%Y}"


def daterange(start: date, end: date) -> Iterator[date]:
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def fetch_day(
    client: FtpClient,
    model: ModelSpec,
    d: date,
    root: Path,
    listing: set[str],
    *,
    remote_dir: str | None = None,
    force: bool = False,
) -> DayResult:
    """Download and unpack every file of ``model`` for date ``d`` found in ``listing``."""
    remote_dir = remote_dir or model.remote_dir
    dest = year_dir(root, d)
    result = DayResult(date=d, required_total=len(model.required))

    for spec in model.files:
        name = spec.name_for(d)
        if name not in listing:
            (result.missing_optional if spec.optional else result.missing).append(name)
            continue
        dest.mkdir(parents=True, exist_ok=True)
        remote_path = posixpath.join(remote_dir, name)
        fetched = _fetch_file(client, spec, remote_path, name, dest, force)
        (result.downloaded if fetched else result.skipped).append(name)

    _remove_if_empty(dest / TMP_DIR)
    return result


def fetch_range(
    client: FtpClient,
    model: ModelSpec,
    start: date,
    end: date,
    root: Path,
    *,
    remote_dir: str | None = None,
    force: bool = False,
) -> list[DayResult]:
    """Fetch every calendar day in ``[start, end]`` using one directory listing.

    Days with none of the required files (weekends, holidays, not yet published)
    come back with status ``no_data``.
    """
    if end < start:
        raise ValueError(f"end date {end} is before start date {start}")
    remote_dir = remote_dir or model.remote_dir
    listing = client.list_dir(remote_dir)
    log.debug("%d entries in %s", len(listing), remote_dir)

    results = []
    for d in daterange(start, end):
        r = fetch_day(client, model, d, root, listing, remote_dir=remote_dir, force=force)
        if r.status == PARTIAL:
            log.warning("%s: partial, missing %s", d, ", ".join(r.missing))
        elif r.status == NO_DATA:
            log.debug("%s: no data", d)
        results.append(r)
    return results


def _fetch_file(
    client: FtpClient, spec: FileSpec, remote_path: str, name: str, dest: Path, force: bool
) -> bool:
    """Fetch one file into ``dest``. Returns False if it was already present and skipped."""
    manifest = dest / MANIFEST_DIR / f"{name}.json"
    if spec.is_zip:
        if not force and _manifest_complete(manifest, dest):
            log.debug("skip %s (already extracted)", name)
            return False
    elif not force and (dest / name).is_file():
        log.debug("skip %s (already present)", name)
        return False

    tmp = dest / TMP_DIR
    tmp.mkdir(exist_ok=True)
    local = tmp / name
    size = client.download(remote_path, local)

    if not spec.is_zip:
        os.replace(local, dest / name)
        log.info("downloaded %s (%s)", name, _fmt_size(size))
        return True

    try:
        members = _extract(local, dest)
    finally:
        local.unlink(missing_ok=True)
    _write_manifest(manifest, name, size, members)
    log.info("downloaded %s (%s) -> %d files", name, _fmt_size(size), len(members))
    return True


def _extract(zip_path: Path, dest: Path) -> list[str]:
    """Extract all file members into ``dest`` flat, by base name only (no zip-slip)."""
    members = []
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = PurePosixPath(info.filename.replace("\\", "/")).name
            if name in ("", ".", ".."):
                continue
            part = dest / TMP_DIR / f"{name}.part"
            with zf.open(info) as src, open(part, "wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
            os.replace(part, dest / name)
            members.append(name)
    return members


def _manifest_complete(manifest: Path, dest: Path) -> bool:
    try:
        members = json.loads(manifest.read_text())["members"]
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return all((dest / m).is_file() for m in members)


def _write_manifest(manifest: Path, name: str, size: int, members: list[str]) -> None:
    manifest.parent.mkdir(exist_ok=True)
    data = {
        "remote_name": name,
        "remote_size": size,
        "members": members,
        "extracted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    part = manifest.with_name(manifest.name + ".part")
    part.write_text(json.dumps(data, indent=2))
    os.replace(part, manifest)


def _remove_if_empty(path: Path) -> None:
    try:
        path.rmdir()
    except OSError:
        pass


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    size = n / 1024
    for unit in ("KB", "MB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"
