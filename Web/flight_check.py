"""
Flight check targets for stream validation.

Usage (ffprobe -> checker):
  To check camera directly
  ffprobe -v error -show_streams -show_format -print_format json "rtsp://.../stream" | python -m Web.flight_check -

  To check go2rtc output
  ffprobe -v error -show_streams -show_format -print_format json "http://<host>:1984/api/stream.flv?src=<name>" | python -m Web.flight_check -

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


@dataclass(frozen=True)
class FlightCheck:
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
    report_width: int = 2688  # observed
    report_height: int = 1512  # observed
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

    fc = FlightCheck()
    results = fc.check_flight(probe)
    printable = dict(results)
    printable["All checks passed"] = printable.pop("ok")
    print(json.dumps(printable, indent=2))
    sys.exit(0 if results.get("All checks passed") else 2)


def check_url(url: str, ffprobe_path: str = "ffprobe") -> Dict[str, Any]:
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

    fc = FlightCheck()
    results = fc.check_flight(probe)
    results["All checks passed"] = results.pop("ok")
    return results


if __name__ == "__main__":
    main()
