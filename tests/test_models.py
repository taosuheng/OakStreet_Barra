from datetime import date

from barra.download import year_dir
from barra.models import CNE5, FileSpec


def test_name_for_fills_short_and_long_dates():
    d = date(2026, 9, 28)
    assert FileSpec("SMD_CNE5S_100_{yymmdd}.zip").name_for(d) == "SMD_CNE5S_100_260928.zip"
    assert FileSpec("CNE5_CountryHolidays.{yyyymmdd}").name_for(d) == "CNE5_CountryHolidays.20260928"


def test_is_zip():
    assert FileSpec("FPD_CNE5L_{yymmdd}.zip").is_zip
    assert not FileSpec("CNE5_CountryHolidays.{yyyymmdd}").is_zip


def test_cne5_file_list():
    d = date(2026, 9, 28)
    assert [f.name_for(d) for f in CNE5.files] == [
        "CNE5_CountryHolidays.20260928",
        "SMD_CNE5S_100_260928.zip",
        "SMD_CNE5L_100_260928.zip",
        "FPD_CNE5L_260928.zip",
        "SMD_CNE5_Market_Data_260928.zip",
        "SMD_CNE5L_100_UnadjCov_260928.zip",
    ]
    assert [f.optional for f in CNE5.files] == [True] + [False] * 5
    assert CNE5.remote_dir == "/cne5/"


def test_year_folder_uses_file_date(tmp_path):
    assert year_dir(tmp_path, date(2025, 12, 31)) == tmp_path / "2025"
    assert year_dir(tmp_path, date(2026, 1, 2)) == tmp_path / "2026"
