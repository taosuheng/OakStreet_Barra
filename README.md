# barra

Utilities to download and process Barra risk model files from the MSCI Models Direct FTP.
CNE5 (Barra China Equity Model) is the first supported model.

## Install

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

The runtime uses only the standard library. `pytest` and `pyftpdlib` are needed for tests only.

## Configuration

| Variable | Purpose |
| --- | --- |
| `BARRA_FTP_USER` | FTP user name (required) |
| `BARRA_FTP_PASSWORD` | FTP password (required) |
| `BARRA_FTP_HOST` | FTP host, default `ftp.barra.com` |
| `BARRA_FTP_PORT` | FTP port, default `21` |
| `BARRA_DATA_ROOT` | Output folder, used when `--root` is not given |

## Usage

```bash
barra-download --root /data/barra                                  # today
barra-download --root /data/barra --date 20260928                  # one day
barra-download --root /data/barra --start 20260901 --end 20260928  # a range
barra-download --root /data/barra --start 20260901                 # 1 Sep through today
```

Other options:
- `--remote-dir`: override the FTP folder (default `/cne5/`).
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

### Output layout

Every zip is extracted flat into `<root>/<yyyy>/`, where `yyyy` is the year of the file date. The zip is then deleted. For each zip, a small manifest is written to `<root>/<yyyy>/.manifest/<zip>.json`. A re-run skips a zip if its manifest exists and all of its extracted files are still present, so re-running a range is cheap and resumes after an interruption.

Some files are shared by the S and L zips, e.g. `CNE5_Rates` and `CNE5_Daily_Asset_Price`. They are identical, so the second copy just overwrites the first.

### Exit codes

- **Single date** (including the default, today): `0` only if all required files were found. If the date isn't published yet, the exit code is `1`.
- **Range:** `0` unless a day was *partial* (some required files present, others missing) or an error occurred. Days with no files at all, such as weekends and holidays, are fine.
- **Errors:** bad credentials, network failures and corrupt zips always exit `1`. Re-running resumes where the failed run stopped.

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

To add a model, define another `ModelSpec` in `src/barra/models.py` and register it in `MODELS`.

## Tests

```bash
.venv/bin/pytest
```

`tests/test_ftp_integration.py` starts a local `pyftpdlib` server and exercises the real `ftplib` path, including reconnecting after a dropped connection.
