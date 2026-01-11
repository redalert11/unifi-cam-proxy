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
        if not logging.getLogger().handlers:
            logging.basicConfig(
                level=logging.INFO,
                format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            )
        self.settings = settings or CameraSettings()

        main_log = setup_logger("main", self.settings.get("logging.main.level", logging.INFO))
        api_log_level = self.settings.get("logging.api.level", logging.DEBUG)
        disc_log_level = self.settings.get("logging.discovery.level", logging.INFO)
        wss_log_level = self.settings.get("logging.wss.level", logging.INFO)
        upload_log_level = self.settings.get("logging.upload_server.level", logging.INFO)

        self.main_log = main_log
        self.api_log = setup_logger("api_https", api_log_level)
        self.discovery_log = setup_logger("discovery", disc_log_level)
        self.wss_log = setup_logger("wss", wss_log_level)
        self.upload_log = setup_logger("upload_server", upload_log_level)

        self.token_event = threading.Event()
        self.stop_event = threading.Event()

        self.driver = build_camera_driver(self.settings, self.wss_log)

        self.api_service = ApiService(self.settings, self.api_log, self.token_event, driver=self.driver)
        self.discovery_service = DiscoveryService(self.settings, self.discovery_log)
        self.upload_service = UploadService(self.upload_log)
        self.wss_service = WssService(self.settings, self.token_event, self.stop_event, self.wss_log, driver=self.driver)

        self._uptime_thread: threading.Thread | None = None

    def _start_uptime(self) -> None:
        if self._uptime_thread and self._uptime_thread.is_alive():
            return
        now_ms = int(time.time() * 1000)
        self.settings.update({"upSince": now_ms, "lastSeen": None, "uptime": 0, "connectedSince": None})
        self._uptime_thread = threading.Thread(
            target=increment_uptime,
            args=(self.settings, setup_logger("uptime", self.settings.get("logging.uptime.level", logging.INFO))),
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
