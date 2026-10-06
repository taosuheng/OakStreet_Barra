"""Thin SFTP client for the Barra Models Direct server."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TypeVar

import paramiko

from .proxy import Proxy

log = logging.getLogger(__name__)

T = TypeVar("T")

ENV_USER = "BARRA_FTP_USER"
ENV_PASSWORD = "BARRA_FTP_PASSWORD"
ENV_HOST = "BARRA_FTP_HOST"
ENV_PORT = "BARRA_FTP_PORT"
DEFAULT_HOST = "ftp.barra.com"
DEFAULT_PORT = 22
DEFAULT_KNOWN_HOSTS = "~/.ssh/known_hosts"


class MissingCredentialsError(RuntimeError):
    pass


class IncompleteDownloadError(OSError):
    """Local size does not match the server's. Subclasses OSError so it is retried."""


class UnknownHostKeyError(paramiko.SSHException):
    """The server's host key is not in known_hosts."""


# Errors worth reconnecting and retrying for: network failures and dropped connections.
TRANSIENT_ERRORS = (OSError, EOFError, paramiko.SSHException)
# Except these, which a retry cannot fix: a bad login, an unknown or changed host key,
# and "no such file" / "permission denied".
PERMANENT_ERRORS = (
    paramiko.AuthenticationException,
    paramiko.BadHostKeyException,
    UnknownHostKeyError,
    FileNotFoundError,
    PermissionError,
)


@dataclass(frozen=True)
class FtpCredentials:
    user: str
    password: str
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT

    @classmethod
    def from_env(cls) -> FtpCredentials:
        missing = [v for v in (ENV_USER, ENV_PASSWORD) if not os.environ.get(v)]
        if missing:
            raise MissingCredentialsError(
                f"SFTP credentials not set: define environment variable(s) {', '.join(missing)}"
            )
        return cls(
            user=os.environ[ENV_USER],
            password=os.environ[ENV_PASSWORD],
            host=os.environ.get(ENV_HOST) or DEFAULT_HOST,
            port=int(os.environ.get(ENV_PORT) or DEFAULT_PORT),
        )

    def __repr__(self) -> str:
        return f"FtpCredentials(user={self.user!r}, host={self.host!r}, port={self.port})"


class BarraFTP:
    """Context-managed SFTP session that reconnects and retries on transient errors.

    The server's host key must be listed in ``known_hosts`` (OpenSSH format, default
    ``~/.ssh/known_hosts``). With ``proxy`` set, the connection is tunnelled through it.
    """

    def __init__(
        self,
        credentials: FtpCredentials,
        *,
        proxy: Proxy | None = None,
        known_hosts: str | Path = DEFAULT_KNOWN_HOSTS,
        timeout: float = 60.0,
        retries: int = 3,
        backoff: float = 5.0,
    ) -> None:
        self.credentials = credentials
        self.proxy = proxy
        self.known_hosts = Path(known_hosts).expanduser()
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self._ssh: paramiko.SSHClient | None = None
        self._sftp: paramiko.SFTPClient | None = None

    def __enter__(self) -> BarraFTP:
        self._call(lambda sftp: None)  # connect eagerly so bad credentials fail fast
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        if self._ssh is not None:
            self._ssh.close()  # also ends the SFTP session
        self._ssh = self._sftp = None

    def list_dir(self, remote_dir: str) -> set[str]:
        """Return the names of all entries in ``remote_dir``."""
        return set(self._call(lambda sftp: sftp.listdir(remote_dir)))

    def download(self, remote_path: str, local_path: Path) -> int:
        """Download ``remote_path`` to ``local_path`` atomically; return its size in bytes."""
        local_path = Path(local_path)
        part = local_path.with_name(local_path.name + ".part")

        def _get(sftp: paramiko.SFTPClient) -> int:
            expected = sftp.stat(remote_path).st_size
            with open(part, "wb") as fh:
                sftp.getfo(remote_path, fh)
            actual = part.stat().st_size
            if expected is not None and actual != expected:
                raise IncompleteDownloadError(
                    f"{remote_path}: got {actual} bytes, server reported {expected}"
                )
            os.replace(part, local_path)
            return actual

        try:
            return self._call(_get)
        finally:
            part.unlink(missing_ok=True)

    def _connect(self) -> None:
        c = self.credentials
        via = "" if self.proxy is None else f" via {self.proxy}"
        log.debug("connecting to %s:%s as %s%s", c.host, c.port, c.user, via)
        ssh = paramiko.SSHClient()
        if self.known_hosts.is_file():
            ssh.load_system_host_keys(str(self.known_hosts))
        ssh.set_missing_host_key_policy(_RejectUnknownHostKey(self.known_hosts))
        try:
            sock = None if self.proxy is None else self.proxy.open(c.host, c.port, self.timeout)
            ssh.connect(
                c.host,
                c.port,
                username=c.user,
                password=c.password,
                sock=sock,
                timeout=self.timeout,
                banner_timeout=self.timeout,
                auth_timeout=self.timeout,
                channel_timeout=self.timeout,
                allow_agent=False,
                look_for_keys=False,
            )
            sftp = ssh.open_sftp()
            sftp.get_channel().settimeout(self.timeout)
        except BaseException:
            ssh.close()
            raise
        self._ssh, self._sftp = ssh, sftp

    def _call(self, fn: Callable[[paramiko.SFTPClient], T]) -> T:
        for attempt in range(1, self.retries + 1):
            try:
                if self._sftp is None:
                    self._connect()
                assert self._sftp is not None
                return fn(self._sftp)
            except PERMANENT_ERRORS:
                raise
            except TRANSIENT_ERRORS as exc:
                self.close()
                if attempt == self.retries:
                    raise
                delay = self.backoff * attempt
                log.warning("SFTP error (%s); retry %d/%d in %.0fs", exc, attempt, self.retries - 1, delay)
                time.sleep(delay)
        raise AssertionError("unreachable")


class _RejectUnknownHostKey(paramiko.MissingHostKeyPolicy):
    """Refuse servers missing from known_hosts, saying how to trust one after checking it."""

    def __init__(self, known_hosts: Path) -> None:
        self.known_hosts = known_hosts

    def missing_host_key(self, client, hostname, key):
        raise UnknownHostKeyError(
            f"{hostname} is not in {self.known_hosts}; its {key.get_name()} host key fingerprint "
            f"is {key.fingerprint}. If that is the server's key, add this line to "
            f"{self.known_hosts}:\n{hostname} {key.get_name()} {key.get_base64()}"
        )
