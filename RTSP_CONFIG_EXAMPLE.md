# RTSP Driver Configuration Guide

## How Video Streams Work

UniFi Protect uses 3 video streams:

- **video1** (Stream 0) - High quality (2688x1512, 3 Mbps) - Main stream
- **video2** (Stream 1) - Low quality (640x360, 200 Kbps) - Mobile/remote viewing
- **video3** (Stream 2) - Medium quality (1280x720, 500 Kbps) - Optional middle ground

## FFmpeg Arguments

You can customize ffmpeg behavior using the `FFMPEG_ARGS` environment variable in `docker-compose.yml`.

### Default (Recommended) - Based on unifi-cam-proxy-kinda/Frigate
```yaml
environment:
  # Copy video, normalize audio for UniFi Protect compatibility
  - FFMPEG_ARGS=-c:v copy -ar 32000 -ac 1 -codec:a aac -b:a 32k
```

**Pros:**
- ✅ Very low CPU usage (no video transcoding)
- ✅ Fast, minimal latency
- ✅ Preserves original video quality
- ✅ Audio normalization ensures compatibility with UniFi Protect
- ✅ Proven configuration from unifi-cam-proxy-kinda/Frigate project

**When to use:**
- Your camera already outputs H.264 video compatible with UniFi Protect
- You want to minimize CPU usage and latency
- Your camera's native resolution/bitrate is acceptable

### Stream Copy (No Processing)
```yaml
environment:
  - FFMPEG_ARGS=-c:v copy -c:a copy
```

**Pros:**
- ✅ Absolutely minimal CPU usage
- ✅ Fastest possible streaming

**Cons:**
- ❌ Audio format may not be compatible with UniFi Protect
- ❌ May have sync issues if camera's audio format is unusual

### Hardware Acceleration (VAAPI)
```yaml
environment:
  - FFMPEG_ARGS=-hwaccel vaapi -hwaccel_device /dev/dri/renderD128 -hwaccel_output_format yuv420p
```

**Note:** Requires GPU access and additional Docker configuration.

### Custom Transcoding (If needed)
```yaml
environment:
  - FFMPEG_ARGS=-c:v libx264 -preset veryfast -tune zerolatency -b:v 1200k -maxrate 2000k -bufsize 2400k -c:a aac -b:a 32k -ar 32000 -ac 1
```

**When to use:**
- Camera doesn't output H.264 (e.g., uses H.265/HEVC)
- Need to adjust resolution or bitrate
- Camera's native format is incompatible with UniFi Protect

### Advanced Options

The RTSP driver includes additional ffmpeg base arguments for robustness (automatically added):
- `-avoid_negative_ts make_zero` - Fixes timestamp issues
- `-fflags +genpts+discardcorrupt` - Handles corrupted packets gracefully
- `-use_wallclock_as_timestamps 1` - Uses system time for timestamps
- `-stimeout 15000000` - 15 second RTSP timeout

These are based on proven configurations from the unifi-cam-proxy-kinda project.

## Configuration

Add this to your `settings.json`:

```json
{
  "mac": "02:42:c0:a8:01:28",
  "host": "192.168.1.40",
  "camera": {
    "type": "rtsp"
  },
  "rtsp": {
    "snapshot_url": "rtsp://admin:password@192.168.1.100:554/stream1",
    "video1": "rtsp://admin:password@192.168.1.100:554/stream1",
    "video2": "rtsp://admin:password@192.168.1.100:554/stream2",
    "video3": "rtsp://admin:password@192.168.1.100:554/stream2"
  }
}
```

## Common Camera RTSP URLs

### Amcrest/Dahua Cameras
```json
{
  "rtsp": {
    "snapshot_url": "rtsp://admin:password@192.168.1.100:554/cam/realmonitor?channel=1&subtype=0",
    "video1": "rtsp://admin:password@192.168.1.100:554/cam/realmonitor?channel=1&subtype=0",
    "video2": "rtsp://admin:password@192.168.1.100:554/cam/realmonitor?channel=1&subtype=1"
  }
}
```

### Hikvision Cameras
```json
{
  "rtsp": {
    "snapshot_url": "rtsp://admin:password@192.168.1.100:554/Streaming/Channels/101",
    "video1": "rtsp://admin:password@192.168.1.100:554/Streaming/Channels/101",
    "video2": "rtsp://admin:password@192.168.1.100:554/Streaming/Channels/102"
  }
}
```

### Reolink Cameras
```json
{
  "rtsp": {
    "snapshot_url": "rtsp://admin:password@192.168.1.100:554/h264Preview_01_main",
    "video1": "rtsp://admin:password@192.168.1.100:554/h264Preview_01_main",
    "video2": "rtsp://admin:password@192.168.1.100:554/h264Preview_01_sub"
  }
}
```

### TP-Link Tapo Cameras
```json
{
  "rtsp": {
    "snapshot_url": "rtsp://admin:password@192.168.1.100:554/stream1",
    "video1": "rtsp://admin:password@192.168.1.100:554/stream1",
    "video2": "rtsp://admin:password@192.168.1.100:554/stream2"
  }
}
```

## Minimal Configuration

If your camera only has one stream, just use:

```json
{
  "camera": {
    "type": "rtsp"
  },
  "rtsp": {
    "snapshot_url": "rtsp://admin:password@192.168.1.100:554/stream1"
  }
}
```

The driver will automatically use the snapshot URL for all video streams.

## Testing

1. Update your `settings.json` with the rtsp configuration
2. Restart the container: `docker compose restart`
3. Check logs: `docker compose logs -f unifi-cam-proxy`
4. You should see: `Starting RTSP->FLV stream for video2: rtsp://...`

## Troubleshooting

### No video showing
- Verify RTSP URL works: `ffplay rtsp://admin:password@192.168.1.100:554/stream1`
- Check ffmpeg is installed: `docker exec unifi-cam-proxy-redalert which ffmpeg`
- Look for ffmpeg errors in logs

### Permission errors
- Make sure ffmpeg can access the RTSP URL
- Check camera username/password are correct
- Verify firewall allows RTSP traffic (port 554)

### Stream quality issues
- Use `stream1` (main/high) for video1
- Use `stream2` (sub/low) for video2
- Adjust bitrates in `rtsp.py` if needed
