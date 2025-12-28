from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import ssl
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from Unifi.drivers.camera_driver import CameraDriver

_CONTROL_PAYLOAD = bytes.fromhex(
    "00020003000101000100000000000cffffffff0000000000ff0000"
)
_CONTROL_HEAD_LEN = 11  # controller usually sends first 11 bytes, but can arrive separately
_CONTROL_TAIL_LEN = len(_CONTROL_PAYLOAD) - _CONTROL_HEAD_LEN
_TELEMETRY_INTERVAL_S = 1.0
_TCP_PAYLOAD_LIMIT = 1460


@dataclass
class StreamConfig:
    video_id: str
    host: str
    port: int
    encrypted: bool
    dest_display: str
    stream_name: str
    channel_id: int
    width: int
    height: int
    fps: int
    bitrate: int
    audio_cfg: Dict[str, Any]
    clip_path: Optional[Path]
    video_profile: Dict[str, Any]
    source_hint: str = "generated"


class CameraStreamWorker:
    def __init__(self, driver: CameraDriver, cfg: StreamConfig, log: logging.Logger):
        self.driver = driver
        self.cfg = cfg
        self.log = log
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._forward_task: Optional[asyncio.Task] = None
        self._control_task: Optional[asyncio.Task] = None
        self._write_lock = asyncio.Lock()
        self._stopped = asyncio.Event()
        self._telemetry_sent = False
        self._telemetry_fallback: Optional[asyncio.Task] = None
        self._telemetry_heartbeat: Optional[asyncio.Task] = None
        profile = cfg.video_profile or {}
        default_delay_ms = 5.0
        delay_ms: float = default_delay_ms
        try:
            if profile.get("chunk_delay_ms") is not None:
                delay_ms = float(profile.get("chunk_delay_ms"))
        except (TypeError, ValueError):
            delay_ms = default_delay_ms
        self._chunk_delay = max(0.0, delay_ms / 1000.0)

    async def start(self) -> bool:
        ssl_ctx = None
        if self.cfg.encrypted:
            ssl_ctx = ssl.create_default_context()
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE
        try:
            reader, writer = await asyncio.open_connection(
                self.cfg.host,
                self.cfg.port,
                ssl=ssl_ctx,
            )
        except Exception as exc:
            self.log.error("Failed to connect to %s: %s", self.cfg.dest_display, exc)
            return False

        self._reader = reader
        self._writer = writer
        try:
            raw_sock = writer.get_extra_info("socket")
            if raw_sock:
                raw_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                try:
                    raw_sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
                except OSError:
                    pass
        except Exception as exc:
            self.log.debug("Failed to tweak socket options for %s: %s", self.cfg.dest_display, exc)
        await self._consume_play_request()
        if not self._control_task:
            self._control_task = asyncio.create_task(self._control_loop())
        try:
            bootstrap = await self.driver.build_bootstrap_chunk(self.cfg)
        except Exception as exc:
            self.log.error("Failed to build bootstrap for %s: %s", self.cfg.dest_display, exc)
            writer.close()
            await writer.wait_closed()
            return False

        stream_task = asyncio.create_task(self.driver.start_stream_process(self.cfg))

        try:
            await self._write(bootstrap)
            try:
                followups = await self.driver.bootstrap_followup_chunks(self.cfg)
            except AttributeError:
                followups = []
            for extra_chunk in followups or []:
                await self._write(extra_chunk)
        except Exception as exc:
            stream_task.cancel()
            with contextlib.suppress(Exception):
                await stream_task
            self.log.error("Failed to send bootstrap for %s: %s", self.cfg.dest_display, exc)
            await self.stop()
            return False
        self._start_telemetry_fallback()

        try:
            proc = await stream_task
        except Exception as exc:
            self.log.error("Failed to start stream process for %s: %s", self.cfg.dest_display, exc)
            await self.stop()
            return False

        if not proc:
            self.log.error("Stream process missing for %s", self.cfg.dest_display)
            await self.stop()
            return False

        self._proc = proc

        await self._send_avc_sequence_header()

        if proc and proc.stdout:
            self._forward_task = asyncio.create_task(self._forward_loop())
        else:
            self._forward_task = None
        pid_display = proc.pid if proc else "n/a"
        self.log.info(
            "Streaming FLV for %s -> %s (pid=%s)",
            self.cfg.video_id,
            self.cfg.dest_display,
            pid_display,
        )
        return True

    async def stop(self, current_task: Optional[asyncio.Task] = None) -> None:
        if self._stopped.is_set():
            return
        self._stopped.set()
        current = asyncio.current_task()
        tasks = []
        if self._forward_task and self._forward_task is not current:
            self._forward_task.cancel()
            tasks.append(self._forward_task)
        if self._control_task and self._control_task is not current_task:
            self._control_task.cancel()
            tasks.append(self._control_task)
        self._stop_telemetry_heartbeat()
        self._cancel_telemetry_fallback()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._forward_task = None
        self._control_task = None

        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=.05)
            except asyncio.TimeoutError:
                self._proc.kill()
        self._proc = None

        self._reader = None
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
        self._writer = None

    async def _forward_loop(self) -> None:
        if not self._proc or not self._proc.stdout:
            return
        try:
            while not self._stopped.is_set():
                chunk = await self._proc.stdout.read(65536)
                if not chunk:
                    break
                await self._write_chunked(chunk)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.log.error("FLV forward error for %s: %s", self.cfg.dest_display, exc)
        finally:
            if not self._stopped.is_set():
                self.log.info("Stream ended for %s", self.cfg.dest_display)
                await self.stop()

    async def _control_loop(self) -> None:
        reader = self._reader
        if not reader:
            return
        expected = _CONTROL_PAYLOAD
        head_fragment: Optional[bytes] = None
        tail_fragment: Optional[bytes] = None
        try:
            while not self._stopped.is_set():
                chunk = await reader.read(4096)
                if not chunk:
                    break
                mv = memoryview(chunk)
                while mv:
                    frame = None
                    if len(mv) >= len(expected) and mv[: len(expected)] == expected:
                        frame = bytes(mv[: len(expected)])
                        mv = mv[len(expected) :]
                        head_fragment = None
                        tail_fragment = None
                    elif len(mv) >= _CONTROL_HEAD_LEN and mv[: _CONTROL_HEAD_LEN] == expected[:_CONTROL_HEAD_LEN]:
                        head_fragment = bytes(mv[: _CONTROL_HEAD_LEN])
                        mv = mv[_CONTROL_HEAD_LEN :]
                        self.log.info(
                            "Controller partial payload head (%d bytes): %s",
                            _CONTROL_HEAD_LEN,
                            head_fragment.hex(),
                        )
                        frame = None
                    elif len(mv) >= _CONTROL_TAIL_LEN and mv[: _CONTROL_TAIL_LEN] == expected[_CONTROL_HEAD_LEN :]:
                        tail_fragment = bytes(mv[: _CONTROL_TAIL_LEN])
                        mv = mv[_CONTROL_TAIL_LEN :]
                        self.log.info(
                            "Controller partial payload tail (%d bytes): %s",
                            _CONTROL_TAIL_LEN,
                            tail_fragment.hex(),
                        )
                        frame = None
                    else:
                        blob = bytes(mv)
                        mv = mv[len(mv) :]
                        frame = None
                        if blob:
                            self.log.info(
                                "Controller payload (%d bytes): %s",
                                len(blob),
                                blob.hex(),
                            )
                            head_fragment = None
                            tail_fragment = None

                    if frame is None and head_fragment and tail_fragment:
                        frame = expected
                        head_fragment = None
                        tail_fragment = None

                    if frame:
                        if not await self._handle_controller_payload(frame):
                            self.log.info(
                                "Controller payload (%d bytes): %s",
                                len(frame),
                                frame.hex(),
                            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.log.debug("Control loop ended for %s: %s", self.cfg.dest_display, exc)
        finally:
            if not self._stopped.is_set():
                await self.stop(current_task=asyncio.current_task())

    async def _write(self, chunk: bytes) -> None:
        if not self._writer:
            raise RuntimeError("writer not initialized")
        async with self._write_lock:
            self._writer.write(chunk)
            await self._writer.drain()

    async def _write_chunked(self, chunk: bytes) -> None:
        view = memoryview(chunk)
        limit = _TCP_PAYLOAD_LIMIT
        for offset in range(0, len(view), limit):
            await self._write(view[offset : offset + limit].tobytes())
            if self._chunk_delay:
                await asyncio.sleep(self._chunk_delay)

    async def _send_initial_telemetry(self) -> None:
        if self._telemetry_sent:
            return
        try:
            self.log.debug("Sending telemetry sequence for %s", self.cfg.dest_display)
            await self.driver.send_telemetry_sequence(self._write)
            self._telemetry_sent = True
            self._cancel_telemetry_fallback()
            self._start_telemetry_heartbeat()
        except Exception as exc:
            self.log.error("Failed to send telemetry sequence for %s: %s", self.cfg.dest_display, exc)

    async def _send_avc_sequence_header(self) -> None:
        if not self._proc or not self._proc.stdout:
            return
        reader = self._proc.stdout
        override_header: Optional[bytes] = None
        try:
            override_header = await self.driver.build_avc_sequence_header(self.cfg)
        except AttributeError:
            override_header = None
        except Exception as exc:
            self.log.error("Driver build_avc_sequence_header failed for %s: %s", self.cfg.dest_display, exc)
            override_header = None

        seq_sent = False
        if override_header is not None:
            self.log.debug("Sending synthetic AVC sequence header for %s", self.cfg.dest_display)
            try:
                await self._write(override_header)
                seq_sent = True
            except Exception as exc:
                self.log.error("Failed to send synthetic AVC header for %s: %s", self.cfg.dest_display, exc)
                seq_sent = False

        try:
            await reader.readexactly(9)
            await reader.readexactly(4)
        except (asyncio.IncompleteReadError, AttributeError) as exc:
            self.log.debug("Unable to read FLV header for %s: %s", self.cfg.dest_display, exc)
            return

        max_tags = 256
        pending_after_seq: list[bytes] = []
        for _ in range(max_tags):
            try:
                tag_header = await reader.readexactly(11)
            except (asyncio.IncompleteReadError, AttributeError) as exc:
                self.log.debug("Failed to read FLV tag header for %s: %s", self.cfg.dest_display, exc)
                return
            data_size = int.from_bytes(tag_header[1:4], "big")
            try:
                data = await reader.readexactly(data_size)
                prev_size = await reader.readexactly(4)
            except (asyncio.IncompleteReadError, AttributeError) as exc:
                self.log.debug("Failed to read FLV tag payload for %s: %s", self.cfg.dest_display, exc)
                return

            tag_bytes = tag_header + data + prev_size
            tag_type = tag_header[0]
            if tag_type == 0x09 and data_size >= 4:
                avc_packet_type = data[1]
                if avc_packet_type == 0x00:
                    if not seq_sent:
                        seq_sent = True
                        avc_tag = tag_bytes
                        payload = override_header if override_header is not None else avc_tag
                        msg = "synthetic" if override_header is not None else "primed"
                        self.log.debug("Sending %s AVC sequence header for %s", msg, self.cfg.dest_display)
                        try:
                            await self._write(payload)
                        except Exception as exc:
                            self.log.error("Failed to forward AVC sequence header for %s: %s", self.cfg.dest_display, exc)
                    continue
                if seq_sent:
                    self.log.debug("Sending primed keyframe chunk for %s", self.cfg.dest_display)
                    try:
                        await self._write(tag_bytes)
                        for buffered in pending_after_seq:
                            await self._write(buffered)
                    except Exception as exc:
                        self.log.error("Failed to forward primed data for %s: %s", self.cfg.dest_display, exc)
                    return
            else:
                if seq_sent:
                    pending_after_seq.append(tag_bytes)
                # Drop pre-sequence metadata/audio (already covered by bootstrap)
        self.log.debug("AVC sequence header/keyframe not found during priming for %s", self.cfg.dest_display)

    async def _handle_controller_payload(self, data: bytes) -> bool:
        if data != _CONTROL_PAYLOAD:
            return False
        self.log.info("Controller control payload (%d bytes)", len(data))
        if not self._telemetry_sent:
            await self._send_initial_telemetry()
        return True

    def _start_telemetry_fallback(self) -> None:
        self._cancel_telemetry_fallback()
        loop = asyncio.get_running_loop()
        self._telemetry_fallback = loop.create_task(self._telemetry_fallback_loop())

    async def _telemetry_fallback_loop(self) -> None:
        try:
            await asyncio.sleep(_TELEMETRY_INTERVAL_S)
            if not self._telemetry_sent and not self._stopped.is_set():
                self.log.debug(
                    "Telemetry fallback firing for %s after %.0fms",
                    self.cfg.dest_display,
                    _TELEMETRY_INTERVAL_S * 1000,
                )
                await self._send_initial_telemetry()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.log.debug("Telemetry fallback failed for %s: %s", self.cfg.dest_display, exc)
        finally:
            if self._telemetry_fallback is asyncio.current_task():
                self._telemetry_fallback = None

    def _cancel_telemetry_fallback(self) -> None:
        task = self._telemetry_fallback
        if task:
            task.cancel()
        self._telemetry_fallback = None

    def _start_telemetry_heartbeat(self) -> None:
        if self._telemetry_heartbeat:
            return
        loop = asyncio.get_running_loop()
        self._telemetry_heartbeat = loop.create_task(self._telemetry_heartbeat_loop())

    async def _telemetry_heartbeat_loop(self) -> None:
        try:
            while not self._stopped.is_set():
                await asyncio.sleep(_TELEMETRY_INTERVAL_S)
                if self._stopped.is_set():
                    break
                self.log.debug("Sending telemetry heartbeat for %s", self.cfg.dest_display)
                await self.driver.send_telemetry_sequence(self._write)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.log.debug("Telemetry heartbeat failed for %s: %s", self.cfg.dest_display, exc)
        finally:
            if self._telemetry_heartbeat is asyncio.current_task():
                self._telemetry_heartbeat = None

    def _stop_telemetry_heartbeat(self) -> None:
        task = self._telemetry_heartbeat
        if task:
            task.cancel()
        self._telemetry_heartbeat = None

    async def _consume_play_request(self) -> None:
        reader = self._reader
        if not reader:
            return
        try:
            length_bytes = await asyncio.wait_for(reader.readexactly(4), timeout=.05)
        except (asyncio.IncompleteReadError, asyncio.TimeoutError):
            return
        length = struct.unpack(">I", length_bytes)[0]
        if not (0 < length <= 65536):
            return
        try:
            payload = await reader.readexactly(length)
        except asyncio.IncompleteReadError:
            return
        try:
            msg = json.loads(payload.decode("utf-8", "ignore"))
            self.log.debug(
                "Controller requested %s (params=%s)",
                msg.get("cmd"),
                msg.get("params"),
            )
        except Exception:
            self.log.debug("Controller play payload not JSON")


class CameraStreamFactory:
    def __init__(self, driver: CameraDriver, log: logging.Logger):
        self.driver = driver
        self.log = log.getChild("factory")
        self.streams: Dict[str, CameraStreamWorker] = {}

    async def start_stream(self, cfg: StreamConfig) -> bool:
        await self.stop_stream(cfg.video_id)
        worker = CameraStreamWorker(self.driver, cfg, self.log)
        ok = await worker.start()
        if ok:
            self.streams[cfg.video_id] = worker
        return ok

    async def stop_stream(self, video_id: str) -> None:
        worker = self.streams.pop(video_id, None)
        if worker:
            await worker.stop()

    async def stop_all(self) -> None:
        await asyncio.gather(*(worker.stop() for worker in list(self.streams.values())), return_exceptions=True)
        self.streams.clear()
