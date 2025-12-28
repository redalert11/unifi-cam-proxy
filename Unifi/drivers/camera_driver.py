from typing import Any
import asyncio, shutil, json
from urllib.parse import urlparse, parse_qs

class CameraDriver:
    def __init__(self, settings, log):
        self.settings = settings
        self.log = log
        self._push_procs = {}  # videoId -> asyncio.subprocess.Process

    async def get_snapshot_jpeg(self, timeout_s: int = 2) -> bytes:
        raise NotImplementedError

    async def apply_isp_settings(self, payload: dict) -> dict:
        return {}

    async def describe(self) -> dict:
        return {"driver": self.__class__.__name__}

    async def apply_video_settings(self, cfg: Any) -> dict:
        # coerce cfg to dict
        if isinstance(cfg, (bytes, bytearray)):
            try: cfg = cfg.decode("utf-8", "ignore")
            except Exception: cfg = "{}"
        if isinstance(cfg, str):
            try: cfg = json.loads(cfg)
            except Exception: cfg = {}
        if not isinstance(cfg, dict):
            cfg = {}

        video = (cfg or {}).get("video") or {}
        applied = {"video": {}}
        if not video:
            return applied

        if not shutil.which("ffmpeg"):
            self.log.error("ffmpeg not found in PATH; cannot push stream")
            return applied

        for vid, vcfg in video.items():
            # >>> IMPORTANT GUARD <<<
            if not isinstance(vcfg, dict):
                self.log.warning("video[%s] is %s; stopping/ignoring", vid, type(vcfg).__name__)
                await self._stop_push(vid)
                continue

            ser = (vcfg.get("avSerializer") or {})
            dests = ser.get("destinations") or []
            if ser.get("type") != "extendedFlv" or not dests:
                await self._stop_push(vid)
                continue

            url = dests[0]
            u = urlparse(url)
            host, port = u.hostname, u.port
            if not host or not port:
                self.log.error("Bad destination for %s: %r", vid, url)
                await self._stop_push(vid)
                continue

            q = parse_qs(u.query)
            encrypted = (q.get("encrypted", ["false"])[0].lower() == "true")
            proto = "tls" if encrypted else "tcp"

            jpg = await self.get_snapshot_jpeg(timeout_s=2)
            still = f"/tmp/still_{vid}.jpg"
            with open(still, "wb") as f:
                f.write(jpg)

            await self._stop_push(vid)
            cmd = [
                "ffmpeg", "-loglevel", "error",
                "-re", "-stream_loop", "-1", "-r", "1",
                "-i", still,
                "-c:v", "libx264", "-tune", "stillimage",
                "-pix_fmt", "yuv420p", "-g", "2", "-keyint_min", "2",
                "-profile:v", "baseline", "-b:v", "800k",
                "-an",
                "-f", "flv", f"{proto}://{host}:{port}",
            ]
            self.log.info("Starting FLV push for %s: %s", vid, " ".join(cmd))
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
            )
            self._push_procs[vid] = proc

            applied["video"][vid] = {
                "status": "started",
                "destination": f"{proto}://{host}:{port}",
                "type": vcfg.get("type", "h264"),
                "avSerializer": ser,
            }

        return applied

    async def _stop_push(self, vid: str):
        proc = self._push_procs.pop(vid, None)
        if not proc:
            return
        self.log.info("Stopping FLV push for %s (pid=%s)", vid, proc.pid)
        try:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2)
            except asyncio.TimeoutError:
                proc.kill()
        except ProcessLookupError:
            pass

    async def build_bootstrap_chunk(self, cfg):
        raise NotImplementedError

    async def bootstrap_followup_chunks(self, cfg):
        return []

    async def start_stream_process(self, cfg):
        raise NotImplementedError

    async def build_avc_sequence_header(self, cfg):
        """
        Drivers can override this to supply a precomputed AVC sequence header (FLV tag).
        Returning None falls back to sniffing the ffmpeg output stream.
        """
        return None
