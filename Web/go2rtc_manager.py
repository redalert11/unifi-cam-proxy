import os
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import requests
from collections import deque

try:
    from Unifi.utils.logging_utils import rotate_file
except ImportError:
    rotate_file = None


@dataclass
class Go2RTCStatus:
    running: bool
    pid: Optional[int]
    started_at: Optional[float]
    last_exit: Optional[int]
    binary_path: Optional[str]
    config_path: str
    config_exists: bool
    streams_total: Optional[int] = None
    streams_online: Optional[int] = None
    message: str = ""


class Go2RTCManager:
    """Lightweight process supervisor for go2rtc."""

    def __init__(
        self,
        binary_path: str = "go2rtc",
        config_path: Path = Path("go2rtc/config.yaml"),
        log_path: Path = Path("logs/go2rtc.log"),
        api_base: str = "http://127.0.0.1:1984",
        log_max_bytes: int = 5_000_000,
        log_backup_count: int = 3,
    ):
        base_dir = Path(__file__).resolve().parent.parent
        self.binary_path = Path(binary_path)
        cfg_path = Path(config_path)
        log_path = Path(log_path)
        if not cfg_path.is_absolute():
            cfg_path = base_dir / cfg_path
        if not log_path.is_absolute():
            log_path = base_dir / log_path
        self.config_path = cfg_path.resolve()
        self.log_path = log_path.resolve()
        self.api_base = api_base.rstrip("/")
        self.log_max_bytes = log_max_bytes
        self.log_backup_count = log_backup_count
        self._process: Optional[subprocess.Popen] = None
        self._lock = threading.RLock()
        self._last_exit: Optional[int] = None
        self._started_at: Optional[float] = None
        self._stopped_at: Optional[float] = None

    def _resolve_binary(self) -> Optional[Path]:
        if self.binary_path.is_file():
            return self.binary_path
        found = shutil.which(str(self.binary_path))
        return Path(found) if found else None

    def _ensure_dirs(self):
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def _wait_for_stop(self, timeout: float = 5.0) -> Optional[int]:
        """Wait for the current process to exit; returns exit code or None if still running."""
        if not self._process:
            return None
        proc = self._process
        if proc.poll() is None:
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                return None
        self._last_exit = proc.returncode
        self._process = None
        self._started_at = None
        self._stopped_at = time.time()
        return self._last_exit

    def _wait_for_cooldown(self, cooldown: float = 3.0):
        """Ensure the process has been stopped for at least `cooldown` seconds before starting."""
        if self._stopped_at is None:
            return
        remaining = cooldown - (time.time() - self._stopped_at)
        if remaining > 0:
            time.sleep(remaining)

    def _rotate_log(self):
        """Rotate go2rtc log if it exceeds configured size."""
        if rotate_file:
            rotate_file(self.log_path, self.log_max_bytes, self.log_backup_count)
            return
        # Fallback inline rotation if helper not available
        if self.log_max_bytes <= 0 or self.log_backup_count <= 0:
            return
        if not self.log_path.exists():
            return
        try:
            if self.log_path.stat().st_size < self.log_max_bytes:
                return
        except OSError:
            return

        for idx in range(self.log_backup_count - 1, 0, -1):
            older = self.log_path.with_suffix(self.log_path.suffix + f".{idx}")
            newer = self.log_path.with_suffix(self.log_path.suffix + f".{idx + 1}")
            if newer.exists():
                newer.unlink(missing_ok=True)
            if older.exists():
                older.rename(newer)

        first = self.log_path.with_suffix(self.log_path.suffix + ".1")
        if first.exists():
            first.unlink(missing_ok=True)
        try:
            self.log_path.rename(first)
        except OSError:
            return

    def _fetch_stream_counts(self, timeout: float = 1.5) -> Tuple[Optional[int], Optional[int]]:
        """Return (total_streams, online_streams) from go2rtc API."""
        try:
            res = requests.get(f"{self.api_base}/api/streams", timeout=timeout)
            res.raise_for_status()
            data = res.json()
            total = len(data) if isinstance(data, dict) else None
            online = None
            if isinstance(data, dict):
                online = sum(
                    1
                    for v in data.values()
                    if isinstance(v, dict)
                    and v.get("state", v.get("status")) == "online"
                )
            return total, online
        except Exception:
            return None, None

    def read_log_tail(self, max_lines: int = 200) -> Optional[list[str]]:
        """Return the last `max_lines` from the log file."""
        if max_lines <= 0:
            return []
        if not self.log_path.exists():
            return None
        lines = deque(maxlen=max_lines)
        with self.log_path.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                lines.append(line.rstrip("\n"))
        return list(lines)

    def status(self) -> Go2RTCStatus:
        with self._lock:
            # Ensure paths are absolute in case caller passed a relative path at runtime
            self.config_path = Path(self.config_path).resolve()
            self.log_path = Path(self.log_path).resolve()
            running = self._process is not None and self._process.poll() is None
            pid = self._process.pid if running else None
            binary = self._resolve_binary()
            status = Go2RTCStatus(
                running=running,
                pid=pid,
                started_at=self._started_at,
                last_exit=self._last_exit,
                binary_path=str(binary) if binary else None,
                config_path=str(self.config_path.resolve()),
                config_exists=self.config_path.is_file(),
                message="running" if running else "stopped",
            )
        # Fetch stream counts outside the lock to avoid blocking
        if status.running:
            total, online = self._fetch_stream_counts()
            status.streams_total = total
            status.streams_online = online
        return status

    def write_config(self, content: str):
        with self._lock:
            self._ensure_dirs()
            self.config_path.write_text(content or "", encoding="utf-8")

    def read_config(self) -> Optional[str]:
        if not self.config_path.exists():
            return None
        return self.config_path.read_text(encoding="utf-8")

    def start(self) -> Go2RTCStatus:
        with self._lock:
            if self._process and self._process.poll() is None:
                return self.status()
            # Clean up any prior process that has exited
            self._wait_for_stop()
            # Ensure downtime before starting again
            self._wait_for_cooldown()

            binary = self._resolve_binary()
            if not binary:
                return Go2RTCStatus(
                    running=False,
                    pid=None,
                    started_at=None,
                    last_exit=self._last_exit,
                    binary_path=None,
                    config_path=str(self.config_path),
                    config_exists=self.config_path.is_file(),
                    message="go2rtc binary not found in PATH",
                )

            # If we recently stopped, wait a bit before starting again
            if self._stopped_at:
                since_stop = time.time() - self._stopped_at
                if since_stop < 3.0:
                    time.sleep(3.0 - since_stop)

            self._ensure_dirs()
            self._rotate_log()
            log_file = self.log_path.open("a", encoding="utf-8")
            config_file = self.config_path.resolve()
            cmd = [str(binary), "-c", str(config_file)]
            self._process = subprocess.Popen(
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                cwd=config_file.parent,
            )
            self._started_at = time.time()
            self._last_exit = None
            return self.status()

    def stop(self, timeout: float = 5.0) -> Go2RTCStatus:
        with self._lock:
            if not self._process:
                return self.status()
            proc = self._process
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=timeout)
            self._last_exit = proc.returncode
            self._process = None
            self._started_at = None
            self._stopped_at = time.time()
            return self.status()

    def reload(self) -> Go2RTCStatus:
        with self._lock:
            if self._process and self._process.poll() is None:
                try:
                    os.kill(self._process.pid, signal.SIGHUP)
                except Exception:
                    # Fallback to full restart if SIGHUP fails
                    self.stop()
                    return self.start()
                # Give the process a brief moment to settle after SIGHUP
                time.sleep(0.3)
                if self._process and self._process.poll() is None:
                    return self.status()
                # If it exited after SIGHUP, wait for clean stop then start again
                self._wait_for_stop(timeout=2.0)
                self._wait_for_cooldown()
                return self.start()
            # If not running, just attempt start
            self._wait_for_stop()
            self._wait_for_cooldown()
            return self.start()
