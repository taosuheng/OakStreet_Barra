"""End-to-end test of BarraFTP + fetch_range against a local paramiko SFTP server."""

import socket
from dataclasses import replace
from datetime import date

import paramiko
import pytest
from proxy_servers import DENIED, UNREACHABLE, HttpConnectProxy, Socks5Proxy
from sftp_server import SftpServer

from barra.download import COMPLETE, fetch_range
from barra.ftp import BarraFTP, FtpCredentials, UnknownHostKeyError
from barra.models import CNE5
from barra.proxy import Proxy, ProxyConnectionError, ProxyError

MON = date(2026, 9, 28)

# A name the test machine cannot resolve, like an SFTP host that is only reachable via the proxy.
FTP_NAME = "ftp.barra.test"

BOTH_PROXIES = pytest.mark.parametrize("kind", [HttpConnectProxy, Socks5Proxy], ids=["http", "socks5h"])


@pytest.fixture
def sftp_server(remote):
    server = SftpServer(remote.path.parent, user="barra", password="secret")
    yield server
    server.close()


@pytest.fixture
def creds(sftp_server):
    return FtpCredentials(user="barra", password="secret", host="127.0.0.1", port=sftp_server.port)


@pytest.fixture
def known_hosts(sftp_server, tmp_path):
    path = tmp_path / "known_hosts"
    path.write_text("".join(f"{sftp_server.known_hosts_line(h)}\n" for h in ("127.0.0.1", FTP_NAME)))
    return path


@pytest.fixture
def start_proxy():
    """Start a test proxy of the given kind that resolves FTP_NAME to the local SFTP server."""
    started = []

    def start(kind, **kwargs):
        started.append(kind(hosts={FTP_NAME: "127.0.0.1"}, **kwargs))
        return started[-1]

    yield start
    for server in started:
        server.close()


def test_fetch_over_real_sftp(creds, known_hosts, remote, root):
    contents = remote.add_day(MON)

    with BarraFTP(creds, known_hosts=known_hosts, timeout=10) as client:
        [r] = fetch_range(client, CNE5, MON, MON, root)

    assert r.status == COMPLETE
    assert sorted(r.downloaded) == sorted(contents)
    extracted = {p.name for p in (root / "2026").iterdir() if p.is_file()}
    assert "CNE5L_100_UnadjCovariance.20260928" in extracted
    assert "CNE5_CountryHolidays.20260928" in extracted
    assert list(root.rglob("*.zip")) == []
    assert list(root.rglob("*.part")) == []


def test_dropped_connection_reconnects_and_retries(sftp_server, creds, known_hosts, remote, tmp_path):
    remote.add_day(MON)
    name = "FPD_CNE5L_260928.zip"

    with BarraFTP(creds, known_hosts=known_hosts, timeout=10, backoff=0) as client:
        dropped = client._ssh
        dropped.get_transport().sock.shutdown(socket.SHUT_RDWR)  # simulate a dropped connection
        size = client.download(f"/cne5/{name}", tmp_path / name)
        assert client._ssh is not dropped

    assert sftp_server.connections == 2
    assert size == (remote.path / name).stat().st_size
    assert (tmp_path / name).read_bytes() == (remote.path / name).read_bytes()
    assert not (tmp_path / f"{name}.part").exists()


def test_bad_login_is_not_retried(sftp_server, creds, known_hosts):
    with pytest.raises(paramiko.AuthenticationException):
        with BarraFTP(replace(creds, password="wrong"), known_hosts=known_hosts, timeout=10, backoff=0):
            pass

    assert sftp_server.connections == 1


def test_missing_remote_dir_is_not_retried(sftp_server, creds, known_hosts):
    with BarraFTP(creds, known_hosts=known_hosts, timeout=10, backoff=0) as client:
        with pytest.raises(FileNotFoundError):
            client.list_dir("/no-such-dir/")

    assert sftp_server.connections == 1


def test_unknown_host_key_is_rejected_with_the_line_to_trust_it(sftp_server, creds, tmp_path):
    known_hosts = tmp_path / "known_hosts"  # does not exist

    with pytest.raises(UnknownHostKeyError) as exc:
        with BarraFTP(creds, known_hosts=known_hosts, timeout=10, backoff=0):
            pass

    assert sftp_server.host_key.fingerprint in str(exc.value)
    assert str(exc.value).endswith(f"\n{sftp_server.known_hosts_line()}")
    assert sftp_server.connections == 1


def test_changed_host_key_is_rejected(sftp_server, creds, tmp_path):
    other = paramiko.ECDSAKey.generate()
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text(f"[127.0.0.1]:{sftp_server.port} {other.get_name()} {other.get_base64()}\n")

    with pytest.raises(paramiko.BadHostKeyException):
        with BarraFTP(creds, known_hosts=known_hosts, timeout=10, backoff=0):
            pass

    assert sftp_server.connections == 1


@BOTH_PROXIES
def test_fetch_through_proxy(kind, start_proxy, sftp_server, creds, known_hosts, remote, root):
    contents = remote.add_day(MON)
    server = start_proxy(kind)

    with BarraFTP(
        replace(creds, host=FTP_NAME), proxy=Proxy.parse(server.url()), known_hosts=known_hosts, timeout=10
    ) as client:
        [r] = fetch_range(client, CNE5, MON, MON, root)

    assert r.status == COMPLETE
    assert sorted(r.downloaded) == sorted(contents)
    assert (root / "2026" / "CNE5L_100_UnadjCovariance.20260928").is_file()
    # One tunnel for everything, requested from the proxy by the server's name.
    assert server.targets == [(FTP_NAME, sftp_server.port)]


def test_socks5_resolves_the_host_locally(start_proxy, sftp_server, creds, known_hosts, remote, monkeypatch):
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

    with BarraFTP(
        replace(creds, host=FTP_NAME),
        proxy=Proxy.parse(server.url("socks5")),
        known_hosts=known_hosts,
        timeout=10,
    ) as client:
        assert "FPD_CNE5L_260928.zip" in client.list_dir("/cne5/")

    assert server.targets == [("127.0.0.1", sftp_server.port)]
    assert server.address_types == [1]


@BOTH_PROXIES
def test_proxy_login(kind, start_proxy, creds, known_hosts, remote):
    remote.add_day(MON)
    login = ("alice", "s3cret")
    server = start_proxy(kind, auth=login)
    proxy = Proxy.parse(server.url(auth=login))

    with BarraFTP(replace(creds, host=FTP_NAME), proxy=proxy, known_hosts=known_hosts, timeout=10) as client:
        assert "FPD_CNE5L_260928.zip" in client.list_dir("/cne5/")


@BOTH_PROXIES
@pytest.mark.parametrize("login", [None, ("alice", "wrong")], ids=["no login", "bad password"])
def test_rejected_proxy_login_is_not_retried(kind, login, start_proxy, creds, known_hosts):
    server = start_proxy(kind, auth=("alice", "s3cret"))
    proxy = Proxy.parse(server.url(auth=login))

    with pytest.raises(ProxyError) as exc:
        with BarraFTP(replace(creds, host=FTP_NAME), proxy=proxy, known_hosts=known_hosts, timeout=10, backoff=0):
            pass

    assert not isinstance(exc.value, OSError)
    assert "wrong" not in str(exc.value)
    assert server.connections == 1


@BOTH_PROXIES
def test_destination_denied_by_proxy_is_not_retried(kind, start_proxy, creds, known_hosts):
    server = start_proxy(kind, outcome=DENIED)
    proxy = Proxy.parse(server.url())

    with pytest.raises(ProxyError, match="403 Forbidden|not allowed by ruleset") as exc:
        with BarraFTP(replace(creds, host=FTP_NAME), proxy=proxy, known_hosts=known_hosts, timeout=10, backoff=0):
            pass

    assert not isinstance(exc.value, OSError)
    assert server.connections == 1


@BOTH_PROXIES
def test_destination_unreachable_from_proxy_is_retried(kind, start_proxy, creds, known_hosts):
    server = start_proxy(kind, outcome=UNREACHABLE)
    proxy = Proxy.parse(server.url())

    with pytest.raises(ProxyConnectionError, match="502 Bad Gateway|connection refused"):
        with BarraFTP(
            replace(creds, host=FTP_NAME), proxy=proxy, known_hosts=known_hosts, timeout=10, retries=3, backoff=0
        ):
            pass

    assert server.connections == 3
