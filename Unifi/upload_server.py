import os
import ssl
import time
import hashlib
import threading
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional

SNAP_PREFIX = "/internal/camera-upload/"
DEBUG_LAST_PATH = "/debug/last-snapshot"

def _utc_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

def _safe_int(s: str, default: int = 0) -> int:
    try:
        return int(s)
    except Exception:
        return default

def _make_handler(logger, save_dir: Optional[str], preview_bytes: int):
    class _UploadHandler(BaseHTTPRequestHandler):
        # give handler access to logger/config
        _logger = logger
        _save_dir = save_dir
        _preview_bytes = max(0, preview_bytes)

        def do_GET(self):
            # Simple debug endpoint to fetch the last snapshot the server saw
            if self.path == DEBUG_LAST_PATH:
                data = getattr(self.server, "last_snapshot_bytes", None)
                meta = getattr(self.server, "last_snapshot_meta", {})
                if not data:
                    self.send_response(404); self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("X-Bytes", str(len(data)))
                # bubble some context in headers for quick checks
                for k, v in meta.items():
                    self.send_header(f"X-Meta-{k}", str(v))
                self.end_headers()
                self.wfile.write(data)
                return

            self.send_response(404); self.end_headers()

            # Log at DEBUG so it doesn’t spam unless enabled
            if self._logger:
                self._logger.debug("GET %s from %s -> 404",
                                   self.path, self.client_address[0])

        def do_PUT(self):
            # Basic routing
            if not self.path.startswith(SNAP_PREFIX):
                self.send_response(404); self.end_headers()
                if self._logger:
                    self._logger.warning("PUT %s from %s -> 404 (unknown path)",
                                         self.path, self.client_address[0])
                return

            try:
                # Read request info
                length = _safe_int(self.headers.get("Content-Length", "0"))
                ctype  = self.headers.get("Content-Type", "")
                agent  = self.headers.get("User-Agent", "")

                # Drain body
                body = self.rfile.read(length) if length > 0 else b""

                # Quick fingerprint
                sha256 = hashlib.sha256(body).hexdigest() if body else "-"
                head_preview = body[:self._preview_bytes] if self._preview_bytes else b""

                # Persist for debug endpoint
                self.server.last_snapshot_bytes = body
                self.server.last_snapshot_meta = {
                    "when": _utc_ts(),
                    "length": length,
                    "sha256": sha256,
                    "path": self.path,
                    "client": self.client_address[0],
                }

                # Optional save-to-disk
                saved_path = None
                if self._save_dir:
                    os.makedirs(self._save_dir, exist_ok=True)
                    token = self.path.split("/")[-1] or "snapshot"
                    ts = time.strftime("%Y%m%d_%H%M%S")
                    saved_path = os.path.join(self._save_dir, f"{ts}_{token}.jpg")
                    with open(saved_path, "wb") as f:
                        f.write(body)

                # Respond OK to controller
                self.send_response(200)
                self.end_headers()

                # Structured log line
                if self._logger:
                    msg = (f"PUT snapshot OK len={length} sha256={sha256[:12]}… "
                           f"path={self.path} from={self.client_address[0]} "
                           f"ctype='{ctype}' ua='{agent}'")
                    if saved_path:
                        msg += f" saved='{saved_path}'"
                    if head_preview:
                        # print first bytes as hex for quick SOI check (FFD8)
                        msg += f" head={head_preview.hex()}"
                    self._logger.debug(msg)

            except Exception as e:
                self.send_response(500); self.end_headers()
                if self._logger:
                    self._logger.exception("Upload handler error for %s: %s",
                                           self.path, e)

        # Silence default BaseHTTPRequestHandler prints (we log ourselves)
        def log_message(self, fmt, *args):
            if self._logger:
                # Drop to TRACE-like detail: use .debug
                self._logger.debug("http.server: " + fmt, *args)

    return _UploadHandler


def start_upload_server(
    cert: str = "cert.pem",
    key: str = "key.pem",
    host: str = "0.0.0.0",
    port: int = 7444,
    logger=None,
    *,
    # new optional knobs:
    save_dir: Optional[str] = None,          # e.g. "/tmp/unifi-uploads"
    preview_bytes: int = 8                   # show first N bytes as hex in logs (0 = off)
):
    """
    Start the HTTPS upload server used by the controller for snapshots.

    Adds:
      - /debug/last-snapshot  (GET): returns the most recently uploaded JPEG
      - Detailed per-upload logging (length, sha256, path, client, saved file)
      - Optional on-disk saving for inspection (save_dir)
      - Hex preview of the first few bytes to confirm JPEG SOI (FFD8)
    """
    Handler = _make_handler(logger, save_dir, preview_bytes)
    httpd = HTTPServer((host, port), Handler)
    httpd.last_snapshot_bytes = None
    httpd.last_snapshot_meta = {}

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    try:
        ctx.load_cert_chain(certfile=cert, keyfile=key)
    except Exception as e:
        if logger:
            logger.error("Failed to load TLS cert/key (%s, %s): %s", cert, key, e)
        raise
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)

    t = threading.Thread(target=httpd.serve_forever, daemon=True, name="UploadServer")
    t.start()

    if logger:
        logger.info("Upload server listening on https://%s:%d", host, port)
        if save_dir:
            logger.info("Upload server will save snapshots to %s", save_dir)
        logger.debug("Debug endpoint available at GET %s", DEBUG_LAST_PATH)

    return httpd
