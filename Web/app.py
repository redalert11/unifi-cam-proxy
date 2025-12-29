import json
from pathlib import Path
from typing import Optional

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi import Depends

try:
    from Unifi.utils.settings_manager import SettingsManager
except ImportError:
    from ..Unifi.utils.settings_manager import SettingsManager  # type: ignore

try:
    from .go2rtc_manager import Go2RTCManager
except ImportError:
    # Fallbacks for script execution without package context
    try:
        from Web.go2rtc_manager import Go2RTCManager
    except ImportError:
        from go2rtc_manager import Go2RTCManager

app = FastAPI(title="UniFi Cam Proxy Supervisor", version="0.1.0")

static_dir = Path(__file__).parent / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")

manager = Go2RTCManager()

web_settings_path = Path(__file__).resolve().parent / "data" / "web_settings.json"
web_settings_store = SettingsManager(web_settings_path, defaults={"autostart_go2rtc": False})

web_log_path = (Path(__file__).resolve().parent.parent / "logs" / "webserver.log").resolve()


def read_log_tail(path: Path, max_lines: int = 200):
    if max_lines <= 0:
        return []
    if not path.exists():
        return None
    buf = []
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        buf = f.readlines()[-max_lines:]
    return [line.rstrip("\n") for line in buf]


@app.get("/")
def index():
    index_file = static_dir / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="index.html not found")
    return FileResponse(index_file)


@app.get("/api/go2rtc/status")
def go2rtc_status():
    return manager.status().__dict__


@app.on_event("startup")
def go2rtc_autostart():
    try:
        if web_settings_store.get("autostart_go2rtc", False):
            status = manager.status()
            if not status.running:
                manager.start()
    except Exception:
        # Best-effort autostart
        pass


@app.get("/api/go2rtc/config")
def go2rtc_get_config():
    content = manager.read_config()
    if content is None:
        raise HTTPException(status_code=404, detail="config not found")
    return JSONResponse({"content": content})


@app.put("/api/go2rtc/config")
def go2rtc_put_config(content: str = Body(..., embed=True)):
    manager.write_config(content)
    return {"message": "config updated", "config_path": manager.config_path}


@app.post("/api/go2rtc/start")
def go2rtc_start(content: Optional[str] = Body(None, embed=True)):
    if content is not None:
        manager.write_config(content)
    status = manager.start()
    if not status.running:
        raise HTTPException(status_code=400, detail=status.message or "failed to start go2rtc")
    return status.__dict__


@app.post("/api/go2rtc/stop")
def go2rtc_stop():
    return manager.stop().__dict__


@app.post("/api/go2rtc/reload")
def go2rtc_reload():
    status = manager.reload()
    if not status.running:
        raise HTTPException(status_code=400, detail=status.message or "go2rtc not running")
    return status.__dict__


@app.get("/api/settings")
def web_settings():
    return web_settings_store.all()


@app.put("/api/settings")
def web_settings_update(settings: dict = Body(...)):
    if not isinstance(settings, dict):
        raise HTTPException(status_code=400, detail="settings must be an object")
    web_settings_store.update(settings)
    return web_settings_store.all()


@app.get("/api/go2rtc/logs")
def go2rtc_logs(lines: int = Query(200, ge=1, le=2000)):
    content = manager.read_log_tail(lines)
    if content is None:
        raise HTTPException(status_code=404, detail="log file not found")
    abs_path = Path(manager.log_path).resolve()
    try:
        rel_path = abs_path.relative_to(Path.cwd())
    except ValueError:
        rel_path = abs_path
    return {"lines": content, "path": str(abs_path), "path_rel": str(rel_path), "count": len(content)}


@app.get("/api/go2rtc/logs/download")
def go2rtc_logs_download():
    if not manager.log_path.exists():
        raise HTTPException(status_code=404, detail="log file not found")
    return FileResponse(manager.log_path, media_type="text/plain", filename=manager.log_path.name)


@app.get("/api/web/logs")
def web_logs(lines: int = Query(200, ge=1, le=2000)):
    content = read_log_tail(web_log_path, lines)
    if content is None:
        raise HTTPException(status_code=404, detail="log file not found")
    abs_path = web_log_path
    try:
        rel_path = abs_path.relative_to(Path.cwd())
    except ValueError:
        rel_path = abs_path
    return {"lines": content, "path": str(abs_path), "path_rel": str(rel_path), "count": len(content)}


@app.get("/api/web/logs/download")
def web_logs_download():
    if not web_log_path.exists():
        raise HTTPException(status_code=404, detail="log file not found")
    return FileResponse(web_log_path, media_type="text/plain", filename=web_log_path.name)
