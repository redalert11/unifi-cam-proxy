import json
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


class SettingsManager:
    """
    Thread-safe JSON settings store with dot-notation access for nested keys.
    """

    def __init__(self, settings_file: Path, defaults: Optional[Dict[str, Any]] = None):
        self.settings_path = Path(settings_file).expanduser().resolve()
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = {}
        self._load(defaults or {})

    # Public API
    def get(self, dotted_key: str, default: Any = None) -> Any:
        with self._lock:
            return self._get_nested(dotted_key, default)

    def set(self, dotted_key: str, value: Any) -> None:
        with self._lock:
            if self._set_nested(dotted_key, value):
                self._save()

    def update(self, updates: Dict[str, Any]) -> None:
        with self._lock:
            changed = False
            for key, value in updates.items():
                changed |= self._set_nested(key, value)
            if changed:
                self._save()

    def delete(self, dotted_key: str) -> None:
        with self._lock:
            if self._delete_nested(dotted_key):
                self._save()

    def all(self) -> Dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._data))  # deep copy via json

    # Internal helpers
    def _load(self, defaults: Dict[str, Any]) -> None:
        if self.settings_path.exists():
            try:
                self._data = json.loads(self.settings_path.read_text(encoding="utf-8"))
            except Exception:
                self._data = {}
        if defaults:
            changed = False
            for k, v in defaults.items():
                changed |= self._set_nested(k, v, overwrite=False)
            if changed:
                self._save()

    def _save(self) -> None:
        self.settings_path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")

    def _get_nested(self, dotted_key: str, default: Any = None) -> Any:
        parts = dotted_key.split(".")
        cur: Any = self._data
        for p in parts:
            if not isinstance(cur, dict) or p not in cur:
                return default
            cur = cur[p]
        return cur

    def _set_nested(self, dotted_key: str, value: Any, overwrite: bool = True) -> bool:
        parts = dotted_key.split(".")
        cur = self._data
        for p in parts[:-1]:
            nxt = cur.get(p)
            if nxt is None or not isinstance(nxt, dict):
                nxt = {}
                cur[p] = nxt
            cur = nxt
        leaf = parts[-1]
        if not overwrite and leaf in cur:
            return False
        if leaf in cur and cur[leaf] == value:
            return False
        cur[leaf] = value
        return True

    def _delete_nested(self, dotted_key: str) -> bool:
        parts = dotted_key.split(".")
        cur = self._data
        stack = []
        for p in parts[:-1]:
            if not isinstance(cur, dict) or p not in cur:
                return False
            stack.append((cur, p))
            cur = cur[p]
        if not isinstance(cur, dict):
            return False
        leaf = parts[-1]
        if leaf not in cur:
            return False
        del cur[leaf]
        # clean empty dicts up the chain
        for parent, key in reversed(stack):
            if isinstance(parent.get(key), dict) and not parent[key]:
                del parent[key]
            else:
                break
        return True


class CameraStore:
    """
    Manages per-camera settings files using SettingsManager.
    """

    def __init__(self, base_dir: Path = Path("data/cameras")):
        self.base_dir = Path(base_dir).expanduser().resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, camera_id: str) -> Path:
        safe_id = camera_id.strip()
        return self.base_dir / f"{safe_id}.json"

    def list_cameras(self) -> Iterable[str]:
        return sorted(p.stem for p in self.base_dir.glob("*.json"))

    def get_manager(self, camera_id: str, defaults: Optional[Dict[str, Any]] = None) -> SettingsManager:
        return SettingsManager(self._path_for(camera_id), defaults or {})

    def delete(self, camera_id: str) -> None:
        path = self._path_for(camera_id)
        if path.exists():
            path.unlink()
