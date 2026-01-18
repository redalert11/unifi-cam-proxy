import logging
import sys
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Deque, Dict, List, Optional


# In-memory buffers keyed by logger name so other components (e.g., API endpoints)
# can expose recent logs without writing to disk.
_LOG_BUFFERS: Dict[str, Deque[str]] = {}


class InMemoryLogHandler(logging.Handler):
    def __init__(self, name: str, maxlen: int = 500):
        super().__init__()
        self.name = name
        self.buffer = deque(maxlen=maxlen)
        _LOG_BUFFERS.setdefault(name, self.buffer)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:
            msg = record.getMessage()
        self.buffer.append(msg)


def _build_formatter():
    return logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        "%H:%M:%S",
    )

def _maybe_get_buffer_size(buffer_size: Optional[int]) -> int:
    if buffer_size is not None:
        return buffer_size
    return 500


def _file_logging_enabled(enable_file: Optional[bool]) -> bool:
    if enable_file is not None:
        return enable_file
    return True  # default: on


def get_log_buffer(name: str, max_lines: Optional[int] = None) -> List[str]:
    """
    Return a copy of the recent log lines for the given logger name.
    """
    buf = _LOG_BUFFERS.get(name)
    if not buf:
        return []
    lines = list(buf)
    if max_lines is not None and max_lines > 0:
        return lines[-max_lines:]
    return lines


def list_log_sources(prefix: Optional[str] = None) -> List[str]:
    sources = sorted(_LOG_BUFFERS.keys())
    if prefix:
        return [s for s in sources if s.startswith(prefix)]
    return sources


def setup_logger(
    name="camera_app",
    level=logging.DEBUG,
    buffer_size: Optional[int] = None,
    enable_file: Optional[bool] = None,
    log_file: str | Path | None = None,
    max_bytes: int = 5_000_000,
    backup_count: int = 3,
):
    """
    Create or retrieve a logger with the specified name and level.
    Adds an in-memory buffer (deque) for quick access, and only writes to a file
    if enable_file is True.
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    formatter = _build_formatter()

    # Remove any existing stdout/stderr handlers to keep logs in the web UI only.
    for handler in list(logger.handlers):
        if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
            logger.removeHandler(handler)

    # Attach in-memory buffer handler (if not already)
    buf_size = _maybe_get_buffer_size(buffer_size)
    if buf_size > 0 and not any(isinstance(h, InMemoryLogHandler) for h in logger.handlers):
        mem_handler = InMemoryLogHandler(name=name, maxlen=buf_size)
        mem_handler.setFormatter(formatter)
        logger.addHandler(mem_handler)

    # Optional file logging, off by default
    if _file_logging_enabled(enable_file) and not any(isinstance(h, RotatingFileHandler) for h in logger.handlers):
        path = Path(log_file or f"logs/{name}.log").expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backup_count)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    # Avoid duplicate console output via root handlers.
    logger.propagate = False

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
    enable_file: Optional[bool] = None,
):
    """
    Return a dictConfig for uvicorn that logs to stdout and a rotating file.
    """
    use_file = _file_logging_enabled(enable_file)
    log_path = Path(log_file).expanduser().resolve()
    if use_file:
        log_path.parent.mkdir(parents=True, exist_ok=True)
    formatter = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    access_fmt = "%(message)s"

    handlers = {
        "default": {
            "formatter": "default",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
        },
        "access": {
            "formatter": "access",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
        },
    }
    if use_file:
        handlers.update(
            {
                "file": {
                    "formatter": "default",
                    "class": "logging.handlers.RotatingFileHandler",
                    "filename": str(log_path),
                    "maxBytes": max_bytes,
                    "backupCount": backup_count,
                },
                "access_file": {
                    "formatter": "access",
                    "class": "logging.handlers.RotatingFileHandler",
                    "filename": str(log_path),
                    "maxBytes": max_bytes,
                    "backupCount": backup_count,
                },
            }
        )

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
        "handlers": handlers,
        "loggers": {
            "uvicorn": {"handlers": ["default"] + (["file"] if use_file else []), "level": level.upper()},
            "uvicorn.error": {
                "handlers": ["default"] + (["file"] if use_file else []),
                "level": level.upper(),
                "propagate": False,
            },
            "uvicorn.access": {
                "handlers": ["access"] + (["access_file"] if use_file else []),
                "level": level.upper(),
                "propagate": False,
            },
        },
    }
