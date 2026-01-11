import logging
from dataclasses import dataclass
from typing import Optional

from Unifi.api_server import VerboseAPIServer
from Unifi.camera_data.camera_settings import CameraSettings


@dataclass
class ApiServiceStatus:
    running: bool
    port: int
    use_ssl: bool


class ApiService:
    def __init__(
        self,
        settings: CameraSettings,
        logger: logging.Logger,
        token_event,
        driver=None,
        port: int = 443,
        use_ssl: bool = True,
    ) -> None:
        self.settings = settings
        self.logger = logger
        self.token_event = token_event
        self.driver = driver
        self.port = port
        self.use_ssl = use_ssl
        self.server: Optional[VerboseAPIServer] = None

    def start(self) -> bool:
        if self.server and self.server.is_running():
            return False
        self.server = VerboseAPIServer(
            port=self.port,
            use_ssl=self.use_ssl,
            settings=self.settings,
            logger=self.logger,
            token_event=self.token_event,
            driver=self.driver,
        )
        self.server.start()
        self.logger.info("HTTPS API server started on port %s", self.port)
        return True

    def stop(self) -> bool:
        if not self.server:
            return False
        self.server.stop()
        return True

    def status(self) -> ApiServiceStatus:
        running = bool(self.server and self.server.is_running())
        return ApiServiceStatus(running=running, port=self.port, use_ssl=self.use_ssl)
