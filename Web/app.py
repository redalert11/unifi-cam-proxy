import json
import hashlib
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlparse
from datetime import datetime

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi import Depends
import requests
import yaml

try:
    from Unifi.utils.settings_manager import SettingsManager
    from Unifi.camera_data.camera_settings import CameraSettings
    from Unifi.utils.logging_utils import get_log_buffer, list_log_sources, setup_logger
except ImportError:
    from ..Unifi.utils.settings_manager import SettingsManager  # type: ignore
    from ..Unifi.utils.logging_utils import get_log_buffer, list_log_sources, setup_logger  # type: ignore

try:
    from Unifi.camera_data.camera_models import CameraModelDatabase
except ImportError:
    CameraModelDatabase = None  # type: ignore

try:
    from .flight_check import check_url as flight_check_url
    from .flight_check import resolve_mac as flight_check_resolve_mac
    from .flight_check import apply_onvif_encoder_settings as onvif_apply_encoder_settings
    from .flight_check import apply_onvif_encoder_for_profile as onvif_apply_encoder_for_profile
    from .flight_check import apply_onvif_encoder_max_resolution as onvif_apply_encoder_max_resolution
except ImportError:
    try:
        from Web.flight_check import check_url as flight_check_url
        from Web.flight_check import resolve_mac as flight_check_resolve_mac
        from Web.flight_check import apply_onvif_encoder_settings as onvif_apply_encoder_settings
        from Web.flight_check import apply_onvif_encoder_for_profile as onvif_apply_encoder_for_profile
        from Web.flight_check import apply_onvif_encoder_max_resolution as onvif_apply_encoder_max_resolution
    except ImportError:
        flight_check_url = None  # type: ignore
        flight_check_resolve_mac = None  # type: ignore
        onvif_apply_encoder_settings = None  # type: ignore
        onvif_apply_encoder_for_profile = None  # type: ignore
        onvif_apply_encoder_max_resolution = None  # type: ignore

try:
    from .go2rtc_manager import Go2RTCManager
except ImportError:
    # Fallbacks for script execution without package context
    try:
        from Web.go2rtc_manager import Go2RTCManager
    except ImportError:
        from go2rtc_manager import Go2RTCManager

try:
    from Unifi.services.runtime import ServiceRuntime
    from Unifi.services.upload_service import UploadService
except ImportError:
    try:
        from .services.runtime import ServiceRuntime  # type: ignore
        from .services.upload_service import UploadService  # type: ignore
    except ImportError:
        ServiceRuntime = None  # type: ignore
        UploadService = None  # type: ignore

app = FastAPI(title="UniFi Cam Proxy Supervisor", version="0.1.0")

static_dir = Path(__file__).parent / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")

web_settings_path = Path(__file__).resolve().parent / "data" / "web_settings.json"
web_settings_store = SettingsManager(
    web_settings_path,
    defaults={
        "autostart_go2rtc": False,
        "save_go2rtc_logs": True,
        "save_web_logs": True,
    },
)

process_state_path = Path(__file__).resolve().parent / "data" / "process_state.json"
process_state_store = SettingsManager(
    process_state_path,
    defaults={
        "discovery": {
            "running": False,
            "camera": "",
            "started_at": "",
            "last_error": "",
        },
        "cameras": {},
    },
)

manager = Go2RTCManager(log_to_disk=web_settings_store.get("save_go2rtc_logs", True))

GO2RTC_API = "http://127.0.0.1:1984"


def go2rtc_api(path: str, method: str = "GET", params=None, json_body=None, timeout: float = 4.0):
    url = f"{GO2RTC_API}{path}"
    resp = requests.request(method=method, url=url, params=params, json=json_body, timeout=timeout)
    resp.raise_for_status()
    try:
        return resp.json()
    except Exception:
        return resp.text


def _extract_stream_source_url(stream_payload, stream_name: str) -> str:
    """
    Best-effort extraction of the first producer URL for a stream name.
    """
    if isinstance(stream_payload, dict) and "producers" in stream_payload:
        producers = stream_payload.get("producers") or []
        if isinstance(producers, list) and producers:
            first = producers[0]
            if isinstance(first, dict):
                return str(first.get("url") or "")
            return str(first)
    # Fallback: some go2rtc builds return a dict keyed by stream name
    if isinstance(stream_payload, dict) and stream_name in stream_payload:
        entry = stream_payload.get(stream_name)
        if isinstance(entry, dict):
            return _extract_stream_source_url(entry, stream_name)
    return ""


def _extract_stream_source_by_index(stream_payload, stream_name: str, index: int) -> str:
    if isinstance(stream_payload, dict) and "producers" in stream_payload:
        producers = stream_payload.get("producers") or []
        if isinstance(producers, list) and len(producers) > index:
            entry = producers[index]
            if isinstance(entry, dict):
                return str(entry.get("url") or "")
            return str(entry)
    if isinstance(stream_payload, dict) and "streams" in stream_payload:
        entry = stream_payload.get("streams", {}).get(stream_name)
        if entry is not None:
            return _extract_stream_source_by_index(entry, stream_name, index)
    if isinstance(stream_payload, dict) and stream_name in stream_payload:
        entry = stream_payload.get(stream_name)
        if entry is not None:
            return _extract_stream_source_by_index(entry, stream_name, index)
    return ""


def _extract_stream_source_from_config(stream_name: str) -> str:
    """
    Read go2rtc config and return the first source URL for a stream name.
    """
    content = manager.read_config() or ""
    try:
        data = yaml.safe_load(content) if content else {}
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    streams = data.get("streams")
    if not isinstance(streams, dict):
        return ""
    entry = streams.get(stream_name)
    if entry is None:
        return ""
    if isinstance(entry, list):
        return str(entry[0]) if entry else ""
    if isinstance(entry, dict):
        # go2rtc supports {source: ...} or {urls: [...]}
        if "source" in entry:
            return str(entry.get("source") or "")
        if "urls" in entry and isinstance(entry["urls"], list) and entry["urls"]:
            return str(entry["urls"][0])
        # if nested name, ignore
        return ""
    return str(entry)


def _get_stream_source_from_config(stream_name: str, channel: int | None = None) -> str:
    content = manager.read_config() or ""
    try:
        data = yaml.safe_load(content) if content else {}
    except Exception:
        data = {}
    if not isinstance(data, dict):
        return ""
    streams = data.get("streams")
    if not isinstance(streams, dict):
        return ""
    entry = streams.get(stream_name)
    if entry is None:
        return ""
    if isinstance(entry, list):
        if channel is not None and channel < len(entry):
            return str(entry[channel])
        return str(entry[0]) if entry else ""
    if isinstance(entry, dict):
        if "source" in entry:
            return str(entry.get("source") or "")
        if "urls" in entry and isinstance(entry["urls"], list) and entry["urls"]:
            return str(entry["urls"][0])
        return ""
    return str(entry)


def _yaml_quote(value: str) -> str:
    if value == "" or any(ch in value for ch in [' ', '"', "'", "#", ":", "&", "?", "[", "]", "{", "}", ","]):
        escaped = value.replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _upsert_stream_config(content: str, name: str, src: str) -> str:
    lines = content.splitlines()
    streams_idx = next((i for i, line in enumerate(lines) if line.strip() == "streams:"), None)
    if streams_idx is None:
        if lines and lines[-1].strip():
            lines.append("")
        streams_idx = len(lines)
        lines.append("streams:")
    insert_at = streams_idx + 1
    while insert_at < len(lines):
        line = lines[insert_at]
        if line.strip() == "":
            insert_at += 1
            continue
        if not line.startswith(" "):
            break
        insert_at += 1

    def _find_stream_block() -> tuple[int | None, int | None]:
        start = None
        end = None
        for i in range(streams_idx + 1, len(lines)):
            line = lines[i]
            if line.strip() == "":
                continue
            if not line.startswith(" "):
                break
            if line.startswith("  ") and not line.startswith("    "):
                key = line.strip().split(":", 1)[0]
                if key == name:
                    start = i
                    end = i + 1
                    while end < len(lines):
                        next_line = lines[end]
                        if next_line.strip() == "":
                            end += 1
                            continue
                        if not next_line.startswith(" "):
                            break
                        if next_line.startswith("  ") and not next_line.startswith("    "):
                            break
                        end += 1
                    return start, end
        return None, None

    entry_line = f"  {name}: {_yaml_quote(src)}"
    start, end = _find_stream_block()
    if start is not None and end is not None:
        lines[start:end] = [entry_line]
    else:
        lines[insert_at:insert_at] = [entry_line]

    return "\n".join(lines) + ("\n" if content.endswith("\n") or not content else "")


def _resolve_mac_for_stream(stream_name: str, stream_channel: int | None, mac_mode: str) -> dict:
    if mac_mode not in ("lookup", "random"):
        raise HTTPException(status_code=400, detail="macMode must be lookup or random")
    if mac_mode == "lookup" and not stream_name:
        raise HTTPException(status_code=400, detail="stream is required for MAC lookup")
    if flight_check_resolve_mac is None:
        raise HTTPException(status_code=500, detail="MAC resolver not available")

    stream_url = ""
    if stream_name:
        stream_url = f"{GO2RTC_API}/api/stream.flv?src={stream_name}"
        if stream_channel is not None:
            stream_url += f"&channel={stream_channel}"

    if mac_mode == "lookup":
        source_url = _extract_stream_source_from_config(stream_name) if stream_name else ""
        if not source_url:
            stream_payload = go2rtc_api(f"/api/streams/{stream_name}") if stream_name else {}
            source_url = _extract_stream_source_url(stream_payload, stream_name)
        if not source_url:
            raise HTTPException(status_code=400, detail="MAC lookup failed: stream source URL not found")
        mac_report = flight_check_resolve_mac(source_url, mac_mode=mac_mode)
    else:
        mac_report = flight_check_resolve_mac(stream_url, mac_mode=mac_mode)
    mac_value = mac_report.get("value")
    if not mac_value:
        raise HTTPException(status_code=400, detail="MAC lookup failed")
    mac_value = str(mac_value).upper()
    return {"mac": mac_value, "report": mac_report}

web_log_path = (Path(__file__).resolve().parent.parent / "logs" / "webserver.log").resolve()
WEB_LOG_MAX_BYTES = 5_000_000
WEB_LOG_BACKUP_COUNT = 3

_unifi_runtime = None
_unifi_runtime_settings = None
_wss_runtimes = {}
_upload_service = None
_upload_logger = None


def _get_unifi_runtime():
    global _unifi_runtime
    if ServiceRuntime is None:
        raise HTTPException(status_code=500, detail="Unifi runtime not available")
    if _unifi_runtime is None:
        _unifi_runtime = ServiceRuntime()
    return _unifi_runtime


def _sync_discovery_state(runtime: "ServiceRuntime"):
    actual_running = bool(runtime.discovery_service.status().running)
    recorded_running = bool(process_state_store.get("discovery.running", False))
    if recorded_running and not actual_running:
        process_state_store.update(
            {
                "discovery.running": False,
                "discovery.camera": "",
                "discovery.last_error": "",
            }
        )
    elif actual_running and not recorded_running:
        process_state_store.update({"discovery.running": True})


def _set_unifi_runtime(settings_path: Path):
    global _unifi_runtime, _unifi_runtime_settings
    if ServiceRuntime is None:
        raise HTTPException(status_code=500, detail="Unifi runtime not available")
    runtime = ServiceRuntime(CameraSettings(settings_file=str(settings_path)))
    _unifi_runtime = runtime
    _unifi_runtime_settings = str(settings_path)
    return runtime


def _get_upload_service():
    global _upload_service, _upload_logger
    if UploadService is None:
        return None
    if _upload_service is None:
        if _upload_logger is None:
            _upload_logger = setup_logger("upload_server", logging.INFO)
        _upload_service = UploadService(_upload_logger)
    return _upload_service


def _get_wss_runtime(settings_path: Path):
    key = settings_path.name
    runtime = _wss_runtimes.get(key)
    if runtime is None:
        runtime = ServiceRuntime(CameraSettings(settings_file=str(settings_path)))
        _wss_runtimes[key] = runtime
    return runtime


def _set_web_file_logging(enabled: bool):
    """
    Enable/disable uvicorn file logging at runtime by attaching/removing the rotating file handler.
    """
    loggers = ["uvicorn", "uvicorn.error", "uvicorn.access"]
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    for name in loggers:
        lg = logging.getLogger(name)
        # remove handlers pointing at our log file
        for h in list(lg.handlers):
            fname = getattr(h, "baseFilename", None)
            if fname and Path(fname) == web_log_path:
                if not enabled:
                    lg.removeHandler(h)
                    try:
                        h.close()
                    except Exception:
                        pass
        if not enabled:
            continue
        # attach if missing
        has_handler = any(
            isinstance(h, RotatingFileHandler) and Path(getattr(h, "baseFilename", "")) == web_log_path
            for h in lg.handlers
        )
        if not has_handler:
            web_log_path.parent.mkdir(parents=True, exist_ok=True)
            fh = RotatingFileHandler(web_log_path, maxBytes=WEB_LOG_MAX_BYTES, backupCount=WEB_LOG_BACKUP_COUNT)
            fh.setFormatter(formatter)
            lg.addHandler(fh)

# Apply saved preference on startup (module import)
_set_web_file_logging(web_settings_store.get("save_web_logs", True))


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
        src = item.get("src")
        src_list = None
        if isinstance(src, (list, tuple)):
            src_list = [s for s in (str(v).strip() for v in src) if s]
            src = ""
        else:
            src = (src or "").strip()
        if not name or (not src and not src_list):
            continue
        comment = (item.get("comment") or "").strip()
        cleaned.append({"name": name, "src": src, "src_list": src_list, "comment": comment})
    if not cleaned:
        raise HTTPException(status_code=400, detail="no valid streams provided")

    content = manager.read_config() or ""
    try:
        data = yaml.safe_load(content) if content else {}
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    streams = data.get("streams") if isinstance(data, dict) else {}
    if not isinstance(streams, dict):
        streams = {}
    existing = set(streams.keys())

    lines = content.splitlines()
    streams_idx = next((i for i, line in enumerate(lines) if line.strip() == "streams:"), None)
    if streams_idx is None:
        if lines and lines[-1].strip():
            lines.append("")
        streams_idx = len(lines)
        lines.append("streams:")
    insert_at = streams_idx + 1
    while insert_at < len(lines):
        line = lines[insert_at]
        if line.strip() == "":
            insert_at += 1
            continue
        if not line.startswith(" "):
            break
        insert_at += 1

    added = 0
    new_lines = []
    for entry in cleaned:
        name = entry["name"]
        if name in existing:
            continue
        comment = entry.get("comment")
        if comment:
            new_lines.append(f"  #{comment}")
        if entry.get("src_list"):
            new_lines.append(f"  {name}:")
            for src_item in entry["src_list"]:
                new_lines.append(f"    - {src_item}")
        else:
            new_lines.append(f"  {name}: {entry['src']}")
        added += 1

    if new_lines:
        lines[insert_at:insert_at] = new_lines
        content = "\n".join(lines) + ("\n" if content.endswith("\n") or not content else "")
        manager.write_config(content)

    return {"message": "streams persisted", "count": added, "config_path": manager.config_path, "content": content}


@app.post("/api/go2rtc/streams/force-transcode")
def go2rtc_force_transcode(payload: dict = Body(...)):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="stream name is required")
    channel = payload.get("channel")
    if isinstance(channel, str) and channel.isdigit():
        channel = int(channel)
    elif not isinstance(channel, int):
        channel = None

    src = _get_stream_source_from_config(name, channel=channel)
    if not src:
        raise HTTPException(status_code=404, detail="stream source not found in config")
    if src.startswith(GO2RTC_API) or "/api/stream.flv?src=" in src or src.startswith("tapo://") or src.startswith("onvif://"):
        try:
            stream_payload = go2rtc_api(f"/api/streams/{name}")
            producer_url = _extract_stream_source_by_index(stream_payload, name, channel or 0)
            if producer_url:
                src = producer_url
        except Exception:
            pass
    if src.startswith("tapo://") or src.startswith("onvif://"):
        raise HTTPException(status_code=400, detail="ffmpeg requires RTSP/HTTP source; stream source is not compatible")
    if src.startswith("exec:"):
        raise HTTPException(status_code=400, detail="stream already uses exec")

    summary = payload.get("summary") or {}
    video = summary.get("video") or {}
    width = video.get("width")
    height = video.get("height")
    fps = (video.get("avg_frame_rate") or {}).get("value") or (video.get("r_frame_rate") or {}).get("value")
    try:
        width = int(width) if width else None
    except Exception:
        width = None
    try:
        height = int(height) if height else None
    except Exception:
        height = None
    try:
        fps = float(fps) if fps else None
    except Exception:
        fps = None

    if width and width >= 1600:
        bitrate = "4000k"
    elif width and width >= 1280:
        bitrate = "2500k"
    elif width and width >= 960:
        bitrate = "1500k"
    else:
        bitrate = "800k"

    size_arg = f"-s {width}x{height}" if width and height else ""
    fps_arg = f"-r {int(round(fps))}" if fps else ""

    cmd = (
        f"exec:ffmpeg -hide_banner -rtsp_transport tcp -i {src} "
        f"-c:v libx264 -profile:v main -level:v 4.1 -preset ultrafast -bf 0 "
        f"-pix_fmt yuvj420p {size_arg} {fps_arg} -b:v {bitrate} -g 40 -keyint_min 20 "
        f"-an -f flv -"
    )
    cmd = " ".join(cmd.split())

    content = manager.read_config() or ""
    updated = _upsert_stream_config(content, name, cmd)
    manager.write_config(updated)
    status = manager.reload()
    if not status.running:
        status = manager.start()
        if not status.running:
            raise HTTPException(status_code=400, detail=status.message or "failed to restart go2rtc")
    return {"message": "transcode enabled", "name": name, "cmd": cmd}


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


@app.post("/api/hash/sha256")
def hash_sha256(value: str = Body(..., embed=True)):
    if value is None:
        raise HTTPException(status_code=400, detail="value is required")
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest().upper()
    return {"hash": digest}


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

    def _ensure_rtsp_creds(uri: str, user: str, pwd: str) -> str:
        if not uri or not user:
            return uri
        try:
            parsed_uri = urlparse(uri)
            if parsed_uri.scheme.lower() != "rtsp":
                return uri
            if parsed_uri.username:
                return uri
            netloc = parsed_uri.hostname or parsed_uri.netloc or ""
            if parsed_uri.port:
                netloc += f":{parsed_uri.port}"
            creds = user if not pwd else f"{user}:{pwd}"
            netloc = f"{creds}@{netloc}"
            return parsed_uri._replace(netloc=netloc).geturl()
        except Exception:
            return uri

    try:
        data = go2rtc_api("/api/onvif", params=params, timeout=8.0)
        sources = []
        if isinstance(data, dict) and "sources" in data and isinstance(data["sources"], list):
            sources = data["sources"]
        profile_streams = {}
        try:
            try:
                from .onvif_client import OnvifSoapClient
            except ImportError:
                from Web.onvif_client import OnvifSoapClient  # type: ignore
            client = OnvifSoapClient(
                host=parsed.hostname or host,
                port=parsed.port or 80,
                username=user,
                password=pwd,
                https=parsed.scheme == "onvifs",
            )
            device_url = client.endpoint("/onvif/device_service")
            media_url = client.get_media_xaddr(device_url) or device_url
            profiles = client.get_profiles(media_url, include_streams=True)
            for prof in profiles:
                token = prof.get("token")
                uri = prof.get("stream_uri")
                if uri:
                    uri = _ensure_rtsp_creds(str(uri), user, pwd)
                if token and uri:
                    profile_streams[str(token)] = str(uri)
        except Exception:
            profile_streams = {}
        if profile_streams:
            for src in sources:
                url = src.get("url") if isinstance(src, dict) else ""
                if not url:
                    continue
                parsed_url = urlparse(url)
                query = dict(parse_qsl(parsed_url.query))
                token = query.get("subtype") or query.get("profile") or query.get("token")
                if token and token in profile_streams:
                    src["stream_uri"] = profile_streams[token]
        return {"sources": sources, "src": src}
    except requests.HTTPError as exc:
        detail = exc.response.text if exc.response is not None else str(exc)
        raise HTTPException(status_code=exc.response.status_code if exc.response else 500, detail=detail or "go2rtc error")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/onvif/encoder/apply")
def onvif_apply_encoder(payload: dict = Body(...)):
    if onvif_apply_encoder_settings is None:
        raise HTTPException(status_code=500, detail="ONVIF encoder setter not available")
    host = (payload.get("host") or "").strip()
    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""
    config = payload.get("config") or {}
    if not host or not username or not password:
        raise HTTPException(status_code=400, detail="host, username, and password are required")
    if not isinstance(config, dict) or not config:
        raise HTTPException(status_code=400, detail="config is required")
    port = payload.get("port")
    if isinstance(port, str) and port.isdigit():
        port = int(port)
    elif not isinstance(port, int):
        port = None
    result = onvif_apply_encoder_settings(
        host=host,
        username=username,
        password=password,
        config=config,
        port=port,
        is_tapo=bool(payload.get("is_tapo")),
        device_path=payload.get("device_path") or "/onvif/device_service",
        https=bool(payload.get("https")),
        auth_mode=payload.get("auth_mode") or "digest",
        wsse_mode=payload.get("wsse_mode") or "digest",
    )
    return result


@app.post("/api/onvif/encoder/apply-for-stream")
def onvif_apply_encoder_for_stream(payload: dict = Body(...)):
    if onvif_apply_encoder_for_profile is None:
        raise HTTPException(status_code=500, detail="ONVIF encoder setter not available")
    host = (payload.get("host") or "").strip()
    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""
    width = payload.get("width")
    height = payload.get("height")
    if not host or not username or not password:
        raise HTTPException(status_code=400, detail="host, username, and password are required")
    try:
        width = int(width)
        height = int(height)
    except Exception:
        raise HTTPException(status_code=400, detail="width and height are required")
    port = payload.get("port")
    if isinstance(port, str) and port.isdigit():
        port = int(port)
    elif not isinstance(port, int):
        port = None
    result = onvif_apply_encoder_for_profile(
        host=host,
        username=username,
        password=password,
        width=width,
        height=height,
        port=port,
        is_tapo=bool(payload.get("is_tapo")),
        device_path=payload.get("device_path") or "/onvif/device_service",
        https=bool(payload.get("https")),
        auth_mode=payload.get("auth_mode") or "digest",
        wsse_mode=payload.get("wsse_mode") or "digest",
        profile=payload.get("profile") or "Main",
        quality=payload.get("quality"),
        framerate_limit=payload.get("framerate_limit"),
        bitrate=payload.get("bitrate"),
        gov_length=payload.get("gov_length"),
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "ONVIF apply failed")
    return result


@app.post("/api/onvif/encoder/apply-max")
def onvif_apply_encoder_max(payload: dict = Body(...)):
    if onvif_apply_encoder_max_resolution is None:
        raise HTTPException(status_code=500, detail="ONVIF encoder setter not available")
    host = (payload.get("host") or "").strip()
    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""
    if not host or not username or not password:
        raise HTTPException(status_code=400, detail="host, username, and password are required")
    port = payload.get("port")
    if isinstance(port, str) and port.isdigit():
        port = int(port)
    elif not isinstance(port, int):
        port = None
    result = onvif_apply_encoder_max_resolution(
        host=host,
        username=username,
        password=password,
        port=port,
        is_tapo=bool(payload.get("is_tapo")),
        device_path=payload.get("device_path") or "/onvif/device_service",
        https=bool(payload.get("https")),
        auth_mode=payload.get("auth_mode") or "digest",
        wsse_mode=payload.get("wsse_mode") or "digest",
        profile=payload.get("profile") or "Main",
        quality=payload.get("quality"),
        framerate_limit=payload.get("framerate_limit"),
        bitrate=payload.get("bitrate"),
        gov_length=payload.get("gov_length"),
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "ONVIF apply failed")
    return result


@app.get("/api/flightcheck/{name}")
def flightcheck_stream(
    name: str,
    url: Optional[str] = Query(None, description="Optional full URL to probe"),
    channel: Optional[int] = Query(None, description="Optional go2rtc channel index"),
    full: bool = Query(False, description="Include full ffprobe payload and summary"),
    mac: str = Query("lookup", description="MAC source: lookup or random"),
):
    if flight_check_url is None:
        raise HTTPException(status_code=500, detail="flight_check not available")
    status = manager.status()
    if not status.running:
        # best-effort start
        manager.start()
    target_url = url or f"http://127.0.0.1:1984/api/stream.flv?src={name}"
    if channel is not None:
        try:
            parsed = urlparse(target_url)
            query = dict(parse_qsl(parsed.query))
            query["channel"] = str(channel)
            target_url = parsed._replace(query=urlencode(query)).geturl()
        except Exception:
            joiner = "&" if "?" in target_url else "?"
            target_url = f"{target_url}{joiner}channel={channel}"
    result = flight_check_url(target_url, full=full, mac_mode=mac)
    return result


@app.get("/api/unifi/status")
def unifi_status():
    runtime = _get_unifi_runtime()
    _sync_discovery_state(runtime)
    status = runtime.status()
    discovery_state = process_state_store.all().get("discovery", {})
    mgmt_initialized = bool(runtime.settings.get("mgmt.initialized", False))
    mgmt_last_adopted = runtime.settings.get("mgmt.lastAdoptedAt", "")
    wss_active = []
    for key, rt in _wss_runtimes.items():
        try:
            if rt.wss_service.status().running:
                wss_active.append(key)
        except Exception:
            continue
    return {
        "api": status.api.__dict__,
        "discovery": status.discovery.__dict__,
        "wss": status.wss.__dict__,
        "upload": status.upload.__dict__,
        "management": {
            "initialized": mgmt_initialized,
            "lastAdoptedAt": mgmt_last_adopted,
        },
        "active_settings": _unifi_runtime_settings,
        "wss_active": wss_active,
        "discovery_lock": discovery_state,
    }


@app.get("/api/unifi/logs/sources")
def unifi_log_sources(prefix: str | None = Query(None)):
    sources = list_log_sources(prefix=prefix)
    return {"sources": sources}


@app.get("/api/unifi/logs")
def unifi_logs(
    source: str = Query(..., description="Logger name (e.g., wss.<MAC> or api_https)"),
    lines: int = Query(200, ge=1, le=2000),
):
    if not source:
        raise HTTPException(status_code=400, detail="source is required")
    output = get_log_buffer(source, max_lines=lines)
    return {"source": source, "lines": output}


@app.get("/api/unifi/camera-models")
def unifi_camera_models():
    if CameraModelDatabase is None:
        raise HTTPException(status_code=500, detail="Camera model database not available")
    models = sorted(set(CameraModelDatabase.CameraPlatformsByType.keys()))
    return {"models": models, "eol": CameraModelDatabase.EOLCameraTypes}


@app.post("/api/unifi/settings/resolve-mac")
def unifi_resolve_mac(payload: dict = Body(...)):
    mac_mode = (payload.get("macMode") or "lookup").strip().lower()
    stream = payload.get("stream")
    if stream is None:
        streams = payload.get("streams") or []
        stream = streams[0] if streams else ""
    stream_name = ""
    stream_channel = None
    if isinstance(stream, str):
        stream_name = stream
    elif isinstance(stream, dict):
        stream_name = stream.get("name") or ""
        stream_channel = stream.get("channel")
    if "::" in stream_name:
        name_part, channel_part = stream_name.split("::", 1)
        stream_name = name_part
        if channel_part.isdigit():
            stream_channel = int(channel_part)

    resolved = _resolve_mac_for_stream(stream_name, stream_channel, mac_mode)
    mac_value = resolved["mac"]
    filename = f"{mac_value.replace(':', '').upper()}_Settings.json"
    out_dir = Path(__file__).resolve().parent.parent / "Unifi" / "camera_data"
    exists = (out_dir / filename).exists()

    return {
        "mac": mac_value,
        "filename": filename,
        "exists": exists,
    }


@app.post("/api/unifi/settings/generate")
def unifi_generate_settings(payload: dict = Body(...)):
    if CameraModelDatabase is None:
        raise HTTPException(status_code=500, detail="Camera model database not available")
    model = (payload.get("model") or "").strip()
    if not model:
        raise HTTPException(status_code=400, detail="model is required")
    mac_mode = (payload.get("macMode") or "lookup").strip().lower()
    streams = payload.get("streams") or []
    first_stream = next((s for s in streams if s), "")
    stream_name = ""
    stream_channel = None
    if isinstance(first_stream, str):
        stream_name = first_stream
    elif isinstance(first_stream, dict):
        stream_name = first_stream.get("name") or ""
        stream_channel = first_stream.get("channel")
    if "::" in stream_name:
        name_part, channel_part = stream_name.split("::", 1)
        stream_name = name_part
        if channel_part.isdigit():
            stream_channel = int(channel_part)

    mac_value = (payload.get("mac") or "").strip().upper()
    if not mac_value:
        resolved = _resolve_mac_for_stream(stream_name, stream_channel, mac_mode)
        mac_value = resolved["mac"]

    from Unifi.camera_data.camera_settings import CameraSettings

    filename = f"{mac_value.replace(':', '').upper()}_Settings.json"
    out_dir = Path(__file__).resolve().parent.parent / "Unifi" / "camera_data"
    out_path = out_dir / filename
    if out_path.exists():
        raise HTTPException(status_code=409, detail="File already exists")

    streams_section = {}
    mapped = []
    summaries = payload.get("streamSummaries") or []
    for idx, entry in enumerate(streams):
        if not entry:
            continue
        if isinstance(entry, dict):
            stream_name = entry.get("name") or ""
            stream_channel = entry.get("channel")
        else:
            stream_name = entry
            stream_channel = None
        if "::" in stream_name:
            name_part, channel_part = stream_name.split("::", 1)
            stream_name = name_part
            if channel_part.isdigit():
                stream_channel = int(channel_part)
        if not stream_name:
            continue
        channel_label = f"{stream_name}:{stream_channel}" if stream_channel is not None else stream_name
        stream_url = f"{GO2RTC_API}/api/stream.flv?src={stream_name}" + (
            f"&channel={stream_channel}" if stream_channel is not None else ""
        )
        summary = {}
        if idx < len(summaries) and isinstance(summaries[idx], dict):
            summary = summaries[idx]
        elif flight_check_url is not None:
            try:
                report = flight_check_url(stream_url, full=True)
                summary = report.get("summary") or {}
            except Exception:
                summary = {}
        video = summary.get("video") or {}
        audio = summary.get("audio") or {}
        streams_section[f"stream{idx}"] = {
            "codec": video.get("codec"),
            "profile": video.get("profile"),
            "level": video.get("level"),
            "pixFmt": video.get("pix_fmt"),
            "width": video.get("width"),
            "height": video.get("height"),
            "fps": (video.get("r_frame_rate") or {}).get("value"),
            "avgFps": (video.get("avg_frame_rate") or {}).get("value"),
            "bitrate": video.get("bitrate"),
            "container": summary.get("container"),
            "go2rtcChannel": stream_name,
            "streamUrl": stream_url,
            "audioCodec": audio.get("codec"),
            "audioSampleRate": audio.get("sample_rate"),
            "audioChannels": audio.get("channels"),
        }
        mapped.append(channel_label)

    firmware_version = (payload.get("firmwareVersion") or "").strip()
    if not firmware_version:
        firmware_version = CameraSettings.fetch_latest_firmware_version(status="GA")

    settings_payload = CameraSettings.build_settings(
        market_name=model,
        mac=mac_value,
        host=payload.get("host") or "",
        firmware_version=firmware_version,
        streams=streams_section,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(settings_payload, indent=2), encoding="utf-8")

    return {
        "message": "settings generated",
        "path": str(out_path),
        "filename": filename,
        "mac": mac_value,
        "streams": mapped,
    }


@app.delete("/api/unifi/settings/delete")
def unifi_delete_settings(payload: dict = Body(...)):
    filename = (payload.get("filename") or "").strip()
    if not filename or not filename.endswith("_Settings.json"):
        raise HTTPException(status_code=400, detail="invalid filename")
    base_dir = (Path(__file__).resolve().parent.parent / "Unifi" / "camera_data").resolve()
    target = (base_dir / filename).resolve()
    if base_dir not in target.parents:
        raise HTTPException(status_code=400, detail="invalid filename")
    if not target.exists():
        raise HTTPException(status_code=404, detail="file not found")
    target.unlink()
    return {"message": "deleted", "filename": filename}


@app.post("/api/unifi/runtime/start")
def unifi_runtime_start():
    runtime = _get_unifi_runtime()
    runtime.start_all()
    _sync_discovery_state(runtime)
    return unifi_status()


@app.post("/api/unifi/runtime/stop")
def unifi_runtime_stop():
    runtime = _get_unifi_runtime()
    runtime.stop_all()
    return unifi_status()


@app.post("/api/unifi/api/start")
def unifi_api_start(payload: dict = Body(None)):
    runtime = _get_unifi_runtime()
    filename = (payload or {}).get("settings")
    if filename:
        settings_dir = Path(__file__).resolve().parent.parent / "Unifi" / "camera_data"
        settings_path = (settings_dir / filename).resolve()
        if settings_dir not in settings_path.parents or not settings_path.exists():
            raise HTTPException(status_code=404, detail="settings file not found")
        runtime = _set_unifi_runtime(settings_path)
    runtime.api_service.start()
    return unifi_status()


@app.post("/api/unifi/api/stop")
def unifi_api_stop():
    runtime = _get_unifi_runtime()
    runtime.api_service.stop()
    return unifi_status()


def _list_camera_settings():
    settings_dir = Path(__file__).resolve().parent.parent / "Unifi" / "camera_data"
    files = sorted(settings_dir.glob("*_Settings.json"))
    cameras = []
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        device = data.get("device") or {}
        mgmt = data.get("management") or {}
        streams = data.get("streams") or {}
        stream0 = streams.get("stream0") or {}
        name = device.get("name") or device.get("marketName") or device.get("model") or path.stem
        mac = device.get("mac") or ""
        host = device.get("host") or ""
        can_adopt = mgmt.get("canAdopt")
        if isinstance(can_adopt, str):
            can_adopt = can_adopt.strip().lower() in ("true", "1", "yes")
        if can_adopt is None:
            can_adopt = not bool(mgmt.get("initialized", False))
        status = process_state_store.get(f"cameras.{path.name}.status", "stopped")
        wss_runtime = _wss_runtimes.get(path.name)
        wss_running = False
        if wss_runtime is not None:
            wss_running = bool(wss_runtime.wss_service.status().running)
        go2rtc_channel = stream0.get("go2rtcChannel") or ""
        cameras.append(
            {
                "id": path.name,
                "name": name,
                "model": device.get("model") or "",
                "marketName": device.get("marketName") or "",
                "mac": mac,
                "host": host,
                "canAdopt": bool(can_adopt),
                "status": status,
                "wssRunning": wss_running,
                "go2rtcChannel": go2rtc_channel,
                "path": str(path),
            }
        )
    return cameras


@app.get("/api/unifi/cameras")
def unifi_list_cameras():
    return {"cameras": _list_camera_settings()}


@app.post("/api/unifi/camera/start")
def unifi_start_camera(payload: dict = Body(...)):
    filename = (payload.get("settings") or "").strip()
    if not filename:
        raise HTTPException(status_code=400, detail="settings is required")
    settings_dir = Path(__file__).resolve().parent.parent / "Unifi" / "camera_data"
    settings_path = (settings_dir / filename).resolve()
    if settings_dir not in settings_path.parents or not settings_path.exists():
        raise HTTPException(status_code=404, detail="settings file not found")

    runtime = _set_unifi_runtime(settings_path)
    runtime.stop_all()

    can_adopt = runtime.settings.get("canAdopt", True)
    runtime._start_uptime()
    if can_adopt:
        if process_state_store.get("discovery.running", False):
            raise HTTPException(status_code=409, detail="Discovery already running")
        runtime.discovery_service.start()
        process_state_store.update(
            {
                "discovery.running": True,
                "discovery.camera": filename,
                "discovery.started_at": datetime.utcnow().isoformat() + "Z",
                "discovery.last_error": "",
            }
        )
    runtime.api_service.start()
    runtime.upload_service.start()
    runtime.wss_service.start()
    process_state_store.update({f"cameras.{filename}.status": "running"})
    return unifi_status()


@app.post("/api/unifi/camera/stop")
def unifi_stop_camera(payload: dict = Body(...)):
    filename = (payload.get("settings") or "").strip()
    if not filename:
        raise HTTPException(status_code=400, detail="settings is required")
    runtime = _get_unifi_runtime()
    runtime.stop_all()
    process_state_store.update(
        {
            f"cameras.{filename}.status": "stopped",
            "discovery.running": False,
            "discovery.camera": "",
        }
    )
    return unifi_status()


@app.post("/api/unifi/discovery/start")
def unifi_discovery_start(payload: dict = Body(None)):
    runtime = _get_unifi_runtime()
    _sync_discovery_state(runtime)
    filename = (payload or {}).get("settings")
    if filename:
        settings_dir = Path(__file__).resolve().parent.parent / "Unifi" / "camera_data"
        settings_path = (settings_dir / filename).resolve()
        if settings_dir not in settings_path.parents or not settings_path.exists():
            raise HTTPException(status_code=404, detail="settings file not found")
        runtime = _set_unifi_runtime(settings_path)
    if process_state_store.get("discovery.running", False):
        raise HTTPException(status_code=409, detail="Discovery already running")
    runtime.discovery_service.start()
    process_state_store.update(
        {
            "discovery.running": True,
            "discovery.camera": filename or "",
            "discovery.started_at": datetime.utcnow().isoformat() + "Z",
            "discovery.last_error": "",
        }
    )
    return unifi_status()


@app.post("/api/unifi/discovery/stop")
def unifi_discovery_stop():
    runtime = _get_unifi_runtime()
    runtime.discovery_service.stop()
    process_state_store.update(
        {
            "discovery.running": False,
            "discovery.camera": "",
        }
    )
    return unifi_status()


@app.post("/api/unifi/wss/start")
def unifi_wss_start(payload: dict = Body(...)):
    filename = (payload.get("settings") or "").strip()
    if not filename:
        raise HTTPException(status_code=400, detail="settings is required")
    settings_dir = Path(__file__).resolve().parent.parent / "Unifi" / "camera_data"
    settings_path = (settings_dir / filename).resolve()
    if settings_dir not in settings_path.parents or not settings_path.exists():
        raise HTTPException(status_code=404, detail="settings file not found")
    runtime = _get_wss_runtime(settings_path)
    if runtime.wss_service.status().running:
        raise HTTPException(status_code=409, detail="WSS already running for this settings file")
    runtime.wss_service.start()
    process_state_store.update({f"cameras.{filename}.wss": "running"})
    return unifi_status()


@app.post("/api/unifi/wss/stop")
def unifi_wss_stop(payload: dict = Body(...)):
    filename = (payload.get("settings") or "").strip()
    if not filename:
        raise HTTPException(status_code=400, detail="settings is required")
    runtime = _wss_runtimes.get(filename)
    if runtime is None:
        raise HTTPException(status_code=404, detail="WSS runtime not found")
    runtime.wss_service.stop()
    process_state_store.update({f"cameras.{filename}.wss": "stopped"})
    return unifi_status()


@app.post("/api/unifi/upload/start")
def unifi_upload_start():
    runtime = _get_unifi_runtime()
    runtime.upload_service.start()
    return unifi_status()


@app.post("/api/unifi/upload/stop")
def unifi_upload_stop():
    runtime = _get_unifi_runtime()
    runtime.upload_service.stop()
    return unifi_status()


@app.get("/api/settings")
def web_settings():
    return web_settings_store.all()


@app.put("/api/settings")
def web_settings_update(settings: dict = Body(...)):
    if not isinstance(settings, dict):
        raise HTTPException(status_code=400, detail="settings must be an object")
    web_settings_store.update(settings)
    if "save_go2rtc_logs" in settings:
        manager.set_log_to_disk(bool(settings["save_go2rtc_logs"]))
    if "save_web_logs" in settings:
        _set_web_file_logging(bool(settings["save_web_logs"]))
    return web_settings_store.all()


@app.get("/api/go2rtc/logs")
def go2rtc_logs(lines: int = Query(200, ge=1, le=2000)):
    content = manager.read_log_tail(lines)
    file_logging = manager.is_file_logging_active()
    if file_logging and content is None:
        raise HTTPException(status_code=404, detail="log file not found")
    resp = {
        "lines": content or [],
        "count": len(content or []),
        "log_to_disk": manager.log_to_disk,
    }
    abs_path = Path(manager.log_path).resolve()
    if file_logging and abs_path.exists():
        try:
            rel_path = abs_path.relative_to(Path.cwd())
        except ValueError:
            rel_path = abs_path
        resp["path"] = str(abs_path)
        resp["path_rel"] = str(rel_path)
    else:
        resp["path"] = None
        resp["path_rel"] = None
    return resp


@app.get("/api/go2rtc/logs/download")
def go2rtc_logs_download():
    file_logging = manager.is_file_logging_active()
    if not file_logging:
        raise HTTPException(status_code=404, detail="log saving to disk is disabled")
    if not manager.log_path.exists():
        raise HTTPException(status_code=404, detail="log file not found")
    return FileResponse(manager.log_path, media_type="text/plain", filename=manager.log_path.name)


@app.get("/api/web/logs")
def web_logs(lines: int = Query(200, ge=1, le=2000)):
    log_to_disk = bool(web_settings_store.get("save_web_logs", True))
    content = read_log_tail(web_log_path, lines) if log_to_disk else []
    if log_to_disk and content is None:
        raise HTTPException(status_code=404, detail="log file not found")
    abs_path = web_log_path
    try:
        rel_path = abs_path.relative_to(Path.cwd())
    except ValueError:
        rel_path = abs_path
    return {
        "lines": content or [],
        "path": str(abs_path) if log_to_disk else None,
        "path_rel": str(rel_path) if log_to_disk else None,
        "count": len(content or []),
        "log_to_disk": log_to_disk,
    }


@app.get("/api/web/logs/download")
def web_logs_download():
    if not web_settings_store.get("save_web_logs", True):
        raise HTTPException(status_code=404, detail="web log saving is disabled")
    if not web_log_path.exists():
        raise HTTPException(status_code=404, detail="log file not found")
    return FileResponse(web_log_path, media_type="text/plain", filename=web_log_path.name)
