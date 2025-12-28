from __future__ import annotations
from typing import Dict, Any
import requests
from requests.auth import HTTPDigestAuth

import asyncio

from Unifi.drivers.camera_driver import CameraDriver

def _amcrest_snapshot_sync(ip: str, user: str, pwd: str, channel: int, https: bool, verify_ssl: bool, timeout: int) -> bytes:
    proto = "https" if https else "http"
    url = f"{proto}://{ip}/cgi-bin/snapshot.cgi?channel={channel}"
    r = requests.get(url, auth=HTTPDigestAuth(user, pwd), timeout=timeout, verify=verify_ssl)
    r.raise_for_status()
    return r.content

class AmcrestDriver(CameraDriver):
    async def get_snapshot_jpeg(self, *, timeout_s: int = 5) -> bytes:
        ip   = self.settings["ip"]
        user = self.settings["user"]
        pwd  = self.settings["pass"]
        chan = self.settings.get("channel", 0)
        https = self.settings.get("https", False)
        verify_ssl = self.settings.get("verify_ssl", False)

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            _amcrest_snapshot_sync, ip, user, pwd, chan, https, verify_ssl, timeout_s
        )

    # Optional: actually push some settings to the camera.
    # For now we just acknowledge and echo.
    async def apply_video_settings(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.log.debug("Amcrest apply_video_settings: %s", payload)
        return {"video": payload.get("video", {})}

    async def apply_isp_settings(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.log.debug("Amcrest apply_isp_settings: %s", payload)
        # Minimal “accepted” echo; add real calls later if needed
        out = {"statusCode": 0, "status": "ok"}
        out.update(payload)
        # Example: enforce a default mountPosition if none provided
        out.setdefault("mountPosition", "ceiling")
        return out