"""Minimal threaded HTTP CONNECT and SOCKS5 proxies for the tests."""

from __future__ import annotations

import base64
import select
import socket
import threading

OK = "ok"
DENIED = "denied"
UNREACHABLE = "unreachable"


class TunnelProxy:
    """Accepts tunnel requests on 127.0.0.1 and records every requested target."""

    scheme: str

    def __init__(
        self,
        *,
        auth: tuple[str, str] | None = None,
        hosts: dict[str, str] | None = None,
        outcome: str = OK,
    ) -> None:
        self.auth = auth
        self.hosts = hosts or {}  # names only this proxy can resolve
        self.outcome = outcome  # answer every request with this instead of connecting
        self.targets: list[tuple[str, int]] = []
        self.connections = 0
        self._listener = socket.create_server(("127.0.0.1", 0))
        self.port = self._listener.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def url(self, scheme: str | None = None, auth: tuple[str, str] | None = None) -> str:
        login = f"{auth[0]}:{auth[1]}@" if auth else ""
        return f"{scheme or self.scheme}://{login}127.0.0.1:{self.port}"

    def close(self) -> None:
        self._listener.close()

    def negotiate(self, client: socket.socket) -> tuple[str, int] | None:
        """Read the tunnel request; return its target, or None if it was rejected."""
        raise NotImplementedError

    def reply(self, client: socket.socket, outcome: str) -> None:
        raise NotImplementedError

    def _serve(self) -> None:
        while True:
            try:
                client, _ = self._listener.accept()
            except OSError:
                return
            self.connections += 1
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def _handle(self, client: socket.socket) -> None:
        with client:
            try:
                target = self.negotiate(client)
                if target is None:
                    return
                self.targets.append(target)
                if self.outcome != OK:
                    self.reply(client, self.outcome)
                    return
                host, port = target
                try:
                    upstream = socket.create_connection((self.hosts.get(host, host), port), 10)
                except OSError:
                    self.reply(client, UNREACHABLE)
                    return
                with upstream:
                    self.reply(client, OK)
                    _relay(client, upstream)
            except (OSError, EOFError):
                pass


class HttpConnectProxy(TunnelProxy):
    scheme = "http"

    def negotiate(self, client):
        head = b""
        while not head.endswith(b"\r\n\r\n"):
            head += _recv(client, 1)
        request, *headers = head.decode("latin-1").split("\r\n")
        _, target, _ = request.split()
        if self.auth is not None:
            token = base64.b64encode(":".join(self.auth).encode()).decode()
            if f"Proxy-Authorization: Basic {token}" not in headers:
                client.sendall(
                    b"HTTP/1.1 407 Proxy Authentication Required\r\n"
                    b'Proxy-Authenticate: Basic realm="test"\r\n\r\n'
                )
                return None
        host, _, port = target.rpartition(":")
        return host, int(port)

    def reply(self, client, outcome):
        status = {
            OK: "200 Connection established",
            DENIED: "403 Forbidden",
            UNREACHABLE: "502 Bad Gateway",
        }[outcome]
        client.sendall(f"HTTP/1.1 {status}\r\n\r\n".encode())


class Socks5Proxy(TunnelProxy):
    scheme = "socks5h"

    def __init__(self, **kwargs) -> None:
        self.address_types: list[int] = []
        super().__init__(**kwargs)

    def negotiate(self, client):
        methods = _recv(client, _recv(client, 2)[1])
        if self.auth is None:
            client.sendall(b"\x05\x00")
        elif 2 not in methods:
            client.sendall(b"\x05\xff")
            return None
        else:
            client.sendall(b"\x05\x02")
            user = _recv(client, _recv(client, 2)[1]).decode()
            password = _recv(client, _recv(client, 1)[0]).decode()
            ok = (user, password) == self.auth
            client.sendall(b"\x01\x00" if ok else b"\x01\x01")
            if not ok:
                return None

        address_type = _recv(client, 4)[3]
        self.address_types.append(address_type)
        if address_type == 1:
            host = socket.inet_ntop(socket.AF_INET, _recv(client, 4))
        elif address_type == 4:
            host = socket.inet_ntop(socket.AF_INET6, _recv(client, 16))
        else:
            host = _recv(client, _recv(client, 1)[0]).decode()
        return host, int.from_bytes(_recv(client, 2), "big")

    def reply(self, client, outcome):
        code = {OK: 0, DENIED: 2, UNREACHABLE: 5}[outcome]
        client.sendall(bytes([5, code, 0, 1]) + bytes(6))


def _recv(sock: socket.socket, n: int) -> bytes:
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise EOFError
        data += chunk
    return data


def _relay(a: socket.socket, b: socket.socket) -> None:
    while True:
        readable, _, _ = select.select([a, b], [], [], 30)
        if not readable:
            return
        for src in readable:
            data = src.recv(1 << 16)
            if not data:
                return
            (b if src is a else a).sendall(data)
