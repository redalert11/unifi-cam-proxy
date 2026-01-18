import logging
import threading
import time
from dataclasses import dataclass

from Unifi.camera_data.camera_settings import CameraSettings
from Unifi.drivers.camera_factory import build_camera_driver
from Unifi.utils.logging_utils import setup_logger
from Unifi.utils.uptime_utils import increment_uptime
from Unifi.services.api_service import ApiService, ApiServiceStatus
from Unifi.services.discovery_service import DiscoveryService, DiscoveryServiceStatus
from Unifi.services.upload_service import UploadService, UploadServiceStatus
from Unifi.services.wss_service import WssService, WssServiceStatus


@dataclass
class RuntimeStatus:
    api: ApiServiceStatus
    discovery: DiscoveryServiceStatus
    wss: WssServiceStatus
    upload: UploadServiceStatus


class ServiceRuntime:
    def __init__(self, settings: CameraSettings | None = None) -> None:
        self.settings = settings or CameraSettings()

        # Always capture full logs for the web UI (in-memory filtering happens in the UI).
        log_level = logging.DEBUG
        self.main_log = setup_logger("main", log_level)
        self.api_log = setup_logger("api_https", log_level)
        self.discovery_log = setup_logger("discovery", log_level)
        mac = (self.settings.get("mac", "") or "").strip()
        wss_logger_name = f"wss.{mac}" if mac else "wss"
        self.wss_log = setup_logger(wss_logger_name, log_level)
        self.wss_tcp_in_log = setup_logger(f"{wss_logger_name}.tcp_in", log_level) if mac else None
        self.wss_tcp_out_log = setup_logger(f"{wss_logger_name}.tcp_out", log_level) if mac else None
        self.upload_log = setup_logger("upload_server", log_level)

        self.token_event = threading.Event()
        self.stop_event = threading.Event()

        self.driver = build_camera_driver(self.settings, self.wss_log)

        self.api_service = ApiService(self.settings, self.api_log, self.token_event)
        self.discovery_service = DiscoveryService(self.settings, self.discovery_log)
        self.upload_service = UploadService(self.upload_log)
        self.wss_service = WssService(
            self.settings,
            self.token_event,
            self.stop_event,
            self.wss_log,
            tcp_in_log=self.wss_tcp_in_log,
            tcp_out_log=self.wss_tcp_out_log,
            driver=self.driver,
        )

        self._uptime_thread: threading.Thread | None = None

    def _start_uptime(self) -> None:
        if self._uptime_thread and self._uptime_thread.is_alive():
            return
        now_ms = int(time.time() * 1000)
        self.settings.update({"upSince": now_ms, "lastSeen": None, "uptime": 0, "connectedSince": None})
        self._uptime_thread = threading.Thread(
            target=increment_uptime,
            args=(self.settings, setup_logger("uptime", log_level)),
            daemon=True,
            name="UptimeThread",
        )
        self._uptime_thread.start()
        self.main_log.info("Uptime counter started")

    def start_all(self) -> None:
        self._start_uptime()
        self.discovery_service.start_if_enabled()
        self.api_service.start()
        self.upload_service.start()
        self.wss_service.start()

    def stop_all(self) -> None:
        self.stop_event.set()
        self.discovery_service.stop()
        self.wss_service.stop()
        self.api_service.stop()
        self.upload_service.stop()

    def status(self) -> RuntimeStatus:
        return RuntimeStatus(
            api=self.api_service.status(),
            discovery=self.discovery_service.status(),
            wss=self.wss_service.status(),
            upload=self.upload_service.status(),
        )
