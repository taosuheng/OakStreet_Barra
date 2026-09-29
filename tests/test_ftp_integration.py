"""End-to-end test of BarraFTP + fetch_range against a local pyftpdlib server."""

import ftplib
import logging
import socket
import threading
from datetime import date

import pytest

pytest.importorskip("pyftpdlib")

from pyftpdlib.authorizers import DummyAuthorizer  # noqa: E402
from pyftpdlib.handlers import FTPHandler  # noqa: E402
from pyftpdlib.servers import FTPServer  # noqa: E402

from barra.download import COMPLETE, fetch_range  # noqa: E402
from barra.ftp import BarraFTP, FtpCredentials  # noqa: E402
from barra.models import CNE5  # noqa: E402

MON = date(2026, 9, 28)


@pytest.fixture
def ftp_server(remote):
    logging.getLogger("pyftpdlib").setLevel(logging.WARNING)
    authorizer = DummyAuthorizer()
    authorizer.add_user("barra", "secret", str(remote.path.parent), perm="elr")
    handler = type("Handler", (FTPHandler,), {"authorizer": authorizer, "auth_failed_timeout": 0})
    server = FTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"timeout": 0.05}, daemon=True)
    thread.start()
    yield FtpCredentials(user="barra", password="secret", host="127.0.0.1", port=server.address[1])
    server.close_all()
    thread.join(timeout=5)


def test_fetch_over_real_ftp(ftp_server, remote, root):
    contents = remote.add_day(MON)

    with BarraFTP(ftp_server, timeout=10) as client:
        [r] = fetch_range(client, CNE5, MON, MON, root)

    assert r.status == COMPLETE
    assert sorted(r.downloaded) == sorted(contents)
    extracted = {p.name for p in (root / "2026").iterdir() if p.is_file()}
    assert "CNE5L_100_UnadjCovariance.20260928" in extracted
    assert "CNE5_CountryHolidays.20260928" in extracted
    assert list(root.rglob("*.zip")) == []
    assert list(root.rglob("*.part")) == []


def test_dropped_connection_reconnects_and_retries(ftp_server, remote, tmp_path):
    remote.add_day(MON)
    name = "FPD_CNE5L_260928.zip"

    with BarraFTP(ftp_server, timeout=10, backoff=0) as client:
        dropped = client._ftp
        dropped.sock.shutdown(socket.SHUT_RDWR)  # simulate a dropped control connection
        size = client.download(f"/cne5/{name}", tmp_path / name)
        assert client._ftp is not dropped

    assert size == (remote.path / name).stat().st_size
    assert (tmp_path / name).read_bytes() == (remote.path / name).read_bytes()
    assert not (tmp_path / f"{name}.part").exists()


def test_bad_login_is_not_retried(ftp_server):
    creds = FtpCredentials(user="barra", password="wrong", host=ftp_server.host, port=ftp_server.port)

    with pytest.raises(ftplib.error_perm):
        with BarraFTP(creds, timeout=10, backoff=0):
            pass
