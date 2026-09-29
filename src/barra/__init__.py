"""Utilities to download and process Barra risk model files."""

from .download import DayResult, fetch_day, fetch_range
from .ftp import BarraFTP, FtpCredentials, MissingCredentialsError
from .models import CNE5, MODELS, FileSpec, ModelSpec

__version__ = "0.1.0"

__all__ = [
    "BarraFTP",
    "CNE5",
    "DayResult",
    "FileSpec",
    "FtpCredentials",
    "MODELS",
    "MissingCredentialsError",
    "ModelSpec",
    "fetch_day",
    "fetch_range",
]
