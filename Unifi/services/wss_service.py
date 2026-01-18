import logging
from dataclasses import dataclass
from typing import Optional

from Unifi.camera_data.camera_settings import CameraSettings
from Unifi.wss_manager import WssManager


@dataclass
class WssServiceStatus:
    running: bool
    token_present: bool
    host: Optional[str]


class WssService:
    def __init__(
        self,
        settings: CameraSettings,
        token_event,
        stop_event,
        logger: logging.Logger,
        tcp_in_log: logging.Logger | None = None,
        tcp_out_log: logging.Logger | None = None,
        driver=None,
    ) -> None:
        self.settings = settings
        self.token_event = token_event
        self.stop_event = stop_event
        self.logger = logger
        self.tcp_in_log = tcp_in_log
        self.tcp_out_log = tcp_out_log
        self.driver = driver
        self._manager: Optional[WssManager] = None

    def start(self) -> bool:
        if self._manager and self._manager.is_alive():
            return False
        self.stop_event.clear()
        self._manager = WssManager(
            self.settings,
            self.token_event,
            self.stop_event,
            self.logger,
            tcp_in_log=self.tcp_in_log,
            tcp_out_log=self.tcp_out_log,
            driver=self.driver,
        )
        self._manager.start()
        self.logger.info("WSS manager started")
        return True

    def stop(self) -> bool:
        if not self._manager:
            return False
        self.stop_event.set()
        return True

    def status(self) -> WssServiceStatus:
        running = bool(self._manager and self._manager.is_alive())
        token_present = bool(self.settings.get("mgmt.token"))
        host = self.settings.get("mgmt.connectionHost")
        return WssServiceStatus(running=running, token_present=token_present, host=host)
