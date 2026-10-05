"""Thin FTP client for the Barra Models Direct server."""

from __future__ import annotations

import ftplib
import logging
import os
import posixpath
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TypeVar

from .proxy import ProxiedFTP, Proxy

log = logging.getLogger(__name__)

T = TypeVar("T")

ENV_USER = "BARRA_FTP_USER"
ENV_PASSWORD = "BARRA_FTP_PASSWORD"
ENV_HOST = "BARRA_FTP_HOST"
ENV_PORT = "BARRA_FTP_PORT"
DEFAULT_HOST = "ftp.barra.com"


class MissingCredentialsError(RuntimeError):
    pass


class IncompleteDownloadError(OSError):
    """Local size does not match the server's SIZE. Subclasses OSError so it is retried."""


# Errors worth reconnecting and retrying for. ftplib.error_perm (e.g. 530 bad login,
# 550 no such file) is deliberately excluded.
TRANSIENT_ERRORS = (ftplib.error_temp, OSError, EOFError)


@dataclass(frozen=True)
class FtpCredentials:
    user: str
    password: str
    host: str = DEFAULT_HOST
    port: int = 21

    @classmethod
    def from_env(cls) -> FtpCredentials:
        missing = [v for v in (ENV_USER, ENV_PASSWORD) if not os.environ.get(v)]
        if missing:
            raise MissingCredentialsError(
                f"FTP credentials not set: define environment variable(s) {', '.join(missing)}"
            )
        return cls(
            user=os.environ[ENV_USER],
            password=os.environ[ENV_PASSWORD],
            host=os.environ.get(ENV_HOST) or DEFAULT_HOST,
            port=int(os.environ.get(ENV_PORT) or 21),
        )

    def __repr__(self) -> str:
        return f"FtpCredentials(user={self.user!r}, host={self.host!r}, port={self.port})"


class BarraFTP:
    """Context-managed FTP session that reconnects and retries on transient errors.

    With ``proxy`` set, the control and data connections are tunnelled through it.
    """

    def __init__(
        self,
        credentials: FtpCredentials,
        *,
        proxy: Proxy | None = None,
        timeout: float = 60.0,
        retries: int = 3,
        backoff: float = 5.0,
    ) -> None:
        self.credentials = credentials
        self.proxy = proxy
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self._ftp: ftplib.FTP | None = None

    def __enter__(self) -> BarraFTP:
        self._call(lambda ftp: None)  # connect eagerly so bad credentials fail fast
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        if self._ftp is None:
            return
        try:
            self._ftp.quit()
        except Exception:
            self._ftp.close()
        self._ftp = None

    def list_dir(self, remote_dir: str) -> set[str]:
        """Return the base names of all entries in ``remote_dir``."""

        def _nlst(ftp: ftplib.FTP) -> list[str]:
            try:
                return ftp.nlst(remote_dir)
            except ftplib.error_perm as exc:
                # Some servers answer NLST on an empty directory with "550 No files found".
                if str(exc).startswith("550") and "no files" in str(exc).lower():
                    return []
                raise

        return {posixpath.basename(n.rstrip("/")) for n in self._call(_nlst)}

    def download(self, remote_path: str, local_path: Path) -> int:
        """Download ``remote_path`` to ``local_path`` atomically; return its size in bytes."""
        local_path = Path(local_path)
        part = local_path.with_name(local_path.name + ".part")

        def _retr(ftp: ftplib.FTP) -> int:
            ftp.voidcmd("TYPE I")
            expected = ftp.size(remote_path)
            with open(part, "wb") as fh:
                ftp.retrbinary(f"RETR {remote_path}", fh.write, blocksize=1 << 16)
            actual = part.stat().st_size
            if expected is not None and actual != expected:
                raise IncompleteDownloadError(
                    f"{remote_path}: got {actual} bytes, server reported {expected}"
                )
            os.replace(part, local_path)
            return actual

        try:
            return self._call(_retr)
        finally:
            part.unlink(missing_ok=True)

    def _connect(self) -> None:
        c = self.credentials
        if self.proxy is None:
            log.debug("connecting to %s:%s as %s", c.host, c.port, c.user)
            ftp = ftplib.FTP(timeout=self.timeout)
        else:
            log.debug("connecting to %s:%s as %s via %s", c.host, c.port, c.user, self.proxy)
            ftp = ProxiedFTP(self.proxy, timeout=self.timeout)
        try:
            ftp.connect(c.host, c.port)
            ftp.login(c.user, c.password)
            ftp.set_pasv(True)
        except BaseException:
            ftp.close()
            raise
        self._ftp = ftp

    def _call(self, fn: Callable[[ftplib.FTP], T]) -> T:
        for attempt in range(1, self.retries + 1):
            try:
                if self._ftp is None:
                    self._connect()
                assert self._ftp is not None
                return fn(self._ftp)
            except TRANSIENT_ERRORS as exc:
                if self._ftp is not None:
                    self._ftp.close()
                    self._ftp = None
                if attempt == self.retries:
                    raise
                delay = self.backoff * attempt
                log.warning("FTP error (%s); retry %d/%d in %.0fs", exc, attempt, self.retries - 1, delay)
                time.sleep(delay)
        raise AssertionError("unreachable")
