import socket

import pytest

from barra.proxy import Proxy, ProxyConnectionError


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://proxy.corp:8080", Proxy("http", "proxy.corp", 8080)),
        ("HTTP://Proxy.Corp:8080/", Proxy("http", "proxy.corp", 8080)),
        ("socks5://127.0.0.1:1080", Proxy("socks5", "127.0.0.1", 1080)),
        ("socks5h://alice@[::1]:1080", Proxy("socks5h", "::1", 1080, "alice")),
        (
            "http://alice:p%40ss%3Aword@proxy.corp:8080",
            Proxy("http", "proxy.corp", 8080, "alice", "p@ss:word"),
        ),
    ],
)
def test_parse(url, expected):
    assert Proxy.parse(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "",
        "proxy.corp:8080",
        "127.0.0.1:8080",
        "http://proxy.corp",
        "http://:8080",
        "http://proxy.corp:port",
        "https://proxy.corp:443",
        "socks4://proxy.corp:1080",
    ],
)
def test_parse_rejects_anything_but_scheme_host_port(url):
    with pytest.raises(ValueError, match="invalid proxy URL"):
        Proxy.parse(url)


def test_password_is_never_shown():
    proxy = Proxy.parse("http://alice:s3cret@proxy.corp:8080")

    assert str(proxy) == "http://alice:***@proxy.corp:8080"
    assert "s3cret" not in repr(proxy)
    with pytest.raises(ValueError) as exc:
        Proxy.parse("http://alice:s3cret@proxy.corp")
    assert "s3cret" not in str(exc.value)


def test_ipv6_proxy_is_shown_in_brackets():
    assert str(Proxy("socks5", "::1", 1080)) == "socks5://[::1]:1080"


def test_unreachable_proxy_is_named_in_the_error(monkeypatch):
    def refuse(*args, **kwargs):
        raise ConnectionRefusedError("connection refused")

    monkeypatch.setattr(socket, "create_connection", refuse)

    with pytest.raises(ProxyConnectionError, match=r"cannot connect to proxy socks5h://127\.0\.0\.1:1080"):
        Proxy("socks5h", "127.0.0.1", 1080).open("ftp.barra.com", 21)


def test_from_env(monkeypatch):
    monkeypatch.delenv("BARRA_FTP_PROXY", raising=False)
    assert Proxy.from_env() is None

    monkeypatch.setenv("BARRA_FTP_PROXY", "socks5h://127.0.0.1:7890")
    assert Proxy.from_env() == Proxy("socks5h", "127.0.0.1", 7890)
