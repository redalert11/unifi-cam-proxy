from __future__ import annotations
from typing import Dict, Any
import asyncio
import os
import subprocess
from pathlib import Path

from Unifi.drivers.camera_driver import CameraDriver


class RtspDriver(CameraDriver):
    """
    Driver that pulls from RTSP camera streams and pushes to UniFi Protect.
    
    Configure in settings.json:
    {
      "camera": {"type": "rtsp"},
      "rtsp": {
        "snapshot_url": "rtsp://admin:password@192.168.1.100:554/stream1",
        "video1": "rtsp://admin:password@192.168.1.100:554/stream1",
        "video2": "rtsp://admin:password@192.168.1.100:554/stream2",
        "video3": "rtsp://admin:password@192.168.1.100:554/stream2"
      }
    }
    
    Use VIDEO_STREAM_METHOD environment variable to choose streaming method:
    - 'pipeline' (default): Use unifi-cam-proxy-kinda style ffmpeg → clock_sync → nc
    - 'direct': Use direct TCP streaming (simpler, but may have timing issues)
    """
    
    def __init__(self, settings, log):
        super().__init__(settings, log)
        # Get RTSP URLs from settings
        self.snapshot_url = settings.get("snapshot_url", "")
        self.video1_url = settings.get("video1", "")
        self.video2_url = settings.get("video2", "")
        self.video3_url = settings.get("video3", "")
        
        # Map video stream IDs to RTSP URLs
        self.stream_urls = {
            "video1": self.video1_url or self.snapshot_url,
            "video2": self.video2_url or self.video1_url or self.snapshot_url,
            "video3": self.video3_url or self.video1_url or self.snapshot_url,
        }
        
        # Check which video streaming method to use
        self.video_stream_method = os.getenv("VIDEO_STREAM_METHOD", "pipeline").lower()
        if self.video_stream_method == "pipeline":
            from Unifi.video_stream_service_alt import VideoStreamServiceAlt
            self.video_service = VideoStreamServiceAlt({"rtsp": settings}, log)
            self.log.info("Using pipeline video streaming method (unifi-cam-proxy-kinda style)")
        else:
            self.video_service = None
            self.log.info("Using direct TCP video streaming method")
    
    async def get_snapshot_jpeg(self, *, timeout_s: int = 5) -> bytes:
        """Capture a single JPEG frame from the RTSP stream."""
        url = self.snapshot_url
        if not url:
            raise ValueError("No snapshot_url configured in rtsp settings")
        
        cmd = [
            "ffmpeg",
            "-loglevel", "error",
            "-rtsp_transport", "tcp",
            "-i", url,
            "-vframes", "1",
            "-f", "image2",
            "-"
        ]
        
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout_s
            )
            if proc.returncode != 0:
                raise RuntimeError(f"ffmpeg failed: {stderr.decode()}")
            return stdout
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise TimeoutError(f"Snapshot timeout after {timeout_s}s")
    
    async def apply_video_settings(self, cfg: Any) -> dict:
        """Override to stream from RTSP instead of looping a still image."""
        import json
        from urllib.parse import urlparse, parse_qs
        
        # Coerce cfg to dict
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
        
        for vid, vcfg in video.items():
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
                self.log.debug("Bad destination for %s: %r - stopping stream", vid, url)
                await self._stop_push(vid)
                continue
            
            q = parse_qs(u.query)
            encrypted = (q.get("encrypted", ["false"])[0].lower() == "true")
            proto = "tls" if encrypted else "tcp"
            
            # Get RTSP source for this stream
            rtsp_url = self.stream_urls.get(vid)
            if not rtsp_url:
                self.log.error("No RTSP URL configured for %s", vid)
                await self._stop_push(vid)
                continue
            
            # Use alternative pipeline method if configured
            if self.video_service:
                # Stop any existing stream
                self.video_service.stop_video_stream(vid)
                
                # Generate stream name (from avSerializer parameters)
                ser_params = ser.get("parameters", {})
                stream_name = ser_params.get("streamName", f"{vid}_{host}_{port}")
                
                # Start the pipeline stream
                await self.video_service.start_video_stream(vid, stream_name, (host, port))
                
                applied["video"][vid] = {
                    "status": "started",
                    "source": rtsp_url,
                    "destination": f"{proto}://{host}:{port}",
                    "method": "pipeline",
                    "type": vcfg.get("type", "h264"),
                    "avSerializer": ser,
                }
                continue
            
            # Otherwise use direct TCP streaming method
            await self._stop_push(vid)
            
            # Get custom ffmpeg args from environment
            # Default matches unifi-cam-proxy-kinda: copy video, normalize audio
            import shlex
            ffmpeg_args_str = os.getenv("FFMPEG_ARGS", "-c:v copy -ar 32000 -ac 1 -codec:a aac -b:a 32k")
            
            # Parse the FFMPEG_ARGS string into individual arguments
            try:
                ffmpeg_args = shlex.split(ffmpeg_args_str)
            except ValueError:
                self.log.warning("Failed to parse FFMPEG_ARGS with shlex, using simple split")
                ffmpeg_args = ffmpeg_args_str.split()
            
            # Build ffmpeg command with proven configuration from unifi-cam-proxy-kinda
            cmd = [
                "ffmpeg",
                "-loglevel", "error",
                # Base args for timestamp handling and robustness
                "-avoid_negative_ts", "make_zero",
                "-fflags", "+genpts+discardcorrupt",
                "-use_wallclock_as_timestamps", "1",
                "-stimeout", "15000000",  # 15 second timeout
                # Input configuration
                "-rtsp_transport", "tcp",
                "-i", rtsp_url,
            ]
            
            # Add the parsed codec/audio arguments
            cmd.extend(ffmpeg_args)
            
            # Add output parameters with stream metadata
            cmd.extend([
                "-metadata", f"streamName={vid}_{host}_{port}",
                "-f", "flv",
                f"{proto}://{host}:{port}",
            ])
            
            self.log.info("Starting RTSP->FLV stream for %s: %s -> %s://%s:%s", 
                         vid, rtsp_url, proto, host, port)
            
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )
            self._push_procs[vid] = proc
            
            applied["video"][vid] = {
                "status": "started",
                "source": rtsp_url,
                "destination": f"{proto}://{host}:{port}",
                "method": "direct",
                "type": vcfg.get("type", "h264"),
                "avSerializer": ser,
            }
        
        return applied
