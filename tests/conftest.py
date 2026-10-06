from __future__ import annotations

import posixpath
import shutil
import zipfile
from datetime import date
from pathlib import Path

import pytest


def day_contents(d: date) -> dict[str, list[str] | None]:
    """Remote file name -> zip member names (None for a plain file) for one CNE5 day."""
    ymd, full = f"{d:%y%m%d}", f"{d:%Y%m%d}"
    return {
        f"CNE5_CountryHolidays.{full}": None,
        f"SMD_CNE5S_100_{ymd}.zip": [
            f"CNE5S_100_Asset_Data.{full}",
            f"CNE5S_100_Covariance.{full}",
            f"CNE5_Rates.{full}",
        ],
        f"SMD_CNE5L_100_{ymd}.zip": [
            f"CNE5L_100_Asset_Data.{full}",
            f"CNE5L_100_Covariance.{full}",
            f"CNE5_Rates.{full}",
        ],
        f"FPD_CNE5L_{ymd}.zip": [f"CNE5L_100_Asset_Exposure.{full}", "CNE5L_100_Factors.dat"],
        f"SMD_CNE5_Market_Data_{ymd}.zip": [f"CNE5_Market_Data.{full}"],
        f"SMD_CNE5L_100_UnadjCov_{ymd}.zip": [
            f"CNE5L_100_UnadjCovariance.{full}",
            f"CNE5L_100_preVRACovariance.{full}",
        ],
    }


class Remote:
    """A local directory standing in for the SFTP model directory."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.mkdir(parents=True, exist_ok=True)

    def add_day(self, d: date, *, holidays: bool = True, skip: tuple[str, ...] = ()) -> dict:
        contents = day_contents(d)
        for name, members in contents.items():
            if name.startswith("CNE5_CountryHolidays") and not holidays:
                continue
            if any(name.startswith(prefix) for prefix in skip):
                continue
            if members is None:
                (self.path / name).write_text(f"holidays {d}\n")
            else:
                self.add_zip(name, {m: f"{m} data\n" for m in members})
        return contents

    def add_zip(self, name: str, members: dict[str, str]) -> None:
        with zipfile.ZipFile(self.path / name, "w", zipfile.ZIP_DEFLATED) as zf:
            for member, text in members.items():
                zf.writestr(member, text)


class FakeFTP:
    """Implements the FtpClient protocol by copying from a Remote directory."""

    def __init__(self, remote: Remote) -> None:
        self.remote = remote
        self.downloads: list[str] = []

    def __enter__(self) -> FakeFTP:
        return self

    def __exit__(self, *exc_info: object) -> None:
        pass

    def list_dir(self, remote_dir: str) -> set[str]:
        return {p.name for p in self.remote.path.iterdir()}

    def download(self, remote_path: str, local_path: Path) -> int:
        src = self.remote.path / posixpath.basename(remote_path)
        self.downloads.append(src.name)
        shutil.copyfile(src, local_path)
        return src.stat().st_size


@pytest.fixture
def remote(tmp_path: Path) -> Remote:
    return Remote(tmp_path / "remote" / "cne5")


@pytest.fixture
def fake_ftp(remote: Remote) -> FakeFTP:
    return FakeFTP(remote)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "data"
