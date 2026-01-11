"""
Flight check targets for stream validation.

Usage (ffprobe -> checker):
  To check camera directly
  ffprobe -v error -show_streams -show_format -print_format json "rtsp://.../stream" | python -m Web.flight_check -

  To check go2rtc output
  ffprobe -v error -show_streams -show_format -print_format json "http://<host>:1984/api/stream.flv?src=<name>" | python -m Web.flight_check -

Usage (ONVIF apply + re-check):
  python -m Web.onvif_probe --host <ip> --port 2020 --user <user> --password <pass> --encoder-config-token main
  python -m Web.onvif_probe --host <ip> --port 2020 --user <user> --password <pass> \
    --set-encoder-config-json '{"token":"main","name":"VideoEncoder_1","encoding":"H264","width":1920,"height":1080,"quality":3,"framerate_limit":15,"bitrate":2500,"gov_length":25,"profile":"Main"}'
  ffprobe -v error -show_streams -show_format -print_format json "rtsp://<user>:<pass>@<ip>:554/stream1"

This class is the single source of truth for thresholds and preferred formats
when validating streams against the Protect/go2rtc expectations.

Checks performed:
  - Transport/container: format name (RTSP vs FLV path)
  - Video: codec, profile, level, pixel format, width, height, fps/level (normalized), color/HDR/orientation, bitrate behavior
  - Audio (mic track): codec, sample rate, channels, presence
  - Speaker/backchannel: presence/config placeholders
"""

from dataclasses import dataclass, field
import json
import argparse
import sys
import subprocess
from typing import Dict, Any, Tuple
from urllib.parse import quote, urlparse
from urllib.request import urlopen
import socket
import random

from Web.onvif_client import OnvifSoapClient
from urllib.error import HTTPError


@dataclass(frozen=True)
class FinalFlightCheck:
    """
    Static “source of truth” settings derived from the observed stream in test-dev/target video format.md.
    Terminology:
      - enforce_*: must be forced to this value (observed)
      - report_*: values we report to the controller but do not force (observed/not verified)
    Comment format: target / observed / controller HELLO (if applicable).
    """

    # Container / transport notes
    enforce_transport: str = "flv-tcp"  # observed Protect raw FLV-over-TCP with UniFi headers; must be forced
    enforce_container: str = "flv"  # observed FLV-over-TCP framing
    container_note: str = "Protect receives FLV framing over TCP; media is H.264."

    #######################################
    # Video/Audio (camera -> controller)  #
    #######################################

    # Enforce video
    enforce_video_codec: str = "h264"  # observed; controller HELLO allows h264/h265/mjpg, but we serve h264
    enforce_profile: str = "main"  # observed
    enforce_level: float = 5.0  # observed
    enforce_pix_fmt: str = "yuvj420p"  # observed
    enforce_bits_per_raw_sample: int = 8  # observed
    enforce_has_b_frames: int = 0  # observed
    enforce_tcp_frame_limit_bytes: int = 5_000_000  # hard limit per TCP frame

    # Report video (informational)
    report_width: int = 0  # not enforced
    report_height: int = 0  # not enforced
    report_framerate_num: int = 24  # observed (r_frame_rate: 24/1)
    report_framerate_den: int = 1  # observed
    report_gop_seconds: float = 0.0  # not verified
    report_bitrate_mbps: float = 3.1  # observed
    report_color_range: str = "pc"  # observed
    report_color_space: str = "bt709"  # observed
    report_color_transfer: str = "bt709"  # observed
    report_color_primaries: str = "bt709"  # observed
    report_sample_aspect_ratio: str = "1:1"  # observed
    report_display_aspect_ratio: str = "16:9"  # observed
    report_pix_fmt_alt: Tuple[str, ...] = ()  # not verified (placeholder for alternates)
    report_hdr: str = "sdr"  # observed (SDR = bt709; HDR e.g., HDR10/PQ bt2020 may need conversion)
    report_orientation: str = "upright"  # observed
    report_bitrate_behavior: str = "vbr"  # observed; enforce per-frame size limit to avoid disconnects
    # Controller HELLO declares: videoCodecs [h264,h265,mjpg], modes [default,highFps,sport,slowShutter] with max fps [24,48,24,20], hdr=True, fullHdSnapshot=True, videoSourceCount=2.

    # Audio (single track = mic)
    audio_has_track: bool = False  # observed: no mic/audio track; HELLO declares mic=True
    enforce_audio_codec: str = "pcm_mulaw"  # target: pcm_mulaw (Protect); observed: none; HELLO also lists aac/opus as supported
    enforce_audio_rate: int = 8000  # target: 8000 Hz (mulaw); observed: none
    enforce_audio_channels: int = 1  # target: mono; observed: none
    enforce_talkback: bool = False  # observed: not available; enable only if camera backchannel exists
    # If audio appears, enforce pcm_mulaw mono 8 kHz; otherwise note absence.

    #######################################
    #        Additional Features          #
    #######################################

    # Speaker / backchannel (egress, controller -> camera)
    speaker_has_track: bool = False  # observed: none; HELLO declares speaker=True/aec=[fullband]; requires ONVIF/backchannel
    speaker_codec: str = ""  # target if backchannel supported (e.g., opus/pcm)
    speaker_rate_hz: int = 0  # target rate if backchannel supported

    #######################################
    #          Flight Checks              #
    #######################################

    @staticmethod
    def _get_streams_by_type(probe: Dict[str, Any], kind: str) -> list:
        """Extract streams of a given codec_type ('video' or 'audio') from ffprobe JSON."""
        return [s for s in probe.get("streams", []) if s.get("codec_type") == kind]

    def check_transport(self, probe: Dict[str, Any]) -> Dict[str, list]:
        matches, errors, report = [], [], []
        fmt = (probe.get("format") or {}).get("format_name", "")
        if fmt:
            report.append(f"format: {fmt}")
        # ffprobe on RTSP reports format_name=rtsp; we enforce FLV framing downstream for Protect ingestion.
        if (
            self.enforce_container
            and fmt
            and fmt.lower() != "rtsp"
            and self.enforce_container.lower() not in fmt.lower()
        ):
            errors.append(f"transport/container: expected {self.enforce_container}, observed {fmt}")
        elif self.enforce_container and fmt:
            matches.append(f"transport/container matches {fmt}")
        return {"matches": matches, "errors": errors, "report": report}

    def check_video(self, probe: Dict[str, Any]) -> Dict[str, list]:
        matches, errors, report = [], [], []
        vids = self._get_streams_by_type(probe, "video")
        if not vids:
            return {"matches": matches, "errors": ["video: no video stream found"], "report": report}
        v = vids[0]
        codec = v.get("codec_name", "").lower()
        profile = (v.get("profile") or "").lower()
        level_raw = v.get("level") or 0
        # ffprobe reports levels like 50 for 5.0; normalize if needed
        level = level_raw / 10.0 if isinstance(level_raw, (int, float)) and level_raw >= 10 else level_raw
        pix_fmt = (v.get("pix_fmt") or "").lower()
        width = v.get("width") or 0
        height = v.get("height") or 0
        if codec != self.enforce_video_codec:
            errors.append(f"video codec: expected {self.enforce_video_codec}, observed {codec}")
        else:
            matches.append(f"video codec matches {codec}")
        if profile and profile != self.enforce_profile:
            errors.append(f"video profile: expected {self.enforce_profile}, observed {profile}")
        elif profile:
            matches.append(f"video profile matches {profile}")
        if level and level > self.enforce_level:
            errors.append(f"video level: expected <= {self.enforce_level}, observed {level}")
        elif level:
            matches.append(f"video level ok {level}")
        if pix_fmt and pix_fmt != self.enforce_pix_fmt:
            errors.append(f"pixel format: expected {self.enforce_pix_fmt}, observed {pix_fmt}")
        elif pix_fmt:
            matches.append(f"pixel format matches {pix_fmt}")
        if self.report_width and width:
            if width != self.report_width:
                matches.append(f"width observed {width} (target {self.report_width})")
            else:
                matches.append(f"width matches {width}")
        if self.report_height and height:
            if height != self.report_height:
                matches.append(f"height observed {height} (target {self.report_height})")
            else:
                matches.append(f"height matches {height}")
        return {"matches": matches, "errors": errors, "report": report}

    def check_audio(self, probe: Dict[str, Any]) -> Dict[str, list]:
        matches, errors, report = [], [], []
        auds = self._get_streams_by_type(probe, "audio")
        if not auds:
            if self.audio_has_track:
                errors.append("audio: expected track present, none observed")
            else:
                report.append("audio: no track observed")
            return {"matches": matches, "errors": errors, "report": report}
        a = auds[0]
        codec = a.get("codec_name", "").lower()
        rate = int(a.get("sample_rate") or 0)
        channels = int(a.get("channels") or 0)
        if codec != self.enforce_audio_codec:
            errors.append(f"audio codec: expected {self.enforce_audio_codec}, observed {codec}")
        else:
            matches.append(f"audio codec matches {codec}")
        if rate and rate != self.enforce_audio_rate:
            errors.append(f"audio rate: expected {self.enforce_audio_rate}, observed {rate}")
        elif rate:
            matches.append(f"audio rate matches {rate}")
        if channels and channels != self.enforce_audio_channels:
            errors.append(f"audio channels: expected {self.enforce_audio_channels}, observed {channels}")
        elif channels:
            matches.append(f"audio channels match {channels}")
        return {"matches": matches, "errors": errors, "report": report}

    def check_speaker(self, _probe: Dict[str, Any]) -> Dict[str, list]:
        matches, errors, report = [], [], []
        if self.speaker_has_track and not self.speaker_codec:
            errors.append("speaker: expected backchannel codec/rate but none configured")
        # Without backchannel probe data, we cannot verify; note expectation
        if not self.speaker_has_track and not self.speaker_codec and not self.speaker_rate_hz:
            report.append("speaker: no backchannel/talkback observed; requires ONVIF/backchannel support")
        return {"matches": matches, "errors": errors, "report": report}

    def check_flight(self, probe: Dict[str, Any]) -> Dict[str, Any]:
        """
        Run all checks against ffprobe JSON output.
        Returns a dict with lists of messages per group and an overall pass flag.
        """
        results = {
            "transport": self.check_transport(probe),
            "video": self.check_video(probe),
            "audio": self.check_audio(probe),
            "speaker": self.check_speaker(probe),
        }
        all_errors = sum(len(v["errors"]) for v in results.values())
        results["ok"] = all_errors == 0
        return results


class RtspProbe:
    """
    Fetch stream metadata via ffprobe/ffmpeg for RTSP/FLV/go2rtc outputs.
    """

    def __init__(self, ffprobe_path: str = "ffprobe", ffmpeg_path: str = "ffmpeg") -> None:
        self.ffprobe_path = ffprobe_path
        self.ffmpeg_path = ffmpeg_path

    def summarize_flv(self, url: str) -> Dict[str, Any]:
        return summarize_flv_stream(url)

    def test_flv(self, url: str) -> Dict[str, Any]:
        return test_flv_stream(url)

    def summarize_go2rtc_stream(self, name: str, base_url: str = "http://localhost:1984") -> Dict[str, Any]:
        return summarize_go2rtc_stream(name, base_url=base_url, include_producers=True)

    def summarize_go2rtc_stream_channels(self, name: str, base_url: str = "http://localhost:1984") -> Dict[str, Any]:
        return summarize_go2rtc_stream_channels(name, base_url=base_url)

    def summarize_rtsp_stream(
        self,
        url: str,
        go2rtc_channel: str | None = None,
        stream_mac: str | None = None,
    ) -> Dict[str, Any]:
        summary = summarize_flv_stream(url)
        mac_info = _mac_from_url(url)
        video = summary.get("video") or {}
        return {
            "codec": video.get("codec"),
            "profile": video.get("profile"),
            "level": video.get("level"),
            "pixFmt": video.get("pix_fmt"),
            "width": video.get("width"),
            "height": video.get("height"),
            "fps": (video.get("r_frame_rate") or {}).get("value"),
            "avgFps": (video.get("avg_frame_rate") or {}).get("value"),
            "bitrate": summary.get("bitrate") or video.get("bitrate"),
            "container": summary.get("container"),
            "go2rtcChannel": go2rtc_channel,
            "streamUrl": url,
            "streamMac": stream_mac or mac_info.get("mac"),
            "audioCodec": (summary.get("audio") or {}).get("codec"),
            "audioSampleRate": (summary.get("audio") or {}).get("sample_rate"),
            "audioChannels": (summary.get("audio") or {}).get("channels"),
        }


class OnvifProbe:
    """
    Fetch device and profile settings via ONVIF when available.
    """

    @staticmethod
    def _profile_to_stream_fields(profile: Dict[str, Any]) -> Dict[str, Any]:
        video_encoder = profile.get("video_encoder") or {}
        codec = (profile.get("video_encoding") or "").lower() or None
        return {
            "codec": codec,
            "profile": video_encoder.get("profile"),
            "level": None,
            "pixFmt": None,
            "width": profile.get("video_width"),
            "height": profile.get("video_height"),
            "fps": None if not video_encoder.get("framerate_limit") else float(video_encoder.get("framerate_limit")),
            "avgFps": None,
            "bitrate": None if not video_encoder.get("bitrate_limit") else int(video_encoder.get("bitrate_limit")),
            "container": "rtsp",
            "go2rtcChannel": None,
            "streamUrl": profile.get("stream_uri"),
            "streamMac": _mac_from_url(profile.get("stream_uri") or "").get("mac"),
        }

    def summarize_camera(
        self,
        host: str,
        username: str,
        password: str,
        port: int | None = None,
        is_tapo: bool = False,
        device_path: str = "/onvif/device_service",
        https: bool = False,
        auth_mode: str = "digest",
        wsse_mode: str = "digest",
    ) -> Dict[str, Any]:
        result = summarize_onvif_camera(
            host=host,
            username=username,
            password=password,
            port=port,
            is_tapo=is_tapo,
            device_path=device_path,
            https=https,
            auth_mode=auth_mode,
            wsse_mode=wsse_mode,
        )
        mac_info = _mac_from_url(f"http://{host}")
        result["device_ip"] = mac_info.get("ip")
        result["device_mac"] = mac_info.get("mac")
        result["device_mac_skipped"] = mac_info.get("skipped")
        profiles = result.get("profiles") or []
        result["streams"] = [self._profile_to_stream_fields(p) for p in profiles]
        return result


class FlightCheckRunner:
    """
    Orchestrate ONVIF discovery and stream probing, then run FinalFlightCheck rules.
    """

    def __init__(self) -> None:
        self.rules = FinalFlightCheck()
        self.streams = RtspProbe()
        self.onvif = OnvifProbe()


def main():
    parser = argparse.ArgumentParser(description="Run flight checks against ffprobe JSON.")
    parser.add_argument(
        "probe",
        help="Path to ffprobe JSON file or raw JSON string. Use '-' to read from stdin.",
    )
    args = parser.parse_args()

    raw = ""
    if args.probe == "-":
        raw = sys.stdin.read()
    else:
        try:
            # If file exists, read it; otherwise treat as raw JSON
            with open(args.probe, "r", encoding="utf-8") as f:
                raw = f.read()
        except FileNotFoundError:
            raw = args.probe

    try:
        probe = json.loads(raw)
    except Exception as exc:
        print(f"Failed to parse probe JSON: {exc}", file=sys.stderr)
        sys.exit(1)

    fc = FinalFlightCheck()
    results = fc.check_flight(probe)
    printable = dict(results)
    printable["All checks passed"] = printable.pop("ok")
    print(json.dumps(printable, indent=2))
    sys.exit(0 if results.get("All checks passed") else 2)


def check_url(
    url: str,
    ffprobe_path: str = "ffprobe",
    full: bool = False,
    mac_mode: str = "lookup",
) -> Dict[str, Any]:
    """
    Convenience helper: run ffprobe against a URL and return the flight check report.
    """
    cmd = [
        ffprobe_path,
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-print_format",
        "json",
        url,
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        probe = json.loads(res.stdout)
    except subprocess.CalledProcessError as exc:
        return {"All checks passed": False, "error": f"ffprobe failed: {exc.stderr or exc.stdout or exc}"}
    except json.JSONDecodeError as exc:
        return {"All checks passed": False, "error": f"failed to parse ffprobe JSON: {exc}"}

    fc = FinalFlightCheck()
    results = fc.check_flight(probe)
    results["All checks passed"] = results.pop("ok")
    if full:
        results["probe"] = probe
        results["summary"] = summarize_probe(probe)
        results["mac"] = _resolve_mac_report(url, mac_mode)
    return results


def _resolve_mac_report(url: str, mac_mode: str) -> Dict[str, Any]:
    mode = (mac_mode or "lookup").lower()
    report: Dict[str, Any] = {"mode": mode, "value": None, "source": None, "detail": None}
    if mode == "random":
        mac = _generate_random_mac()
        report.update({"value": mac, "source": "random"})
        return report
    info = _mac_from_url(url)
    report["detail"] = info
    if info.get("mac"):
        report.update({"value": info.get("mac"), "source": "arp"})
    else:
        report.update({"value": None, "source": "arp"})
    return report


def _generate_random_mac() -> str:
    # Locally administered, unicast MAC (set bit1, clear bit0)
    first = random.randint(0x00, 0xFF)
    first = (first & 0xFE) | 0x02
    octets = [first] + [random.randint(0x00, 0xFF) for _ in range(5)]
    return ":".join(f"{b:02x}" for b in octets)


def resolve_mac(url: str, mac_mode: str = "lookup") -> Dict[str, Any]:
    """
    Public wrapper for MAC lookup/generation without running ffprobe.
    """
    return _resolve_mac_report(url, mac_mode)


def summarize_probe(probe: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build a compact summary from an ffprobe JSON payload (no network calls).
    """
    summary: Dict[str, Any] = {
        "container": None,
        "bitrate": None,
        "video": {},
        "audio": {},
    }
    fmt_block = probe.get("format") or {}
    fmt = fmt_block.get("format_name")
    fmt_bitrate = fmt_block.get("bit_rate")
    summary["container"] = fmt
    summary["bitrate"] = int(fmt_bitrate) if fmt_bitrate else None

    vids = FinalFlightCheck._get_streams_by_type(probe, "video")
    if vids:
        v = vids[0]
        summary["video"] = {
            "codec": v.get("codec_name"),
            "profile": v.get("profile"),
            "level": v.get("level"),
            "pix_fmt": v.get("pix_fmt"),
            "width": v.get("width"),
            "height": v.get("height"),
            "r_frame_rate": _parse_rate(v.get("r_frame_rate", "")),
            "avg_frame_rate": _parse_rate(v.get("avg_frame_rate", "")),
            "bits_per_raw_sample": v.get("bits_per_raw_sample"),
            "has_b_frames": v.get("has_b_frames"),
            "bitrate": int(v.get("bit_rate")) if v.get("bit_rate") else None,
        }

    auds = FinalFlightCheck._get_streams_by_type(probe, "audio")
    if auds:
        a = auds[0]
        summary["audio"] = {
            "codec": a.get("codec_name"),
            "sample_rate": a.get("sample_rate"),
            "channels": a.get("channels"),
        }

    return summary


def _parse_rate(rate: str) -> Dict[str, Any]:
    if not rate or rate == "0/0":
        return {"raw": rate, "value": None}
    try:
        num, den = rate.split("/", 1)
        num_i = int(num)
        den_i = int(den)
        value = round(num_i / den_i, 3) if den_i else None
        return {"raw": rate, "value": value}
    except Exception:
        return {"raw": rate, "value": None}


def _fetch_json(url: str, timeout: float = 4.0) -> Dict[str, Any]:
    with urlopen(url, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw)


def _local_ips() -> set[str]:
    ips = {"127.0.0.1", "localhost"}
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None):
            ip = info[4][0]
            ips.add(ip)
    except Exception:
        pass
    return ips


def _arp_lookup(ip: str) -> str | None:
    try:
        with open("/proc/net/arp", "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except Exception:
        return None
    for line in lines[1:]:
        parts = line.split()
        if len(parts) >= 4 and parts[0] == ip:
            mac = parts[3].lower()
            if mac != "00:00:00:00:00:00":
                return mac
    return None


def _mac_from_url(url: str) -> Dict[str, Any]:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if not host:
        return {"ip": None, "mac": None, "skipped": False}
    if host in _local_ips():
        return {"ip": host, "mac": None, "skipped": True}
    mac = _arp_lookup(host)
    return {"ip": host, "mac": mac, "skipped": False}


def _go2rtc_add_stream(base_url: str, name: str, src: str, timeout: float = 4.0) -> Dict[str, Any]:
    query = f"?name={quote(name)}&src={quote(src)}"
    url = f"{base_url.rstrip('/')}/api/streams{query}"
    try:
        with urlopen(url, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
        return {"ok": True, "response": raw}
    except HTTPError as exc:
        return {"ok": False, "error": f"go2rtc add stream failed: {exc}"}
    except Exception as exc:
        return {"ok": False, "error": f"go2rtc add stream failed: {exc}"}


def _estimate_bitrate(url: str, seconds: int = 5) -> Dict[str, Any]:
    cmd = [
        "ffmpeg",
        "-v",
        "info",
        "-stats",
        "-i",
        url,
        "-t",
        str(seconds),
        "-f",
        "null",
        "-",
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        return {"bitrate_bps": None, "error": exc.stderr or exc.stdout or str(exc)}

    last_bitrate = None
    for line in res.stderr.splitlines():
        if "bitrate=" in line:
            last_bitrate = line
    if not last_bitrate:
        return {"bitrate_bps": None, "error": "no bitrate reported"}

    try:
        # Example: "... bitrate= 3212.3kbits/s"
        parts = last_bitrate.split("bitrate=", 1)[1].strip().split()
        if not parts or parts[0].upper() == "N/A":
            return {"bitrate_bps": None, "error": "bitrate N/A"}
        value = float(parts[0])
        unit = parts[1] if len(parts) > 1 else ""
        if unit.startswith("kbit"):
            bitrate_bps = int(value * 1000)
        elif unit.startswith("mbit"):
            bitrate_bps = int(value * 1000 * 1000)
        else:
            bitrate_bps = int(value)
        return {"bitrate_bps": bitrate_bps, "error": None}
    except Exception as exc:
        return {"bitrate_bps": None, "error": f"parse error: {exc}"}


def summarize_flv_stream(url: str) -> Dict[str, Any]:
    """
    Return a compact summary of ffprobe fields we care about.
    """
    summary: Dict[str, Any] = {
        "url": url,
        "container": None,
        "bitrate": None,
        "video": {},
        "audio": {},
    }
    probe_cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-print_format",
        "json",
        url,
    ]
    try:
        probe_res = subprocess.run(probe_cmd, capture_output=True, text=True, check=True)
        probe = json.loads(probe_res.stdout)
    except subprocess.CalledProcessError as exc:
        return {"ok": False, "error": "ffprobe failed", "stderr": exc.stderr or exc.stdout or str(exc)}
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"failed to parse ffprobe JSON: {exc}"}

    fmt_block = probe.get("format") or {}
    fmt = fmt_block.get("format_name")
    fmt_bitrate = fmt_block.get("bit_rate")
    summary["container"] = fmt
    summary["bitrate"] = int(fmt_bitrate) if fmt_bitrate else None

    vids = FinalFlightCheck._get_streams_by_type(probe, "video")
    if vids:
        v = vids[0]
        summary["video"] = {
            "codec": v.get("codec_name"),
            "profile": v.get("profile"),
            "level": v.get("level"),
            "pix_fmt": v.get("pix_fmt"),
            "width": v.get("width"),
            "height": v.get("height"),
            "r_frame_rate": _parse_rate(v.get("r_frame_rate", "")),
            "avg_frame_rate": _parse_rate(v.get("avg_frame_rate", "")),
            "bits_per_raw_sample": v.get("bits_per_raw_sample"),
            "has_b_frames": v.get("has_b_frames"),
            "bitrate": int(v.get("bit_rate")) if v.get("bit_rate") else None,
        }

    auds = FinalFlightCheck._get_streams_by_type(probe, "audio")
    if auds:
        a = auds[0]
        summary["audio"] = {
            "codec": a.get("codec_name"),
            "sample_rate": a.get("sample_rate"),
            "channels": a.get("channels"),
        }

    if summary["bitrate"] is None and summary["video"].get("bitrate") is None:
        estimate = _estimate_bitrate(url, seconds=5)
        summary["bitrate"] = estimate.get("bitrate_bps")
        if estimate.get("error"):
            summary["bitrate_error"] = estimate["error"]

    summary["ok"] = True
    return summary


def summarize_go2rtc_streams(base_url: str = "http://localhost:1984") -> Dict[str, Any]:
    """
    Query go2rtc for all stream names and summarize each FLV output stream.
    """
    base = base_url.rstrip("/")
    try:
        data = _fetch_json(f"{base}/api/streams")
    except Exception as exc:
        return {"ok": False, "error": f"failed to query go2rtc streams: {exc}"}

    names: list[str] = []
    if isinstance(data, dict) and "streams" in data:
        streams = data.get("streams")
        if isinstance(streams, dict):
            names = list(streams.keys())
    elif isinstance(data, dict):
        names = [k for k in data.keys() if isinstance(k, str)]

    results: Dict[str, Any] = {}
    for name in sorted(set(names)):
        stream_url = f"{base}/api/stream.flv?src={quote(name)}"
        results[name] = summarize_flv_stream(stream_url)

    return {"ok": True, "streams": results}


def summarize_go2rtc_stream(
    name: str, base_url: str = "http://localhost:1984", include_producers: bool = True
) -> Dict[str, Any]:
    """
    Summarize a single go2rtc stream name. Optionally test each producer URL.
    """
    base = base_url.rstrip("/")
    try:
        data = _fetch_json(f"{base}/api/streams")
    except Exception as exc:
        return {"ok": False, "error": f"failed to query go2rtc streams: {exc}"}

    stream_info = None
    if isinstance(data, dict) and "streams" in data:
        streams = data.get("streams")
        if isinstance(streams, dict):
            stream_info = streams.get(name)
    elif isinstance(data, dict):
        stream_info = data.get(name)

    if stream_info is None:
        return {"ok": False, "error": f"stream not found: {name}"}

    stream_url = f"{base}/api/stream.flv?src={quote(name)}"
    result: Dict[str, Any] = {
        "ok": True,
        "name": name,
        "stream_url": stream_url,
        "summary": summarize_flv_stream(stream_url),
    }

    if include_producers:
        producers = []
        if isinstance(stream_info, dict):
            producers = stream_info.get("producers") or []
        producer_urls: list[str] = []
        for producer in producers:
            if isinstance(producer, dict):
                url = producer.get("url")
            else:
                url = producer
            if url:
                producer_urls.append(str(url))
        result["producers"] = {
            "urls": producer_urls,
            "note": "Producers are source URLs; go2rtc does not expose them as /api/stream.flv src.",
        }

    return result


def summarize_go2rtc_stream_channels(
    name: str,
    base_url: str = "http://localhost:1984",
) -> Dict[str, Any]:
    """
    For a stream with multiple producers, summarize each channel via ?channel=<index>.
    """
    base = base_url.rstrip("/")
    data = summarize_go2rtc_stream(name, base_url=base, include_producers=True)
    if not data.get("ok"):
        return data

    producers = data.get("producers", {}).get("urls", [])
    if not producers:
        return {"ok": False, "error": f"no producers found for stream: {name}"}

    channels: list[Dict[str, Any]] = []
    for idx, src in enumerate(producers):
        stream_url = f"{base}/api/stream.flv?src={quote(name)}&channel={idx}"
        entry: Dict[str, Any] = {
            "channel": idx,
            "src": src,
            "stream_url": stream_url,
            "summary": summarize_flv_stream(stream_url),
        }
        channels.append(entry)

    return {"ok": True, "name": name, "channels": channels}


def summarize_onvif_camera(
    host: str,
    username: str,
    password: str,
    port: int | None = None,
    is_tapo: bool = False,
    device_path: str = "/onvif/device_service",
    https: bool = False,
) -> Dict[str, Any]:
    """
    Use the ONVIF client to fetch device info, media profiles, and encoder details.
    """
    if port is None:
        port = 2020 if is_tapo else 80

    client = OnvifSoapClient(
        host=host,
        port=port,
        username=username,
        password=password,
        https=https,
    )
    device_url = client.endpoint(device_path)
    media_url = client.get_media_xaddr(device_url) or device_url

    device_info = client.get_device_info(device_url)
    profiles = client.get_profiles(media_url, include_streams=True)
    for prof in profiles:
        video_token = prof.get("video_encoder_token")
        audio_token = prof.get("audio_encoder_token")
        if video_token:
            prof["video_encoder"] = client.get_video_encoder_configuration(media_url, video_token)
            prof["video_encoder_options"] = client.get_video_encoder_configuration_options(
                media_url, profile_token=prof.get("token")
            )
        if audio_token:
            prof["audio_encoder"] = client.get_audio_encoder_configuration(media_url, audio_token)
            prof["audio_encoder_options"] = client.get_audio_encoder_configuration_options(
                media_url, profile_token=prof.get("token")
            )

    return {
        "ok": True,
        "device_url": device_url,
        "media_url": media_url,
        "device_info": device_info,
        "profiles": profiles,
    }


def summarize_onvif_profiles(
    host: str,
    port: int,
    username: str,
    password: str,
    device_path: str = "/onvif/device_service",
    https: bool = False,
) -> Dict[str, Any]:
    """
    Backwards-compatible wrapper for summarize_onvif_camera.
    """
    return summarize_onvif_camera(
        host=host,
        username=username,
        password=password,
        port=port,
        is_tapo=False,
        device_path=device_path,
        https=https,
    )


def apply_onvif_encoder_settings(
    host: str,
    username: str,
    password: str,
    config: Dict[str, Any],
    port: int | None = None,
    is_tapo: bool = False,
    device_path: str = "/onvif/device_service",
    https: bool = False,
    auth_mode: str = "digest",
    wsse_mode: str = "digest",
) -> Dict[str, Any]:
    """
    Apply ONVIF video encoder settings via SetVideoEncoderConfiguration.
    """
    if port is None:
        port = 2020 if is_tapo else 80
    client = OnvifSoapClient(
        host=host,
        port=port,
        username=username,
        password=password,
        auth_mode=auth_mode,
        wsse_mode=wsse_mode,
        https=https,
    )
    device_url = client.endpoint(device_path)
    media_url = client.get_media_xaddr(device_url) or device_url
    result = client.set_video_encoder_configuration(media_url, config, force_persistence=True)
    return {
        "ok": bool(result.get("ok")),
        "device_url": device_url,
        "media_url": media_url,
        "result": result,
    }


def apply_onvif_encoder_for_profile(
    host: str,
    username: str,
    password: str,
    width: int,
    height: int,
    *,
    port: int | None = None,
    is_tapo: bool = False,
    device_path: str = "/onvif/device_service",
    https: bool = False,
    auth_mode: str = "digest",
    wsse_mode: str = "digest",
    profile: str = "Main",
    quality: int | None = None,
    framerate_limit: int | None = None,
    bitrate: int | None = None,
    gov_length: int | None = None,
) -> Dict[str, Any]:
    """
    Find ONVIF profile by resolution and apply encoder settings.
    """
    if port is None:
        port = 2020 if is_tapo else 80
    client = OnvifSoapClient(
        host=host,
        port=port,
        username=username,
        password=password,
        auth_mode=auth_mode,
        wsse_mode=wsse_mode,
        https=https,
    )
    device_url = client.endpoint(device_path)
    media_url = client.get_media_xaddr(device_url) or device_url
    profiles = client.get_profiles(media_url, include_streams=False)
    target = next(
        (p for p in profiles if p.get("video_width") == width and p.get("video_height") == height),
        None,
    )
    if target is None and profiles:
        target = profiles[0]
    if not target:
        return {"ok": False, "error": "no profiles found", "device_url": device_url, "media_url": media_url}
    token = target.get("video_encoder_token")
    if not token:
        return {"ok": False, "error": "missing video encoder token", "device_url": device_url, "media_url": media_url}
    current = client.get_video_encoder_configuration(media_url, token)
    config = {
        "token": current.get("token") or token,
        "name": current.get("name") or "",
        "encoding": current.get("encoding") or "H264",
        "width": width,
        "height": height,
        "quality": quality if quality is not None else current.get("quality"),
        "framerate_limit": framerate_limit if framerate_limit is not None else current.get("framerate_limit"),
        "bitrate": bitrate if bitrate is not None else current.get("bitrate_limit"),
        "gov_length": gov_length if gov_length is not None else current.get("gov_length"),
        "profile": profile,
    }
    result = client.set_video_encoder_configuration(media_url, config, force_persistence=True)
    return {
        "ok": bool(result.get("ok")),
        "device_url": device_url,
        "media_url": media_url,
        "profile_used": target.get("token"),
        "encoder_token": token,
        "applied": config,
        "result": result,
    }


def apply_onvif_encoder_max_resolution(
    host: str,
    username: str,
    password: str,
    *,
    port: int | None = None,
    is_tapo: bool = False,
    device_path: str = "/onvif/device_service",
    https: bool = False,
    auth_mode: str = "digest",
    wsse_mode: str = "digest",
    profile: str = "Main",
    quality: int | None = None,
    framerate_limit: int | None = None,
    bitrate: int | None = None,
    gov_length: int | None = None,
) -> Dict[str, Any]:
    """
    Pick the highest-resolution ONVIF profile and apply encoder settings.
    """
    if port is None:
        port = 2020 if is_tapo else 80
    client = OnvifSoapClient(
        host=host,
        port=port,
        username=username,
        password=password,
        auth_mode=auth_mode,
        wsse_mode=wsse_mode,
        https=https,
    )
    device_url = client.endpoint(device_path)
    media_url = client.get_media_xaddr(device_url) or device_url
    profiles = client.get_profiles(media_url, include_streams=False)
    if not profiles:
        return {"ok": False, "error": "no profiles found", "device_url": device_url, "media_url": media_url}
    def _res_score(p: Dict[str, Any]) -> int:
        try:
            return int(p.get("video_width") or 0) * int(p.get("video_height") or 0)
        except Exception:
            return 0
    target = max(profiles, key=_res_score)
    token = target.get("video_encoder_token")
    if not token:
        return {"ok": False, "error": "missing video encoder token", "device_url": device_url, "media_url": media_url}
    current = client.get_video_encoder_configuration(media_url, token)
    width = target.get("video_width") or current.get("width")
    height = target.get("video_height") or current.get("height")
    config = {
        "token": current.get("token") or token,
        "name": current.get("name") or "",
        "encoding": current.get("encoding") or "H264",
        "width": width,
        "height": height,
        "quality": quality if quality is not None else current.get("quality"),
        "framerate_limit": framerate_limit if framerate_limit is not None else current.get("framerate_limit"),
        "bitrate": bitrate if bitrate is not None else current.get("bitrate_limit"),
        "gov_length": gov_length if gov_length is not None else current.get("gov_length"),
        "profile": profile,
    }
    result = client.set_video_encoder_configuration(media_url, config, force_persistence=True)
    return {
        "ok": bool(result.get("ok")),
        "device_url": device_url,
        "media_url": media_url,
        "profile_used": target.get("token"),
        "encoder_token": token,
        "applied": config,
        "result": result,
    }


def test_flv_stream(url: str) -> Dict[str, Any]:
    """
    Run ffprobe + a quick ffmpeg decode against a FLV stream URL and return details.
    """
    details: Dict[str, Any] = {"url": url}

    probe_cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-print_format",
        "json",
        url,
    ]
    try:
        probe_res = subprocess.run(probe_cmd, capture_output=True, text=True, check=True)
        details["ffprobe_raw"] = probe_res.stdout
        details["ffprobe"] = json.loads(probe_res.stdout)
    except subprocess.CalledProcessError as exc:
        details["ffprobe_error"] = exc.stderr or exc.stdout or str(exc)
    except json.JSONDecodeError as exc:
        details["ffprobe_error"] = f"failed to parse ffprobe JSON: {exc}"

    decode_cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        url,
        "-t",
        "2",
        "-f",
        "null",
        "-",
    ]
    try:
        decode_res = subprocess.run(decode_cmd, capture_output=True, text=True, check=True)
        details["ffmpeg_stderr"] = decode_res.stderr.strip()
        details["ok"] = True
    except subprocess.CalledProcessError as exc:
        details["ok"] = False
        details["error"] = "ffmpeg failed to decode stream"
        details["ffmpeg_stderr"] = exc.stderr or exc.stdout or str(exc)

    return details


if __name__ == "__main__":
    main()
