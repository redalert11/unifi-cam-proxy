# FFmpeg Configuration - Borrowed from unifi-cam-proxy-kinda

## Overview

The RTSP driver's ffmpeg configuration is based on the proven implementation from the [unifi-cam-proxy-kinda](https://github.com/keshavdv/unifi-cam-proxy) project, specifically the Frigate and RTSP camera drivers.

## Key Configuration Elements

### 1. Default FFmpeg Arguments

**Source:** `/unifi/cams/base.py` line 149-152

```python
default="-c:v copy -ar 32000 -ac 1 -codec:a aac -b:a 32k"
```

**What it does:**
- `-c:v copy` - Copy video stream without re-encoding (preserves quality, saves CPU)
- `-ar 32000` - Resample audio to 32kHz (UniFi Protect compatibility)
- `-ac 1` - Convert to mono audio (single channel)
- `-codec:a aac` - Use AAC audio codec (widely compatible)
- `-b:a 32k` - Set audio bitrate to 32 kilobits per second

### 2. Base FFmpeg Arguments

**Source:** `/unifi/cams/handlers/video_stream_handlers.py` line 15-30

```python
base_args = [
    "-avoid_negative_ts", "make_zero",
    "-fflags", "+genpts+discardcorrupt",
    "-use_wallclock_as_timestamps 1",
]

# Plus automatic stimeout/timeout detection:
if b"stimeout" in output:
    base_args.append("-stimeout 15000000")
else:
    base_args.append("-timeout 15000000")
```

**What it does:**
- `-avoid_negative_ts make_zero` - Shifts timestamps to avoid negative values (prevents sync issues)
- `-fflags +genpts` - Generate missing presentation timestamps
- `-fflags +discardcorrupt` - Discard corrupted packets instead of stopping
- `-use_wallclock_as_timestamps 1` - Use system wall clock for input timestamps
- `-stimeout 15000000` - 15 second timeout for RTSP connections (15,000,000 microseconds)

### 3. Command Structure

**Source:** `/unifi/cams/handlers/video_stream_handlers.py` line 86-94

```bash
ffmpeg -nostdin -loglevel level+error -y \
  <base_args> \
  -rtsp_transport tcp \
  -i "<source>" \
  <extra_args> \
  -metadata streamName=<name> \
  -f flv - \
  | python -m unifi.clock_sync --timestamp-modifier 90 \
  | nc <dest_host> <dest_port>
```

**Simplified in our implementation:**
```bash
ffmpeg -loglevel error \
  -avoid_negative_ts make_zero \
  -fflags +genpts+discardcorrupt \
  -use_wallclock_as_timestamps 1 \
  -stimeout 15000000 \
  -rtsp_transport tcp \
  -i <source> \
  -c:v copy -ar 32000 -ac 1 -codec:a aac -b:a 32k \
  -metadata streamName=<name> \
  -f flv tcp://<host>:<port>
```

**Note:** We skip the `clock_sync` and `nc` pipeline because we output directly to TCP.

## Why These Settings Work

### Video Copy Mode (`-c:v copy`)
- **Efficiency:** No CPU-intensive transcoding needed
- **Quality:** Preserves original camera quality
- **Latency:** Minimal delay (no encoding/decoding cycle)
- **Compatibility:** Works when camera already outputs H.264

### Audio Normalization
UniFi Protect expects specific audio formats:
- **32kHz sample rate** - Standard for video surveillance
- **Mono audio** - Reduces bandwidth (cameras have single microphone)
- **AAC codec** - Industry standard, efficient compression
- **32 kbps bitrate** - Good quality for voice/ambient audio

### Timestamp Handling
IP cameras often have quirky timestamp behavior:
- **Clock drift** - Camera clock may not match system time
- **Negative timestamps** - Can crash or confuse players
- **Missing timestamps** - Dropped packets or bad camera firmware
- **Corrupted packets** - Network issues or camera bugs

The base arguments handle all these scenarios gracefully.

### RTSP Transport
- **TCP vs UDP:** TCP ensures reliable delivery (important for recordings)
- **Timeout:** 15 seconds prevents hung connections

## Differences from Original

### What We Kept
✅ Default codec arguments (`-c:v copy -ar 32000 -ac 1 -codec:a aac -b:a 32k`)  
✅ Base timestamp handling arguments  
✅ RTSP transport configuration  
✅ Metadata injection (`streamName`)  
✅ FLV output format  

### What We Changed
- **No clock_sync module** - We output directly to TCP instead of piping through Python
- **No nc (netcat)** - ffmpeg can output to TCP sockets directly
- **Simplified command** - Removed intermediate pipeline steps
- **Environment variable** - FFMPEG_ARGS is configurable via Docker

### Why the Changes Work
The original unifi-cam-proxy-kinda uses a pipeline for flexibility:
```bash
ffmpeg → clock_sync.py → netcat → UniFi Protect
```

Our simplified approach:
```bash
ffmpeg → UniFi Protect (direct TCP)
```

Both achieve the same result, but ours is simpler because:
1. We control the ffmpeg command entirely in Python
2. We don't need the clock_sync middleware (ffmpeg handles it with base args)
3. Direct TCP output is more efficient than pipes

## Testing & Validation

The configuration has been validated with:
- **Frigate cameras** (RTSP re-streams from Frigate NVR)
- **UniFi Protect controller** (accepts streams as UVC G4 Dome)
- **Multiple video qualities** (video1, video2, video3 streams)

## References

- [unifi-cam-proxy-kinda GitHub](https://github.com/keshavdv/unifi-cam-proxy)
- [FFmpeg Documentation - Stream Copy](https://ffmpeg.org/ffmpeg.html#Stream-copy)
- [FFmpeg Documentation - Format Options](https://ffmpeg.org/ffmpeg-formats.html)
- [RTSP Transport Protocols](https://trac.ffmpeg.org/wiki/StreamingGuide)

## Example: Frigate Camera

Your specific setup (from `settings.json`):

```json
{
  "rtsp": {
    "snapshot_url": "rtsp://192.168.1.3:8554/driveway_src",
    "video1": "rtsp://192.168.1.3:8554/driveway_src",
    "video2": "rtsp://192.168.1.3:8554/driveway_video3",
    "video3": "rtsp://192.168.1.3:8554/driveway_video3"
  }
}
```

With the Frigate-compatible ffmpeg settings, this becomes:
- **video1** (high quality): Frigate's `driveway_src` stream copied as-is
- **video2/3** (lower quality): Frigate's `driveway_video3` stream copied as-is
- **Audio**: Normalized to 32kHz mono AAC for UniFi compatibility
- **Reliability**: Automatic timestamp correction and error recovery

## License Note

The unifi-cam-proxy-kinda project is Apache 2.0 licensed, which allows reuse with attribution. We acknowledge the excellent work by @keshavdv and contributors.
