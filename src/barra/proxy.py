"""Tunnel the SFTP connection through an HTTP CONNECT or SOCKS5 proxy."""

from __future__ import annotations

import base64
import ipaddress
import os
import socket
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

ENV_PROXY = "BARRA_FTP_PROXY"

SCHEMES = ("http", "socks5", "socks5h")

# RFC 1928 reply codes. 2, 7 and 8 are refusals; the rest are worth retrying.
SOCKS5_REPLIES = {
    1: "general SOCKS server failure",
    2: "connection not allowed by ruleset",
    3: "network unreachable",
    4: "host unreachable",
    5: "connection refused",
    6: "TTL expired",
    7: "command not supported",
    8: "address type not supported",
}
SOCKS5_REFUSALS = (2, 7, 8)


class ProxyError(Exception):
    """The proxy refused the tunnel (bad proxy login, destination not allowed). Not retried."""


class ProxyConnectionError(ProxyError, OSError):
    """The proxy is down or cannot reach the destination. Also an OSError so it is retried."""


@dataclass(frozen=True)
class Proxy:
    """A proxy given as ``scheme://[user:password@]host:port``.

    ``http`` tunnels with HTTP CONNECT. ``socks5`` resolves the server name locally,
    ``socks5h`` leaves the lookup to the proxy.
    """

    scheme: str
    host: str
    port: int
    user: str | None = None
    password: str | None = None

    @classmethod
    def parse(cls, url: str) -> Proxy:
        # The URL may hold a password, so it is not echoed back in the message.
        error = ValueError(
            f"invalid proxy URL, expected {'|'.join(SCHEMES)}://[user:password@]host:port"
        )
        try:
            parts = urlsplit(url.strip())
            host, port = parts.hostname, parts.port
        except ValueError:
            raise error from None
        if parts.scheme not in SCHEMES or not host or not port:
            raise error
        return cls(
            scheme=parts.scheme,
            host=host,
            port=port,
            user=None if parts.username is None else unquote(parts.username),
            password=None if parts.password is None else unquote(parts.password),
        )

    @classmethod
    def from_env(cls) -> Proxy | None:
        url = os.environ.get(ENV_PROXY)
        return cls.parse(url) if url else None

    def __str__(self) -> str:
        auth = "" if self.user is None else f"{self.user}:***@"
        return f"{self.scheme}://{auth}{_hostport(self.host, self.port)}"

    def __repr__(self) -> str:
        return f"Proxy({str(self)!r})"

    def open(self, host: str, port: int, timeout: float | None = None) -> socket.socket:
        """Return a connected socket tunnelled through the proxy to ``host:port``."""
        try:
            sock = socket.create_connection((self.host, self.port), timeout)
        except OSError as exc:
            raise ProxyConnectionError(f"cannot connect to proxy {self}: {exc}") from exc
        try:
            if self.scheme == "http":
                self._http_connect(sock, host, port)
            else:
                self._socks5(sock, host, port)
        except BaseException:
            sock.close()
            raise
        return sock

    def _http_connect(self, sock: socket.socket, host: str, port: int) -> None:
        target = _hostport(host, port)
        request = [f"CONNECT {target} HTTP/1.1", f"Host: {target}"]
        if self.user is not None:
            token = base64.b64encode(f"{self.user}:{self.password or ''}".encode()).decode("ascii")
            request.append(f"Proxy-Authorization: Basic {token}")
        sock.sendall("\r\n".join([*request, "", ""]).encode())

        # Read byte by byte: whatever follows the blank line already comes from the server.
        head = b""
        while not head.endswith((b"\r\n\r\n", b"\n\n")):
            if len(head) > 1 << 16:
                raise ProxyError(f"{self} is not an HTTP proxy")
            head += _recv_exact(sock, 1)
        status = head.splitlines()[0].decode("latin-1")
        version, _, rest = status.partition(" ")
        code = rest[:3]
        if not version.startswith("HTTP/") or not code.isdigit():
            raise ProxyError(f"{self} is not an HTTP proxy")
        if code[0] == "2":
            return
        error = ProxyConnectionError if code[0] == "5" else ProxyError
        raise error(f"proxy {self} refused CONNECT {target}: {status}")

    def _socks5(self, sock: socket.socket, host: str, port: int) -> None:
        methods = b"\x00" if self.user is None else b"\x00\x02"
        sock.sendall(b"\x05" + _lv(methods))
        version, method = _recv_exact(sock, 2)
        if version != 5:
            raise ProxyError(f"{self} is not a SOCKS5 proxy")
        if method == 2 and self.user is not None:
            sock.sendall(b"\x01" + _lv(self.user.encode()) + _lv((self.password or "").encode()))
            if _recv_exact(sock, 2)[1] != 0:
                raise ProxyError(f"proxy {self} rejected the user name or password")
        elif method != 0:
            needs = "a user name and password" if self.user is None else "an unsupported login method"
            raise ProxyError(f"proxy {self} requires {needs}")

        if self.scheme == "socks5":
            host = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)[0][4][0]
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            address = b"\x03" + _lv(host.encode("idna"))
        else:
            address = (b"\x01" if ip.version == 4 else b"\x04") + ip.packed
        sock.sendall(b"\x05\x01\x00" + address + port.to_bytes(2, "big"))

        _, reply, _, bound_type = _recv_exact(sock, 4)
        if reply != 0:
            error = ProxyError if reply in SOCKS5_REFUSALS else ProxyConnectionError
            reason = SOCKS5_REPLIES.get(reply, f"reply code {reply}")
            raise error(f"proxy {self} could not connect to {_hostport(host, port)}: {reason}")
        # Skip the bound address so the stream starts at the server's first byte.
        if bound_type == 3:
            size = _recv_exact(sock, 1)[0]
        else:
            size = 16 if bound_type == 4 else 4
        _recv_exact(sock, size + 2)


def _hostport(host: str, port: int) -> str:
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def _lv(data: bytes) -> bytes:
    """A SOCKS5 field: one length byte, then the data."""
    if len(data) > 255:
        raise ProxyError("SOCKS5 names and passwords are limited to 255 bytes")
    return bytes([len(data)]) + data


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ProxyConnectionError("proxy closed the connection during the handshake")
        data += chunk
    return data
