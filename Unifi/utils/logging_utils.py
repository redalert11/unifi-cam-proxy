import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def _build_formatter():
    return logging.Formatter(
        "%(asctime)s [%(threadName)s] [%(levelname)s] %(name)s: %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )

def setup_logger(name="camera_app", level=logging.DEBUG):
    """
    Create or retrieve a logger with the specified name and level.
    Ensures each logger only has one handler.
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if logger.handlers:
        return logger

    root_logger = logging.getLogger()
    if logger is not root_logger and root_logger.handlers:
        logger.propagate = True
        return logger

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_build_formatter())
    logger.addHandler(handler)

    return logger


def setup_rotating_logger(
    name="camera_app",
    level=logging.INFO,
    log_file: str | Path = "logs/app.log",
    max_bytes: int = 5_000_000,
    backup_count: int = 3,
    also_stdout: bool = True,
):
    """
    Like setup_logger but adds a RotatingFileHandler. Avoids duplicate handlers.
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if logger.handlers:
        return logger

    formatter = _build_formatter()
    handlers = []

    if also_stdout:
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        handlers.append(stream_handler)

    log_path = Path(log_file).expanduser().resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_path, maxBytes=max_bytes, backupCount=backup_count
    )
    file_handler.setFormatter(formatter)
    handlers.append(file_handler)

    for h in handlers:
        logger.addHandler(h)

    root_logger = logging.getLogger()
    if logger is not root_logger and root_logger.handlers:
        logger.propagate = True

    return logger


def rotate_file(log_path: str | Path, max_bytes: int, backup_count: int):
    """
    Lightweight size-based rotation for arbitrary files.
    Renames log_path to .1, shifts existing backups, keeps backup_count files total.
    """
    path = Path(log_path).expanduser().resolve()
    if max_bytes <= 0 or backup_count <= 0:
        return
    if not path.exists():
        return
    try:
        if path.stat().st_size < max_bytes:
            return
    except OSError:
        return

    for idx in range(backup_count - 1, 0, -1):
        older = path.with_suffix(path.suffix + f".{idx}")
        newer = path.with_suffix(path.suffix + f".{idx + 1}")
        if newer.exists():
            newer.unlink(missing_ok=True)
        if older.exists():
            older.rename(newer)

    first = path.with_suffix(path.suffix + ".1")
    if first.exists():
        first.unlink(missing_ok=True)
    try:
        path.rename(first)
    except OSError:
        return


def build_uvicorn_log_config(
    log_file: str | Path = "logs/webserver.log",
    level: str = "info",
    max_bytes: int = 5_000_000,
    backup_count: int = 3,
):
    """
    Return a dictConfig for uvicorn that logs to stdout and a rotating file.
    """
    log_path = Path(log_file).expanduser().resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    formatter = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    access_fmt = "%(message)s"

    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format": formatter,
                "datefmt": datefmt,
            },
            "access": {
                "format": "%(asctime)s [%(levelname)s] " + access_fmt,
                "datefmt": datefmt,
            },
        },
        "handlers": {
            "default": {
                "formatter": "default",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
            },
            "file": {
                "formatter": "default",
                "class": "logging.handlers.RotatingFileHandler",
                "filename": str(log_path),
                "maxBytes": max_bytes,
                "backupCount": backup_count,
            },
            "access": {
                "formatter": "access",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
            },
            "access_file": {
                "formatter": "access",
                "class": "logging.handlers.RotatingFileHandler",
                "filename": str(log_path),
                "maxBytes": max_bytes,
                "backupCount": backup_count,
            },
        },
        "loggers": {
            "uvicorn": {"handlers": ["default", "file"], "level": level.upper()},
            "uvicorn.error": {"handlers": ["default", "file"], "level": level.upper(), "propagate": False},
            "uvicorn.access": {"handlers": ["access", "access_file"], "level": level.upper(), "propagate": False},
        },
    }
