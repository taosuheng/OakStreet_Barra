"""Descriptions of the Models Direct files published for each Barra model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class FileSpec:
    """One daily file on the SFTP server.

    ``pattern`` may contain ``{yymmdd}`` and/or ``{yyyymmdd}`` placeholders,
    which are filled in with the file date.
    """

    pattern: str
    optional: bool = False

    def name_for(self, d: date) -> str:
        return self.pattern.format(yymmdd=f"{d:%y%m%d}", yyyymmdd=f"{d:%Y%m%d}")

    @property
    def is_zip(self) -> bool:
        return self.pattern.lower().endswith(".zip")


@dataclass(frozen=True)
class ModelSpec:
    name: str
    remote_dir: str
    files: tuple[FileSpec, ...]

    @property
    def required(self) -> tuple[FileSpec, ...]:
        return tuple(f for f in self.files if not f.optional)


CNE5 = ModelSpec(
    name="CNE5",
    remote_dir="/cne5/",
    files=(
        FileSpec("CNE5_CountryHolidays.{yyyymmdd}", optional=True),
        FileSpec("SMD_CNE5S_100_{yymmdd}.zip"),
        FileSpec("SMD_CNE5L_100_{yymmdd}.zip"),
        FileSpec("FPD_CNE5L_{yymmdd}.zip"),
        FileSpec("SMD_CNE5_Market_Data_{yymmdd}.zip"),
        FileSpec("SMD_CNE5L_100_UnadjCov_{yymmdd}.zip"),
    ),
)

MODELS: dict[str, ModelSpec] = {m.name: m for m in (CNE5,)}
