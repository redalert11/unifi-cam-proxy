## WSS Manager Documentation Outline (Draft)

### Index
1. [Introduction](#introduction)
2. [Scope and Non-Goals](#scope-and-non-goals)
3. [Prerequisites](#prerequisites)
4. [WSS Connection Information](#wss-connection-information)
5. [Message Flow](#message-flow)
6. [WSS Functions](#wss-functions)
7. [Definitions](#definitions)
8. [Error Handling](#error-handling)
9. [Logging and Tracing](#logging-and-tracing)
10. [Versioning and Compatibility](#versioning-and-compatibility)
11. [Test Strategy](#test-strategy)
12. [Known Gaps](#known-gaps)

### Introduction
`wss_manager` simulates a UniFi Protect AVClient camera over WSS (7442). It builds the
hello handshake, responds to controller commands, and emits minimal camera events.

### Scope and Non-Goals
- Covers: message formats, expected responses, logging, and tests.
- Does not cover: video streaming, ONVIF, or controller UI behavior.

### Prerequisites
- Settings file per camera (repo-specific path).
- Web service for control and logs (repo-specific entrypoint).
- Optional replay traces (local capture directory).

### WSS Connection Information
- Endpoint: `wss://192.0.2.10:7442/camera/1.0/ws?token=kwriu7hRFqfQPR5yx9lcSmGjX61q8TNH`
- Subprotocol: `secure_transfer`
- Headers:
  - `Camera-Mac`: lowercase, no colons (example: `68d79ae592d3`)
  - `Camera-Model`: hex model id (example: `0xa573`)

### Message Flow
1. Camera connects to WSS and sends `ubnt_avclient_hello`.
2. Controller replies with `ubnt_avclient_hello`.
3. Controller sends `ubnt_avclient_paramAgreement`; camera responds with `authToken`.
4. Controller pushes settings changes (video/isp/etc).

### WSS Functions
- Each function has a spec section with payload and response schema.
- Functions are grouped by direction ([Camera -> Controller](#controller-handled-camera---controller), [Controller -> Camera](#controller-sent-controller---camera)).

#### Controller-handled (Camera -> Controller)
(from controller message handlers)
(refresh this list from the controller message handler switch when it changes)
- [`ubnt_avclient_hello`](#ubnt_avclient_hello): hello handshake payload.
- [`ubnt_avclient_timeSync`](#ubnt_avclient_timesync): controller time sync.
- [EventSmartDetect](#eventsmartdetect): smart detection events.
- [EventSmartAudio](#eventsmartaudio): audio alarm events.
- [EventSmartMotion](#eventsmartmotion): motion events.
- EventAnalytics: analytics events (motion path).
- MCUEventMessage: doorbell and MCU events.
- EventUpdateFirmwareFailure: firmware update failure status.
- [EventUpdateFirmwareStatus](#eventupdatefirmwarestatus): firmware update progress.
- EventIspSceneStatus: day/night scene updates.
- EventFeatureFlagsUpdated: feature flags refresh.
- EventTransferRequestStatus: transfer failure status.
- EventIspSettingsChanged: ISP settings changed event.
- EventPinReset: pin reset event.
- EventPoorNetwork: poor network status.
- EventEnclosureAttached: enclosure attach state.
- EventDoorAccessHubMac: access hub MAC update.
- EventAccess: access event.
- NotifyLensProtectLevel: lens thermal alarm level.
- NotifyTiltProtectLevel: tilt thermal alarm level.
- PtMotorFaultStatus: PT motor fault status.
- NotifyTiltLimits: tilt limit update.
- EventAccessSettingsChanged: access settings update.
- EventNfcCardScan: NFC card scan event.
- EventIdentifyUserStatus: fingerprint identify status.
- EventEnrollFingerprintStatus: fingerprint enroll status.
- s.PtzUpdateFunctionName.EventCommandStatus: PTZ command status.
- s.PtzUpdateFunctionName.EventMotorState: PTZ motor state.
- EventAIPortStatus: AI Port streaming status.
- b.CAMERA_EVENT.EVENT_SD_CARD_MOUNTED: SD card mounted.
- b.CAMERA_EVENT.EVENT_SD_CARD_UNMOUNTED: SD card unmounted.
- b.CAMERA_EVENT.EVENT_SD_CARD_INSERTED: SD card inserted.
- b.CAMERA_EVENT.EVENT_SD_CARD_REMOVED: SD card removed.
- b.CAMERA_EVENT.MANUAL_POI_RESPONSE: manual POI response.
- b.CAMERA_EVENT.EVENT_RESET_ISP_SETTINGS: reset ISP settings.
- b.CAMERA_EVENT.DEFER_CHANGE_VIDEO_SETTINGS: deferred video settings update.

#### Controller-sent (Controller -> Camera)
(from controller message routing + traces)
- ubnt_avclient_hello: controller hello response.
- [`ubnt_avclient_paramAgreement`](#ubnt_avclient_paramagreement): param agreement + auth token.
- [`ubnt_avclient_timeSync`](#ubnt_avclient_timesync): controller time sync.
- Adopt: adoption request.
- TryAdopt: adoption retry request.
- ChangeAnalyticsSettings: analytics configuration.
- ChangeAudioEventsSettings: audio event settings update.
- ChangeClarityZones: clarity zone configuration.
- ChangeDeviceSettings: device-level settings.
- ChangeInterfaceSettings: UI/interface settings.
- [`ChangeIspSettings`](#changeispsettings): ISP settings update.
- ChangeLcmGuiSettings: LCD GUI configuration.
- ChangeOsdSettings: on-screen display settings.
- ChangeSmartDetectSettings: smart detect configuration.
- ChangeSmartMotionSettings: smart motion configuration.
- ChangeSoundLedSettings: sound/LED configuration.
- ChangeTalkbackSettings: talkback audio configuration.
- [`ChangeVideoSettings`](#changevideosettings): controller video configuration update.
- CustomAnimOperation: custom animation operation.
- DoorAccessGetHubMac: request door access hub MAC.
- GetACVoltage: request AC voltage.
- GetEdgeRecordingFiles: request edge recording file list.
- GetIlluminance: request illuminance value.
- GetRequest: snapshot or upload request.
- GetSystemStats: request system stats.
- M43StatusLV: request M43 status.
- NetworkStatus: request for network status report.
- PlayState: request playback state.
- PutRequest: upload request.
- Reboot: reboot request.
- ResetToDefaults: factory reset request.
- ScanAP: WiFi AP scan request.
- SetChimeDuration: chime duration update.
- StartService: service start request.
- StopService: service stop request (e.g., ssh).
- TestAndApplyNetworkSettings: network settings test/apply.
- TurnOnColorNightVision: color night vision request.
- TurnOnFlashlightBriefly: flashlight request.
- UpdateFaceDBRequest: face DB update request.
- [UpdateFirmwareRequest](#updatefirmwarerequest): firmware update request.
- UpdateUsernamePassword: credentials update.
  
#### ubnt_avclient_hello
Overview
Use this message to initiate the camera handshake over WSS. The controller validates
`semver` and responds with `ubnt_avclient_hello` followed by `ubnt_avclient_paramAgreement`.

Direction
Camera -> Controller

Expected Response
Controller replies with `ubnt_avclient_hello` and then `ubnt_avclient_paramAgreement`.

Payload Schema
- `adoptionCode` (string)
- `connectionHost` (string)
- `connectionSecurePort` (number)
- `features` (object)
- `fwVersion` (string)
- `semver` (string, semver-compatible)
- `hwrev` (number)
- `ip` (string)
- `mac` (string, uppercase, no colons)
- `model` (string)
- `name` (string)
- `protocolVersion` (number)
- `rebootTimeoutSec` (number)
- `upgradeTimeoutSec` (number)
- `uptime` (number)

Default Values
- `adoptionCode`: `""`
- `connectionSecurePort`: `7442`
- `rebootTimeoutSec`: `30`
- `upgradeTimeoutSec`: `150`
- `uptime`: `0`

Errors / Failure Modes
- Invalid `semver` causes controller handshake failure.

Example
```json
{"from":"ubnt_avclient","to":"UniFiVideo","functionName":"ubnt_avclient_hello","messageId":0,"inResponseTo":0,"payload":{"adoptionCode":"","connectionHost":"192.0.2.10","connectionSecurePort":7442,"features":{},"fwVersion":"UVC.S5L.v5.1.217","semver":"5.1.217","hwrev":10,"ip":"192.0.2.84","mac":"68D79AE592D3","model":"UVC G4 Dome","name":"G4 Dome","protocolVersion":67,"rebootTimeoutSec":30,"upgradeTimeoutSec":150,"uptime":0}}
```

Tests
- Replay: use a local trace replay tool.
- Live: start the web control service, then `POST /api/unifi/wss/start`

Evidence
- Local WSS raw dump (per-camera).

#### ubnt_avclient_paramAgreement
Overview
Use this message to complete handshake negotiation after hello. The controller sends
connection parameters and expects the camera to respond with an auth token.

Direction
Controller -> Camera

Expected Response
Camera replies with `ubnt_avclient_paramAgreement` containing `authToken`.

Payload Schema
| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| enableStatusCodes | boolean | yes | Enables statusCode in replies. |
| useHeartbeats | boolean | yes | Enables heartbeat loop. |
| heartbeatsTimeoutMs | number | yes | Heartbeat timeout in ms. |

Response Schema
| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| authToken | string | yes | Token used for WSS auth. |

Default Values
- `enableStatusCodes`: `true`
- `useHeartbeats`: `false`
- `heartbeatsTimeoutMs`: `60000`

Errors / Failure Modes
- Missing `authToken` response causes handshake failure.

Example (Controller -> Camera)
```json
{"from":"UniFiVideo","to":"ubnt_avclient","responseExpected":true,"functionName":"ubnt_avclient_paramAgreement","messageId":10156,"inResponseTo":0,"payload":{"enableStatusCodes":true,"useHeartbeats":false,"heartbeatsTimeoutMs":60000}}
```

Example (Camera -> Controller)
```json
{"from":"ubnt_avclient","to":"UniFiVideo","functionName":"ubnt_avclient_paramAgreement","messageId":78249995,"inResponseTo":10156,"payload":{"authToken":"kwriu7hRFqfQPR5yx9lcSmGjX61q8TNH"}}
```

Tests
- Replay: use a local trace replay tool.
- Live: start the web control service, then `POST /api/unifi/wss/start`

Evidence
- Observed controller trace (local capture).

#### ubnt_avclient_timeSync
Overview
Use this message to synchronize controller time with the camera. The controller sends
timestamps and the camera replies with `timeDelta`.

Direction
Controller -> Camera

Expected Response
Camera replies with `ubnt_avclient_timeSync` containing `timeDelta`.

Payload Schema
| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| t1 | number | yes | Controller timestamp (ms). |
| t2 | number | yes | Controller timestamp (ms). |

Response Schema
| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| timeDelta | number | yes | Time offset in ms (0 if unknown). |

Default Values
- `timeDelta`: `0`

Errors / Failure Modes
- None observed; camera can reply with `timeDelta: 0`.

Example (Controller -> Camera)
```json
{"from":"UniFiVideo","to":"ubnt_avclient","responseExpected":false,"functionName":"ubnt_avclient_timeSync","messageId":10144,"inResponseTo":78249984,"payload":{"t1":1766543803159,"t2":1766543803159}}
```

Example (Camera -> Controller)
```json
{"from":"ubnt_avclient","to":"UniFiVideo","functionName":"ubnt_avclient_timeSync","messageId":78249985,"inResponseTo":10144,"payload":{"timeDelta":0}}
```

Tests
- Replay: use a local trace replay tool.
- Live: start the web control service, then `POST /api/unifi/wss/start`

Evidence
- Observed controller trace (local capture).

#### EventSmartDetect
Overview
Use this message to send smart detection events (person/vehicle/animal/package) from
camera to controller.

Direction
Camera -> Controller

Expected Response
None (controller consumes events asynchronously).

Supported edgeType Values
From controller message handlers:
- `enter`
- `moving`
- `leave`
- `packageDetected`
- `none` (raw/noise event used for insights)

Supported objectTypes Values
From controller message handlers:
- `person`
- `vehicle`
- `package`
- `licensePlate`
- `face`
- `animal`

Payload Schema
| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| edgeType | string | yes | Event type (see supported values). |
| eventId | number | no | Event sequence id. |
| objectTypes | array | no | Detected object types. |
| descriptors | array | no | Object descriptors with `coord`, `confidenceLevel`, etc. |
| zonesStatus | object | no | Zone state per zone id. |
| clockWall | number | no | Wall clock timestamp (ms). |
| clockMonotonic | number | no | Monotonic clock (ms). |
| clockStream | number | no | Stream clock (ms). |
| clockStreamRate | number | no | Stream clock rate. |
| displayTimeoutMSec | number | no | UI display timeout. |
| smartDetectSnapshots | array | no | Snapshot metadata list. |
| smartDetectSnapshotFullFoV | string | no | Full FoV snapshot filename. |

Errors / Failure Modes
- Missing or unknown `edgeType` may be ignored by the controller.

Example
```json
{"from":"ubnt_avclient","to":"UniFiVideo","functionName":"EventSmartDetect","messageId":1001,"inResponseTo":0,"payload":{"edgeType":"enter","eventId":1,"objectTypes":["person"],"clockWall":1766543821684,"descriptors":[{"confidenceLevel":88,"coord":[470,386,236,591]}],"zonesStatus":{"2":{"level":60,"status":"enter"}}}}
```

Tests
- Replay: use a local trace replay tool.
- Live: start the web control service, then `POST /api/unifi/wss/start`

Evidence
- Observed controller trace (local capture).

#### EventSmartMotion
Overview
Use this message to send motion events from camera to controller. The controller
maps the combination of `eventType` and `edgeType` into motion topics.

Direction
Camera -> Controller

Expected Response
None (controller consumes events asynchronously).

Supported eventType Values
From controller message handlers:
- `pulse` (translated to `motion.pulse`)
- `motion` (combined with `edgeType` to form `motion.start`/`motion.stop`)

Supported edgeType Values
Observed in traces:
- `start`
- `stop`

Payload Schema
| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| eventType | string | yes | See supported values. |
| edgeType | string | yes | See supported values. |
| clockWall | number | no | Wall clock timestamp (ms). |
| clockMonotonic | number | no | Monotonic clock (ms). |
| zones | object | no | Motion zones or region data. |

Errors / Failure Modes
- Unknown `eventType`/`edgeType` combinations may be ignored by the controller.

Example
```json
{"from":"ubnt_avclient","to":"UniFiVideo","functionName":"EventSmartMotion","messageId":1101,"inResponseTo":0,"payload":{"eventType":"motion","edgeType":"start","clockWall":1766543821684}}
```

Tests
- Replay: use a local trace replay tool.
- Live: start the web control service, then `POST /api/unifi/wss/start`

Evidence
- Observed controller trace (local capture).

#### EventSmartAudio
Overview
Use this message to send audio alarm events (smoke, siren, baby cry, etc.) from
camera to controller.

Direction
Camera -> Controller

Expected Response
None (controller consumes events asynchronously).

Supported audioTypes Values
From controller message handlers:
- `alrmSmoke`
- `alrmCmonx`
- `alrmSiren`
- `alrmBabyCry`
- `alrmSpeak`
- `alrmBark`
- `alrmBurglar`
- `alrmCarHorn`
- `alrmGlassBreak`

Payload Schema
| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| alrmSmoke | string | no | Alarm state (example: `none`). |
| alrmCmonx | string | no | Alarm state (example: `none`). |
| alrmSiren | string | no | Alarm state (example: `none`). |
| alrmBabyCry | string | no | Alarm state (example: `none`). |
| alrmSpeak | string | no | Alarm state (example: `none`). |
| alrmBark | string | no | Alarm state (example: `none`). |
| alrmBurglar | string | no | Alarm state (example: `none`). |
| alrmCarHorn | string | no | Alarm state (example: `none`). |
| alrmGlassBreak | string | no | Alarm state (example: `none`). |

Errors / Failure Modes
- If all audio fields are `none`, the controller ignores the event.

Example
```json
{"from":"ubnt_avclient","to":"UniFiVideo","functionName":"EventSmartAudio","messageId":1201,"inResponseTo":0,"payload":{"alrmBabyCry":"detected","alrmSmoke":"none","alrmCmonx":"none"}}
```

Tests
- Replay: use a local trace replay tool.
- Live: start the web control service, then `POST /api/unifi/wss/start`

Evidence
- Observed controller trace (local capture).

#### EventUpdateFirmwareStatus
Overview
Use this message to report firmware update progress from camera to controller.

Direction
Camera -> Controller

Expected Response
None (controller consumes status updates asynchronously).

Supported status Values
From controller message handlers:
- `FW_DOWNLOADING`
- `FW_UPDATING`

Payload Schema
| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| status | string | yes | Firmware update status. |

Errors / Failure Modes
- Unknown `status` values may be ignored by the controller.

Example
```json
{"from":"ubnt_avclient","to":"UniFiVideo","functionName":"EventUpdateFirmwareStatus","messageId":1301,"inResponseTo":0,"payload":{"status":"FW_DOWNLOADING"}}
```

Tests
- Replay: use a local trace replay tool.
- Live: start the web control service, then `POST /api/unifi/wss/start`

Evidence
- Observed controller trace (local capture).

#### ChangeVideoSettings
Overview
Use this message to update camera video stream configuration.

Direction
Controller -> Camera

Expected Response
Camera replies with `ChangeVideoSettings` status and echoed settings.

Payload Schema
| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| video | object | yes | Stream configuration container. |
| video.video3 | object | no | Example stream definition. |
| video.video3.avSerializer | object | no | Serializer settings. |
| video.video3.avSerializer.type | string | no | Example: `extendedFlv`. |
| video.video3.avSerializer.parameters | object | no | Serializer parameters. |
| video.video3.avSerializer.parameters.streamName | string | no | Stream name. |
| video.video3.avSerializer.parameters.withOpus | boolean | no | Enable Opus audio. |
| video.video3.avSerializer.parameters.opusSampleRate | number | no | Opus sample rate. |
| video.video3.avSerializer.destinations | array | no | Output destinations. |
| video.video3.type | string | no | Video codec (e.g., `h264`). |

Response Schema
| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| statusCode | number | yes | 0 on success. |
| status | string | yes | `ok` on success. |
| deviceID | string | yes | Camera MAC (no colons). |
| videoMode | string | no | Example: `default`. |
| hdrMode | string | no | Example: `off`. |
| downScaleMode | string | no | Example: `original`. |

Default Values
- `videoMode`: `default`
- `hdrMode`: `off`
- `downScaleMode`: `original`

Errors / Failure Modes
- Invalid payload shape may trigger controller retries or WSS close.

Example (Controller -> Camera)
```json
{"from":"UniFiVideo","to":"ubnt_avclient","responseExpected":false,"functionName":"ChangeVideoSettings","messageId":10157,"inResponseTo":0,"payload":{"video":{"video3":{"avSerializer":{"type":"extendedFlv","parameters":{"streamName":"cam_g4_dome_720p","withOpus":true,"opusSampleRate":24000},"destinations":["tcp://192.0.2.10:7550?retryInterval=1&connectTimeout=5"]},"type":"h264"}}}}
```

Example (Camera -> Controller)
```json
{"from":"ubnt_avclient","to":"UniFiVideo","functionName":"ChangeVideoSettings","messageId":3,"inResponseTo":10157,"payload":{"statusCode":0,"status":"ok","deviceID":"<mac>","videoMode":"default","hdrMode":"off","downScaleMode":"original"}}
```

Tests
- Replay: use a local trace replay tool.
- Live: start the web control service, then `POST /api/unifi/wss/start`

Evidence
- Observed controller trace (local capture).

#### ChangeIspSettings
Overview
Use this message to update or query camera ISP settings. If the controller sends an
empty payload, treat it as a request for the current state.

Direction
Controller -> Camera

Expected Response
Camera replies with `ChangeIspSettings` payload plus `statusCode`, `status`, and
`deviceID`. When the request payload is empty, reply with the cached last ISP payload
or defaults.

Request Handling
- Empty payload: return cached `lastReceived.ChangeIspSettings` if present; otherwise
  reply with `DEFAULT_CHANGE_ISP_PAYLOAD` and persist it as the last received state.
- Non-empty payload: apply fields to the ISP driver, persist the payload as
  `lastReceived.ChangeIspSettings`, and reply with the applied payload.

Payload Schema
| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| aeMode | string | no | Auto exposure mode (example: `auto`). |
| autoFreq | number | no | Anti-flicker frequency (example: `60`). |
| awbAlgoMethod | string | no | Auto white balance mode. |
| hdrMode | string | no | HDR setting (example: `normal`). |
| isDayMode | number | no | 1 = day, 0 = night. |
| wdr | number | no | Wide dynamic range toggle. |
| irLedMode | string | no | IR LED mode (example: `manual`). |
| brightness | number | no | 0-100. |
| contrast | number | no | 0-100. |
| saturation | number | no | 0-100. |
| sharpness | number | no | 0-100. |
| flip | number | no | 1 = flip, 0 = normal. |
| mirror | number | no | 1 = mirror, 0 = normal. |
| touchFocusX | number | no | Focus X coordinate. |
| touchFocusY | number | no | Focus Y coordinate. |
| zoomPosition | number | no | Digital zoom position. |

Response Schema
| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| statusCode | number | yes | 0 on success. |
| status | string | yes | `ok` on success. |
| deviceID | string | yes | Camera MAC (no colons). |
| ...payload | object | yes | Echoed ISP payload (applied or cached). |

Default Values
- Defaults are taken from the implementation constant `DEFAULT_CHANGE_ISP_PAYLOAD`.

Errors / Failure Modes
- Empty payload with no cached state returns defaults; controller may retry if missing
  expected fields.

Example (Controller -> Camera, request current state)
```json
{"from":"UniFiVideo","to":"ubnt_avclient","responseExpected":true,"functionName":"ChangeIspSettings","messageId":24715,"inResponseTo":0,"payload":{}}
```

Example (Camera -> Controller, response)
```json
{"from":"ubnt_avclient","to":"UniFiVideo","functionName":"ChangeIspSettings","messageId":5,"inResponseTo":24715,"payload":{"statusCode":0,"status":"ok","deviceID":"<mac>","aeMode":"auto","autoFreq":60,"awbAlgoMethod":"advanced","hdrMode":"normal","isDayMode":1,"wdr":1,"irLedMode":"manual","brightness":50,"contrast":50,"saturation":50,"sharpness":50}}
```

Tests
- Replay: use a local trace replay tool.
- Live: start the web control service, then `POST /api/unifi/wss/start`

Evidence
- Local WSS raw dump (per-camera).

#### UpdateFirmwareRequest
Overview
Use this message to request a firmware update from the camera. The controller
provides a download URI and checksum metadata.

Direction
Controller -> Camera

Expected Response
None in observed traces (`responseExpected: false`). If `responseExpected: true`,
reply with status fields indicating acceptance or failure.

Payload Schema
| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| uri | string | yes | Firmware download URL. |
| timeoutMs | number | yes | Update timeout in ms. |
| md5 | string | yes | Firmware checksum. |
| fwPath | string | no | Controller-side firmware path. |

Errors / Failure Modes
- Missing `uri` or invalid checksum should result in a non-zero status reply when
  `responseExpected` is true.

Example (Controller -> Camera)
```json
{"from":"UniFiVideo","to":"ubnt_avclient","responseExpected":false,"functionName":"UpdateFirmwareRequest","messageId":16767,"inResponseTo":0,"payload":{"uri":"https://<controller_ip>:7444/internal/update?platform=s5l&product=uvc&updateType=firmware&version=5.1.217&mac=<mac>","timeoutMs":600000,"md5":"4dc0449f9a09e733d27f517f88de9821","fwPath":"/srv/unifi-protect/downloads/firmware.bin"}}
```

Tests
- Replay: use a local trace replay tool.
- Live: start the web control service, then `POST /api/unifi/wss/start`

Evidence
- Observed controller trace (local capture).

### Definitions
- `semver`: `X.Y.Z` format used by controller semver checks. Example: `5.1.217`.
- `fwVersion`: full firmware string. Example: `UVC.S5L.v5.1.217`.
- `protocolVersion`: WSS protocol integer. Example: `67`.
- `authToken`: token returned in `ubnt_avclient_paramAgreement`. Example: `kwriu7hRFqfQPR5yx9lcSmGjX61q8TNH`.
- `enableStatusCodes`: controller requests status codes in replies. Example: `true`.
- `useHeartbeats`: controller toggles heartbeat loop. Example: `false`.
- `heartbeatsTimeoutMs`: heartbeat timeout in milliseconds. Example: `60000`.
- `t1`: controller time (ms) used in time sync. Example: `1766543803159`.
- `t2`: controller time (ms) used in time sync. Example: `1766543803159`.
- `timeDelta`: camera time offset (ms) returned to controller. Example: `0`.
- `streamName`: stream identifier used by serializers. Example: `cam_g4_dome_720p`.
- `withOpus`: enable Opus audio in stream. Example: `true`.
- `opusSampleRate`: Opus sample rate. Example: `24000`.
- `destinations`: output destinations for serializers. Example: `["tcp://192.0.2.10:7550?retryInterval=1&connectTimeout=5"]`.
- `edgeType`: smart detect event edge type. Example: `enter`.
- `objectTypes`: detected object type list. Example: `["person"]`.
- `eventType`: motion event type. Example: `motion`.
- `alrmSmoke`: smart audio alarm state. Example: `none`.
- `alrmCmonx`: smart audio alarm state. Example: `none`.
- `alrmSiren`: smart audio alarm state. Example: `none`.
- `alrmBabyCry`: smart audio alarm state. Example: `detected`.
- `alrmSpeak`: smart audio alarm state. Example: `none`.
- `alrmBark`: smart audio alarm state. Example: `none`.
- `alrmBurglar`: smart audio alarm state. Example: `none`.
- `alrmCarHorn`: smart audio alarm state. Example: `none`.
- `alrmGlassBreak`: smart audio alarm state. Example: `none`.
- `status`: firmware update status. Example: `FW_DOWNLOADING`.
- `aeMode`: auto exposure mode. Example: `auto`.
- `autoFreq`: anti-flicker frequency in Hz. Example: `60`.
- `awbAlgoMethod`: auto white balance mode. Example: `advanced`.
- `hdrMode`: HDR mode. Example: `normal`.
- `isDayMode`: day/night flag (1 = day). Example: `1`.
- `wdr`: wide dynamic range flag. Example: `1`.
- `irLedMode`: IR LED mode. Example: `manual`.
- `brightness`: ISP brightness level. Example: `50`.
- `contrast`: ISP contrast level. Example: `50`.
- `saturation`: ISP saturation level. Example: `50`.
- `sharpness`: ISP sharpness level. Example: `50`.
- `flip`: image flip flag. Example: `0`.
- `mirror`: image mirror flag. Example: `0`.
- `touchFocusX`: focus X coordinate. Example: `512`.
- `touchFocusY`: focus Y coordinate. Example: `384`.
- `zoomPosition`: digital zoom position. Example: `0`.
- `uri`: firmware download URL. Example: `https://<controller_ip>:7444/internal/update?...`.
- `timeoutMs`: firmware update timeout in ms. Example: `600000`.
- `md5`: firmware checksum. Example: `4dc0449f9a09e733d27f517f88de9821`.
- `fwPath`: controller firmware path. Example: `/srv/unifi-protect/downloads/firmware.bin`.

### Error Handling
- Invalid `semver` triggers controller handshake failure.
- Missing settings fields should be treated as hard errors (no fallback).
- WSS closes with code `4012` on handshake or provisioning failure.

### Logging and Tracing
- Local raw dump (one line per message).
- Web logs: shown in UI and terminal.
- Controller logs: controller service log directory.

### Versioning and Compatibility
- `semver` must be valid for controller parsing.
- `fwVersion` should match camera firmware pattern.

### Test Strategy
- Replay: use a local trace replay tool.
- Live: start the web control service, then POST `/api/unifi/wss/start`.

### Known Gaps
- Streaming and snapshot upload are stubbed.
- Many controller-originated settings changes are not implemented.

### Notes
- Style: technical reference / API spec (similar to protocol documentation).
- Purpose: define structure before writing full content.
- Redaction: replace real values with safe placeholders (example: `<controller_ip>`, `<camera_ip>`, `<mac>`, `<token>`, `<uuid>`, `<stream_name>`, `<controller_name>`), and keep the placeholder set consistent across the document.

### Per-Function Update Checklist
Use this checklist each time a new WSS function is added or updated:
1. Add a section under "WSS Functions" with the standard subsections (Overview, Direction, Expected Response, Payload Schema, Default Values, Errors, Example, Tests, Evidence).
2. Review the controller message handlers for expectations relevant to the function.
3. Populate the payload schema from the latest traces or controller expectations.
4. Redact real values using the Safe Placeholder Values (no real IPs, MACs, UUIDs, or tokens).
5. Update the Message Flow or Definitions if new fields are introduced.
6. Add or update Definitions entries for any new fields, using the Safe Placeholder Values for examples.
7. Add or update the Evidence entry with the trace/log source.
8. Add or update the WSS Functions list entry with a link to the section anchor.
9. Verify links in the Index and WSS Functions list point to the new section.

### Deviation Notes
- `ubnt_avclient_paramAgreement`: controller message handlers do not reveal payload fields; schema derived from observed traces.

### Safe Placeholder Values
Use these example values in all docs and snippets (random, realistic, and safe):
- `<subnet>`: `192.0.2.0/24`
- `<controller_ip>`: `192.0.2.10`
- `<camera_ip>`: `192.0.2.84`
- `<mac>`: `68D79AE592D3`
- `<mac_colon>`: `68:D7:9A:E5:92:D3`
- `<model_id>`: `0xa573`
- `<uuid>`: `a429fbf9-1464-4ab8-89a8-bfb08ff9c445`
- `<token>`: `kwriu7hRFqfQPR5yx9lcSmGjX61q8TNH`
- `<stream_name>`: `cam_g4_dome_720p`
- `<controller_name>`: `Unifi Protect`
