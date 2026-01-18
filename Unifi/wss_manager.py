"""
wss_manager.py

Structured UniFi Protect AVClient WebSocket manager with:
  1. Configuration helpers and small utilities.
  2. Message dataclasses + schema validation.
  3. Logging filters and optional message capture.
  4. Handler registry with dedicated handler groups.
  5. Protocol core responsible for parsing/dispatching.
  6. Thread wrapper that owns connection + reconnection logic.

"""

# Debug tip: on the UniFi Protect controller, run `tail -f /volume1/.srv/unifi-protect/logs/cameras.log` to watch camera logs live.

# TODO: remove temporary settings once handshake is stable: wss.minimalMode, wss.skipHello.

# TODO (Protect interoperability backlog):
# - ChangeSmartMotionSettings: accept/ack enhanced motion config (sample payload sanitized from debug log).
# - SmartMotionTest: respond to linger test probes (sample payload sanitized, only contains lingerTestStopSec).
# - ChangeAudioEventsSettings: persist audio alarm enable flags (payload enumerates enableAlrm* booleans).
# - ChangeSmartDetectSettings: store/respond to smart detect profile + region/zones (payload sanitized; includes region code and zone maps).

from __future__ import annotations

import asyncio
import copy
import io
import json
import logging
import os
import re
import ssl
import threading
import time
import subprocess
import uuid
from collections import deque
from urllib.parse import urlparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Iterable, Optional, Tuple

import hashlib
from PIL import Image
import websockets  # type: ignore
from websockets.client import WebSocketClientProtocol  # type: ignore
from websockets.exceptions import ConnectionClosed  # type: ignore

from Unifi.drivers.camera_factory import build_camera_driver


# --------------------------------------------------------------------------- #
# 1. Config & small utilities                                                 #
# --------------------------------------------------------------------------- #


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_hostport(hostport: str, default_port: int = 7442) -> Tuple[str, int]:
    if ":" in hostport:
        host, port_s = hostport.rsplit(":", 1)
        try:
            return host, int(port_s)
        except ValueError:
            return host, default_port
    return hostport, default_port


def _get_setting(settings, keys: Iterable[str], allow_empty: bool = False) -> Any:
    for key in keys:
        val = settings.get(key)
        if val is None:
            continue
        if isinstance(val, str) and not val.strip() and not allow_empty:
            continue
        return val
    return None


def _require_setting(settings, keys: Iterable[str], name: str, allow_empty: bool = False) -> Any:
    val = _get_setting(settings, keys, allow_empty=allow_empty)
    if val is None:
        raise ValueError(f"Missing required setting: {name}")
    return val


def _extract_semver(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    match = re.search(r"(\d+\.\d+\.\d+)", value)
    if not match:
        return ""
    return match.group(1)


def _semver_safe_fw_version(fw_version: str, semver: str) -> str:
    if re.match(r"^\d+\.\d+\.\d+(?:\+[0-9A-Za-z.-]+)?$", fw_version or ""):
        return fw_version
    base = semver or _extract_semver(fw_version) or "0.0.0"
    if not fw_version:
        return base
    build = re.sub(r"[^0-9A-Za-z.-]", ".", fw_version).strip(".")
    if build:
        return f"{base}+{build}"
    return base


@dataclass
class WssConfig:
    use_secure_transfer: bool = True
    log_only: set[str] = field(default_factory=set)
    silence: set[str] = field(default_factory=set)
    default_noisy: set[str] = field(
        default_factory=lambda: {
            "NetworkStatus",
            "GetSystemStats",
            "ubnt_avclient_paramAgreement",
            "ChangeOsdSettings",
            "ChangeSoundLedSettings",
            "ChangeTalkbackSettings",
            "ChangeAnalyticsSettings",
            "ChangeDeviceSettings",
            "ChangeVideoSettings",
            "ChangeIspSettings",
            "UpdateUsernamePassword",
        }
    )
    throttle_secs: float = 0.0
    capture_enabled: bool = False
    capture_file: Optional[Path] = None
    capture_unique: bool = True
    capture_unique_limit: int = 1000
    snapshot_debug: bool = False
    snapshot_debug_dir: Path = Path("/workspaces/unifi-cam-proxy/debug_snaps")
    snapshot_debug_keep: int = 5

    @staticmethod
    def from_env() -> "WssConfig":
        return WssConfig(
            use_secure_transfer=True,
            log_only=set(),
            silence=set(),
            throttle_secs=0.0,
            capture_enabled=False,
            capture_file=None,
            capture_unique=True,
            capture_unique_limit=1000,
            snapshot_debug=False,
            snapshot_debug_dir=WssConfig.snapshot_debug_dir,
            snapshot_debug_keep=WssConfig.snapshot_debug_keep,
        )


# --------------------------------------------------------------------------- #
# 2. Direction & message types                                                #
# --------------------------------------------------------------------------- #


class Direction(Enum):
    RX = "<-"
    TX = "->"


@dataclass
class ControllerMessage:
    raw: str
    function_name: str
    message_id: int
    in_response_to: int
    response_expected: bool
    payload: Any

    @classmethod
    def from_json(cls, raw: str) -> "ControllerMessage":
        data = json.loads(raw)
        return cls(
            raw=raw,
            function_name=str(data.get("functionName") or ""),
            message_id=int(data.get("messageId") or 0),
            in_response_to=int(data.get("inResponseTo") or 0),
            response_expected=bool(data.get("responseExpected")),
            payload=data.get("payload"),
        )

    def expects_response(self) -> bool:
        return self.response_expected


@dataclass
class CameraMessage:
    function_name: str
    message_id: int
    in_response_to: int
    payload: Dict[str, Any]
    status_code: Optional[int] = None
    status: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        out = {
            "from": "ubnt_avclient",
            "to": "UniFiVideo",
            "functionName": self.function_name,
            "messageId": self.message_id,
            "inResponseTo": self.in_response_to,
            "payload": self.payload,
        }
        if self.status_code is not None:
            out["statusCode"] = self.status_code
        if self.status is not None:
            out["status"] = self.status
        return out

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def reply_to(cls, in_msg: ControllerMessage, message_id: int, payload: Dict[str, Any]) -> "CameraMessage":
        status_code = payload.get("statusCode")
        status = payload.get("status")
        return cls(
            function_name=in_msg.function_name,
            message_id=message_id,
            in_response_to=in_msg.message_id,
            payload=payload,
            status_code=status_code,
            status=status,
        )


# --------------------------------------------------------------------------- #
# 3. Schemas & validation                                                     #
# --------------------------------------------------------------------------- #


@dataclass
class MessageSchema:
    payload_required_keys: Iterable[str] = field(default_factory=list)
    payload_optional_keys: Iterable[str] = field(default_factory=list)
    volatile_payload_keys: Iterable[str] = field(default_factory=list)


SCHEMAS: Dict[str, MessageSchema] = {
    "ubnt_avclient_paramAgreement": MessageSchema(
        payload_optional_keys=["enableStatusCodes", "useHeartbeats", "heartbeatsTimeoutMs"],
    ),
    "EventPoorNetwork": MessageSchema(
        payload_optional_keys=["isPoor", "startOffsetMSec", "stopStreamLevel"],
    ),
    "GetSystemStats": MessageSchema(),
    "NetworkStatus": MessageSchema(),
    "ChangeVideoSettings": MessageSchema(
        payload_optional_keys=["video", "videoMode", "hdrMode", "downScaleMode"],
    ),
    "ChangeIspSettings": MessageSchema(),
    "ChangeAudioEventsSettings": MessageSchema(),
    "ChangeOsdSettings": MessageSchema(),
    "ChangeSoundLedSettings": MessageSchema(),
    "ChangeTalkbackSettings": MessageSchema(),
    "ChangeAnalyticsSettings": MessageSchema(),
    "ChangeDeviceSettings": MessageSchema(),
    "ChangeClarityZones": MessageSchema(
        payload_optional_keys=["autoMode", "zones"],
    ),
    "GetRequest": MessageSchema(
        payload_required_keys=["what", "uri"],
        payload_optional_keys=["timeoutMs", "quality", "filename"],
        volatile_payload_keys=["uri"],
    ),
    "UpdateFaceDBRequest": MessageSchema(
        payload_required_keys=["uri"],
        volatile_payload_keys=["uri"],
    ),
    "UpdateFirmwareRequest": MessageSchema(
        payload_required_keys=["uri", "timeoutMs", "md5"],
        payload_optional_keys=["fwPath"],
        volatile_payload_keys=["uri", "fwPath"],
    ),
}

DEFAULT_CHANGE_ISP_PAYLOAD: Dict[str, Any] = {
    "aeMode": "auto",
    "aeTargetPercent": 50,
    "afExtendRange": 0,
    "aggressiveAntiFlicker": 0,
    "autoFlipMirror": 1,
    "autoFreq": 60,
    "awbAlgoMethod": "advanced",
    "brightness": 50,
    "contrast": 50,
    "criticalTmpOfProtect": 40,
    "dZoomCenterX": 50,
    "dZoomCenterY": 50,
    "dZoomScale": 0,
    "dZoomStreamId": 4,
    "darkAreaCompensateLevel": 0,
    "denoise": 50,
    "enable3dnr": 1,
    "enableExternalIr": 0,
    "enableMicroTmpProtect": 1,
    "enablePauseMotion": 0,
    "flip": 0,
    "focusMode": "ztrig",
    "focusPosition": 0,
    "forceFilterIrSwitchEvents": 0,
    "hdrMode": "normal",
    "hue": 50,
    "icrCustomValue": 2,
    "icrLightSensorNightThd": 0,
    "icrSensitivity": 0,
    "icrSwitchMode": "lux",
    "irLedLevel": 0,
    "irLedMode": "manual",
    "irOnStsBrightness": 0,
    "irOnStsContrast": 0,
    "irOnStsDenoise": 0,
    "irOnStsHue": 0,
    "irOnStsSaturation": 0,
    "irOnStsSharpness": 0,
    "irOnStsWdr": 0,
    "irOnValBrightness": 50,
    "irOnValContrast": 50,
    "irOnValDenoise": 50,
    "irOnValHue": 50,
    "irOnValSaturation": 50,
    "irOnValSharpness": 50,
    "irOnValWdr": 1,
    "isDayMode": 1,
    "lensDistortionCorrection": 1,
    "masks": {"0": None},
    "mirror": 0,
    "queryIrLedStatus": 0,
    "saturation": 50,
    "sceneMode": "auto",
    "sharpness": 50,
    "touchFocusX": 671,
    "touchFocusY": 116,
    "wdr": 1,
    "zonesAutoFlipMirror": 0,
    "zoomPosition": 0,
}


def validate_message_schema(msg: ControllerMessage, logger: logging.Logger) -> None:
    schema = SCHEMAS.get(msg.function_name)
    if not schema:
        return
    payload = msg.payload
    if not isinstance(payload, dict):
        logger.warning("Schema: %s expected dict payload, got %r", msg.function_name, type(payload).__name__)
        return
    missing = [k for k in schema.payload_required_keys if k not in payload]
    if missing:
        logger.debug("Schema: %s missing required payload keys: %s", msg.function_name, missing)


# --------------------------------------------------------------------------- #
# 4. Logging, normalization & capture                                         #
# --------------------------------------------------------------------------- #


class LogFilter:
    def __init__(self, config: WssConfig):
        self._only = config.log_only
        self._silence = config.silence
        self._default_noisy = config.default_noisy
        self._throttle_secs = config.throttle_secs
        self._last_log_ts: Dict[str, float] = {}

    def should_log(self, fn: str) -> bool:
        if not fn:
            return True
        if self._only:
            return fn in self._only
        if fn in self._silence or fn in self._default_noisy:
            return False
        return True

    def throttle_ok(self, fn: str) -> bool:
        if self._throttle_secs <= 0 or fn not in {"NetworkStatus", "GetSystemStats"}:
            return True
        now = time.monotonic()
        last = self._last_log_ts.get(fn, 0.0)
        if now - last >= self._throttle_secs:
            self._last_log_ts[fn] = now
            return True
        return False

    def log(self, logger: logging.Logger, direction: Direction, fn: str, raw: str) -> None:
        if self.should_log(fn) and self.throttle_ok(fn):
            logger.debug("WSS %s %s: %s", direction.value, fn or "?", raw)


class MessageCapture:
    VOLATILE_TOP_LEVEL = {"messageId", "inResponseTo", "timeStamp"}

    def __init__(self, config: WssConfig, logger: logging.Logger):
        self.enabled = config.capture_enabled
        self.unique = config.capture_unique
        self.file = config.capture_file
        self.unique_limit = config.capture_unique_limit
        self.logger = logger
        self._seen_hashes: set[str] = set()
        self._seen_order: deque[str] = deque()

    def _canonical_json(self, obj: Dict[str, Any]) -> str:
        return json.dumps(obj, sort_keys=True, separators=(",", ":"))

    def _hash_obj(self, obj: Dict[str, Any]) -> str:
        s = self._canonical_json(obj)
        return hashlib.sha1(s.encode("utf-8")).hexdigest()

    def _normalize_for_hash(self, msg: ControllerMessage) -> Optional[Dict[str, Any]]:
        base: Dict[str, Any] = {
            "functionName": msg.function_name,
            "responseExpected": msg.response_expected,
        }
        payload = msg.payload if isinstance(msg.payload, dict) else None
        if payload is not None:
            schema = SCHEMAS.get(msg.function_name)
            vol_payload = set(schema.volatile_payload_keys) if schema else set()
            normalized_payload = {}
            for k, v in payload.items():
                if k in vol_payload:
                    continue
                normalized_payload[k] = v
            base["payload"] = normalized_payload
        return base

    def maybe_record(self, msg: ControllerMessage) -> None:
        if not self.enabled or not self.file:
            return
        normalized = self._normalize_for_hash(msg)
        if normalized is None:
            return
        if self.unique:
            h = self._hash_obj(normalized)
            if h in self._seen_hashes:
                return
            self._seen_hashes.add(h)
            self._seen_order.append(h)
            while len(self._seen_order) > self.unique_limit:
                old = self._seen_order.popleft()
                self._seen_hashes.discard(old)
        record = {
            "ts": _now_iso(),
            "functionName": msg.function_name,
            "messageId": msg.message_id,
            "responseExpected": msg.response_expected,
            "payload": msg.payload,
            "raw": msg.raw,
        }
        line = json.dumps(record, default=str)
        try:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            with self.file.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception as exc:
            self.logger.error("MessageCapture write failed: %s", exc)


# --------------------------------------------------------------------------- #
# 5. Handler registry & handler groups                                        #
# --------------------------------------------------------------------------- #


HandlerFn = Callable[[WebSocketClientProtocol, ControllerMessage], Awaitable[None]]


@dataclass
class HandlerRegistry:
    _handlers: Dict[str, HandlerFn] = field(default_factory=dict)

    def register(self, name: str, fn: HandlerFn) -> None:
        self._handlers[name] = fn

    def get(self, name: str) -> Optional[HandlerFn]:
        return self._handlers.get(name)


class BaseHandlers:
    def __init__(self, settings, driver, logger: logging.Logger, protocol: "WssProtocol"):
        self.settings = settings
        self.driver = driver
        self.log = logger
        self.protocol = protocol

    def _device_id(self) -> str:
        mac = _require_setting(self.settings, ["device.mac", "mac"], "device.mac")
        return str(mac).upper()

    async def _reply_ok(self, ws: WebSocketClientProtocol, msg: ControllerMessage, extra: Optional[Dict[str, Any]] = None):
        payload: Dict[str, Any] = {"statusCode": 0, "status": "ok", "deviceID": self._device_id()}
        if extra:
            payload.update(extra)
        out = self.protocol.build_reply(msg, payload)
        await self.protocol.send(ws, out)

    def _persist_incoming_payload(self, fn: str, payload: Any) -> None:
        if not isinstance(payload, dict) or not payload:
            return
        try:
            self.settings.update(payload)
        except Exception:
            self.log.exception("Failed to apply payload for %s", fn)
        try:
            self.settings.update({f"lastReceived.{fn}": payload})
        except Exception:
            self.log.exception("Failed to persist payload snapshot for %s", fn)


class MaintenanceHandlers(BaseHandlers):
    async def on_param_agreement(self, ws: WebSocketClientProtocol, msg: ControllerMessage):
        if not msg.expects_response():
            return
        token = _get_setting(
            self.settings,
            ["management.token", "mgmt.token", "wss.authToken", "authToken"],
            allow_empty=True,
        ) or ""
        payload = {"authToken": token}
        out = self.protocol.build_reply(msg, payload)
        await self.protocol.send(ws, out)

    async def on_time_sync(self, ws: WebSocketClientProtocol, msg: ControllerMessage):
        payload = {"timeDelta": 0}
        out = self.protocol.build_reply(msg, payload)
        await self.protocol.send(ws, out)

    async def on_event_poor_network(self, ws: WebSocketClientProtocol, msg: ControllerMessage):
        incoming = msg.payload if isinstance(msg.payload, dict) else {}
        self._persist_incoming_payload(msg.function_name, incoming)
        if msg.expects_response():
            await self._reply_ok(ws, msg, incoming)

    async def on_get_system_stats(self, ws: WebSocketClientProtocol, msg: ControllerMessage):
        if not msg.expects_response():
            return
        payload = {
            "cpu": 5,
            "memory": 20,
            "temperature": 45,
            "uptime": int(_require_setting(self.settings, ["runtime.uptime", "uptime"], "runtime.uptime") or 0),
            "statusCode": 0,
            "status": "ok",
            "deviceID": self._device_id(),
        }
        out = self.protocol.build_reply(msg, payload)
        await self.protocol.send(ws, out)

    async def on_network_status(self, ws: WebSocketClientProtocol, msg: ControllerMessage):
        if not msg.expects_response():
            return
        ip = _require_setting(self.settings, ["device.host", "host"], "device.host")
        mac = _require_setting(self.settings, ["device.mac", "mac"], "device.mac")
        payload = {
            "status": "connected",
            "ip": ip,
            "mac": str(mac).lower(),
            "statusCode": 0,
            "deviceID": self._device_id(),
        }
        out = self.protocol.build_reply(msg, payload)
        await self.protocol.send(ws, out)

    async def on_stop_service(self, ws: WebSocketClientProtocol, msg: ControllerMessage):
        incoming = msg.payload if isinstance(msg.payload, dict) else {}
        self._persist_incoming_payload(msg.function_name, incoming)
        if msg.expects_response():
            await self._reply_ok(ws, msg, incoming)

    async def on_enable_logging(self, ws: WebSocketClientProtocol, msg: ControllerMessage):
        incoming = msg.payload if isinstance(msg.payload, dict) else {}
        self._persist_incoming_payload(msg.function_name, incoming)
        if msg.expects_response():
            await self._reply_ok(ws, msg, incoming)


class SettingsHandlers(BaseHandlers):
    async def _send_firmware_status(self, ws: WebSocketClientProtocol, status: str) -> None:
        payload = {"status": status}
        msg = CameraMessage(
            function_name="EventUpdateFirmwareStatus",
            message_id=self.protocol._next_msg_id(),
            in_response_to=0,
            payload=payload,
        )
        await self.protocol.send(ws, msg)

    async def on_change_audio_events_settings(self, ws: WebSocketClientProtocol, msg: ControllerMessage):
        if msg.expects_response():
            incoming = msg.payload if isinstance(msg.payload, dict) else {}
            self._persist_incoming_payload(msg.function_name, incoming)
            await self._reply_ok(ws, msg, incoming)

    async def on_change_osd_settings(self, ws: WebSocketClientProtocol, msg: ControllerMessage):
        if msg.expects_response():
            incoming = msg.payload if isinstance(msg.payload, dict) else {}
            self._persist_incoming_payload(msg.function_name, incoming)
            await self._reply_ok(ws, msg, incoming)

    async def on_change_sound_led_settings(self, ws: WebSocketClientProtocol, msg: ControllerMessage):
        if msg.expects_response():
            incoming = msg.payload if isinstance(msg.payload, dict) else {}
            self._persist_incoming_payload(msg.function_name, incoming)
            await self._reply_ok(ws, msg, incoming)

    async def on_change_talkback_settings(self, ws: WebSocketClientProtocol, msg: ControllerMessage):
        if msg.expects_response():
            incoming = msg.payload if isinstance(msg.payload, dict) else {}
            self._persist_incoming_payload(msg.function_name, incoming)
            await self._reply_ok(ws, msg, incoming)

    async def on_change_analytics_settings(self, ws: WebSocketClientProtocol, msg: ControllerMessage):
        if msg.expects_response():
            incoming = msg.payload if isinstance(msg.payload, dict) else {}
            self._persist_incoming_payload(msg.function_name, incoming)
            await self._reply_ok(ws, msg, incoming)

    async def on_change_device_settings(self, ws: WebSocketClientProtocol, msg: ControllerMessage):
        incoming = msg.payload if isinstance(msg.payload, dict) else {}
        self._persist_incoming_payload(msg.function_name, incoming)
        if msg.expects_response():
            await self._reply_ok(ws, msg, incoming)

    async def on_change_clarity_zones(self, ws: WebSocketClientProtocol, msg: ControllerMessage):
        if msg.expects_response():
            incoming = msg.payload if isinstance(msg.payload, dict) else {}
            self._persist_incoming_payload(msg.function_name, incoming)
            await self._reply_ok(ws, msg, incoming)

    async def on_update_username_password(self, ws: WebSocketClientProtocol, msg: ControllerMessage):
        if msg.expects_response():
            await self._reply_ok(ws, msg)

    async def on_audio_agent_change_tuning(self, ws: WebSocketClientProtocol, msg: ControllerMessage):
        incoming = msg.payload if isinstance(msg.payload, dict) else {}
        self._persist_incoming_payload(msg.function_name, incoming)
        if msg.expects_response():
            await self._reply_ok(ws, msg, incoming)

    async def on_change_video_settings(self, ws: WebSocketClientProtocol, msg: ControllerMessage):
        payload = self._coerce_payload_to_dict(msg.payload)
        self._persist_incoming_payload(msg.function_name, payload)
        mode_defaults = {
            "videoMode": payload.get("videoMode") or "default",
            "hdrMode": payload.get("hdrMode") or "off",
            "downScaleMode": payload.get("downScaleMode") or "original",
        }

        video = payload.get("video")
        if not isinstance(video, dict):
            video = _get_setting(
                self.settings,
                [
                    "videoSettings.video",
                    "video",
                    "lastReceived.ChangeVideoSettings.video",
                ],
                allow_empty=True,
            )
        defaults: Optional[Dict[str, Any]] = None
        if not isinstance(video, dict):
            video = {}
        defaults = self._default_video_reply(mode_defaults)
        base_video = defaults["video"]
        base_video.update(video)
        video = base_video
        has_pushable = False
        if isinstance(video, dict):
            for vcfg in video.values():
                if isinstance(vcfg, dict):
                    ser = (vcfg.get("avSerializer") or {})
                    if ser.get("type") == "extendedFlv" and (ser.get("destinations") or []):
                        has_pushable = True
                        break

        reply_payload: Dict[str, Any] = {
            "statusCode": 0,
            "status": "ok",
            "deviceID": self._device_id(),
            **mode_defaults,
        }

        mirrored: Dict[str, Dict[str, Any]] = {}
        for vid, vcfg in video.items():
            vcfg = vcfg if isinstance(vcfg, dict) else {}
            with_defaults = {**vcfg, **mode_defaults}
            if "type" not in with_defaults:
                with_defaults["type"] = "h264"
            mirrored[vid] = with_defaults
        reply_payload["video"] = mirrored
        if defaults and "chip" in defaults:
            reply_payload["chip"] = defaults["chip"]

        self._persist_video_settings(payload, reply_payload)

        if not has_pushable:
            out = self.protocol.build_reply(msg, reply_payload)
            await self.protocol.send(ws, out)
            return

        try:
            applied = await self.driver.apply_video_settings(payload)
        except Exception as exc:
            self.log.error("apply_video_settings failed: %s", exc)
            error_payload = {
                "statusCode": 1,
                "status": "error",
                "deviceID": self._device_id(),
            }
            out = self.protocol.build_reply(msg, error_payload)
            await self.protocol.send(ws, out)
            return

        if applied:
            applied_video = applied.get("video") if isinstance(applied, dict) else None
            if isinstance(applied_video, dict):
                merged_video = reply_payload.get("video") or {}
                for key, value in applied_video.items():
                    if isinstance(merged_video.get(key), dict) and isinstance(value, dict):
                        merged_video[key].update(value)
                    else:
                        merged_video[key] = value
                reply_payload["video"] = merged_video
                applied = {k: v for k, v in applied.items() if k != "video"}
            reply_payload.update(applied)

        out = self.protocol.build_reply(msg, reply_payload)
        await self.protocol.send(ws, out)

    async def on_change_isp_settings(self, ws: WebSocketClientProtocol, msg: ControllerMessage):
        payload = msg.payload if isinstance(msg.payload, dict) else {}
        if not payload:
            cached = _get_setting(
                self.settings,
                ["lastReceived.ChangeIspSettings", "ispSettings", "device.ispSettings"],
                allow_empty=True,
            )
            if not isinstance(cached, dict) or not cached:
                cached = copy.deepcopy(DEFAULT_CHANGE_ISP_PAYLOAD)
            self._persist_incoming_payload(msg.function_name, cached)
            if msg.expects_response():
                out_payload: Dict[str, Any] = {
                    "statusCode": 0,
                    "status": "ok",
                    "deviceID": self._device_id(),
                }
                out_payload.update(cached)
                out = self.protocol.build_reply(msg, out_payload)
                await self.protocol.send(ws, out)
            return
        self._persist_incoming_payload(msg.function_name, payload)
        try:
            applied = await self.driver.apply_isp_settings(payload)
        except Exception as exc:
            self.log.error("apply_isp_settings failed: %s", exc)
            if msg.expects_response():
                error_payload = {
                    "statusCode": 1,
                    "status": "error",
                    "deviceID": self._device_id(),
                }
                out = self.protocol.build_reply(msg, error_payload)
                await self.protocol.send(ws, out)
            return

        if msg.expects_response():
            out_payload: Dict[str, Any] = {
                "statusCode": 0,
                "status": "ok",
                "deviceID": self._device_id(),
            }
            if applied:
                out_payload.update(applied)
            out = self.protocol.build_reply(msg, out_payload)
            await self.protocol.send(ws, out)

    async def on_update_firmware_request(self, ws: WebSocketClientProtocol, msg: ControllerMessage):
        incoming = msg.payload if isinstance(msg.payload, dict) else {}
        self._persist_incoming_payload(msg.function_name, incoming)
        if msg.expects_response():
            await self._reply_ok(ws, msg, incoming)

        await self._send_firmware_status(ws, "FW_DOWNLOADING")
        await asyncio.sleep(5)
        await self._send_firmware_status(ws, "FW_UPDATING")
        await asyncio.sleep(5)
        try:
            await ws.close(code=1012, reason="rebooting")
        except Exception as exc:
            self.log.debug("Firmware reboot close failed: %s", exc)

    def _persist_video_settings(self, payload: Dict[str, Any], reply_payload: Dict[str, Any]) -> None:
        stored: Dict[str, Any] = {
            "videoMode": reply_payload.get("videoMode"),
            "hdrMode": reply_payload.get("hdrMode"),
            "downScaleMode": reply_payload.get("downScaleMode"),
        }
        if "video" in reply_payload:
            stored["video"] = reply_payload["video"]
        stored["raw"] = payload
        try:
            self.settings["videoSettings"] = stored
        except Exception:
            self.log.exception("Failed to persist ChangeVideoSettings payload")

    def _coerce_payload_to_dict(self, payload: Any) -> Dict[str, Any]:
        if isinstance(payload, (bytes, bytearray)):
            try:
                payload = payload.decode("utf-8", "ignore")
            except Exception:
                payload = "{}"
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                self.log.warning("ChangeVideoSettings payload non-JSON string (len=%s)", len(payload))
                payload = {}
        if not isinstance(payload, dict):
            self.log.warning("ChangeVideoSettings payload type=%s; using {}", type(payload).__name__)
            payload = {}
        return payload

    def _default_video_reply(self, mode_defaults: Dict[str, Any]) -> Dict[str, Any]:
        hi, mid, lo = self._default_video_dims()
        def _entry(width: int, height: int, stream_id: int) -> Dict[str, Any]:
            return {
                "type": "h264",
                "width": width,
                "height": height,
                "fps": 24,
                "streamId": stream_id,
                "streamOrdinal": stream_id,
                **mode_defaults,
            }
        video = {
            "video1": _entry(hi[0], hi[1], 0),
            "video2": _entry(lo[0], lo[1], 1),
            "video3": _entry(mid[0], mid[1], 2),
        }
        return {"video": video, "chip": self._default_chip(mode_defaults)}

    def _default_video_dims(self) -> Tuple[Tuple[int, int], Tuple[int, int], Tuple[int, int]]:
        dims = []
        streams = _get_setting(self.settings, ["streams"], allow_empty=True)
        if isinstance(streams, dict):
            for stream in streams.values():
                if not isinstance(stream, dict):
                    continue
                width = stream.get("width")
                height = stream.get("height")
                if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
                    dims.append((width, height))
        if dims:
            dims = sorted(dims, key=lambda wh: wh[0] * wh[1])
            lo = dims[0]
            hi = dims[-1]
            mid = dims[len(dims) // 2]
            return hi, mid, lo
        return (1920, 1080), (1280, 720), (640, 360)

    def _default_chip(self, mode_defaults: Dict[str, Any]) -> Dict[str, Any]:
        hi, _, _ = self._default_video_dims()
        return {
            "common": {"vsync_detection_disable": 0},
            "debug": {"check_disable": 0},
            "vin0": {
                "description": "Input src 0",
                "enabled": True,
                "hdrMode": 1,
                "height": hi[1],
                "width": hi[0],
                "videoMode": mode_defaults.get("videoMode", "default"),
                "vinFps": 24,
                "vsrcCtxSwitch": 0,
                "vsrcId": 0,
            },
        }


class SnapshotHandlers(BaseHandlers):
    SNAPSHOT_TARGETS = {
        "low": (480, 270),
        "medium": (640, 360),
        "high": (1280, 720),
    }

    @staticmethod
    def _jpeg_dimensions(data: bytes) -> Tuple[Optional[int], Optional[int]]:
        if len(data) < 4 or data[0] != 0xFF or data[1] != 0xD8:
            return (None, None)
        i = 2
        while i + 3 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            i += 2
            if marker in (0xD8, 0xD9):
                continue
            if i + 1 >= len(data):
                break
            seglen = (data[i] << 8) | data[i + 1]
            if seglen < 2 or i + seglen > len(data):
                break
            if 0xC0 <= marker <= 0xC3 and seglen >= 7:
                height = (data[i + 3] << 8) | data[i + 4]
                width = (data[i + 5] << 8) | data[i + 6]
                return (width, height)
            i += seglen
        return (None, None)

    def _snapshot_target_dimensions(self, quality: Optional[str]) -> Optional[Tuple[int, int]]:
        if not quality:
            return None
        target = self.SNAPSHOT_TARGETS.get(str(quality).lower())
        if not target:
            return None
        return target

    def _resize_snapshot(self, jpeg: bytes, target: Tuple[int, int]) -> bytes:
        width, height = target
        buf = io.BytesIO(jpeg)
        with Image.open(buf) as img:
            img = img.convert("RGB")
            resized = img.resize((width, height), Image.LANCZOS)
            out = io.BytesIO()
            resized.save(out, format="JPEG", quality=90)
            return out.getvalue()

    async def on_get_request(self, ws: WebSocketClientProtocol, msg: ControllerMessage):
        payload = msg.payload or {}
        if not isinstance(payload, dict) or payload.get("what") != "snapshot":
            if msg.expects_response():
                await self._reply_ok(ws, msg)
            return

        uri = payload.get("uri")
        timeout_ms = int(payload.get("timeoutMs", 60000))
        timeout_s = max(1, timeout_ms // 1000)

        try:
            driver_timeout = max(1, timeout_s // 2)
            jpeg = await self.driver.get_snapshot_jpeg(timeout_s=driver_timeout)
            width = height = None
            try:
                width, height = self._jpeg_dimensions(jpeg)
            except Exception:
                pass
            quality = payload.get("quality")
            target_dims = self._snapshot_target_dimensions(quality)
            if target_dims and (width, height) != target_dims:
                try:
                    jpeg = self._resize_snapshot(jpeg, target_dims)
                    width, height = target_dims
                except Exception as exc:
                    self.log.warning("Snapshot resize to %sx%s failed: %s", target_dims[0], target_dims[1], exc)
                    try:
                        width, height = self._jpeg_dimensions(jpeg)
                    except Exception:
                        pass
            if self.protocol.config.snapshot_debug:
                self._snapshot_debug_write(jpeg)
            if not uri:
                raise ValueError("snapshot URI missing")
            self.log.debug(
                "Snapshot preparing upload: uri=%s quality=%s bytes=%d dims=%sx%s",
                uri,
                quality,
                len(jpeg),
                width or "?",
                height or "?",
            )
            await self._upload_snapshot_and_ack(ws, msg, jpeg, uri, timeout_s, quality=quality, dims=(width, height))
        except Exception as exc:
            self.log.error("get_snapshot_jpeg failed: %s", exc)
            if msg.expects_response():
                error_payload = {"statusCode": 1, "status": "error", "deviceID": self._device_id()}
                out = self.protocol.build_reply(msg, error_payload)
                await self.protocol.send(ws, out)

    async def _upload_snapshot_and_ack(
        self,
        ws: WebSocketClientProtocol,
        in_msg: ControllerMessage,
        jpeg: bytes,
        uri: str,
        timeout_s: int,
        quality: Optional[str] = None,
        dims: Tuple[Optional[int], Optional[int]] = (None, None),
    ):
        start = time.monotonic()
        cert_path = Path("cert.pem")
        key_path = Path("key.pem")
        has_cert = cert_path.exists() and key_path.exists()
        snapshot_dir = self.protocol.config.snapshot_debug_dir
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        snapshot_path = snapshot_dir / f"snapshot_upload_{uuid.uuid4().hex}.jpg"
        snapshot_path.write_bytes(jpeg)
        curl_cmd = [
            "curl",
            "-sS",
            "-k",
            "-X",
            "POST",
            "--fail",
            "--show-error",
            "--form",
            f"payload=@{snapshot_path}",
            uri,
        ]
        if has_cert:
            curl_cmd.extend(["--cert", str(cert_path), "--key", str(key_path)])
        try:
            proc = await asyncio.to_thread(
                subprocess.run,
                curl_cmd,
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
            elapsed = max(time.monotonic() - start, 1e-3)
            success = proc.returncode == 0
            if not success:
                self.log.error(
                    "Snapshot upload curl failed (rc=%s) stdout=%s stderr=%s",
                    proc.returncode,
                    proc.stdout.strip(),
                    proc.stderr.strip(),
                )
            mbps = (len(jpeg) * 8) / elapsed / 1_000_000
            self.log.debug(
                "Snapshot upload curl rc=%s (len=%d) quality=%s dims=%sx%s uri=%s elapsed=%.3fs rate=%.2f Mbps",
                proc.returncode,
                len(jpeg),
                quality or "?",
                dims[0] or "?",
                dims[1] or "?",
                uri,
                elapsed,
                mbps,
            )
            payload: Dict[str, Any] = {}
            dims_payload: Dict[str, Any] = {}
            if dims[0] is not None:
                dims_payload["width"] = dims[0]
            if dims[1] is not None:
                dims_payload["height"] = dims[1]
            if dims_payload:
                payload["payload"] = dims_payload
            if success:
                payload.update({"statusCode": 0})
            else:
                payload.update({"statusCode": 1, "status": "error"})
            out = self.protocol.build_reply(in_msg, payload)
            await self.protocol.send(ws, out)
        except Exception as exc:
            elapsed = max(time.monotonic() - start, 1e-3)
            self.log.error("Snapshot upload exception in %.3fs (%s bytes): %s", elapsed, len(jpeg), exc)
            payload = {"statusCode": 1, "status": "error"}
            out = self.protocol.build_reply(in_msg, payload)
            await self.protocol.send(ws, out)
        finally:
            try:
                snapshot_path.unlink(missing_ok=True)
            except Exception:
                pass

    def _snapshot_debug_write(self, jpeg: bytes):
        try:
            self.protocol.config.snapshot_debug_dir.mkdir(parents=True, exist_ok=True)
            # keep only the most recent snapshot to simplify debugging
            fname = self.protocol.config.snapshot_debug_dir / "snapshot_latest.jpg"
            fname.write_bytes(jpeg)
        except Exception as exc:
            self.log.warning("Snapshot debug failed: %s", exc)


class AnalyticsHandlers(BaseHandlers):
    async def on_analytics_test(self, ws: WebSocketClientProtocol, msg: ControllerMessage):
        if msg.expects_response():
            incoming = msg.payload if isinstance(msg.payload, dict) else {}
            self._persist_incoming_payload(msg.function_name, incoming)
            await self._reply_ok(ws, msg, incoming)

    async def on_update_face_db_request(self, ws: WebSocketClientProtocol, msg: ControllerMessage):
        if msg.expects_response():
            incoming = msg.payload if isinstance(msg.payload, dict) else {}
            self._persist_incoming_payload(msg.function_name, incoming)
            await self._reply_ok(ws, msg, incoming)


def build_handler_registry(settings, driver, logger: logging.Logger, protocol: "WssProtocol") -> HandlerRegistry:
    reg = HandlerRegistry()
    maint = MaintenanceHandlers(settings, driver, logger, protocol)
    sets = SettingsHandlers(settings, driver, logger, protocol)
    snap = SnapshotHandlers(settings, driver, logger, protocol)
    anal = AnalyticsHandlers(settings, driver, logger, protocol)

    reg.register("ubnt_avclient_paramAgreement", maint.on_param_agreement)
    reg.register("ubnt_avclient_timeSync", maint.on_time_sync)
    reg.register("EventPoorNetwork", maint.on_event_poor_network)
    reg.register("GetSystemStats", maint.on_get_system_stats)
    reg.register("NetworkStatus", maint.on_network_status)
    reg.register("StopService", maint.on_stop_service)
    reg.register("EnableLogging", maint.on_enable_logging)

    reg.register("ChangeVideoSettings", sets.on_change_video_settings)
    reg.register("ChangeIspSettings", sets.on_change_isp_settings)
    reg.register("ChangeAudioEventsSettings", sets.on_change_audio_events_settings)
    reg.register("ChangeOsdSettings", sets.on_change_osd_settings)
    reg.register("ChangeSoundLedSettings", sets.on_change_sound_led_settings)
    reg.register("ChangeTalkbackSettings", sets.on_change_talkback_settings)
    reg.register("ChangeAnalyticsSettings", sets.on_change_analytics_settings)
    reg.register("ChangeDeviceSettings", sets.on_change_device_settings)
    reg.register("UpdateUsernamePassword", sets.on_update_username_password)
    reg.register("ChangeClarityZones", sets.on_change_clarity_zones)
    reg.register("AudioAgentChangeTuning", sets.on_audio_agent_change_tuning)
    reg.register("UpdateFirmwareRequest", sets.on_update_firmware_request)

    reg.register("GetRequest", snap.on_get_request)

    reg.register("AnalyticsTest", anal.on_analytics_test)
    reg.register("UpdateFaceDBRequest", anal.on_update_face_db_request)

    return reg


# --------------------------------------------------------------------------- #
# 6. Protocol core                                                            #
# --------------------------------------------------------------------------- #


class WssProtocol:
    def __init__(
        self,
        settings,
        driver,
        logger: logging.Logger,
        tcp_in_log: logging.Logger | None,
        tcp_out_log: logging.Logger | None,
        config: WssConfig,
        log_filter: LogFilter,
        capture: MessageCapture,
    ):
        self.settings = settings
        self.driver = driver
        self.log = logger
        self.tcp_in_log = tcp_in_log
        self.tcp_out_log = tcp_out_log
        self.config = config
        self.log_filter = log_filter
        self.capture = capture
        self._msg_id = 0
        self.minimal_mode = bool(_get_setting(settings, ["wss.minimalMode", "minimalMode"]))
        self._raw_dump_path: Path | None = None
        self._raw_dump_fp = None
        self.handlers = build_handler_registry(settings, driver, logger, self)

    def _next_msg_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    def reset_msg_id(self, start_at: int = 0) -> None:
        self._msg_id = start_at

    def build_reply(self, in_msg: ControllerMessage, payload: Dict[str, Any]) -> CameraMessage:
        return CameraMessage.reply_to(in_msg, self._next_msg_id(), payload)

    def start_raw_dump(self, path: Path | None) -> None:
        if not path:
            return
        if self._raw_dump_fp:
            try:
                self._raw_dump_fp.close()
            except Exception:
                pass
        path.parent.mkdir(parents=True, exist_ok=True)
        self._raw_dump_path = path
        self._raw_dump_fp = path.open("w", encoding="utf-8")

    def stop_raw_dump(self) -> None:
        if not self._raw_dump_fp:
            return
        try:
            self._raw_dump_fp.close()
        except Exception:
            pass
        self._raw_dump_fp = None

    def _raw_dump(self, direction: Direction, raw: str) -> None:
        if not self._raw_dump_fp:
            return
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "dir": direction.value,
            "raw": raw,
        }
        try:
            self._raw_dump_fp.write(json.dumps(entry, ensure_ascii=True) + "\n")
            self._raw_dump_fp.flush()
        except Exception:
            pass

    async def send(self, ws: WebSocketClientProtocol, msg: CameraMessage) -> None:
        raw = msg.to_json()
        self.log_filter.log(self.log, Direction.TX, msg.function_name, raw)
        self._raw_dump(Direction.TX, raw)
        if self.tcp_out_log:
            self.tcp_out_log.debug(raw)
        await ws.send(raw)

    async def send_hello(self, ws: WebSocketClientProtocol) -> None:
        if self.minimal_mode:
            cam_ip = _get_setting(self.settings, ["device.host", "host"], allow_empty=True) or ""
            payload = {
                "fwVersion": _get_setting(self.settings, ["device.firmwareVersion", "firmwareVersion"], allow_empty=True)
                or "",
                "ip": cam_ip,
                "uptime": int(_get_setting(self.settings, ["runtime.uptime", "uptime"], allow_empty=True) or 0),
                "connectionHost": cam_ip,
                "connectionSecurePort": 7442,
                "protocolVersion": 1,
            }
            msg = CameraMessage(
                function_name="ubnt_avclient_hello",
                message_id=self._next_msg_id(),
                in_response_to=0,
                payload=payload,
            )
            await self.send(ws, msg)
            return
        cam_ip = _get_setting(self.settings, ["device.host", "host"], allow_empty=True) or ""
        mgmt_host = _get_setting(
            self.settings,
            ["management.connectionHost", "mgmt.connectionHost", "wss.connectionHost", "connectionHost"],
            allow_empty=True,
        ) or (f"{cam_ip}:7442" if cam_ip else "")
        host_only, port = _parse_hostport(str(mgmt_host))
        features = copy.deepcopy(
            _get_setting(self.settings, ["device.features", "features", "capabilities.features", "capabilities"], allow_empty=True)
            or {}
        )
        protocol_version = _get_setting(self.settings, ["device.protocolVersion", "protocolVersion"], allow_empty=True) or 67
        reboot_timeout = _get_setting(self.settings, ["wss.rebootTimeoutSec", "rebootTimeoutSec"], allow_empty=True) or 30
        upgrade_timeout = _get_setting(self.settings, ["wss.upgradeTimeoutSec", "upgradeTimeoutSec"], allow_empty=True) or 150
        firmware_version = _get_setting(self.settings, ["device.firmwareVersion", "firmwareVersion"], allow_empty=True) or "v5.0.129"
        semver = (
            _get_setting(self.settings, ["device.semver", "semver"], allow_empty=True)
            or _extract_semver(firmware_version)
            or firmware_version
        )
        fw_version_payload = _semver_safe_fw_version(firmware_version, semver)
        model = _get_setting(self.settings, ["device.model", "device.marketName", "type", "marketName"], allow_empty=True) or "UVC Camera"
        name = _get_setting(self.settings, ["device.name", "name", "device.model", "type"], allow_empty=True) or (model or "Camera")
        mac_raw = str(_get_setting(self.settings, ["device.mac", "mac"], allow_empty=True) or "")
        mac_compact = re.sub(r"[^0-9A-Fa-f]", "", mac_raw).upper()
        payload = {
            "adoptionCode": _get_setting(self.settings, ["wss.adoptionCode", "adoptionCode"], allow_empty=True) or "",
            "connectionHost": host_only,
            "connectionSecurePort": port,
            "features": features,
            "fwVersion": fw_version_payload,
            "semver": semver,
            "hwrev": int(_get_setting(self.settings, ["device.hwrev", "hwrev"], allow_empty=True) or 10),
            "ip": cam_ip,
            "mac": mac_compact,
            "model": model,
            "name": name,
            "protocolVersion": int(protocol_version),
            "rebootTimeoutSec": int(reboot_timeout),
            "upgradeTimeoutSec": int(upgrade_timeout),
            "uptime": int(_get_setting(self.settings, ["runtime.uptime", "uptime"], allow_empty=True) or 0),
        }
        msg = CameraMessage(
            function_name="ubnt_avclient_hello",
            message_id=self._next_msg_id(),
            in_response_to=0,
            payload=payload,
        )
        await self.send(ws, msg)

    async def handle_rx(self, ws: WebSocketClientProtocol, incoming: Any) -> None:
        raw = incoming
        if isinstance(incoming, (bytes, bytearray)):
            try:
                raw = incoming.decode("utf-8")
            except Exception:
                self.log_filter.log(self.log, Direction.RX, "", f"(binary {len(incoming)} bytes)")
                if self.tcp_in_log:
                    self.tcp_in_log.debug(f"(binary {len(incoming)} bytes)")
                return
        if not isinstance(raw, str):
            return
        try:
            msg = ControllerMessage.from_json(raw)
        except Exception:
            self.log_filter.log(self.log, Direction.RX, "", raw[:200])
            if self.tcp_in_log:
                self.tcp_in_log.debug(raw)
            self._raw_dump(Direction.RX, raw)
            return

        if self.tcp_in_log:
            self.tcp_in_log.debug(raw)
        self.log_filter.log(self.log, Direction.RX, msg.function_name, raw)
        self._raw_dump(Direction.RX, raw)
        validate_message_schema(msg, self.log)
        self.capture.maybe_record(msg)

        if msg.function_name == "ubnt_avclient_hello":
            self.log.debug("WSS: controller hello received (msgId=%s)", msg.message_id)
            return

        handler = self.handlers.get(msg.function_name)
        if handler:
            await handler(ws, msg)
            return

        if msg.function_name == "ubnt_avclient_paramAgreement" and msg.expects_response():
            await self._reply_ok(ws, msg)
            return

        self.log.debug("WSS: unhandled %s (expect=%s): %s", msg.function_name, msg.response_expected, msg.raw)

    async def _reply_ok(self, ws: WebSocketClientProtocol, msg: ControllerMessage):
        payload = {"statusCode": 0, "status": "ok"}
        out = self.build_reply(msg, payload)
        await self.send(ws, out)


# --------------------------------------------------------------------------- #
# 7. Thread wrapper                                                            #
# --------------------------------------------------------------------------- #


class WssManager(threading.Thread):
    def __init__(
        self,
        settings,
        token_event: threading.Event,
        stop_event: threading.Event,
        logger: logging.Logger,
        tcp_in_log: logging.Logger | None = None,
        tcp_out_log: logging.Logger | None = None,
        driver=None,
    ):
        super().__init__(daemon=True, name="WSSManager")
        self.settings = settings
        self.token_event = token_event
        self.stop_event = stop_event
        self.log = logger
        self.tcp_in_log = tcp_in_log
        self.tcp_out_log = tcp_out_log
        self.driver = driver or build_camera_driver(settings, logger)
        self.config = WssConfig.from_env()
        self.log.info(
            "WSS config: secure_transfer=%s snapshot_debug=%s snapshot_dir=%s capture_enabled=%s capture_file=%s log_only=%s silence=%s throttle=%s",
            self.config.use_secure_transfer,
            self.config.snapshot_debug,
            self.config.snapshot_debug_dir,
            self.config.capture_enabled,
            self.config.capture_file or "",
            sorted(self.config.log_only) if self.config.log_only else [],
            sorted(self.config.silence) if self.config.silence else [],
            self.config.throttle_secs,
        )
        self.log_filter = LogFilter(self.config)
        capture = MessageCapture(self.config, logger)
        self.protocol = WssProtocol(
            settings,
            self.driver,
            logger,
            self.tcp_in_log,
            self.tcp_out_log,
            self.config,
            self.log_filter,
            capture,
        )

    def run(self):
        current_key: Optional[Tuple[str, int, str]] = None
        while not self.stop_event.is_set():
            token = _get_setting(self.settings, ["management.token", "mgmt.token"])
            hostport = _get_setting(self.settings, ["management.connectionHost", "mgmt.connectionHost"])
            if not token or not hostport:
                self.log.debug("WSS: waiting for token/host...")
                self.token_event.wait(timeout=10)
                self.token_event.clear()
                continue

            host, port = _parse_hostport(str(hostport))
            key = (host, port, token)
            if key != current_key:
                self.log.info("WSS: (re)connecting to %s:%s (token/host changed)", host, port)
                current_key = key

            try:
                asyncio.run(self._connect_and_serve(host, port, token))
            except Exception as exc:
                self.log.warning("WSS: connection failed: %s; retrying in 5s", exc)
                self.token_event.wait(timeout=5)
                self.token_event.clear()

    async def _connect_and_serve(self, host: str, port: int, token: str):
        url = f"wss://{host}:{port}/camera/1.0/ws?token={token}"

        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

        if os.path.exists("cert.pem") and os.path.exists("key.pem"):
            try:
                ssl_ctx.load_cert_chain("cert.pem", "key.pem")
            except Exception as exc:
                self.log.warning("WSS: could not load client cert/key: %s", exc)

        headers = {
            "Camera-Mac": str(_require_setting(self.settings, ["device.mac", "mac"], "device.mac")).lower().replace(":", ""),
            "Camera-Model": _require_setting(self.settings, ["device.sysid", "sysid"], "device.sysid"),
        }

        kwargs = dict(ssl=ssl_ctx, additional_headers=headers)
        if self.config.use_secure_transfer:
            kwargs["subprotocols"] = ["secure_transfer"]

        self.log.info("WSS: connecting to controller")
        self.log.debug("WSS: URL=%s subprotocols=%s headers=%s", url, kwargs.get("subprotocols"), headers)

        raw_path = _get_setting(self.settings, ["wss.rawDumpFile", "rawDumpFile"], allow_empty=True)
        if raw_path:
            raw_path = Path(str(raw_path))
        else:
            mac = str(_require_setting(self.settings, ["device.mac", "mac"], "device.mac"))
            suffix = mac.replace(":", "").upper() or "UNKNOWN"
            raw_path = Path("logs") / f"wss_raw_{suffix}.ndjson"
        self.protocol.start_raw_dump(raw_path)
        try:
            async with websockets.connect(url, **kwargs) as ws:
                self.log.info("WSS: connected (agreed subprotocol=%s)", ws.subprotocol)
                try:
                    self.log.debug("WSS: response headers: %s", dict(ws.response_headers))
                except Exception:
                    pass
                await self._serve_loop(ws)
        finally:
            self.protocol.stop_raw_dump()

    async def _serve_loop(self, ws: WebSocketClientProtocol):
        if not _get_setting(self.settings, ["wss.skipHello", "skipHello"]):
            self.protocol.reset_msg_id(-1)
            await self.protocol.send_hello(ws)
        try:
            async for incoming in ws:
                await self.protocol.handle_rx(ws, incoming)
        except ConnectionClosed as exc:
            self.log.warning("WSS: server closed: code=%s reason=%s", getattr(exc, "code", None), getattr(exc, "reason", None))
            raise
        except Exception:
            self.log.exception("WSS: serve_loop crashed")
            raise
