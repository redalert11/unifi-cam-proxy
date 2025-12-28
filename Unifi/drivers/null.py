from io import BytesIO
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont
import asyncio, json, shutil, ssl
from urllib.parse import urlparse, parse_qs
from Unifi.drivers.camera_driver import CameraDriver


UPFLV_PREFIX = bytes.fromhex("de1916154717de19167550")

def _coerce_cfg(cfg):
    if isinstance(cfg, (bytes, bytearray)):
        try:
            cfg = cfg.decode("utf-8", "ignore")
        except Exception:
            return {}
    if isinstance(cfg, str):
        try:
            return json.loads(cfg)
        except Exception:
            return {}
    return cfg if isinstance(cfg, dict) else {}

class NullDriver(CameraDriver):
    def __init__(self, settings, log):
        super().__init__(settings, log)
        # keep our own map of videoId -> ffmpeg process
        self._push_sessions = {}
        self._stats_task = None

    async def get_snapshot_jpeg(self, timeout_s: int = 3) -> bytes:
        w, h = 1280, 720
        img = Image.new("RGB", (w, h), (32, 32, 32))
        d = ImageDraw.Draw(img)

        # Color bars (top half)
        bars = [(255,255,255),(255,255,0),(0,255,255),(0,255,0),(255,0,255),(255,0,0),(0,0,255)]
        bw = w // len(bars)
        for i, c in enumerate(bars):
            d.rectangle([i*bw, 0, (i+1)*bw, h//2], fill=c)

        # Grid (bottom half)
        for x in range(0, w, 80):
            d.line([(x, h//2), (x, h)], fill=(60,60,60))
        for y in range(h//2, h, 80):
            d.line([(0, y), (w, y)], fill=(60,60,60))

        # Label with timestamp
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        name = (self.settings.get("name") or "NullCam")
        text = f"{name}  {ts}  {w}x{h}"
        font = ImageFont.load_default()
        bbox = d.textbbox((0, 0), text, font=font)
        pad = 10
        box = (10, h//2 + 10, 10 + (bbox[2]-bbox[0]) + 2*pad, h//2 + 10 + (bbox[3]-bbox[1]) + 2*pad)
        d.rectangle(box, fill=(0,0,0))
        d.text((box[0]+pad, box[1]+pad), text, fill=(255,255,255), font=font)

        buf = BytesIO()
        img.save(buf, format="JPEG", quality=80, optimize=True)
        return buf.getvalue()

    async def apply_video_settings(self, cfg):
        # Be tolerant of controllers that send JSON strings
        cfg = _coerce_cfg(cfg)
        persisted_vs = self.settings.get("videoSettings", {}) or {}
        if not cfg:
            raw = persisted_vs.get("raw")
            if isinstance(raw, dict):
                cfg = raw
        video = (cfg or {}).get("video") or (persisted_vs.get("video") if isinstance(persisted_vs, dict) else {}) or {}

        # Controller also reads top-level videoMode/hdrMode/downScaleMode
        top_video_mode = cfg.get("videoMode") or (persisted_vs.get("videoMode") if isinstance(persisted_vs, dict) else None) or "default"
        top_hdr_mode   = cfg.get("hdrMode") or (persisted_vs.get("hdrMode") if isinstance(persisted_vs, dict) else None) or "off"
        top_downscale  = cfg.get("downScaleMode") or (persisted_vs.get("downScaleMode") if isinstance(persisted_vs, dict) else None) or "original"

        applied = {
            "video": {},
            "videoMode": top_video_mode,
            "hdrMode": top_hdr_mode,
            "downScaleMode": top_downscale,
        }

        # Even if video is empty, return the top-level fields
        if not video:
            return applied

        for vid, vcfg in video.items():
            vcfg = vcfg if isinstance(vcfg, dict) else {}

            # Per-video entry with fields the controller reads
            entry = {
                "videoMode":     vcfg.get("videoMode",     top_video_mode),
                "hdrMode":       vcfg.get("hdrMode",       top_hdr_mode),
                "downScaleMode": vcfg.get("downScaleMode", top_downscale),
                "type":          vcfg.get("type", "h264"),
            }

            ser   = (vcfg.get("avSerializer") or {})
            dests = (ser.get("destinations") or [])
            can_push = ser.get("type") == "extendedFlv" and dests
            reason = None

            host = port = proto = None  # keep names in scope for clarity

            if can_push:
                url = dests[0]
                u = urlparse(url)
                host, port = u.hostname, u.port
                if not host or not port:
                    can_push = False
                    reason = "bad destination (missing host/port)"
                else:
                    q = parse_qs(u.query)
                    encrypted = (q.get("encrypted", ["false"])[0].lower() == "true")
                    proto = "tls" if encrypted else "tcp"
                    dest_suffix = ""
                    if u.path:
                        dest_suffix += u.path
                    if u.query:
                        dest_suffix += f"?{u.query}"
                    dest_display = f"{proto}://{host}:{port}{dest_suffix}"

            # Only try to push if we have a valid destination AND ffmpeg
            if can_push and shutil.which("ffmpeg"):
                # Write a fresh snapshot to disk
                jpg = await self.get_snapshot_jpeg(timeout_s=2)
                still = f"/tmp/still_{vid}.jpg"
                with open(still, "wb") as f:
                    f.write(jpg)

                ok = await self._start_push(vid, still, host, port, encrypted, dest_display)
                if ok:
                    entry.update({
                        "status": "started",
                        "destination": dest_display,
                        "avSerializer": ser,
                    })
                else:
                    entry.update({
                        "status": "stopped",
                        "reason": "failed to start FLV session",
                    })
            else:
                # Stop any running pusher and still return a valid shape
                await self._stop_push(vid)
                if reason is None:
                    reason = "ffmpeg not found" if can_push else "no valid extendedFlv destination"
                entry.update({
                    "status": "stopped",
                    "reason": reason,
                })

            # ⬅️ make sure this is INSIDE the loop
            applied["video"][vid] = entry

        if self._push_sessions and not self._stats_task:
            self._stats_task = asyncio.create_task(self._log_stats_loop())
        elif not self._push_sessions and self._stats_task:
            self._stats_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._stats_task
            self._stats_task = None

        return applied

    async def _start_push(self, vid: str, still_path: str, host: str, port: int, encrypted: bool, dest_display: str) -> bool:
        await self._stop_push(vid)
        ssl_ctx = None
        if encrypted:
            ssl_ctx = ssl.create_default_context()
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE

        try:
            reader, writer = await asyncio.open_connection(host, port, ssl=ssl_ctx)
        except Exception as exc:
            self.log.error("Failed to connect to %s: %s", dest_display, exc)
            return False

        try:
            writer.write(UPFLV_PREFIX)
            await writer.drain()
        except Exception as exc:
            self.log.error("Failed to send uPFLV prefix for %s: %s", dest_display, exc)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return False

        cmd = [
            "ffmpeg", "-loglevel", "error",
            "-re", "-stream_loop", "-1", "-r", "1",
            "-i", still_path,
            "-c:v", "libx264", "-tune", "stillimage",
            "-pix_fmt", "yuv420p", "-g", "2", "-keyint_min", "2",
            "-profile:v", "baseline", "-b:v", "800k",
            "-an",
            "-f", "flv", "-",
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except Exception as exc:
            self.log.error("Failed to start ffmpeg for %s: %s", dest_display, exc)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return False

        forward_task = asyncio.create_task(self._forward_flv(proc, writer, vid, dest_display))
        self._push_sessions[vid] = {"proc": proc, "task": forward_task, "writer": writer}
        self.log.info("Streaming FLV for %s -> %s (pid=%s)", vid, dest_display, proc.pid)
        return True

    async def _forward_flv(self, proc: asyncio.subprocess.Process, writer, vid: str, dest_display: str):
        try:
            while True:
                chunk = await proc.stdout.read(65536)
                if not chunk:
                    break
                writer.write(chunk)
                await writer.drain()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.log.error("FLV forward error for %s -> %s: %s", vid, dest_display, exc)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2)
                except asyncio.TimeoutError:
                    proc.kill()

    async def _log_stats_loop(self):
        try:
            while True:
                await asyncio.sleep(5)
                entries = []
                for vid, sess in self._push_sessions.items():
                    meta = sess.get("meta", {})
                    dest = meta.get("destination", "?")
                    fps = meta.get("fps", 1)
                    width = meta.get("width", 1280)
                    height = meta.get("height", 720)
                    entries.append(f"{vid}@{dest} {width}x{height} {fps}fps")
                if entries:
                    self.log.info("NullDriver streams: %s", "; ".join(entries))
        except asyncio.CancelledError:
            pass

    async def _stop_push(self, vid: str):
        session = self._push_sessions.pop(vid, None)
        if session:
            proc = session.get("proc")
            task = session.get("task")
            writer = session.get("writer")
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            if writer:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
            if proc and proc.returncode is None:
                self.log.info("Stopping FLV push for %s (pid=%s)", vid, proc.pid)
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2)
                except asyncio.TimeoutError:
                    proc.kill()
        await super()._stop_push(vid)
