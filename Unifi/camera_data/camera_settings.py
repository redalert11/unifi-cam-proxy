import json
import os
import sys
import socket
import threading
import logging
import urllib.request
import urllib.error

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from .camera_models import CameraModelDatabase

class CameraSettings:
    def __init__(self, settings_file=None, logger=None):
        self.logger = logger or logging.getLogger(__name__)
        self.logger.setLevel(logging.NOTSET)
        self._lock = threading.RLock()
        self.settings_file = settings_file or os.path.join(os.path.dirname(__file__), "settings.json")
        self.settings = {}
        self._dirty = False

        self._load_or_initialize()
        self._get_ip_address()
        self._get_mac_address()
        self._ensure_platform_and_sysid()
        status = os.environ.get("FIRMWARE_STATUS", "GA")  # GA | RC | EA | ALL
        self._update_latest_firmware_version(status=status)
        if self._dirty:
            self._save()

    def _load_or_initialize(self):
        if os.path.exists(self.settings_file):
            with open(self.settings_file, "r") as f:
                self.settings = json.load(f)
            self.logger.info("Loaded existing settings from %s", self.settings_file)
        else:
            self.logger.info("Creating default settings...")
            self.settings = self._default_settings()
            self._save()

    def _ensure_platform_and_sysid(self):
        changed = False
        if not self.settings.get("marketName"):
            model = os.environ.get("CAMERA_MODEL", "").strip()
            if not model:
                self.logger.error("CAMERA_MODEL environment variable is required to set type or platform.")
                sys.exit(1)
            changed |= self._set_nested_value("marketName", model)

        if not self.settings.get("platform"):
            platform = CameraModelDatabase.get_platform(self.settings["marketName"])
            if not platform:
                self.logger.error(f"Unknown platform for type: {self.settings['marketName']}")
                sys.exit(1)
            changed |= self._set_nested_value("platform", platform)

        if not self.settings.get("sysid"):
            sysid = CameraModelDatabase.CameraSysIds.get(self.settings["marketName"])
            if sysid is None:
                self.logger.error(f"Unknown system ID for type: {self.settings['marketName']}")
                sys.exit(1)
            changed |= self._set_nested_value("sysid", sysid)

        if not self.settings.get("type"):
            changed |= self._set_nested_value("type", self.settings["marketName"].replace("_", " "))

        self._dirty |= changed

    def _get_mac_address(self, interface="eth0"):
        if not self.settings.get("mac"):
            try:
                with open(f"/sys/class/net/{interface}/address") as f:
                    mac = f.read().strip()
                    if not mac:
                        self.logger.error(f"Empty MAC address for interface '{interface}'.")
                        sys.exit(1)
                    self._set_nested_value("mac", mac)
            except FileNotFoundError:
                self.logger.error(f"Network interface '{interface}' not found.")
                sys.exit(1)

    def _get_ip_address(self):
        if not self.settings.get("host"):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    s.connect(("8.8.8.8", 80))
                    ip = s.getsockname()[0]
                    if not ip:
                        self.logger.error("Failed to retrieve IP address.")
                        sys.exit(1)
                    self._set_nested_value("host", ip)
            except Exception as e:
                self.logger.error(f"Failed to get IP address: {e}")
                sys.exit(1)

    def _update_latest_firmware_version(self, status="GA"):
        """Set settings['firmwareVersion'] to the latest version string (e.g., '5.1.34')."""
        # Only fetch and update if firmwareVersion is not already set
        existing_version = self.settings.get("firmwareVersion")
        if existing_version:
            self.logger.info("Using existing firmware version: %s (skipping API fetch)", existing_version)
            return False
        
        try:
            info = self._fetch_latest_camera_firmware_api(status=status)
            if not info or not info.get("version"):
                self.logger.info("Latest camera firmware: unavailable via API")
                return False
            version = str(info["version"])
            if self._set_nested_value("firmwareVersion", version, overwrite_non_dict=True):
                self.logger.info("Latest camera firmware: %s", version)
                return True
            return False
        except Exception as e:
            self.logger.warning(f"Failed to fetch latest firmware version: {e}")
            self.logger.info("Continuing with existing or default firmware version")
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
        with self._lock:
            value = self._get_nested_value(key, default=None)
            if value is None and not self.__contains__(key):
                raise KeyError(key)
            return value

    def __setitem__(self, key, value):
        """
        Thread-safe write access to a (possibly nested) setting. Automatically persists to disk.

        Usage:
            settings["uplinkDevice.mac"] = "00:11:22:33:44:55"
        """
        with self._lock:
            if self._set_nested_value(key, value):
                self._save()

    def __contains__(self, key):
        """
        Thread-safe key existence check for nested keys.

        Usage:
            if "uplinkDevice.mac" in settings:
                ...
        """
        with self._lock:
            return self._get_nested_value(key, default=None) is not None

    def get(self, key, default=None):
        """
        Thread-safe retrieval with fallback for nested keys.

        Usage:
            mac = settings.get("uplinkDevice.mac", "00:00:00:00:00:00")
        """
        with self._lock:
            return self._get_nested_value(key, default)

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
        with self._lock:
            changed = False
            for k, v in updates.items():
                changed |= self._set_nested_value(k, v)
            if changed:
                self._save()

    def _get_nested_value(self, dotted_key, default=None):
        """Internal helper to retrieve nested values using dot-notation."""
        keys = dotted_key.split(".")
        value = self.settings
        for key in keys:
            if not isinstance(value, dict) or key not in value:
                return default
            value = value[key]
        return value

    def _save(self):
        with self._lock:
            with open(self.settings_file, "w") as f:
                json.dump(self.settings, f, indent=4)
            self._dirty = False

    def _set_nested_value(self, dotted_key, value, overwrite_non_dict=False) -> bool:
        """Set nested value using dot-notation. Return True if it changed."""
        keys = dotted_key.split(".")
        d = self.settings
        for key in keys[:-1]:
            cur = d.get(key)
            if cur is None:
                cur = {}
                d[key] = cur
            elif not isinstance(cur, dict):
                if not overwrite_non_dict:
                    raise TypeError(f"Cannot descend into non-dict at '{key}' for '{dotted_key}'")
                cur = {}
                d[key] = cur
            d = cur
        last = keys[-1]
        if last in d and d[last] == value:
            return False
        d[last] = value
        self._dirty = True
        return True

    def mac_bytes(self, key="mac"):
        """
        Returns the MAC address (from key path) as raw bytes.
        Returns None if value is missing or malformed.

        Usage:
            settings.mac_bytes("mac")
            settings.mac_bytes("uplinkDevice.mac")
        """
        with self._lock:
            mac_str = self._get_nested_value(key)
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
        with self._lock:
            ip_str = self._get_nested_value(key)
        if not ip_str:
            raise RuntimeError("IP address is missing in settings.")
        try:
            return socket.inet_aton(ip_str)
        except OSError:
            raise RuntimeError(f"Malformed IP address: {ip_str!r}")