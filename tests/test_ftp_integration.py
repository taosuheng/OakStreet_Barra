"""End-to-end test of BarraFTP + fetch_range against a local pyftpdlib server."""

import ftplib
import logging
import socket
import threading
from dataclasses import replace
from datetime import date

import pytest

pytest.importorskip("pyftpdlib")

from proxy_servers import DENIED, UNREACHABLE, HttpConnectProxy, Socks5Proxy  # noqa: E402
from pyftpdlib.authorizers import DummyAuthorizer  # noqa: E402
from pyftpdlib.handlers import FTPHandler  # noqa: E402
from pyftpdlib.servers import FTPServer  # noqa: E402

from barra.download import COMPLETE, fetch_range  # noqa: E402
from barra.ftp import BarraFTP, FtpCredentials  # noqa: E402
from barra.models import CNE5  # noqa: E402
from barra.proxy import Proxy, ProxyConnectionError, ProxyError  # noqa: E402

MON = date(2026, 9, 28)

# A name the test machine cannot resolve, like an FTP host that is only reachable via the proxy.
FTP_NAME = "ftp.barra.test"

BOTH_PROXIES = pytest.mark.parametrize("kind", [HttpConnectProxy, Socks5Proxy], ids=["http", "socks5h"])


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


@pytest.fixture
def start_proxy():
    """Start a test proxy of the given kind that resolves FTP_NAME to the local FTP server."""
    started = []

    def start(kind, **kwargs):
        started.append(kind(hosts={FTP_NAME: "127.0.0.1"}, **kwargs))
        return started[-1]

    yield start
    for server in started:
        server.close()


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


@BOTH_PROXIES
def test_fetch_through_proxy(kind, start_proxy, ftp_server, remote, root):
    contents = remote.add_day(MON)
    server = start_proxy(kind)
    creds = replace(ftp_server, host=FTP_NAME)

    with BarraFTP(creds, proxy=Proxy.parse(server.url()), timeout=10) as client:
        [r] = fetch_range(client, CNE5, MON, MON, root)

    assert r.status == COMPLETE
    assert sorted(r.downloaded) == sorted(contents)
    assert (root / "2026" / "CNE5L_100_UnadjCovariance.20260928").is_file()
    # The control connection, then a data connection for the listing and for each file,
    # all requested from the proxy by the FTP server's name.
    control, *data = server.targets
    assert control == (FTP_NAME, ftp_server.port)
    assert len(data) == 1 + len(contents)
    assert all(host == FTP_NAME and port != ftp_server.port for host, port in data)


def test_socks5_resolves_the_ftp_host_locally(start_proxy, ftp_server, remote, monkeypatch):
    remote.add_day(MON)
    server = start_proxy(Socks5Proxy)
    getaddrinfo = socket.getaddrinfo
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, *args, **kwargs: getaddrinfo(
            "127.0.0.1" if host == FTP_NAME else host, *args, **kwargs
        ),
    )
    creds = replace(ftp_server, host=FTP_NAME)

    with BarraFTP(creds, proxy=Proxy.parse(server.url("socks5")), timeout=10) as client:
        assert "FPD_CNE5L_260928.zip" in client.list_dir("/cne5/")

    assert [host for host, _ in server.targets] == ["127.0.0.1", "127.0.0.1"]
    assert server.address_types == [1, 1]


@BOTH_PROXIES
def test_proxy_login(kind, start_proxy, ftp_server, remote):
    remote.add_day(MON)
    login = ("alice", "s3cret")
    server = start_proxy(kind, auth=login)
    creds = replace(ftp_server, host=FTP_NAME)

    with BarraFTP(creds, proxy=Proxy.parse(server.url(auth=login)), timeout=10) as client:
        assert "FPD_CNE5L_260928.zip" in client.list_dir("/cne5/")


@BOTH_PROXIES
@pytest.mark.parametrize("login", [None, ("alice", "wrong")], ids=["no login", "bad password"])
def test_rejected_proxy_login_is_not_retried(kind, login, start_proxy, ftp_server):
    server = start_proxy(kind, auth=("alice", "s3cret"))
    creds = replace(ftp_server, host=FTP_NAME)

    with pytest.raises(ProxyError) as exc:
        with BarraFTP(creds, proxy=Proxy.parse(server.url(auth=login)), timeout=10, backoff=0):
            pass

    assert not isinstance(exc.value, OSError)
    assert "wrong" not in str(exc.value)
    assert server.connections == 1


@BOTH_PROXIES
def test_destination_denied_by_proxy_is_not_retried(kind, start_proxy, ftp_server):
    server = start_proxy(kind, outcome=DENIED)
    creds = replace(ftp_server, host=FTP_NAME)

    with pytest.raises(ProxyError, match="403 Forbidden|not allowed by ruleset") as exc:
        with BarraFTP(creds, proxy=Proxy.parse(server.url()), timeout=10, backoff=0):
            pass

    assert not isinstance(exc.value, OSError)
    assert server.connections == 1


@BOTH_PROXIES
def test_destination_unreachable_from_proxy_is_retried(kind, start_proxy, ftp_server):
    server = start_proxy(kind, outcome=UNREACHABLE)
    creds = replace(ftp_server, host=FTP_NAME)

    with pytest.raises(ProxyConnectionError, match="502 Bad Gateway|connection refused"):
        with BarraFTP(creds, proxy=Proxy.parse(server.url()), timeout=10, retries=3, backoff=0):
            pass

    assert server.connections == 3
