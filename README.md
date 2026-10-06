# barra

Utilities to download and process Barra risk model files from MSCI Models Direct over SFTP.
CNE5 (Barra China Equity Model) is the first supported model.

## Install

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

The only runtime dependency is `paramiko`, for SFTP. `pytest` is needed for tests only.

## Configuration

| Variable | Purpose |
| --- | --- |
| `BARRA_FTP_USER` | SFTP user name (required) |
| `BARRA_FTP_PASSWORD` | SFTP password (required) |
| `BARRA_FTP_HOST` | SFTP host, default `ftp.barra.com` |
| `BARRA_FTP_PORT` | SFTP port, default `22` |
| `BARRA_FTP_PROXY` | Proxy URL, used when `--proxy` is not given (see [Proxy](#proxy)) |
| `BARRA_DATA_ROOT` | Output folder, used when `--root` is not given |

## Usage

```bash
barra-download --root /data/barra                                  # today
barra-download --root /data/barra --date 20260928                  # one day
barra-download --root /data/barra --start 20260901 --end 20260928  # a range
barra-download --root /data/barra --start 20260901                 # 1 Sep through today
```

Other options:
- `--remote-dir`: override the remote folder (default `/cne5/`).
- `--proxy`: connect through a proxy (see [Proxy](#proxy)).
- `--force`: re-download files that are already on disk.
- `-v`: debug logging.
- `python -m barra` works the same as `barra-download`.

For each date, CNE5 fetches:

- `CNE5_CountryHolidays.yyyymmdd` (if it exists)
- `SMD_CNE5S_100_yymmdd.zip`
- `SMD_CNE5L_100_yymmdd.zip`
- `FPD_CNE5L_yymmdd.zip`
- `SMD_CNE5_Market_Data_yymmdd.zip`
- `SMD_CNE5L_100_UnadjCov_yymmdd.zip`

### Server host key

The server's SSH host key must be in `~/.ssh/known_hosts` of the user running the download. An unknown or changed key stops the run with exit code `1`, before the password is sent. The error for an unknown key shows its fingerprint and the line to add to `known_hosts`. Check the fingerprint with MSCI, then add the line, or fetch the key with OpenSSH:

```bash
ssh-keyscan -p 22 ftp.barra.com >> ~/.ssh/known_hosts
```

`ssh-keyscan` connects directly, so behind a proxy use the line from the error message instead.

### Proxy

The connection is direct unless a proxy is given with `--proxy` or `BARRA_FTP_PROXY`. The system-wide `ftp_proxy` / `all_proxy` variables are not read.

```bash
barra-download --root /data/barra --proxy http://proxy.corp:8080       # HTTP CONNECT
barra-download --root /data/barra --proxy socks5h://127.0.0.1:1080     # SOCKS5, proxy resolves ftp.barra.com
barra-download --root /data/barra --proxy socks5://127.0.0.1:1080      # SOCKS5, name resolved locally
```

The URL is `scheme://[user:password@]host:port`; the port is required. Percent-encode special characters in the user name or password (`@` is `%40`). Put a URL with a password in `BARRA_FTP_PROXY` rather than on the command line, where other users of the machine can see it. The password is never logged.

Everything goes over a single SSH connection, so the proxy only has to allow tunnels to the server's SFTP port (`22`). Many HTTP proxies only allow `CONNECT` to port 443 and answer `403`; a SOCKS5 proxy is then the alternative.

A proxy that rejects the login or the destination ends the run immediately with exit code `1`. A proxy that is down, or that cannot reach the server, is retried like any other network failure.

### Output layout

Every zip is extracted flat into `<root>/<yyyy>/`, where `yyyy` is the year of the file date. The zip is then deleted. For each zip, a small manifest is written to `<root>/<yyyy>/.manifest/<zip>.json`. A re-run skips a zip if its manifest exists and all of its extracted files are still present, so re-running a range is cheap and resumes after an interruption.

Some files are shared by the S and L zips, e.g. `CNE5_Rates` and `CNE5_Daily_Asset_Price`. They are identical, so the second copy just overwrites the first.

### Exit codes

- **Single date** (including the default, today): `0` only if all required files were found. If the date isn't published yet, the exit code is `1`.
- **Range:** `0` unless a day was *partial* (some required files present, others missing) or an error occurred. Days with no files at all, such as weekends and holidays, are fine.
- **Errors:** bad credentials, an unknown or changed host key, network failures and corrupt zips always exit `1`. Re-running resumes where the failed run stopped.

### Scheduling

Example crontab entry that runs every weekday at 20:30:

```cron
30 20 * * 1-5  BARRA_FTP_USER=... BARRA_FTP_PASSWORD=... BARRA_DATA_ROOT=/data/barra /path/to/.venv/bin/barra-download >> /var/log/barra.log 2>&1
```

## Library use

```python
from datetime import date
from barra import CNE5, BarraFTP, FtpCredentials, fetch_range

with BarraFTP(FtpCredentials.from_env()) as ftp:
    results = fetch_range(ftp, CNE5, date(2026, 9, 1), date(2026, 9, 28), "/data/barra")
for r in results:
    print(r.date, r.status, r.missing)
```

To go through a proxy, pass `proxy=Proxy.parse("socks5h://127.0.0.1:1080")` (or `proxy=Proxy.from_env()`, which reads `BARRA_FTP_PROXY`) to `BarraFTP`; import `Proxy` from `barra`. Pass `known_hosts=` to use another known_hosts file.

To add a model, define another `ModelSpec` in `src/barra/models.py` and register it in `MODELS`.

## Tests

```bash
.venv/bin/pytest
```

`tests/test_sftp_integration.py` starts a local paramiko SFTP server (`tests/sftp_server.py`) and exercises the real SFTP path, including reconnecting after a dropped connection and the host key checks. It also runs the download through local HTTP CONNECT and SOCKS5 proxies (`tests/proxy_servers.py`).
