from datetime import date

import pytest

from barra import cli
from barra.proxy import Proxy, ProxyError

MON = date(2026, 9, 28)


@pytest.fixture
def run(monkeypatch, fake_ftp, root):
    monkeypatch.setenv("BARRA_FTP_USER", "u")
    monkeypatch.setenv("BARRA_FTP_PASSWORD", "p")
    monkeypatch.delenv("BARRA_DATA_ROOT", raising=False)
    monkeypatch.delenv("BARRA_FTP_PROXY", raising=False)
    monkeypatch.setattr(cli, "BarraFTP", lambda creds, proxy=None: fake_ftp)
    return lambda *args: cli.main(["--root", str(root), *args])


def test_single_date_complete(run, remote, capsys):
    remote.add_day(MON)

    assert run("--date", "20260928") == 0
    assert "2026-09-28 complete: 6 downloaded, 0 skipped" in capsys.readouterr().out


def test_single_date_not_published_fails(run, remote, capsys):
    remote.add_day(MON)

    assert run("--date", "20260929") == 1
    out, err = capsys.readouterr()
    assert "2026-09-29 no_data" in out
    assert "No CNE5 files for these dates in /cne5/" in err


def test_single_date_partial_fails(run, remote, capsys):
    remote.add_day(MON, skip=("SMD_CNE5_Market_Data_",))

    assert run("--date", "20260928") == 1
    assert "missing SMD_CNE5_Market_Data_260928.zip" in capsys.readouterr().out


def test_default_date_is_today(run, remote, monkeypatch, root):
    class FakeDate(date):
        @classmethod
        def today(cls):
            return MON

    monkeypatch.setattr(cli, "date", FakeDate)
    remote.add_day(MON)

    assert run() == 0
    assert (root / "2026" / "CNE5L_100_Covariance.20260928").is_file()


def test_range_with_weekend_succeeds(run, remote, capsys):
    remote.add_day(date(2026, 9, 25))
    remote.add_day(MON)

    assert run("--start", "20260925", "--end", "20260928") == 0
    out = capsys.readouterr().out
    assert "2026-09-26" not in out  # weekend no_data days are not listed
    assert "4 days: 2 complete, 0 partial, 2 no data; 12 files downloaded, 0 skipped" in out


def test_range_with_partial_day_fails(run, remote):
    remote.add_day(date(2026, 9, 25), skip=("FPD_",))
    remote.add_day(MON)

    assert run("--start", "20260925", "--end", "20260928") == 1


def test_root_from_env(run, remote, monkeypatch, fake_ftp, tmp_path):
    monkeypatch.setenv("BARRA_DATA_ROOT", str(tmp_path / "env_root"))
    remote.add_day(MON)

    assert cli.main(["--date", "20260928"]) == 0
    assert (tmp_path / "env_root" / "2026" / "CNE5_Rates.20260928").is_file()


def test_missing_root_is_a_usage_error(run, monkeypatch):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--date", "20260928"])
    assert exc.value.code == 2


def test_end_without_start_is_a_usage_error(run):
    with pytest.raises(SystemExit) as exc:
        run("--end", "20260928")
    assert exc.value.code == 2


def test_missing_credentials_exit_1(run, monkeypatch, caplog):
    monkeypatch.delenv("BARRA_FTP_PASSWORD")

    assert run("--date", "20260928") == 1
    assert "BARRA_FTP_PASSWORD" in caplog.text


def test_proxy_from_flag_or_env(run, remote, monkeypatch, fake_ftp):
    seen = []
    monkeypatch.setattr(cli, "BarraFTP", lambda creds, proxy=None: seen.append(proxy) or fake_ftp)
    remote.add_day(MON)

    assert run("--date", "20260928") == 0
    monkeypatch.setenv("BARRA_FTP_PROXY", "http://env.proxy:8080")
    assert run("--date", "20260928") == 0
    assert run("--date", "20260928", "--proxy", "socks5h://flag.proxy:1080") == 0

    assert seen == [None, Proxy("http", "env.proxy", 8080), Proxy("socks5h", "flag.proxy", 1080)]


def test_invalid_proxy_is_a_usage_error(run, monkeypatch, capsys):
    with pytest.raises(SystemExit) as exc:
        run("--proxy", "proxy.corp:8080")
    assert exc.value.code == 2

    monkeypatch.setenv("BARRA_FTP_PROXY", "http://alice:s3cret@proxy.corp")
    with pytest.raises(SystemExit) as exc:
        run("--date", "20260928")
    assert exc.value.code == 2
    assert "s3cret" not in capsys.readouterr().err


def test_proxy_refusal_exit_1(run, monkeypatch, caplog):
    def refuse(creds, proxy=None):
        raise ProxyError(f"proxy {proxy} refused CONNECT ftp.barra.com:21: HTTP/1.1 403 Forbidden")

    monkeypatch.setattr(cli, "BarraFTP", refuse)

    assert run("--date", "20260928", "--proxy", "http://alice:s3cret@proxy.corp:8080") == 1
    assert "403 Forbidden" in caplog.text
    assert "s3cret" not in caplog.text
