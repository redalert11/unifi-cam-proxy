[![unifi-cam-proxy Discord](https://img.shields.io/discord/937237037466124330?color=0559C9&label=Discord&logo=discord&logoColor=%23FFFFFF&style=for-the-badge)](https://discord.gg/Bxk9uGT6MW)

# UniFi Camera Proxy

## About

This project emulates UniFi Protect cameras so that non-Ubiquiti hardware (or purely virtual cameras) can participate in the Protect ecosystem.

**Current capabilities**
- UDP discovery responder (port 10001) and HTTPS adoption endpoint (port 443)
- Authenticated WebSocket handshake on 7442 with driver plumbing (Null + Amcrest snapshots)
- Driver framework with `NullDriver` demo output and `AmcrestDriver` snapshot support

**Work in progress**
- Command/event bridge over 7442/7550 (settings sync, telemetry, talkback)
- Streaming pipeline: uPFLV video/audio + metadata on the camera-initiated 7550 WSS channel
- Additional hardware drivers (Reolink, Hikvision, etc.)

Drivers live under `Unifi/drivers/` and are selected through `camera_data/settings.json` (`camera.type`). Use `"null"` for the built-in test pattern or `"amcrest"` with `ip`, `user`, `pass`, etc., to exercise a real device while the rest of the driver matrix is being fleshed out.

Due to significant changes in UniFi Protect, this project is being completely rewritten. While the core functionality will remain the same, several improvements are planned — including better support for adoption, motion events, and PTZ controls.

## Table of Contents
1. [About](#about)
2. [Roadmap](#roadmap)
3. [Starting the Dev Container](#starting-the-dev-container)
4. [Documentation](#documentation)
5. [UniFi Protect Discovery & Adoption Process](#unifi-protect-discovery--adoption-process)
6. [High-Level Program Tree](#high-level-program-tree)
7. [Network Surfaces](#network-surfaces)
8. [UniFi Camera FLV & extendedFlv Stream Format](#unifi-camera-flv--extendedflv-stream-format)

## Roadmap
| Step | Direction           | Port   | Protocol  | Purpose                              | Replicated  |
| ---- | ------------------- | ------ | --------- | ---------------------------------    | ------------|
| 1    | Controller → Camera | 10001  | UDP       | Discovery                            | ✅          |
| 2    | Controller → Camera | 443    | HTTPS     | Adoption command (manage)            | ✅          |
| 3    | Camera → Controller | 7442   | WSS       | Authenticated WebSocket handshake    | ✅          |
| 4    | Controller → Camera | 7442   | WSS       | Sends settings + adoption details    |  🚧           |
| 5    | Camera ↔ Controller | 7550   | WSS (extendedFlv + commands) | Metadata/video/audio uplink, SPS/config downlink (in development) |  🚧           |

## Starting The Dev Container
To get started:
1. Clone the repository using Git to a folder on a Linux machine with Docker installed.
2. Remote into the machine and open the folder using Visual Studio Code.
3. Create a custom Docker network using macvlan (see instructions below).
4. When prompted, allow VS Code to build and open the dev container.
You can also rebuild the container at any time by pressing <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>P</kbd> and selecting "Dev Containers: Rebuild Container".
5. Once the container is running and UniFi Protect is active on the same network, a discovery message should be recieved automatically.
After the script responds to the discovery message (within 10–20 seconds), a new device should appear in UniFi Protect, ready for adoption.

Note: Before launching the dev container, you need to create a custom Docker network using macvlan:
```
docker network create -d macvlan \
  --subnet=192.168.0.0/24 \
  --gateway=192.168.0.1 \
  -o parent=lan0 \
  lan_net
```

Replace lan0 with your actual network interface name (e.g., eth0, enp3s0, etc.).

## Documentation

~~View the documentation at <https://unifi-cam-proxy.com>~~

## UniFi Protect Discovery & Adoption Process

The rewrite mirrors the real UniFi Protect handshake in five stages. Each stage is handled by a dedicated component inside `Unifi/main.py`.

| Stage | Triggered By                  | Transport / Port | Entry Point                             | Purpose |
|-------|------------------------------|------------------|-----------------------------------------|---------|
| 1. Discovery        | Protect broadcasts every few seconds | UDP / 10001       | `DiscoveryResponder.start()` (`Unifi/discovery_responder.py`) | Reply with camera identity TLVs so the controller can list the device as “Ready to Adopt.” |
| 2. Adoption request | User clicks **Adopt** in Protect     | HTTPS / 443       | `VerboseAPIServer.start()` (`Unifi/api_server.py`) | Receive `/api/1.2/manage`, persist credentials, and hand off controller token to the WSS manager. |
| 3. AVClient handshake | Camera thread receives token event | WSS / 7442        | `WssManager.start()` (`Unifi/wss_manager.py`) | Perform TLS WebSocket login, negotiate features, and fetch configs. |
| 4. Command/event bus *(WIP)* | Camera initiates after handshake       | WSS / 7550        | `WssManager` secondary socket (`Unifi/wss_manager.py`) | Bidirectional uPFLV transport: metadata/video/audio travel up, control frames down. |
| 5. Streaming *(WIP)*         | Controller requests live view         | WSS / 7550        | Camera drivers (`Unifi/drivers/*`)      | Encode extendedFlv (video/audio + Script tags) once instructed over the 7550 channel. |

### 1. Discovery (UDP 10001)
- Initiated by Protect; every broadcast contains `0x01 0x00 0x0000`.
- `DiscoveryResponder` listens on a raw socket and replies immediately with a TLV payload describing MAC, firmware, feature bits, and optional extras (see `Unifi/discovery_responder.py` for exact layout).
- Adoption state is stored in `camera_data/settings.json`, so subsequent boots can skip discovery if `canAdopt` is false.

### 2. Adoption API (HTTPS 443)
- When the operator clicks **Adopt**, Protect performs `POST /api/1.2/manage` against the camera.
- `VerboseAPIServer` terminates TLS (self-signed cert in repo), verifies credentials, and stores the management payload. The payload includes the `token`, controller hostnames, and user-provided username/password.
- A `threading.Event` (`token_event`) notifies the rest of the program that the controller token is ready.

### 3. AVClient / Settings Handshake (WSS 7442)
- `WssManager` waits on `token_event`, then opens a secure WebSocket to the controller host delivered in the adoption payload.
- During this stage the manager:
  - Sends the “hello” document defined in `HELLO_FEATURES`.
  - Receives baseline settings (video, ISP, motion, etc.).
  - Dispatches those payloads to the active camera driver (`NullDriver`, `AmcrestDriver`, etc.) so each driver can apply or emulate them.
- Token refreshes or reconnects reuse the same flow; the manager loops until `stop_event` is set.

### 4. Command & Video Transport (WSS 7550, Work in Progress)
- After the AVClient handshake, the **camera** opens a second secure socket to the controller on port 7550.
- The protocol matches Protect’s uPFLV channel:
  - **Camera → Controller:** FLV header, AMF metadata (`onMetaData`, `onClockSync`), video tags (AVC sequence headers + H.264 NALUs), and optional AAC audio.
  - **Controller → Camera:** lightweight binary frames prefixed with `DE 19 16 75 50` carrying stream start/stop commands, heartbeats, and clock-sync values.
- No RTSP/HTTP is involved; everything is raw TCP/WebSocket framing around standard FLV tags.
- Loss of controller heartbeats should cause the camera to tear down the session and stop streaming.

### 5. Streaming Pipeline (Work in Progress)
- Protect expects extendedFlv over the same 7550 socket, so there is no RTSP relay.
- Drivers can choose how to generate frames:
  - `NullDriver` fabricates JPEGs for snapshots and will eventually synthesize video frames for testing.
  - Hardware drivers (e.g., Amcrest) can ingest vendor RTSP or SDK feeds locally, transcode with FFmpeg, and push the resulting H.264/AAC payloads into the WSS stream helpers.
- See the “UniFi Camera FLV & extendedFlv Stream Format” section below for the exact tag layout Protect expects.

### High-Level Program Tree
```
Unifi/main.py
├─ CameraSettings()                 # loads/saves camera_data/settings.json
├─ DiscoveryResponder.start()       # UDP 10001 responder (thread)
├─ VerboseAPIServer.start()         # HTTPS /api server (thread)
├─ start_upload_server()            # optional upload shim for Protect firmware updates
└─ WssManager.start()               # owns token wait + both WSS connections
   ├─ build_camera_driver(...)     # Null / Amcrest / future drivers
   ├─ driver.get_snapshot_jpeg()   # used for adoption UI + diagnostics
   ├─ driver.apply_*_settings()    # invoked when Protect sends settings
   └─ streaming helpers            # package extendedFlv metadata/audio/video on 7550
```

### Network Surfaces

| Port  | Protocol | Direction           | Purpose                                                          |
|-------|----------|---------------------|------------------------------------------------------------------|
| 10001 | UDP      | Controller → Camera | Discovery broadcast + TLV response                               |
| 443   | HTTPS    | Controller → Camera | `/api/1.2/manage` adoption endpoint                              |
| 7442  | WSS      | Camera → Controller | Primary AVClient handshake (token auth + settings delivery)      |
| 7550  | WSS *(WIP)* | Camera ↔ Controller | uPFLV stream channel (camera-initiated: metadata/video/audio up, control/SPS down)|

To inspect local sockets during development:
```
ss -tnp | grep 'replace_with_your_camera_IP'
```

> **Note:** UniFi OS often binds these listeners on `tcp6`. When `/proc/sys/net/ipv6/bindv6only` is `0`, IPv4 cameras can still connect via IPv4-mapped addresses (`::ffff:192.168.x.y`). Otherwise expose dedicated IPv4 listeners.

# UniFi Camera FLV & extendedFlv Stream Format

This document explains the structure of UniFi Protect's camera video stream format. Protect wraps standard FLV tags in a tiny “uP” header before handing control to the FLV parser, so cameras must prefix every connection with the UniFi signature.

---

## ⚙️ UniFi uP Header + FLV Header

| Bytes     | Field        | Description                                      |
| --------- | ------------ | ------------------------------------------------ |
| 0x00-0x09 | `DE 19 16 15 47 17 DE 19 16 75 50` | UniFi “uPFLV” magic prefix (controller validates this before parsing FLV) |
| 0x0A-0x0C | `46 4C 56`   | Literal “FLV”                                    |
| 0x0D      | Version      | `0x01`                                           |
| 0x0E      | Flags        | `0x07` = has audio + video + data               |
| 0x0F-0x12 | Header Size  | `0x00000009`                                     |
| 0x13-0x16 | PrevTagSize0 | `0x00000000`                                     |


---

## 🧹 Script Tags Observed on Real Streams

Immediately after the FLV header the camera emits a series of `0x12` script tags that describe the stream and keep the controller synchronized.

### `onMetaData`
Encodes basic stream descriptors in AMF:

| Field             | Example from capture                 | Notes                                    |
|-------------------|--------------------------------------|------------------------------------------|
| `audioBandwidth`  | `0x40EF400000000000` (~119000.0)     | Nominal AAC bitrate in bps                |
| `audioChannels`   | `0x3FF0000000000000` (2)             | Mono/Stereo indicator                     |
| `audioFrequency`  | `0x40E7700000000000` (48 kHz)        | Sample rate                               |
| `channelId`       | `0x4000000000000000` (2)             | Protect’s logical channel index           |
| `extendedFormat`  | `true`                               | Marks the stream as uP extendedFlv        |
| `hasAudio/hasVideo` | `true`                           | Flags consumed by the controller UI       |
| `streamId`        | `0x4000000000000000` (2)             | Mirrors `channelId`                       |
| `streamName`      | `E0nB5LcAqWiOYjDL`                   | Randomly assigned per session             |
| `videoBandwidth`  | `0x413E848000000000` (~499200.0)     | Target H.264 bitrate                      |
| `videoFps`        | `0x4038000000000000` (24 fps)        | Frame rate                                |
| `videoHeight`     | `0x4086800000000000` (1040)          | Height in pixels (device-dependent)       |
| `videoWidth`      | `0x4094000000000000` (1280)          | Width in pixels                           |

### `onMpma`
Protect’s per-module stats channel. Each key (`cs`, `m`, `r`, `sp`, `t`) contains `cur`, `max`, `min` doubles describing current/max/min bitrates for capture, motion, recording, streaming, and talkback.

### `onClockSync`
Provides timing info so Protect can align frames:

| Field            | Description                          |
|------------------|--------------------------------------|
| `streamClock`    | Running presentation timestamp       |
| `streamClockBase`| Offset used to create absolute times |
| `wallClock`      | Unix time (double) generated by camera |

---

## 🎮 Video & Audio Tag Format

The capture shows the first `0x09` (video) tag immediately after `onClockSync`. Its payload begins with the AVC video packet header:

```
09                ; Tag type = video
00 00 0061        ; Data size (0x61 bytes following)
00 01 5f90        ; Timestamp + extension
00 00 00          ; Stream ID
17                ; FrameType=1 (keyframe), CodecID=7 (AVC)
00                ; AVCPacketType=0 (sequence header)
00 00 00          ; Composition time
ff e1 00 1d ...   ; avcC blob (SPS/PPS) and first IDR NALU
```

Subsequent video tags will set `AVCPacketType=1` and contain raw H.264 NALUs. Audio tags follow the standard FLV AAC framing (`0x08` tag, AACPacketType + payload). They are not present in the first 1024 bytes above but show up later in the same session when the camera enables talkback/microphone.

---

## 🧠 Motion & Event Tags

Motion and smart-detect metadata are also delivered via `0x12` script tags. They were not present in the first kilobyte of the sample capture, but production cameras interleave messages such as:

```json
["event", {"motion": true, "zone": "default"}]
```

between video/audio frames whenever activity is detected.

---

## ✅ Tag Summary

| Tag Type | Purpose           | Format     | Notes                              |
| -------- | ----------------- | ---------- | ---------------------------------- |
| `0x12`   | Metadata / Events | AMF0       | `onMetaData`, `onMpma`, `onClockSync`, motion events |
| `0x08`   | Audio             | AAC        | Appears once microphone/talkback is active          |
| `0x09`   | Video             | H.264/AVC  | Keyframes (`AVCPacketType=0`) and delta frames (`=1`) |

---

## 🧪 Stream Notes

- Real sessions start with the UniFi header + FLV header, `onMetaData`, `onMpma`, and `onClockSync` before any video frames.
- Video tags use AVC NALUs exactly as FLV expects; Protect does not wrap them in any extra framing beyond the initial uP prefix.
- Audio tags are optional and appear only when the driver advertises `hasAudio`.
- Controllers can toggle audio dynamically (`suppressAudio` setting) and expect cameras to stop emitting `0x08` tags immediately.

### Sample Hex Stream Breakdown (Real Camera → Controller)

The capture below shows the first 0x200 bytes of an actual Protect session. The UniFi prefix is present (`DE 19 16 15 … 75 50`), followed by the FLV header and `onMetaData`, `onMpma`, `onClockSync`, and AVC sequence headers.

```hex
de 19 16 15 47 17 de 19 16 75 50 46 4c 56 01 07 00 00 00 09
00 00 00 00 12 00 01 22 00 00 00 00 00 00 00 02 00 0a 6f 6e
4d 65 74 61 44 61 74 61 03 00 0e 61 75 64 69 6f 42 61 6e 64
77 69 64 74 68 00 40 ef 40 00 00 00 00 00 00 0d 61 75 64 69
6f 43 68 61 6e 6e 65 6c 73 00 3f f0 00 00 00 00 00 00 0e 61
75 64 69 6f 46 72 65 71 75 65 6e 63 79 00 40 e7 70 00 00 00
00 00 00 09 63 68 61 6e 6e 65 6c 49 64 00 40 00 00 00 00 00
00 00 00 0e 65 78 74 65 6e 64 65 64 46 6f 72 6d 61 74 01 01
00 08 68 61 73 41 75 64 69 6f 01 01 00 08 68 61 73 56 69 64
65 6f 01 01 00 08 73 74 72 65 61 6d 49 64 00 40 00 00 00 00
00 00 00 00 0a 73 74 72 65 61 6d 4e 61 6d 65 02 00 10 45 30
6e 42 35 4c 63 41 71 57 69 4f 59 6a 44 4c 00 0e 76 69 64 65
6f 42 61 6e 64 77 69 64 74 68 00 41 3e 84 80 00 00 00 00 00
... (metadata continues)
00 00 00 00 00 17 00 00 00 00 01 4d 40 1f ff e1 00 1d 67 4d
40 1f 8d 8d 40 28 02 df f8 0b 70 10 10 14 00 00 0f a0 00 02
ee 02 76 82 21 1a 80 01 00 04 68 ee 38 80 ... (AVC SPS/PPS + keyframe)
```

Key sections:
- **0x0000–0x000A**: UniFi uPFLV prefix (`DE 19 16 15 47 17 DE 19 16 75 50`).
- **0x000B–0x0016**: FLV header (`FLV`, version `0x01`, flags `0x07`, header size `0x00000009`).
- **0x0017–0x00F8**: `onMetaData` AMF blob (audio/video bandwidth, FPS, resolution, stream name `E0nB5LcAqWiOYjDL`).
- **0x0150–0x01B8**: `onMpma` metadata (current/max/min bitrates).
- **0x0220–0x0280**: `onClockSync` metadata (`streamClock`, `wallClock`).
- **0x0290–0x02FF**: AVC sequence header (`avcC`, SPS `67 4D 40 1F …`, PPS `68 EE 38 80`).
- **0x0300+**: Start of keyframe data (H.264 IDR NALUs).

---

## Donations

If you would like to make a donation to support development, please use [Github Sponsors](https://github.com/sponsors/keshavdv).
