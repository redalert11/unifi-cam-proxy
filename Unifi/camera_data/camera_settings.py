import logging
import json
import os
import socket
import sys
import urllib.request
import urllib.error

from pathlib import Path

from Unifi.utils.settings_manager import SettingsManager
from .camera_models import CameraModelDatabase


class CameraSettings:
    """
    Camera-specific settings layered on top of the shared SettingsManager.
    Handles defaults, platform/sysid enrichment, and network info population.
    """

    def __init__(self, settings_file=None, logger=None):
        self.logger = logger or logging.getLogger(__name__)
        self.logger.setLevel(logging.INFO)
        self.settings_file = Path(settings_file or (Path(__file__).parent / "settings.json")).resolve()
        self.store = SettingsManager(self.settings_file, defaults=self._default_settings())

        self._ensure_platform_and_sysid()
        self._get_ip_address()
        self._get_mac_address()
        status = os.environ.get("FIRMWARE_STATUS", "GA")  # GA | RC | EA | ALL
        self._update_latest_firmware_version(status=status)

    def _load_or_initialize(self):
        # Legacy compatibility: retained for callers, delegates to store
        return self.store.all()

    def _ensure_platform_and_sysid(self):
        changed = False
        market = self.store.get("marketName")
        if not market:
            model = os.environ.get("CAMERA_MODEL", "").strip()
            if not model:
                self.logger.error("CAMERA_MODEL environment variable is required to set type or platform.")
                sys.exit(1)
            changed |= self.store._set_nested("marketName", model)
            market = model

        if not self.store.get("platform"):
            platform = CameraModelDatabase.get_platform(market)
            if not platform:
                self.logger.error(f"Unknown platform for type: {market}")
                sys.exit(1)
            changed |= self.store._set_nested("platform", platform)

        if not self.store.get("sysid"):
            sysid = CameraModelDatabase.CameraSysIds.get(market)
            if sysid is None:
                self.logger.error(f"Unknown system ID for type: {market}")
                sys.exit(1)
            changed |= self.store._set_nested("sysid", sysid)

        if not self.store.get("type"):
            changed |= self.store._set_nested("type", market.replace("_", " "))

        if changed:
            self.store._save()

    def _get_mac_address(self, interface="eth0"):
        if not self.store.get("mac"):
            try:
                with open(f"/sys/class/net/{interface}/address") as f:
                    mac = f.read().strip()
                    if not mac:
                        self.logger.error(f"Empty MAC address for interface '{interface}'.")
                        sys.exit(1)
                    self.store.set("mac", mac)
            except FileNotFoundError:
                self.logger.error(f"Network interface '{interface}' not found.")
                sys.exit(1)

    def _get_ip_address(self):
        if not self.store.get("host"):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    s.connect(("8.8.8.8", 80))
                    ip = s.getsockname()[0]
                    if not ip:
                        self.logger.error("Failed to retrieve IP address.")
                        sys.exit(1)
                    self.store.set("host", ip)
            except Exception as e:
                self.logger.error(f"Failed to get IP address: {e}")
                sys.exit(1)

    def _update_latest_firmware_version(self, status="GA"):
        """Set settings['firmwareVersion'] to the latest version string (e.g., '5.1.34')."""
        info = self._fetch_latest_camera_firmware_api(status=status)
        if not info or not info.get("version"):
            self.logger.info("Latest camera firmware: unavailable via API")
            return False
        version = str(info["version"])
        try:
            if self.store._set_nested("firmwareVersion", version, overwrite=True):
                self.store._save()
                self.logger.info("Latest camera firmware: %s", version)
                return True
        except Exception:
            return False
        return False

    def _fetch_latest_camera_firmware_api(self, status="GA", limit=10, timeout=5.0):
        """Return {'version','url','stage'} for latest Protect *Cameras* release, preferring `status` stage."""
        preferred_stage = (status or "GA").upper()
        api_url = "https://community.svc.ui.com/graphql"

        query = (
            "query ReleaseFeedListQuery($tags:[String!],$betas:[String!],$alphas:[String!],"
            "$offset:Int,$limit:Int,$sortBy:ReleasesSortBy,$userIsFollowing:Boolean,$featuredOnly:Boolean,"
            "$searchTerm:String,$filterTags:[String!],$filterEATags:[String!]){"
            "releases(tags:$tags,betas:$betas,alphas:$alphas,offset:$offset,limit:$limit,sortBy:$sortBy,"
            "userIsFollowing:$userIsFollowing,featuredOnly:$featuredOnly,searchTerm:$searchTerm,"
            "filterTags:$filterTags,filterEATags:$filterEATags){pageInfo{offset limit}totalCount "
            "items{id title slug tags stage version createdAt lastActivityAt}}}"
        )

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Accept-Encoding": "identity",  # avoid br/gzip; urllib can't decode br
            "Origin": "https://community.ui.com",
            "Referer": "https://community.ui.com/RELEASES",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) CameraSettings/1.0",
        }

        # progressively loosen filters
        var_candidates = [
            # Tightest: Protect + Cameras + search
            {"limit": int(limit), "offset": 0, "sortBy": "LATEST",
            "tags": ["unifi-protect"], "betas": [], "alphas": [],
            "searchTerm": "camera", "filterTags": ["cameras"]},
            # Drop filterTags
            {"limit": int(limit), "offset": 0, "sortBy": "LATEST",
            "tags": ["unifi-protect"], "betas": [], "alphas": [],
            "searchTerm": "camera"},
            # Only tag
            {"limit": int(limit), "offset": 0, "sortBy": "LATEST",
            "tags": ["unifi-protect"], "betas": [], "alphas": []},
            # No tags, search by title keyword
            {"limit": int(limit), "offset": 0, "sortBy": "LATEST",
            "betas": [], "alphas": [], "searchTerm": "UniFi Protect Cameras"},
            # Absolute fallback: no filters
            {"limit": int(limit), "offset": 0, "sortBy": "LATEST"},
        ]

        def post_one(variables):
            payload = {"query": query, "variables": variables, "operationName": "ReleaseFeedListQuery"}
            req = urllib.request.Request(api_url, data=json.dumps(payload).encode("utf-8"),
                                        method="POST", headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", "ignore")
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                self.logger.warning("Firmware API: non-JSON (head): %r", body[:300])
                return []
            if "errors" in data:
                self.logger.warning("Firmware API: GraphQL errors: %s",
                                    "; ".join(e.get("message", "?") for e in data["errors"]))
                return []
            items = (data.get("data") or {}).get("releases", {}).get("items", []) or []
            if not items:
                self.logger.debug("Firmware API: 0 items for vars=%s (totalCount=%s)",
                                variables, (data.get("data") or {}).get("releases", {}).get("totalCount"))
            return items

        # try candidates until we get anything
        items = []
        for vars_ in var_candidates:
            items = post_one(vars_)
            if items:
                break
        if not items:
            return None

        # Prefer the "UniFi Protect Cameras" family, then prefer stage, then newest by version/createdAt
        def is_cameras(item):
            t = (item.get("title") or "").lower()
            s = (item.get("slug") or "").lower()
            return "unifi protect cameras" in t or "unifi-protect-cameras" in s or "cameras" in t

        cam_items = [it for it in items if is_cameras(it)] or items
        prefer_stage = [it for it in cam_items if (it.get("stage") or "").upper() == preferred_stage] or cam_items

        def parse_semver(v):
            try:
                a, b, c = (v or "0.0.0").split(".")[:3]
                return (int(a), int(b), int(c))
            except Exception:
                return (0, 0, 0)

        picked = max(prefer_stage, key=lambda it: (parse_semver(it.get("version")), it.get("lastActivityAt") or ""))
        version = picked.get("version")
        if not version:
            return None
        slug = picked.get("slug")
        url_page = f"https://community.ui.com/releases/{slug}" if slug else None
        return {"version": version, "url": url_page, "stage": picked.get("stage")}

    def _default_settings(self):
        return {
            "mac": "",                         # MAC address of the device
            "host": "",                        # Hostname or IP address
            "type": "",                        # Device type (e.g., camera, sensor)
            "sysid": "",                       # System hardware identifier
            "platform": "",                    # Platform string (hardware type)
            "marketName": "",                  # Commercial model name
            "firmwareVersion": "",
            "canAdopt": True,
            "logging": {
                "level": "INFO",          # a root/fallback level
                "api": { "level": "DEBUG" },
                "discovery": { "level": "INFO" },
                "uptime": { "level": "INFO" },
                "wss": { "level": "DEBUG" }
            }
        }

    def __getitem__(self, key):
        """
        Thread-safe read access to a (possibly nested) setting.
        
        Usage:
            mac = settings["uplinkDevice.mac"]
        """
        value = self.store.get(key, default=None)
        if value is None and not self.__contains__(key):
            raise KeyError(key)
        return value

    def __setitem__(self, key, value):
        """
        Thread-safe write access to a (possibly nested) setting. Automatically persists to disk.

        Usage:
            settings["uplinkDevice.mac"] = "00:11:22:33:44:55"
        """
        self.store.set(key, value)

    def __contains__(self, key):
        """
        Thread-safe key existence check for nested keys.

        Usage:
            if "uplinkDevice.mac" in settings:
                ...
        """
        return self.store.get(key, default=None) is not None

    def get(self, key, default=None):
        """
        Thread-safe retrieval with fallback for nested keys.

        Usage:
            mac = settings.get("uplinkDevice.mac", "00:00:00:00:00:00")
        """
        return self.store.get(key, default)

    def update(self, updates: dict):
        """
        Thread-safe bulk update (flat keys only).
        Automatically persists to disk.

        Usage:
            settings.update({
                "firmwareVersion": "v5.0.0",
                "isUpdating": False
            })
        """
        self.store.update(updates)

    def mac_bytes(self, key="mac"):
        """
        Returns the MAC address (from key path) as raw bytes.
        Returns None if value is missing or malformed.

        Usage:
            settings.mac_bytes("mac")
            settings.mac_bytes("uplinkDevice.mac")
        """
        mac_str = self.store.get(key)
        if not mac_str:
            raise RuntimeError("MAC address is missing in settings.")
        try:
            return bytes.fromhex(mac_str.replace(":", ""))
        except ValueError:
            raise RuntimeError(f"Malformed MAC address: {mac_str!r}")

    def ip_bytes(self, key="host"):
        """
        Returns the IP address (from key path) as raw bytes.
        Returns None if value is missing or malformed.

        Usage:
            settings.ip_bytes("host")
            settings.ip_bytes("wifiConnectionState.apMgmtIp")
        """
        ip_str = self.store.get(key)
        if not ip_str:
            raise RuntimeError("IP address is missing in settings.")
        try:
            return socket.inet_aton(ip_str)
        except OSError:
            raise RuntimeError(f"Malformed IP address: {ip_str!r}")
