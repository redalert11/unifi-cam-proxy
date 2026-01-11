import signal
import sys
from pathlib import Path

# Ensure repo root is on path so package imports resolve when running as a script
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from Unifi.services.runtime import ServiceRuntime

def main():
    runtime = ServiceRuntime()
    runtime.start_all()

    def handle_sig(sig, frame):
        runtime.main_log.info("Shutting down...")
        runtime.stop_all()

    signal.signal(signal.SIGINT, handle_sig)
    signal.signal(signal.SIGTERM, handle_sig)
    runtime.stop_event.wait()
    runtime.main_log.info("Bye!")

if __name__ == "__main__":
    main()
