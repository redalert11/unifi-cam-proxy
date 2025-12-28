"""
Alternative video streaming service using the unifi-cam-proxy-kinda pipeline approach.
This uses: ffmpeg → clock_sync.py → netcat pipeline
"""
import asyncio
import logging
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import urlparse, parse_qs


class VideoStreamServiceAlt:
    """
    Alternative video streaming using the proven unifi-cam-proxy-kinda approach.
    Uses a pipeline: ffmpeg → clock_sync → nc
    """
    
    def __init__(self, settings: dict, log: logging.Logger):
        self.settings = settings
        self.log = log
        self._ffmpeg_handles: Dict[str, subprocess.Popen] = {}
        
        # Get RTSP URLs from settings
        rtsp_config = settings.get("rtsp", {})
        self.stream_sources = {
            "video1": rtsp_config.get("video1", ""),
            "video2": rtsp_config.get("video2", ""),
            "video3": rtsp_config.get("video3", ""),
        }
        
        # FFmpeg configuration (from unifi-cam-proxy-kinda)
        self.ffmpeg_args = os.getenv("FFMPEG_ARGS", "-c:v copy -c:a copy")
        self.loglevel = "error"
        self.rtsp_transport = "tcp"
        self.timestamp_modifier = 90
        
    def get_base_ffmpeg_args(self) -> str:
        """Get base ffmpeg arguments for robustness (from unifi-cam-proxy-kinda)"""
        base_args = [
            "-avoid_negative_ts", "make_zero",
            "-fflags", "+genpts+discardcorrupt",
            "-use_wallclock_as_timestamps", "1",
            "-timeout", "15000000",  # Note: timeout, not stimeout
        ]
        return " ".join(base_args)
    
    async def start_video_stream(self, stream_index: str, stream_name: str, destination: tuple[str, int]):
        """
        Start video stream using the unifi-cam-proxy-kinda pipeline approach.
        
        Args:
            stream_index: "video1", "video2", or "video3"
            stream_name: Stream name for metadata
            destination: (host, port) tuple for streaming destination
        """
        has_spawned = stream_index in self._ffmpeg_handles
        is_dead = has_spawned and self._ffmpeg_handles[stream_index].poll() is not None
        
        if not has_spawned or is_dead:
            source = self.stream_sources.get(stream_index, "")
            if not source:
                self.log.error(f"No RTSP source configured for {stream_index}")
                return
            
            # Build the pipeline command (matching unifi-cam-proxy-kinda exactly)
            clock_sync_script = Path(__file__).parent / "clock_sync.py"
            
            # Use full path to nc (netcat-openbsd provides /bin/nc)
            cmd = (
                f"ffmpeg -nostdin -loglevel level+{self.loglevel} -y"
                f" {self.get_base_ffmpeg_args()} -rtsp_transport"
                f' {self.rtsp_transport} -i "{source}"'
                f" {self.ffmpeg_args} -metadata"
                f" streamName={stream_name} -f flv -"
                f" | {sys.executable} {clock_sync_script} --timestamp-modifier {self.timestamp_modifier}"
                f" | /bin/nc {destination[0]} {destination[1]}"
            )
            
            if is_dead:
                exit_code = self._ffmpeg_handles[stream_index].poll()
                self.log.warning(f"Previous ffmpeg process for {stream_index} died with exit code {exit_code}.")
            
            self.log.info(f"Spawning ffmpeg pipeline for {stream_index} ({stream_name}): {cmd}")
            
            # Start process in a new process group
            try:
                self._ffmpeg_handles[stream_index] = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    shell=True,
                    preexec_fn=os.setsid  # Create new process group
                )
            except Exception as e:
                self.log.error(f"Failed to start ffmpeg pipeline for {stream_index}: {e}")
    
    def stop_video_stream(self, stream_index: str):
        """Stop a video stream by killing the process group"""
        if stream_index in self._ffmpeg_handles:
            self.log.info(f"Stopping stream {stream_index}")
            proc = self._ffmpeg_handles[stream_index]
            
            # Check if process is already dead
            if proc.poll() is not None:
                self.log.debug(f"Process for {stream_index} already terminated with code {proc.poll()}")
                del self._ffmpeg_handles[stream_index]
                return
            
            try:
                # Terminate the process group to kill all processes in the pipeline
                pgid = os.getpgid(proc.pid)
                self.log.debug(f"Sending SIGTERM to process group {pgid} for {stream_index}")
                os.killpg(pgid, signal.SIGTERM)
                
                # Wait for graceful shutdown
                try:
                    proc.wait(timeout=2)
                    self.log.debug(f"Stream {stream_index} terminated gracefully")
                except subprocess.TimeoutExpired:
                    self.log.warning(f"Stream {stream_index} did not terminate gracefully, sending SIGKILL")
                    try:
                        os.killpg(pgid, signal.SIGKILL)
                        proc.wait(timeout=1)
                    except (ProcessLookupError, subprocess.TimeoutExpired):
                        pass
                        
            except (ProcessLookupError, PermissionError, AttributeError, OSError) as e:
                self.log.debug(f"Error stopping {stream_index}: {e}, trying proc.kill()")
                # Fall back to killing just the parent process
                try:
                    proc.kill()
                    proc.wait(timeout=1)
                except Exception:
                    pass
            
            finally:
                if stream_index in self._ffmpeg_handles:
                    del self._ffmpeg_handles[stream_index]
    
    def stop_all_streams(self):
        """Stop all active video streams"""
        for stream_index in list(self._ffmpeg_handles.keys()):
            self.stop_video_stream(stream_index)
