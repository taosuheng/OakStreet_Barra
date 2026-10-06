"""Minimal threaded, read-only SFTP server for the tests."""

from __future__ import annotations

import os
import socket
import threading
from pathlib import Path

import paramiko


class SftpServer:
    """Serves ``root`` over SFTP on 127.0.0.1 to one user with a password login."""

    def __init__(self, root: Path, *, user: str, password: str) -> None:
        self.root = root
        self.user = user
        self.password = password
        self.host_key = paramiko.ECDSAKey.generate()
        self.connections = 0
        self.transports: list[paramiko.Transport] = []
        self._listener = socket.create_server(("127.0.0.1", 0))
        self.port = self._listener.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def known_hosts_line(self, host: str = "127.0.0.1") -> str:
        return f"[{host}]:{self.port} {self.host_key.get_name()} {self.host_key.get_base64()}"

    def close(self) -> None:
        self._listener.close()
        for transport in self.transports:
            transport.close()

    def _serve(self) -> None:
        while True:
            try:
                sock, _ = self._listener.accept()
            except OSError:
                return
            self.connections += 1
            transport = paramiko.Transport(sock)
            transport.add_server_key(self.host_key)
            transport.set_subsystem_handler("sftp", paramiko.SFTPServer, _ReadOnlyFiles, self.root)
            self.transports.append(transport)
            # With an event, the handshake runs in the transport's thread, not this one.
            transport.start_server(event=threading.Event(), server=_Login(self.user, self.password))


class _Login(paramiko.ServerInterface):
    def __init__(self, user: str, password: str) -> None:
        self.login = (user, password)

    def get_allowed_auths(self, username):
        return "password"

    def check_auth_password(self, username, password):
        ok = (username, password) == self.login
        return paramiko.AUTH_SUCCESSFUL if ok else paramiko.AUTH_FAILED

    def check_channel_request(self, kind, chanid):
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED


class _ReadOnlyFiles(paramiko.SFTPServerInterface):
    """Maps SFTP paths onto a local directory; paths are not sandboxed, as this is test-only."""

    def __init__(self, server, root: Path) -> None:
        super().__init__(server)
        self.root = root

    def list_folder(self, path):
        try:
            return [_attributes(p) for p in self._local(path).iterdir()]
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

    def stat(self, path):
        try:
            return _attributes(self._local(path))
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)

    lstat = stat

    def open(self, path, flags, attr):
        if flags & (os.O_WRONLY | os.O_RDWR):
            return paramiko.SFTP_PERMISSION_DENIED
        try:
            fh = open(self._local(path), "rb")
        except OSError as exc:
            return paramiko.SFTPServer.convert_errno(exc.errno)
        handle = _Handle(flags)
        handle.readfile = fh
        return handle

    def _local(self, path: str) -> Path:
        return self.root / path.lstrip("/")


class _Handle(paramiko.SFTPHandle):
    def stat(self):
        return paramiko.SFTPAttributes.from_stat(os.fstat(self.readfile.fileno()))


def _attributes(path: Path) -> paramiko.SFTPAttributes:
    return paramiko.SFTPAttributes.from_stat(path.stat(), path.name)
