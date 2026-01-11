import logging
import threading
from dataclasses import dataclass
from typing import Optional

from Unifi.discovery_responder import DiscoveryResponder
from Unifi.camera_data.camera_settings import CameraSettings


@dataclass
class DiscoveryServiceStatus:
    running: bool
    can_adopt: bool


class DiscoveryService:
    def __init__(self, settings: CameraSettings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger
        self._thread: Optional[threading.Thread] = None
        self._responder: Optional[DiscoveryResponder] = None

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return False
        self.settings.update({"canAdopt": True})
        self._responder = DiscoveryResponder(self.settings, logger=self.logger)
        self._thread = threading.Thread(target=self._responder.start, daemon=True, name="DiscoveryThread")
        self._thread.start()
        self.logger.info("Discovery responder started")
        return True

    def stop(self) -> bool:
        if not self._thread:
            return False
        self.settings.update({"canAdopt": False})
        return True

    def start_if_enabled(self) -> bool:
        if not self.settings.get("canAdopt", True):
            self.logger.warning("Discovery responder skipped as it was previously completed")
            return False
        return self.start()

    def status(self) -> DiscoveryServiceStatus:
        running = bool(self._thread and self._thread.is_alive())
        can_adopt = bool(self.settings.get("canAdopt", True))
        return DiscoveryServiceStatus(running=running, can_adopt=can_adopt)
