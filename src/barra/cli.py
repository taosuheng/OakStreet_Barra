"""Command-line entry point: ``barra-download``."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import zipfile
from datetime import date, datetime
from pathlib import Path

import paramiko

from .download import COMPLETE, NO_DATA, PARTIAL, DayResult, fetch_range
from .ftp import BarraFTP, FtpCredentials, MissingCredentialsError
from .models import MODELS
from .proxy import ENV_PROXY, Proxy, ProxyError

log = logging.getLogger("barra")

ENV_DATA_ROOT = "BARRA_DATA_ROOT"


def _parse_date(s: str) -> date:
    try:
        return datetime.strptime(s, "%Y%m%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid date {s!r}, expected YYYYMMDD") from None


def _parse_proxy(s: str) -> Proxy:
    try:
        return Proxy.parse(s)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="barra-download",
        description="Download Barra Models Direct files and unzip them into <root>/<yyyy>/. "
        "SFTP credentials are read from $BARRA_FTP_USER and $BARRA_FTP_PASSWORD.",
    )
    p.add_argument(
        "--root",
        type=Path,
        default=os.environ.get(ENV_DATA_ROOT) or None,
        help=f"output folder (default: ${ENV_DATA_ROOT})",
    )
    p.add_argument("--model", default="CNE5", choices=sorted(MODELS), help="risk model (default: CNE5)")
    when = p.add_mutually_exclusive_group()
    when.add_argument("--date", type=_parse_date, help="single date YYYYMMDD (default: today)")
    when.add_argument("--start", type=_parse_date, help="first date of a range, YYYYMMDD")
    p.add_argument("--end", type=_parse_date, help="last date of a range, YYYYMMDD (default: today)")
    p.add_argument("--remote-dir", help="remote directory (default: the model's, e.g. /cne5/)")
    p.add_argument(
        "--proxy",
        type=_parse_proxy,
        default=os.environ.get(ENV_PROXY) or None,
        help="connect through a proxy: http://[user:password@]host:port (HTTP CONNECT), "
        f"socks5://... or socks5h://... (default: ${ENV_PROXY}, else a direct connection)",
    )
    p.add_argument("--force", action="store_true", help="re-download files already on disk")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.root is None:
        parser.error(f"--root is required (or set ${ENV_DATA_ROOT})")
    if args.end is not None and args.start is None:
        parser.error("--end requires --start")

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if not args.verbose:
        logging.getLogger("paramiko").setLevel(logging.WARNING)

    model = MODELS[args.model]
    remote_dir = args.remote_dir or model.remote_dir
    single = args.start is None
    if single:
        start = end = args.date or date.today()
    else:
        start, end = args.start, args.end or date.today()
        if end < start:
            parser.error(f"--end {end:%Y%m%d} is before --start {start:%Y%m%d}")

    if args.proxy is not None:
        log.info("using proxy %s", args.proxy)

    try:
        with BarraFTP(FtpCredentials.from_env(), proxy=args.proxy) as client:
            results = fetch_range(
                client, model, start, end, args.root, remote_dir=remote_dir, force=args.force
            )
    except (MissingCredentialsError, ProxyError) as exc:
        log.error("%s", exc)
        return 1
    except (paramiko.SSHException, OSError, EOFError, zipfile.BadZipFile) as exc:
        log.error("download failed: %s", exc)
        return 1

    return _report(results, single=single, model_name=model.name, remote_dir=remote_dir)


def _report(results: list[DayResult], *, single: bool, model_name: str, remote_dir: str) -> int:
    for r in results:
        if single or r.status != NO_DATA:
            print(_day_line(r))

    counts = {s: sum(r.status == s for r in results) for s in (COMPLETE, PARTIAL, NO_DATA)}
    if not single:
        print(
            f"{len(results)} days: {counts[COMPLETE]} complete, {counts[PARTIAL]} partial, "
            f"{counts[NO_DATA]} no data; "
            f"{sum(len(r.downloaded) for r in results)} files downloaded, "
            f"{sum(len(r.skipped) for r in results)} skipped"
        )

    if counts[NO_DATA] == len(results):
        print(
            f"No {model_name} files for these dates in {remote_dir}. Not published yet, "
            "or try another --remote-dir.",
            file=sys.stderr,
        )
    if single:
        return 0 if counts[COMPLETE] == 1 else 1
    return 1 if counts[PARTIAL] else 0


def _day_line(r: DayResult) -> str:
    line = f"{r.date:%Y-%m-%d} {r.status}: {len(r.downloaded)} downloaded, {len(r.skipped)} skipped"
    if r.status != NO_DATA and r.missing:
        line += f", missing {', '.join(r.missing)}"
    return line
