from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any, Dict, Optional


class VideoStreamService:
    """Background watcher that keeps FLV streams aligned with settings['video']."""

    def __init__(
        self,
        settings,
        driver,
        log: Optional[logging.Logger] = None,
        poll_interval: float = 1.0,
    ) -> None:
        self.settings = settings
        self.driver = driver
        self.log = log or logging.getLogger("camera.stream.service")
        self.poll_interval = poll_interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_signature: Optional[str] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="VideoStreamService", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                ready = bool(self.settings.get("state.videoReady", False))
            except Exception:
                ready = False
            if not ready:
                self._last_signature = None
                self._stop.wait(self.poll_interval)
                continue
            try:
                raw_video = self.settings.get("video", {}) or {}
            except Exception as exc:
                self.log.error("Failed to read video config: %s", exc)
                raw_video = {}
            video_cfg = self._filter_video(raw_video)
            signature = self._signature(video_cfg)
            if signature != self._last_signature:
                self._last_signature = signature
                self._apply(video_cfg)
            self._stop.wait(self.poll_interval)

    def _filter_video(self, video_cfg: Dict[str, Any]) -> Dict[str, Any]:
        usable: Dict[str, Any] = {}
        for vid, vcfg in video_cfg.items():
            if not isinstance(vcfg, dict):
                continue
            serializer = (vcfg.get("avSerializer") or {})
            dests = serializer.get("destinations") or []
            if serializer.get("type") == "extendedFlv" and dests:
                usable[vid] = vcfg
        return usable

    def _signature(self, video_cfg: Dict[str, Any]) -> Optional[str]:
        if not video_cfg:
            return None
        try:
            return json.dumps(video_cfg, sort_keys=True)
        except Exception:
            return str(video_cfg)

    def _apply(self, video_cfg: Dict[str, Any]) -> None:
        payload = {"video": video_cfg}
        video_settings = self.settings.get("videoSettings", {}) or {}
        for key in ("videoMode", "hdrMode", "downScaleMode"):
            value = video_settings.get(key)
            if value:
                payload[key] = value
        try:
            asyncio.run(self.driver.apply_video_settings(payload))
            if video_cfg:
                self.log.info("Active streams: %s", ", ".join(sorted(video_cfg.keys())))
            else:
                self.log.info("No active video streams configured; all streams stopped")
        except Exception as exc:
            self.log.error("Failed to apply video settings: %s", exc)
