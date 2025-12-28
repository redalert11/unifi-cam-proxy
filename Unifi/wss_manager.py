"""
wss_manager.py

Structured UniFi Protect AVClient WebSocket manager with:
  1. Configuration helpers and small utilities.
  2. Message dataclasses + schema validation.
  3. Logging filters and optional message capture.
  4. Handler registry with dedicated handler groups.
  5. Protocol core responsible for parsing/dispatching.
  6. Thread wrapper that owns connection + reconnection logic.

Environment knobs (set before running python):
  export SNAPSHOT_DEBUG=true                   # save latest snapshot to debug_snaps
  export SNAPSHOT_DEBUG_DIR=/path/to/dir       # override snapshot output directory
  export WSS_LOG_ONLY=\"fn1,fn2\"              # only log these functionNames
  export WSS_SILENCE=\"fn1,fn2\"               # suppress logging for these functionNames
  export WSS_THROTTLE=60                       # throttle NetworkStatus/GetSystemStats logging (seconds)
  export WSS_CAPTURE_FILE=/tmp/wss.ndjson      # enable JSONL capture of RX messages
  export WSS_CAPTURE_UNIQUE=false              # capture every message (even duplicates)
  export WSS_CAPTURE_UNIQUE_LIMIT=500          # how many hashes to keep for dedupe
  export WSS_DISABLE_SECURE_TRANSFER=true      # skip secure_transfer subprotocol
  export PERSIST_LAST_RECEIVED=false           # disable saving lastReceived.* to settings.json
"""

# Debug tip: on the UniFi Protect controller, run `tail -f /volume1/.srv/unifi-protect/logs/cameras.log` to watch camera logs live.

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

HELLO_FEATURES = {
    "accelerometer": True,
    "adjustableIR": False,
    "adjustableSpeakerVolume": False,
    "aec": ["fullband"],
    "aecTalkbackSwitch": False,
    "audioCodecs": ["aac", "opus"],
    "autoICROnly": True,
    "battery": False,
    "bitrateReduction": ["idleFps"],
    "bluetooth": False,
    "canLoadEncryptedFW": True,
    "chimeControl": False,
    "clarityZones": {"maxZones": 16, "rectangleOnly": False},
    "doorAccessConfig": False,
    "endlesspan": False,
    "excludeZone": {"maxZones": 16, "rectangleOnly": True},
    "externalIR": False,
    "externalIRAutodetect": False,
    "fingerprint": False,
    "fisheye": False,
    "flash": False,
    "fullHdSnapshot": True,
    "hallwayMode": False,
    "hallwayModeHdrOnRequired": False,
    "hdr": True,
    "hotplug": {"extender": {"attached": False}},
    "lcdScreen": False,
    "ldc": True,
    "ledIR": True,
    "ledStatus": True,
    "lidar": False,
    "lightningZoom": False,
    "lineIn": False,
    "locate": False,
    "luxCheck": True,
    "magicZoom": False,
    "maxScaleDownLevel": 1,
    "mic": True,
    "motionDetect": ["enhanced"],
    "nfc": False,
    "opticalZoom": False,
    "optimizeIR": False,
    "opusSampleRates": [12000, 16000, 24000, 48000],
    "orientation": True,
    "pirMotionDetect": False,
    "presetTour": False,
    "privacyMask": True,
    "privacyMasks": {"maxZones": 16, "rectangleOnly": False},
    "ptz": False,
    "resetIC": True,
    "rtc": False,
    "sdmmc": False,
    "smartDetect": [
        "person",
        "vehicle",
        "animal",
        "lineCrossing",
        "faceEnhancedByAiKey",
        "lprEnhancedByAiKey",
        "alrmSmoke",
        "alrmCmonx",
        "alrmBabyCry",
        "alrmSpeak",
    ],
    "smokeCover": False,
    "speaker": True,
    "squareEventThumbnail": True,
    "streamEncryptable": True,
    "supportCustomRingtone": False,
    "touchFocus": False,
    "truedaynight": True,
    "verticalFlipWarning": False,
    "videoCodecs": ["h264", "h265", "mjpg"],
    "videoMode": ["default", "highFps", "sport", "slowShutter"],
    "videoModeMaxFps": [24, 48, 24, 20],
    "videoSourceCount": 2,
    "wifi": False,
}

DEFAULT_CHANGE_VIDEO_PAYLOAD: Dict[str, Any] = {
    "audio": {
        "agc": False,
        "agcTarget": -1,
        "bitRate": 64000,
        "channels": 1,
        "declick": False,
        "declickThld4Khz": 1e-05,
        "declickThldAll": 0.0001,
        "denoise": False,
        "description": "audio track",
        "enableTemporalNoiseShaping": False,
        "enabled": True,
        "highpass": False,
        "hpfCutoff": 0,
        "mode": 0,
        "name": "",
        "nsLevel": -1,
        "quality": 1,
        "sampleRate": 48000,
        "type": "aac",
        "volume": 100,
    },
    "cfgver": 4,
    "chip": {
        "common": {"vsync_detection_disable": 0},
        "debug": {"check_disable": 0},
        "vin0": {
            "description": "Input src 0",
            "enabled": True,
            "hdrMode": 1,
            "height": 1520,
            "videoMode": "default",
            "vinFps": 24,
            "vsrcCtxSwitch": 0,
            "vsrcId": 0,
            "width": 2688,
        },
    },
    "video": {
        "averageMotionAdaptive": 1,
        "downScaleMode": 0,
        "enableHrd": False,
        "encodeMode": -1,
        "hallwayMode": -1,
        "lowDelay": True,
        "mjpg": {
            "autoBitrate": False,
            "autoFps": False,
            "avSerializer": {
                "destinations": ["file:///tmp/snap.jpeg"],
                    "parameters": {
                        "audioId": 1000,
                        "enableTimestampsOverlapAvoidance": False,
                        "streamName": "DEFAULT_3",
                        "suppressAudio": True,
                        "suppressVideo": False,
                        "videoId": 1001,
                    },
                "type": "mjpg",
            },
            "bitRateCbrAvg": 500000,
            "bitRateVbrMax": 500000,
            "bitRateVbrMin": 32000,
            "bufferId": 0,
            "debugEncoderType": 0,
            "description": "JPEG pictures",
            "enabled": True,
            "fps": 2,
            "height": 1512,
            "idleFps": False,
            "isCbr": False,
            "latencyTestSID": 0,
            "maxFps": 2,
            "minClientAdaptiveBitRate": 0,
            "minMotionAdaptiveBitRate": 0,
            "nMultiplier": None,
            "name": "mjpg0.0",
            "quality": 80,
            "sourceId": 3,
            "streamId": 3,
            "streamOrdinal": 3,
            "type": "mjpg",
            "validBitrateRangeMax": 6000000,
            "validBitrateRangeMin": 32000,
            "width": 2688,
        },
        "video1": {
            "M": 1,
            "N": 24,
            "autoBitrate": True,
            "autoFps": True,
                "avSerializer": {
                    "destinations": None,
                    "parameters": {"streamName": "DEFAULT_0", "withTalkback": True},
                    "type": "extendedFlv",
                },
            "bitRateCbrAvg": 3000000,
            "bitRateVbrMax": 10000000,
            "bitRateVbrMin": 32000,
            "bufferId": 2,
            "debugEncoderType": 0,
            "description": "Hi quality video track",
            "dynamicFpsMode": 2,
            "enabled": True,
            "fps": 24,
            "gopModel": 0,
            "height": 1512,
            "horizontalFlip": False,
            "idleFps": False,
            "isCbr": False,
            "latencyTestSID": 0,
            "maxFps": 24,
            "minClientAdaptiveBitRate": 0,
            "minMotionAdaptiveBitRate": 2000000,
            "nMultiplier": 5,
            "name": "video0.0",
            "sourceId": 0,
            "streamId": 0,
            "streamOrdinal": 0,
            "tos": -1,
            "type": "h264",
            "validBitrateRangeMargin": 2000000,
            "validBitrateRangeMax": 12000000,
            "validBitrateRangeMin": 32000,
            "validFpsValues": [1, 2, 3, 4, 5, 6, 8, 9, 10, 12, 15, 16, 18, 20, 24],
            "verticalFlip": False,
            "width": 2688,
        },
        "video2": {
            "M": 1,
            "N": 24,
            "autoBitrate": True,
            "autoFps": True,
                "avSerializer": {
                    "destinations": None,
                    "parameters": {"streamName": "DEFAULT_1", "withTalkback": True},
                    "type": "extendedFlv",
                },
            "bitRateCbrAvg": 200000,
            "bitRateVbrMax": 300000,
            "bitRateVbrMin": 32000,
            "bufferId": 1,
            "debugEncoderType": 0,
            "description": "Low quality video track",
            "dynamicFpsMode": 2,
            "enabled": True,
            "fps": 24,
            "gopModel": 0,
            "height": 360,
            "horizontalFlip": False,
            "idleFps": False,
            "isCbr": False,
            "latencyTestSID": 0,
            "maxFps": 24,
            "minClientAdaptiveBitRate": 0,
            "minMotionAdaptiveBitRate": 200000,
            "nMultiplier": 5,
            "name": "video0.1",
            "sourceId": 1,
            "streamId": 1,
            "streamOrdinal": 1,
            "tos": -1,
            "type": "h264",
            "validBitrateRangeMargin": 100000,
            "validBitrateRangeMax": 1000000,
            "validBitrateRangeMin": 32000,
            "validFpsValues": [1, 2, 3, 4, 5, 6, 8, 9, 10, 12, 15, 16, 18, 20, 24],
            "verticalFlip": False,
            "width": 640,
        },
        "video3": {
            "M": 1,
            "N": 24,
            "autoBitrate": True,
            "autoFps": True,
                "avSerializer": {
                    "destinations": ["tcp://192.168.0.1:7550?retryInterval=1&connectTimeout=5"],
                    "parameters": {"opusSampleRate": 24000, "streamName": "DEFAULT_2", "withOpus": True},
                    "type": "extendedFlv",
                },
            "bitRateCbrAvg": 500000,
            "bitRateVbrMax": 2000000,
            "bitRateVbrMin": 32000,
            "bufferId": 3,
            "debugEncoderType": 0,
            "description": "Medium quality video track",
            "dynamicFpsMode": 2,
            "enabled": True,
            "fps": 24,
            "gopModel": 0,
            "height": 720,
            "horizontalFlip": False,
            "idleFps": False,
            "isCbr": False,
            "latencyTestSID": 0,
            "maxFps": 24,
            "minClientAdaptiveBitRate": 150000,
            "minMotionAdaptiveBitRate": 750000,
            "nMultiplier": 5,
            "name": "video0.2",
            "sourceId": 2,
            "streamId": 2,
            "streamOrdinal": 2,
            "tos": -1,
            "type": "h264",
            "validBitrateRangeMargin": 500000,
            "validBitrateRangeMax": 3000000,
            "validBitrateRangeMin": 32000,
            "validFpsValues": [1, 2, 3, 4, 5, 6, 8, 9, 10, 12, 15, 16, 18, 20, 24],
            "verticalFlip": False,
            "width": 1280,
        },
    },
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


def _mac_stream_suffix(settings) -> str:
    mac = (settings.get("mac") or "").upper().replace(":", "")
    return mac or "000000000000"


def _apply_camera_identity_to_video_payload(payload: Dict[str, Any], settings) -> None:
    video = payload.get("video")
    if not isinstance(video, dict):
        return
    mac_suffix = _mac_stream_suffix(settings)
    mapping = {"mjpg": 3, "video1": 0, "video2": 1, "video3": 2}
    for key, idx in mapping.items():
        vcfg = video.get(key)
        if not isinstance(vcfg, dict):
            continue
        serializer = vcfg.get("avSerializer")
        if not isinstance(serializer, dict):
            continue
        params = serializer.get("parameters")
        if isinstance(params, dict):
            params["streamName"] = f"{mac_suffix}_{idx}"
HELLO_PROTOCOL_VERSION = 67
HELLO_REBOOT_TIMEOUT_SEC = 30
HELLO_UPGRADE_TIMEOUT_SEC = 150


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
    snapshot_debug_dir: Path = Path("/workspace/Unifi/debug_snaps")
    snapshot_debug_keep: int = 5

    @staticmethod
    def from_env() -> "WssConfig":
        log_only = {s.strip() for s in os.getenv("WSS_LOG_ONLY", "").split(",") if s.strip()}
        silence = {s.strip() for s in os.getenv("WSS_SILENCE", "").split(",") if s.strip()}
        throttle = float(os.getenv("WSS_THROTTLE", "0") or 0)

        capture_path = os.getenv("WSS_CAPTURE_FILE", "").strip()
        capture_enabled = bool(capture_path)
        capture_unique = os.getenv("WSS_CAPTURE_UNIQUE", "1").strip().lower() not in {"0", "false"}
        capture_limit = int(os.getenv("WSS_CAPTURE_UNIQUE_LIMIT", "1000") or 1000)

        snapshot_debug = os.getenv("SNAPSHOT_DEBUG", "").strip().lower() in {"true", "1", "yes"}
        snapshot_dir = Path(os.getenv("SNAPSHOT_DEBUG_DIR", str(WssConfig.snapshot_debug_dir)))
        snapshot_keep = int(os.getenv("SNAPSHOT_DEBUG_KEEP", "5") or 5)

        use_secure = os.getenv("WSS_DISABLE_SECURE_TRANSFER", "").strip().lower() not in {"1", "true"}

        return WssConfig(
            use_secure_transfer=use_secure,
            log_only=log_only,
            silence=silence,
            throttle_secs=throttle,
            capture_enabled=capture_enabled,
            capture_file=Path(capture_path) if capture_enabled else None,
            capture_unique=capture_unique,
            capture_unique_limit=capture_limit,
            snapshot_debug=snapshot_debug,
            snapshot_debug_dir=snapshot_dir,
            snapshot_debug_keep=snapshot_keep,
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
        status_code = None
        status = None
        cleaned_payload: Any
        if isinstance(payload, dict):
            cleaned_payload = copy.deepcopy(payload)
            status_code = cleaned_payload.pop("statusCode", None)
            status = cleaned_payload.pop("status", None)
        else:
            cleaned_payload = payload
        return cls(
            function_name=in_msg.function_name,
            message_id=message_id,
            in_response_to=in_msg.message_id,
            payload=cleaned_payload,
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
    "GetSystemStats": MessageSchema(),
    "NetworkStatus": MessageSchema(),
    "ChangeVideoSettings": MessageSchema(
        payload_optional_keys=["video", "videoMode", "hdrMode", "downScaleMode"],
    ),
    "ChangeIspSettings": MessageSchema(),
    "ChangeOsdSettings": MessageSchema(),
    "ChangeSoundLedSettings": MessageSchema(),
    "ChangeTalkbackSettings": MessageSchema(),
    "ChangeAnalyticsSettings": MessageSchema(),
    "ChangeDeviceSettings": MessageSchema(),
    "ChangeSmartMotionSettings": MessageSchema(),
    "SmartMotionTest": MessageSchema(),
    "ChangeAudioEventsSettings": MessageSchema(),
    "ChangeSmartDetectSettings": MessageSchema(),
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
        return (self.settings.get("mac") or "").upper()

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
        
        # Check if persisting lastReceived messages is disabled
        persist_last_received = os.getenv("PERSIST_LAST_RECEIVED", "true").lower() not in {"false", "0", "no"}
        if not persist_last_received:
            return
        
        try:
            self.settings.update({f"lastReceived.{fn}": payload})
        except Exception:
            self.log.exception("Failed to persist payload snapshot for %s", fn)

    def _load_last_payload(self, fn: str) -> Optional[Dict[str, Any]]:
        try:
            stored = self.settings.get(f"lastReceived.{fn}")
        except Exception:
            self.log.exception("Failed to fetch payload snapshot for %s", fn)
            return None
        if isinstance(stored, dict):
            return copy.deepcopy(stored)
        return None


class MaintenanceHandlers(BaseHandlers):
    async def on_param_agreement(self, ws: WebSocketClientProtocol, msg: ControllerMessage):
        if msg.expects_response():
            await self._reply_ok(ws, msg)

    async def on_time_sync(self, ws: WebSocketClientProtocol, msg: ControllerMessage):
        now_ms = int(time.time() * 1000)
        payload = {"t1": now_ms, "t2": now_ms}
        out = self.protocol.build_reply(msg, payload)
        await self.protocol.send(ws, out)

    async def on_get_system_stats(self, ws: WebSocketClientProtocol, msg: ControllerMessage):
        if not msg.expects_response():
            return
        payload = {
            "cpu": 5,
            "memory": 20,
            "temperature": 45,
            "uptime": int(self.settings.get("uptime", 0) or 0),
            "statusCode": 0,
            "status": "ok",
            "deviceID": self._device_id(),
        }
        out = self.protocol.build_reply(msg, payload)
        await self.protocol.send(ws, out)

    async def on_network_status(self, ws: WebSocketClientProtocol, msg: ControllerMessage):
        if not msg.expects_response():
            return
        payload = {
            "status": "connected",
            "ip": self.settings.get("host"),
            "mac": (self.settings.get("mac") or "").lower(),
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

    async def on_update_firmware_request(self, ws: WebSocketClientProtocol, msg: ControllerMessage):
        incoming = msg.payload if isinstance(msg.payload, dict) else {}
        self._persist_incoming_payload(msg.function_name, incoming)
        
        # Extract version from the URI (e.g., version=5.1.190)
        uri = incoming.get("uri", "")
        new_version = None
        if "version=" in uri:
            try:
                # Parse version from URI query string
                from urllib.parse import urlparse, parse_qs
                parsed = urlparse(uri)
                query_params = parse_qs(parsed.query)
                if "version" in query_params:
                    new_version = query_params["version"][0]
            except Exception as exc:
                self.log.warning("Failed to parse version from URI: %s", exc)
        
        # Update the firmware version to match what the controller expects
        current_version = self.settings.get("firmwareVersion", "unknown")
        if new_version:
            try:
                self.settings["firmwareVersion"] = new_version
                self.log.info(
                    "Firmware version updated: %s -> %s (from controller request)",
                    current_version,
                    new_version
                )
            except Exception as exc:
                self.log.error("Failed to update firmwareVersion setting: %s", exc)
        else:
            self.log.info(
                "Firmware update request (proxy mode): uri=%s (version not parsed)",
                uri
            )
        
        if msg.expects_response():
            # Reply with success to prevent controller from retrying
            await self._reply_ok(ws, msg, incoming)


class SettingsHandlers(BaseHandlers):
    async def _reply_with_payload(self, ws, msg, payload: Dict[str, Any]):
        reply = copy.deepcopy(payload)
        reply["statusCode"] = 0
        reply["status"] = "ok"
        reply["deviceID"] = self._device_id()
        out = self.protocol.build_reply(msg, reply)
        await self.protocol.send(ws, out)

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

    async def on_change_smart_motion_settings(self, ws: WebSocketClientProtocol, msg: ControllerMessage):
        incoming = msg.payload if isinstance(msg.payload, dict) else {}
        self._persist_incoming_payload(msg.function_name, incoming)
        if msg.expects_response():
            defaults = {
                "mountPosition": "ceiling",
                "heatmapOverlay": False,
                "interruptReason": "",
                "interruptTimeoutMs": 5000,
                "queueIfDestUnavailable": True,
                "respondFullSettings": True,
                "sendEvents": 2,
                "sendPulse": 2,
                "serviceWaitTimeMSec": 166,
                "transactionId": -1,
                "from": "",
                "to": "",
                "responseExpected": True,
                "functionName": "ChangeSmartMotionSettings",
                "isBroadcast": False,
            }
            reply = copy.deepcopy(defaults)
            reply.update(incoming)
            await self._reply_with_payload(ws, msg, reply)

    async def on_smart_motion_test(self, ws: WebSocketClientProtocol, msg: ControllerMessage):
        if msg.expects_response():
            reply = {"payload": msg.payload if isinstance(msg.payload, dict) else None}
            await self._reply_with_payload(ws, msg, reply)

    async def on_change_audio_events_settings(self, ws: WebSocketClientProtocol, msg: ControllerMessage):
        incoming = msg.payload if isinstance(msg.payload, dict) else {}
        self._persist_incoming_payload(msg.function_name, incoming)
        if msg.expects_response():
            reply = {"payload": incoming}
            await self._reply_with_payload(ws, msg, reply)

    async def on_change_video_settings(self, ws: WebSocketClientProtocol, msg: ControllerMessage):
        payload = self._coerce_payload_to_dict(msg.payload)
        if not payload:
            cached = self._load_last_payload("ChangeVideoSettings") or {}
            if not cached:
                stored_vs = self.settings.get("videoSettings")
                if isinstance(stored_vs, dict):
                    raw = stored_vs.get("raw")
                    if isinstance(raw, dict) and raw:
                        cached = copy.deepcopy(raw)
                    else:
                        fallback = {k: copy.deepcopy(v) for k, v in stored_vs.items() if k != "raw"}
                        cached = fallback
            if not cached:
                cached = copy.deepcopy(DEFAULT_CHANGE_VIDEO_PAYLOAD)
                _apply_camera_identity_to_video_payload(cached, self.settings)
                try:
                    raw_copy = copy.deepcopy(cached)
                    self.settings.update({"videoSettings.raw": raw_copy})
                    self.settings.update({"lastReceived.ChangeVideoSettings": copy.deepcopy(cached)})
                except Exception:
                    self.log.exception("Failed to seed default ChangeVideoSettings payload")
            if cached:
                cached = copy.deepcopy(cached)
                cached["statusCode"] = 0
                cached["status"] = "ok"
                cached["deviceID"] = self._device_id()
                self.log.debug("ChangeVideoSettings replying with cached payload (keys=%s)", list(cached.keys()))
                if msg.expects_response():
                    out = self.protocol.build_reply(msg, cached)
                    await self.protocol.send(ws, out)
            else:
                self.log.warning("ChangeVideoSettings requested current state but no cached payload available")
                if msg.expects_response():
                    await self._reply_ok(ws, msg)
            return
        self._persist_incoming_payload(msg.function_name, payload)
        mode_defaults = {
            "videoMode": payload.get("videoMode") or "default",
            "hdrMode": payload.get("hdrMode") or "off",
            "downScaleMode": payload.get("downScaleMode") or "original",
        }

        video = payload.get("video")
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

        if isinstance(video, dict):
            mirrored: Dict[str, Dict[str, Any]] = {}
            for vid, vcfg in video.items():
                vcfg = vcfg if isinstance(vcfg, dict) else {}
                with_defaults = {**vcfg, **mode_defaults}
                if "type" not in with_defaults:
                    with_defaults["type"] = "h264"
                mirrored[vid] = with_defaults
            reply_payload["video"] = mirrored

        self._persist_video_settings(payload, reply_payload)

        if msg.expects_response():
            out = self.protocol.build_reply(msg, reply_payload)
            await self.protocol.send(ws, out)

    async def on_change_isp_settings(self, ws: WebSocketClientProtocol, msg: ControllerMessage):
        payload = msg.payload if isinstance(msg.payload, dict) else {}
        if not payload:
            cached = self._load_last_payload("ChangeIspSettings")
            if not cached:
                cached = copy.deepcopy(DEFAULT_CHANGE_ISP_PAYLOAD)
                try:
                    self.settings.update({"lastReceived.ChangeIspSettings": copy.deepcopy(DEFAULT_CHANGE_ISP_PAYLOAD)})
                except Exception:
                    self.log.exception("Failed to seed default ChangeIspSettings payload")
            if cached:
                cached["statusCode"] = 0
                cached["status"] = "ok"
                cached["deviceID"] = self._device_id()
                self.log.debug("ChangeIspSettings replying with cached payload (keys=%s)", list(cached.keys()))
                if msg.expects_response():
                    out = self.protocol.build_reply(msg, cached)
                    await self.protocol.send(ws, out)
            else:
                self.log.warning("ChangeIspSettings requested current state but no cached payload available")
                if msg.expects_response():
                    await self._reply_ok(ws, msg)
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
        if "video" in stored:
            try:
                self.settings["video"] = stored["video"]
            except Exception:
                self.log.exception("Failed to persist active video map")
        try:
            self.settings["state.videoReady"] = True
        except Exception:
            self.log.exception("Failed to mark video state ready")

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

    async def on_change_smart_detect_settings(self, ws: WebSocketClientProtocol, msg: ControllerMessage):
        incoming = msg.payload if isinstance(msg.payload, dict) else {}
        self._persist_incoming_payload(msg.function_name, incoming)
        if msg.expects_response():
            reply = {"payload": incoming}
            reply["statusCode"] = 0
            reply["status"] = "ok"
            reply["deviceID"] = self._device_id()
            out = self.protocol.build_reply(msg, reply)
            await self.protocol.send(ws, out)


def build_handler_registry(settings, driver, logger: logging.Logger, protocol: "WssProtocol") -> HandlerRegistry:
    reg = HandlerRegistry()
    maint = MaintenanceHandlers(settings, driver, logger, protocol)
    sets = SettingsHandlers(settings, driver, logger, protocol)
    snap = SnapshotHandlers(settings, driver, logger, protocol)
    anal = AnalyticsHandlers(settings, driver, logger, protocol)

    reg.register("ubnt_avclient_paramAgreement", maint.on_param_agreement)
    reg.register("ubnt_avclient_timeSync", maint.on_time_sync)
    reg.register("GetSystemStats", maint.on_get_system_stats)
    reg.register("NetworkStatus", maint.on_network_status)
    reg.register("StopService", maint.on_stop_service)
    reg.register("EnableLogging", maint.on_enable_logging)
    reg.register("UpdateFirmwareRequest", maint.on_update_firmware_request)

    reg.register("ChangeVideoSettings", sets.on_change_video_settings)
    reg.register("ChangeIspSettings", sets.on_change_isp_settings)
    reg.register("ChangeOsdSettings", sets.on_change_osd_settings)
    reg.register("ChangeSoundLedSettings", sets.on_change_sound_led_settings)
    reg.register("ChangeTalkbackSettings", sets.on_change_talkback_settings)
    reg.register("ChangeAnalyticsSettings", sets.on_change_analytics_settings)
    reg.register("ChangeDeviceSettings", sets.on_change_device_settings)
    reg.register("UpdateUsernamePassword", sets.on_update_username_password)
    reg.register("ChangeClarityZones", sets.on_change_clarity_zones)
    reg.register("AudioAgentChangeTuning", sets.on_audio_agent_change_tuning)
    reg.register("ChangeSmartMotionSettings", sets.on_change_smart_motion_settings)
    reg.register("SmartMotionTest", sets.on_smart_motion_test)
    reg.register("ChangeAudioEventsSettings", sets.on_change_audio_events_settings)

    reg.register("GetRequest", snap.on_get_request)

    reg.register("AnalyticsTest", anal.on_analytics_test)
    reg.register("UpdateFaceDBRequest", anal.on_update_face_db_request)
    reg.register("ChangeSmartDetectSettings", anal.on_change_smart_detect_settings)

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
        config: WssConfig,
        log_filter: LogFilter,
        capture: MessageCapture,
    ):
        self.settings = settings
        self.driver = driver
        self.log = logger
        self.config = config
        self.log_filter = log_filter
        self.capture = capture
        self._msg_id = 0
        self.handlers = build_handler_registry(settings, driver, logger, self)

    def _next_msg_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    def build_reply(self, in_msg: ControllerMessage, payload: Dict[str, Any]) -> CameraMessage:
        return CameraMessage.reply_to(in_msg, self._next_msg_id(), payload)

    async def send(self, ws: WebSocketClientProtocol, msg: CameraMessage) -> None:
        raw = msg.to_json()
        self.log_filter.log(self.log, Direction.TX, msg.function_name, raw)
        await ws.send(raw)

    async def send_hello(self, ws: WebSocketClientProtocol) -> None:
        cam_ip = self.settings.get("host")
        mgmt_host = self.settings.get("mgmt.connectionHost") or f"{cam_ip}:7442"
        host_only, port = _parse_hostport(str(mgmt_host))
        features = copy.deepcopy(self.settings.get("features") or HELLO_FEATURES)
        payload = {
            "adoptionCode": self.settings.get("adoptionCode", ""),
            "connectionHost": host_only,
            "connectionSecurePort": port,
            "features": features,
            "fwVersion": self.settings.get("firmwareVersion", "v5.0.129"),
            "semver": self.settings.get("firmwareVersion", "v5.0.129"),
            "hwrev": int(self.settings.get("hwrev", 10)),
            "ip": cam_ip,
            "mac": (self.settings.get("mac") or "").upper(),
            "model": self.settings.get("type") or self.settings.get("marketName") or "UVC Camera",
            "name": self.settings.get("name") or (self.settings.get("type") or "Camera"),
            "protocolVersion": int(self.settings.get("protocolVersion", HELLO_PROTOCOL_VERSION)),
            "rebootTimeoutSec": int(self.settings.get("rebootTimeoutSec", HELLO_REBOOT_TIMEOUT_SEC)),
            "upgradeTimeoutSec": int(self.settings.get("upgradeTimeoutSec", HELLO_UPGRADE_TIMEOUT_SEC)),
            "uptime": int(self.settings.get("uptime", 0) or 0),
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
                return
        if not isinstance(raw, str):
            return
        try:
            msg = ControllerMessage.from_json(raw)
        except Exception:
            self.log_filter.log(self.log, Direction.RX, "", raw[:200])
            return

        self.log_filter.log(self.log, Direction.RX, msg.function_name, raw)
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
        driver=None,
    ):
        super().__init__(daemon=True, name="WSSManager")
        self.settings = settings
        self.token_event = token_event
        self.stop_event = stop_event
        self.log = logger
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
        self.protocol = WssProtocol(settings, self.driver, logger, self.config, self.log_filter, capture)

    def run(self):
        current_key: Optional[Tuple[str, int, str]] = None
        while not self.stop_event.is_set():
            token = self.settings.get("mgmt.token")
            hostport = self.settings.get("mgmt.connectionHost")
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
            "Camera-Mac": (self.settings.get("mac") or "").lower(),
            "Camera-Model": self.settings.get("sysid") or "0xa573",
        }

        kwargs = dict(ssl=ssl_ctx, additional_headers=headers)
        if self.config.use_secure_transfer:
            kwargs["subprotocols"] = ["secure_transfer"]

        self.log.info("WSS: connecting to controller")
        self.log.debug("WSS: URL=%s subprotocols=%s headers=%s", url, kwargs.get("subprotocols"), headers)

        async with websockets.connect(url, **kwargs) as ws:
            self.log.info("WSS: connected (agreed subprotocol=%s)", ws.subprotocol)
            try:
                self.log.debug("WSS: response headers: %s", dict(ws.response_headers))
            except Exception:
                pass
            await self._serve_loop(ws)

    async def _serve_loop(self, ws: WebSocketClientProtocol):
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
