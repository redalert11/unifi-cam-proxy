import json
from pathlib import Path
from typing import Optional

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi import Depends
import requests
import yaml

try:
    from Unifi.utils.settings_manager import SettingsManager
except ImportError:
    from ..Unifi.utils.settings_manager import SettingsManager  # type: ignore

try:
    from .flight_check import check_url as flight_check_url
except ImportError:
    try:
        from Web.flight_check import check_url as flight_check_url
    except ImportError:
        flight_check_url = None  # type: ignore

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

GO2RTC_API = "http://127.0.0.1:1984"


def go2rtc_api(path: str, method: str = "GET", params=None, json_body=None, timeout: float = 4.0):
    url = f"{GO2RTC_API}{path}"
    resp = requests.request(method=method, url=url, params=params, json=json_body, timeout=timeout)
    resp.raise_for_status()
    try:
        return resp.json()
    except Exception:
        return resp.text

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


@app.get("/api/go2rtc/streams")
def go2rtc_list_streams():
    status = manager.status()
    if not status.running:
        start_status = manager.start()
        if not start_status.running:
            raise HTTPException(status_code=400, detail="go2rtc not running and failed to start")
    try:
        data = go2rtc_api("/api/streams")
        return {"streams": data}
    except requests.HTTPError as exc:
        detail = exc.response.text if exc.response is not None else str(exc)
        raise HTTPException(status_code=exc.response.status_code if exc.response else 500, detail=detail or "go2rtc error")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/go2rtc/streams")
def go2rtc_add_stream(name: str = Body(..., embed=True), src: str = Body(..., embed=True)):
    name = (name or "").strip()
    src = (src or "").strip()
    if not name or not src:
        raise HTTPException(status_code=400, detail="name and src are required")
    status = manager.status()
    if not status.running:
        start_status = manager.start()
        if not start_status.running:
            raise HTTPException(status_code=400, detail="go2rtc not running and failed to start")
    try:
        # go2rtc accepts POST /api/streams?name=<name>&src=<url>
        data = go2rtc_api("/api/streams", method="POST", params={"name": name, "src": src})
        return {"message": "stream added", "stream": name, "api_response": data}
    except requests.HTTPError as exc:
        detail = exc.response.text if exc.response is not None else str(exc)
        raise HTTPException(status_code=exc.response.status_code if exc.response else 500, detail=detail or "go2rtc error")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/go2rtc/streams/persist")
def go2rtc_persist_streams(streams: list[dict] = Body(..., embed=True)):
    """
    Add/overwrite stream entries in go2rtc config file under streams: {name: src}.
    """
    if not isinstance(streams, list) or not streams:
        raise HTTPException(status_code=400, detail="streams must be a non-empty list")
    # Validate entries
    cleaned = []
    for item in streams:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or "").strip()
        src = (item.get("src") or "").strip()
        if not name or not src:
            continue
        cleaned.append({"name": name, "src": src})
    if not cleaned:
        raise HTTPException(status_code=400, detail="no valid streams provided")

    content = manager.read_config() or ""
    try:
        data = yaml.safe_load(content) if content else {}
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("streams", {})
    if not isinstance(data["streams"], dict):
        data["streams"] = {}
    for entry in cleaned:
        data["streams"][entry["name"]] = entry["src"]
    new_content = yaml.safe_dump(data, sort_keys=False)
    manager.write_config(new_content)
    return {"message": "streams persisted", "count": len(cleaned), "config_path": manager.config_path, "content": new_content}


@app.get("/api/go2rtc/streams/{name}/probe")
def go2rtc_probe_stream(name: str):
    status = manager.status()
    if not status.running:
        raise HTTPException(status_code=400, detail="go2rtc not running")
    try:
        # Try direct stream info
        data = go2rtc_api(f"/api/streams/{name}")
        return data
    except requests.HTTPError as exc:
        # Fallback to query style with video/audio/microphone parameters
        try:
          data = go2rtc_api(
              "/api/streams",
              params={"src": name, "video": "all", "audio": "all", "microphone": ""},
              timeout=8.0,
          )
          return data
        except Exception:
          detail = exc.response.text if exc.response is not None else str(exc)
        raise HTTPException(status_code=exc.response.status_code if exc.response else 500, detail=detail or "go2rtc error")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/go2rtc/onvif/discover")
def go2rtc_onvif_discover():
    status = manager.status()
    if not status.running:
        start_status = manager.start()
        if not start_status.running:
            raise HTTPException(status_code=400, detail="go2rtc not running and failed to start")
    def normalize_devices(payload):
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            if "sources" in payload and isinstance(payload["sources"], list):
                return payload["sources"]
            if "items" in payload and isinstance(payload["items"], list):
                return payload["items"]
            if "devices" in payload and isinstance(payload["devices"], list):
                return payload["devices"]
            # Some builds may return map keyed by host
            if all(isinstance(v, dict) for v in payload.values()):
                return list(payload.values())
        return []

    # Try ONVIF-specific endpoint first, then common discovery variants (GET/POST)
    attempts = [
        ("GET", "/api/onvif"),
        ("POST", "/api/onvif"),
        ("GET", "/api/onvif/discover"),
        ("POST", "/api/onvif/discover"),
        ("GET", "/api/discovery"),
        ("POST", "/api/discovery"),
        ("GET", "/api/discover"),
        ("POST", "/api/discover"),
        ("GET", "/onvif/discover"),
        ("GET", "/discover"),
    ]
    last_err = None
    tried = []
    for method, path in attempts:
        tried.append(f"{method} {path}")
        try:
            data = go2rtc_api(path, method=method, timeout=8.0)
            return {"devices": normalize_devices(data), "source": f"{method} {path}", "tried": tried}
        except requests.HTTPError as exc:
            last_err = exc
            # If this endpoint exists but method not allowed, continue to next
            if exc.response is not None and exc.response.status_code in (404, 405):
                continue
            # otherwise capture and stop trying
            continue
        except Exception as exc:
            last_err = exc
            continue

    if isinstance(last_err, requests.HTTPError):
        detail = last_err.response.text if last_err.response is not None else str(last_err)
        raise HTTPException(
            status_code=last_err.response.status_code if last_err.response else 500,
            detail=detail or f"go2rtc error; tried {', '.join(tried)}",
        )
    raise HTTPException(status_code=500, detail=str(last_err or f"go2rtc discovery failed; tried {', '.join(tried)}"))


@app.post("/api/go2rtc/onvif/add")
def go2rtc_onvif_add(
    name: str = Body(..., embed=True),
    host: str = Body(..., embed=True),
    username: str = Body("", embed=True),
    password: str = Body("", embed=True),
):
    name = (name or "").strip()
    host = (host or "").strip()
    if not name or not host:
        raise HTTPException(status_code=400, detail="name and host are required")
    status = manager.status()
    if not status.running:
        start_status = manager.start()
        if not start_status.running:
            raise HTTPException(status_code=400, detail="go2rtc not running and failed to start")
    # Build ONVIF source URL
    creds = ""
    if username:
        creds = username
        if password:
            creds += f":{password}"
        creds += "@"
    # Accept host already prefixed
    if host.startswith("onvif://"):
        src = host
    else:
        src = f"onvif://{creds}{host}"
    try:
        data = go2rtc_api("/api/streams", method="POST", params={"name": name, "src": src})
        return {"message": "stream added", "stream": name, "api_response": data, "src": src}
    except requests.HTTPError as exc:
        detail = exc.response.text if exc.response is not None else str(exc)
        raise HTTPException(status_code=exc.response.status_code if exc.response else 500, detail=detail or "go2rtc error")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/go2rtc/onvif/profiles")
def go2rtc_onvif_profiles(
    host: str = Body(..., embed=True),
    username: str = Body("", embed=True),
    password: str = Body("", embed=True),
):
    host = (host or "").strip()
    if not host:
        raise HTTPException(status_code=400, detail="host is required")
    status = manager.status()
    if not status.running:
        start_status = manager.start()
        if not start_status.running:
            raise HTTPException(status_code=400, detail="go2rtc not running and failed to start")

    # Normalize/merge credentials and host
    from urllib.parse import urlparse

    parsed = urlparse(host if host.startswith("onvif://") else f"onvif://{host}")
    # Prefer user-supplied creds; fall back to embedded creds if provided
    user = username or (parsed.username or "")
    pwd = password or (parsed.password or "")
    creds = ""
    if user:
        creds = user
        if pwd:
            creds += f":{pwd}"
        creds += "@"

    netloc = parsed.hostname or parsed.netloc or ""
    if parsed.port:
        netloc += f":{parsed.port}"
    path = parsed.path or ""
    query = f"?{parsed.query}" if parsed.query else ""
    src = f"onvif://{creds}{netloc}{path}{query}"

    params = {"src": src}

    try:
        data = go2rtc_api("/api/onvif", params=params, timeout=8.0)
        sources = []
        if isinstance(data, dict) and "sources" in data and isinstance(data["sources"], list):
            sources = data["sources"]
        return {"sources": sources, "src": src}
    except requests.HTTPError as exc:
        detail = exc.response.text if exc.response is not None else str(exc)
        raise HTTPException(status_code=exc.response.status_code if exc.response else 500, detail=detail or "go2rtc error")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/flightcheck/{name}")
def flightcheck_stream(name: str, url: Optional[str] = Query(None, description="Optional full URL to probe")):
    if flight_check_url is None:
        raise HTTPException(status_code=500, detail="flight_check not available")
    status = manager.status()
    if not status.running:
        # best-effort start
        manager.start()
    target_url = url or f"http://127.0.0.1:1984/api/stream.flv?src={name}"
    result = flight_check_url(target_url)
    return result


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
