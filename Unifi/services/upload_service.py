import logging
from dataclasses import dataclass
from typing import Optional

from Unifi.upload_server import start_upload_server


@dataclass
class UploadServiceStatus:
    running: bool
    port: int


class UploadService:
    def __init__(
        self,
        logger: logging.Logger,
        host: str = "0.0.0.0",
        port: int = 7444,
    ) -> None:
        self.logger = logger
        self.host = host
        self.port = port
        self._server = None

    def start(self) -> bool:
        if self._server is not None:
            return False
        self._server = start_upload_server(host=self.host, port=self.port, logger=self.logger)
        return True

    def stop(self) -> bool:
        if not self._server:
            return False
        try:
            self._server.shutdown()
        finally:
            try:
                self._server.server_close()
            except Exception:
                pass
            self._server = None
        return True

    def status(self) -> UploadServiceStatus:
        return UploadServiceStatus(running=self._server is not None, port=self.port)
