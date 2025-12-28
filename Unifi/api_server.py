import asyncio
import json
import logging
import os
import secrets
import ssl
import subprocess
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional, Tuple

from utils.logging_utils import setup_logger
from camera_data.camera_settings import CameraSettings

# Global WSS thread reference
wss_task: Optional[threading.Thread] = None


def start_wss(host: str, port: int, token: str, settings: CameraSettings):
    """
    Placeholder for your actual WSS connection logic.
    Replace with real implementation.
    """
    print(f"[WSS] Starting WebSocket to {host}:{port} with token {token}...")
    # TODO: implement your websocket client here


class VerboseAPIServer:
    def __init__(
        self,
        port: int = 443,
        use_ssl: bool = True,
        certfile: str = "cert.pem",
        keyfile: str = "key.pem",
        settings: Optional[CameraSettings] = None,
        logger: Optional[logging.Logger] = None,
        token_event: threading.Event | None = None,
        driver=None,
    ):
        self.port = port
        self.use_ssl = use_ssl
        self.certfile = certfile
        self.keyfile = keyfile
        self.token_event = token_event
        self.driver = driver
        # If no logger provided, default to DEBUG so you see request logs
        self.logger = logger or setup_logger("api_https", logging.DEBUG)

        # Use the provided settings or create a new one
        self.settings: CameraSettings = settings or CameraSettings()
        self._last_session_cookie = None

        # Inject api_server instance into handler via factory
        def handler_factory(*args, **kwargs):
            return self.RequestHandler(self, *args, **kwargs)

        self.server = HTTPServer(("0.0.0.0", port), handler_factory)

        if self.use_ssl:
            self._ensure_cert_exists()
            self.server.socket = ssl.wrap_socket(
                self.server.socket,
                keyfile=self.keyfile,
                certfile=self.certfile,
                server_side=True,
            )

    class RequestHandler(BaseHTTPRequestHandler):
        def __init__(self, api_server: "VerboseAPIServer", request, client_address, server):
            self.api_server = api_server
            self.log = api_server.logger
            super().__init__(request, client_address, server)

        @staticmethod
        def _jpeg_dimensions(data: bytes) -> Tuple[Optional[int], Optional[int]]:
            if len(data) < 4 or data[0] != 0xFF or data[1] != 0xD8:
                return (None, None)
            i = 2
            while i + 3 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                i += 2
                if marker in (0xD8, 0xD9):
                    continue
                if i + 1 >= len(data):
                    break
                seglen = (data[i] << 8) | data[i + 1]
                if seglen < 2 or i + seglen > len(data):
                    break
                if 0xC0 <= marker <= 0xC3 and seglen >= 7:
                    height = (data[i + 3] << 8) | data[i + 4]
                    width = (data[i + 5] << 8) | data[i + 6]
                    return (width, height)
                i += seglen
            return (None, None)

        # ----------------- helpers -----------------

        def _send_json(self, data, status: int = 200, headers: Optional[dict] = None):
            self.send_response(status)
            # 204 should not include a body; we’ll skip writing data then,
            # but sending Content-Type is harmless.
            self.send_header("Content-Type", "application/json")
            if headers:
                for key, value in headers.items():
                    self.send_header(key, value)
            self.end_headers()
            if status != 204:
                self.wfile.write(json.dumps(data).encode())

        def _read_body(self) -> bytes:
            try:
                length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                length = 0
            if length <= 0:
                return b""
            return self.rfile.read(length)

        def log_request_info(self):
            self.log.info("📌 Incoming Request Headers:")
            for k, v in self.headers.items():
                self.log.info("  %s: %s", k, v)

        @staticmethod
        def now_local_iso():
            try:
                dt = datetime.now().astimezone()
                tz_name = getattr(dt.tzinfo, "key", dt.tzname())
            except Exception:
                dt = datetime.now(timezone.utc)
                tz_name = "UTC"
            return dt, tz_name
        
        # ----------------- HTTP methods -----------------

        def do_POST(self):
            if self.path == "/api/1.2/manage":
                self._handle_manage_post()
                return
            if self.path == "/api/1.2/login":
                self._handle_login_post()
                return
            if self.path == "/api/1.2/snapshot":
                self._serve_snapshot()
                return
            self._send_json({"error": "Not Found"}, 404)

        def _handle_manage_post(self):
            self.log_request_info()
            body = self._read_body()

            try:
                data = json.loads(body)
            except Exception:
                self._send_json({"error": "Invalid JSON body"}, 400)
                return

            self.log.info("📥 Incoming Request Body:")
            self.log.info(json.dumps(data, indent=2))

            mgmt = data.get("mgmt", {}) or {}
            token = mgmt.get("token")
            hosts = mgmt.get("hosts") or []

            if not token or not hosts:
                self._send_json({"error": "Missing token or hosts"}, 400)
                return

            controller_host = str(hosts[0])
            if ":" in controller_host:
                host, port_str = controller_host.rsplit(":", 1)
                try:
                    port = int(port_str)
                except ValueError:
                    port = 7442
            else:
                host = controller_host
                port = 7442

            self.log.info("🔗 Controller Host: %s:%s", host, port)

            dt, tz_name = self.now_local_iso()

            # First-time provision vs subsequent refresh
            is_initialized = bool(self.api_server.settings.get("mgmt.initialized", False))
            if not is_initialized:
                # FIRST CALL: persist all the mgmt details and mark initialized
                self.api_server.settings.update({
                    "mgmt.connectionHost": f"{host}:{port}",
                    "mgmt.hosts": hosts,
                    "mgmt.protocol": mgmt.get("protocol"),
                    "mgmt.consoleId": mgmt.get("consoleId"),
                    "mgmt.controller": mgmt.get("controller"),
                    "mgmt.nvr": mgmt.get("nvr"),
                    "mgmt.consoleName": mgmt.get("consoleName"),
                    "mgmt.token": token,
                    "mgmt.tokenUpdatedAt": dt.isoformat(timespec="seconds"),
                    "mgmt.timezone": tz_name,
                    "mgmt.initialized": True,
                    "canAdopt": False,
                })
            else:
                # SUBSEQUENT CALLS: update token only (and timestamp)
                self.api_server.settings.update({
                    "mgmt.token": token,
                    "mgmt.tokenUpdatedAt": dt.isoformat(timespec="seconds"),
                })
                # Optional: if controller host changed, log it but don't overwrite
                saved_host = self.api_server.settings.get("mgmt.connectionHost")
                current = f"{host}:{port}"
                if saved_host and saved_host != current:
                    self.log.warning("Mgmt host changed (%s -> %s); keeping original.", saved_host, current)

            # Notify WSS manager
            if self.api_server.token_event:
                self.api_server.token_event.set()

            # Start WSS once (or let your WssManager react to token_event)
            global wss_task
            if wss_task is None or not wss_task.is_alive():
                wss_task = threading.Thread(
                    target=start_wss,
                    args=(host, port, token, self.api_server.settings),
                    daemon=True,
                    name="WSSHandshake",
                )
                wss_task.start()

            # Build response (prefer saved connection host)
            s = self.api_server.settings
            conn_host = s.get("mgmt.connectionHost", f"{host}:{port}")
            resp = {
                "mac": s["mac"] or "",
                "model": (s["type"] or s["marketName"] or ""),
                "firmwareVersion": s.get("firmwareVersion", "") or "",
                "sysid": s["sysid"] or "",   # keep as string like "0xa573"
                "token": token,
                "hosts": [conn_host],
                "services": {"https": 443, "wss": 7442},
            }

            self.log.info("📤 Response:")
            self.log.info(json.dumps(resp, indent=2))
            self._send_json(resp, 200)

        def _handle_login_post(self):
            self.log_request_info()
            body = self._read_body()
            parsed = {}
            if body:
                try:
                    parsed = json.loads(body)
                except Exception:
                    self.log.debug("Login payload not JSON (len=%d)", len(body))
            if parsed:
                self.log.info("🔐 Login payload: %s", json.dumps(parsed, indent=2))
            cookie_val = secrets.token_hex(16)
            self.api_server._last_session_cookie = cookie_val
            headers = {"Set-Cookie": f"TOKEN={cookie_val}; Path=/; HttpOnly"}
            resp = {"status": "ok", "uniqueId": self.api_server.settings.get("mac") or ""}
            self._send_json(resp, 200, headers=headers)

        def _serve_snapshot(self):
            driver = self.api_server.driver
            if driver is None:
                self._send_json({"error": "snapshot driver unavailable"}, 503)
                return

            parsed_path = self.path.split("?", 1)[0]
            self.log.info("📸 Snapshot request via %s", parsed_path)

            try:
                jpeg = asyncio.run(driver.get_snapshot_jpeg(timeout_s=3))
            except Exception as exc:
                self.log.error("Snapshot capture failed: %s", exc)
                self._send_json({"error": "snapshot capture failed"}, 500)
                return
            width, height = self._jpeg_dimensions(jpeg)
            self.log.info(
                "📸 Snapshot ready bytes=%d dims=%sx%s (fallback HTTP)",
                len(jpeg),
                width or "?",
                height or "?",
            )

            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Pragma", "no-cache")
            if width:
                self.send_header("X-Image-Width", str(width))
            if height:
                self.send_header("X-Image-Height", str(height))
            self.send_header("Content-Length", str(len(jpeg)))
            self.end_headers()
            self.wfile.write(jpeg)

        def do_GET(self):
            if self.path.startswith("/api/1.2/snapshot"):
                self._serve_snapshot()
                return
            s = self.api_server.settings
            self._send_json(
                {
                    "status": "ok",
                    "mac": s["mac"],
                    "host": s["host"],
                    "model": s["type"] or s["marketName"],
                    "firmwareVersion": s.get("firmwareVersion", ""),
                    "sysid": s["sysid"],  # string like "0xa573"
                },
                200,
            )

        def do_PUT(self):
            # Treat PUT same as POST for this endpoint
            self.do_POST()

        def do_DELETE(self):
            # No content
            self._send_json({}, 204)

        # Quiet default http.server console prints; keep them in our logger
        def log_message(self, format: str, *args):
            self.log.debug("HTTP: " + (format % args))

    # ----------------- server helpers -----------------

    def _ensure_cert_exists(self):
        if os.path.exists(self.certfile) and os.path.exists(self.keyfile):
            return

        self.logger.warning("[!] cert.pem or key.pem not found. Generating self-signed certificate...")
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-keyout",
                self.keyfile,
                "-out",
                self.certfile,
                "-days",
                "365",
                "-subj",
                "/CN=localhost",
            ],
            check=True,
        )
        self.logger.info("[+] Self-signed certificate generated.")

    def start(self):
        def _thread():
            protocol = "HTTPS" if self.use_ssl else "HTTP"
            self.logger.info(f"[+] {protocol} API server running on port {self.port}")
            self.server.serve_forever()

        self.logger.info(
            "[+] VerboseAPIServer starting; level=%s",
            logging.getLevelName(self.logger.level),
        )
        threading.Thread(target=_thread, daemon=True, name="APIServer").start()
