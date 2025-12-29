import logging
import signal
import sys
import threading
import time
from pathlib import Path

# Ensure repo root is on path so package imports resolve when running as a script
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from Unifi.camera_data.camera_settings import CameraSettings
from Unifi.discovery_responder import DiscoveryResponder
from Unifi.api_server import VerboseAPIServer
from Unifi.utils.logging_utils import setup_logger
from Unifi.utils.uptime_utils import increment_uptime
from Unifi.wss_manager import WssManager
from Unifi.drivers.camera_factory import build_camera_driver
from Unifi.upload_server import start_upload_server

def main():
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s"
        )

    settings = CameraSettings()

    # Logging levels from settings (with sane fallbacks)
    main_log = setup_logger("main", settings.get("logging.main.level", logging.INFO))
    api_log_level = settings.get("logging.api.level", logging.DEBUG)
    disc_log_level = settings.get("logging.discovery.level", logging.INFO)
    wss_log_level = settings.get("logging.wss.level", logging.INFO)
    upload_server_log_level = settings.get("logging.upload_server.level", logging.INFO)
    wss_log = setup_logger("wss", wss_log_level)
    driver = build_camera_driver(settings, wss_log)

    # Uptime seed
    now_ms = int(time.time() * 1000)
    settings.update({"upSince": now_ms, "lastSeen": None, "uptime": 0, "connectedSince": None})
    threading.Thread(target=increment_uptime, args=(settings, setup_logger("uptime", settings.get("logging.uptime.level", logging.INFO))),
                     daemon=True, name="UptimeThread").start()
    main_log.info("Uptime counter started")

    # Discovery
    if settings.get("canAdopt", True):
        disc_log = setup_logger("discovery", disc_log_level)
        responder = DiscoveryResponder(settings, logger=disc_log)
        threading.Thread(target=responder.start, daemon=True, name="DiscoveryThread").start()
        disc_log.info("Discovery responder started")
    else:
        main_log.warning("Discovery responder skipped as it was previously completed")

    # Token event & stop event
    token_event = threading.Event()
    stop_event = threading.Event()

    # API server (passes token_event so it can .set() when token arrives)
    api_log = setup_logger("api_https", api_log_level)
    api_server = VerboseAPIServer(
        port=443,
        use_ssl=True,
        settings=settings,
        logger=api_log,
        token_event=token_event,
        driver=driver,
    )
    threading.Thread(target=api_server.start, daemon=True, name="APIServerThread").start()
    api_log.info("HTTPS API server started on port 443")

    # start Upload server
    upload_server_log = setup_logger("upload_server", upload_server_log_level)
    start_upload_server(logger=upload_server_log)

    # WSS manager (waits for token/host)
    wss_mgr = WssManager(settings, token_event, stop_event, wss_log, driver=driver)
    wss_mgr.start()

    # Shutdown handling
    def handle_sig(sig, frame):
        main_log.info("Shutting down...")
        stop_event.set()

    signal.signal(signal.SIGINT, handle_sig)
    signal.signal(signal.SIGTERM, handle_sig)
    stop_event.wait()
    main_log.info("Bye!")

if __name__ == "__main__":
    main()
