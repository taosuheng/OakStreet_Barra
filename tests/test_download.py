import zipfile
from datetime import date

import pytest

from barra.download import COMPLETE, MANIFEST_DIR, NO_DATA, PARTIAL, TMP_DIR, fetch_range
from barra.ftp import FtpCredentials, MissingCredentialsError
from barra.models import CNE5

MON = date(2026, 9, 28)


def expected_files(contents: dict) -> set[str]:
    names = set()
    for name, members in contents.items():
        names.update([name] if members is None else members)
    return names


def files_in(path):
    return {p.name for p in path.iterdir() if p.is_file()}


def test_full_day_is_extracted_flat_into_year_folder(remote, fake_ftp, root):
    contents = remote.add_day(MON)

    [r] = fetch_range(fake_ftp, CNE5, MON, MON, root)

    assert r.status == COMPLETE
    assert sorted(r.downloaded) == sorted(contents)
    ydir = root / "2026"
    assert files_in(ydir) == expected_files(contents)
    assert (ydir / "CNE5L_100_Covariance.20260928").read_text() == "CNE5L_100_Covariance.20260928 data\n"
    assert list(root.rglob("*.zip")) == []
    assert list(root.rglob("*.part")) == []
    assert not (ydir / TMP_DIR).exists()
    zips = {n for n in contents if n.endswith(".zip")}
    assert files_in(ydir / MANIFEST_DIR) == {f"{z}.json" for z in zips}


def test_missing_holidays_file_does_not_fail_the_day(remote, fake_ftp, root):
    remote.add_day(MON, holidays=False)

    [r] = fetch_range(fake_ftp, CNE5, MON, MON, root)

    assert r.status == COMPLETE
    assert r.missing_optional == ["CNE5_CountryHolidays.20260928"]
    assert len(r.downloaded) == 5


def test_rerun_skips_and_force_redownloads(remote, fake_ftp, root):
    remote.add_day(MON)
    fetch_range(fake_ftp, CNE5, MON, MON, root)
    fake_ftp.downloads.clear()

    [r] = fetch_range(fake_ftp, CNE5, MON, MON, root)
    assert r.status == COMPLETE
    assert r.downloaded == [] and len(r.skipped) == 6
    assert fake_ftp.downloads == []

    [r] = fetch_range(fake_ftp, CNE5, MON, MON, root, force=True)
    assert len(r.downloaded) == 6
    assert len(fake_ftp.downloads) == 6


def test_deleted_member_triggers_redownload_of_its_zip(remote, fake_ftp, root):
    remote.add_day(MON)
    fetch_range(fake_ftp, CNE5, MON, MON, root)
    (root / "2026" / "CNE5L_100_UnadjCovariance.20260928").unlink()
    fake_ftp.downloads.clear()

    [r] = fetch_range(fake_ftp, CNE5, MON, MON, root)

    assert fake_ftp.downloads == ["SMD_CNE5L_100_UnadjCov_260928.zip"]
    assert (root / "2026" / "CNE5L_100_UnadjCovariance.20260928").is_file()
    assert len(r.skipped) == 5


def test_range_classifies_partial_and_no_data_days(remote, fake_ftp, root):
    remote.add_day(MON, skip=("FPD_",))
    sat = date(2026, 9, 26)

    results = fetch_range(fake_ftp, CNE5, sat, MON, root)

    assert [r.status for r in results] == [NO_DATA, NO_DATA, PARTIAL]
    assert results[2].missing == ["FPD_CNE5L_260928.zip"]
    assert not (root / "2026" / "CNE5L_100_Asset_Exposure.20260928").exists()


def test_zip_members_are_extracted_by_basename_only(remote, fake_ftp, root):
    remote.add_day(MON, skip=("SMD_CNE5S_",))
    remote.add_zip(
        "SMD_CNE5S_100_260928.zip",
        {"../evil": "x", "nested/dir/CNE5S_100_Asset_Data.20260928": "y", "somedir/": ""},
    )

    fetch_range(fake_ftp, CNE5, MON, MON, root)

    assert (root / "2026" / "evil").read_text() == "x"
    assert not (root / "evil").exists()
    assert (root / "2026" / "CNE5S_100_Asset_Data.20260928").read_text() == "y"
    assert not (root / "2026" / "nested").exists()
    assert not (root / "2026" / "somedir").exists()


def test_corrupt_zip_raises_and_is_not_marked_done(remote, fake_ftp, root):
    remote.add_day(MON, skip=("FPD_",))
    (remote.path / "FPD_CNE5L_260928.zip").write_bytes(b"not a zip")

    with pytest.raises(zipfile.BadZipFile):
        fetch_range(fake_ftp, CNE5, MON, MON, root)

    assert not (root / "2026" / MANIFEST_DIR / "FPD_CNE5L_260928.zip.json").exists()
    assert list(root.rglob("*.zip")) == []


def test_end_before_start_is_rejected(fake_ftp, root):
    with pytest.raises(ValueError):
        fetch_range(fake_ftp, CNE5, MON, date(2026, 9, 1), root)


def test_credentials_from_env(monkeypatch):
    monkeypatch.setenv("BARRA_FTP_USER", "alice")
    monkeypatch.setenv("BARRA_FTP_PASSWORD", "s3cret")
    monkeypatch.delenv("BARRA_FTP_HOST", raising=False)
    monkeypatch.delenv("BARRA_FTP_PORT", raising=False)

    creds = FtpCredentials.from_env()

    assert (creds.user, creds.password, creds.host, creds.port) == ("alice", "s3cret", "ftp.barra.com", 22)
    assert "s3cret" not in repr(creds)


def test_missing_credentials_names_the_variables(monkeypatch):
    monkeypatch.delenv("BARRA_FTP_USER", raising=False)
    monkeypatch.delenv("BARRA_FTP_PASSWORD", raising=False)

    with pytest.raises(MissingCredentialsError, match="BARRA_FTP_USER, BARRA_FTP_PASSWORD"):
        FtpCredentials.from_env()
