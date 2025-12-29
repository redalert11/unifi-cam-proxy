import logging
import sys
from pathlib import Path

import uvicorn

# Ensure repo root is on path so we can import logging_utils regardless of cwd
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

try:
    from Unifi.utils.logging_utils import build_uvicorn_log_config
except ImportError:
    from utils.logging_utils import build_uvicorn_log_config

try:
    from .app import app  # when executed as a module: python -m Web.main
except ImportError:  # fallback for direct script execution
    try:
        from Web.app import app
    except ImportError:
        from app import app


def main():
    log_config = build_uvicorn_log_config(
        log_file=Path("logs/webserver.log"),
        level="info",
        max_bytes=5_000_000,
        backup_count=3,
    )
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
        log_config=log_config,
    )


if __name__ == "__main__":
    main()
