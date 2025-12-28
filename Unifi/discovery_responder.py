import socket
import struct
import logging

'''
13701
| **Field ID (Dec)** | **Field ID (Hex)** | **Field Name**                     | **Length**              | **Example**                            |
| ------------------ | ------------------ | ---------------------------------- | ----------------------- | -------------------------------------- |
| 1                  | `0x01`             | `HWADDR`                           | 6 bytes                 | `01:23:45:67:89:ab`                    |
| 2                  | `0x02`             | `IPINFO`                           | 10 bytes (6 MAC + 4 IP) | `01:23:45:67:89:ab 192.168.1.1`        |
| 3                  | `0x03`             | `FWVERSION`                        | Variable (string)       | `6.5.54`                               |
| 6                  | `0x06`             | `USERNAME`                         | Variable (string)       | `ubnt`                                 |
| 7                  | `0x07`             | `SALT`                             | 16 bytes                | `a3 b4 c5 ...`                         |
| 8                  | `0x08`             | `RND_CHALLENGE`                    | 16 bytes                | `f1 e2 d3 ...`                         |
| 9                  | `0x09`             | `CHALLENGE`                        | 16 bytes                | `9a 8b 7c ...`                         |
| 10                 | `0x0A`             | `UPTIME`                           | 4 bytes (uint32)        | `3600` (1 hour)                        |
| 11                 | `0x0B`             | `HOSTNAME`                         | Variable (string)       | `AP-Lobby`                             |
| 12                 | `0x0C`             | `PLATFORM`                         | Variable (string)       | `UAP-AC-LR`                            |
| 13                 | `0x0D`             | `ESSID`                            | Variable (string)       | `OfficeWiFi`                           |
| 14                 | `0x0E`             | `WMODE`                            | 1 byte                  | `1` (wired)                            |
| 15                 | `0x0F`             | `WEBUI`                            | 4 bytes                 | `0x00001f90` (8080)                    |
| 16                 | `0x10`             | `SYSTEM_ID`                        | 2 bytes                 | `0x1234`                               |
| 20                 | `0x14`             | `MODEL`                            | Variable (string)       | `UAP-AC-PRO`                           |
| 21                 | `0x15`             | `MODEL_SHORT`                      | Variable (string)       | `AC-PRO`                               |
| 23                 | `0x17`             | `MGMT_IS_DEFAULT`                  | 1 byte (bool)           | `1`                                    |
| 32                 | `0x20`             | `DEVICE_ID`                        | Variable (string)       | `f4:92:bf:12:34:56`                    |
| 38                 | `0x26`             | `CONTROLLER_ID`                    | 16 bytes (UUID)         | `550e8400-e29b-41d4-a716-446655440000` |
| 43                 | `0x2B`             | `GUID`                             | 16 bytes (UUID)         | `123e4567-e89b-12d3-a456-426614174000` |
| 44                 | `0x2C`             | `DEVICE_DEFAULT_CREDENTIALS`       | 1 byte                  | `0`                                    |
| 46                 | `0x2E`             | `ADOPTED_BY_CONTROLLER_UID`        | 16 bytes (UUID)         | `c0ffee00-1234-5678-9abc-def123456789` |
| 47                 | `0x2F`             | `PRIMARY_ADDRESS`                  | 10 bytes (MAC + IP)     | `f4:92:bf:12:34:56 192.168.1.10`       |
| 129                | `0x81`             | `CUSTOM_AIRMAX_FIELDS`             | Variable (TLV block)    | (nested)                               |
| 130                | `0x82`             | `CUSTOM_UNIFI_FIELDS`              | Variable (TLV block)    | (nested)                               |
| 131                | `0x83`             | `CUSTOM_EDGEMAX_FIELDS`            | Variable (TLV block)    | (nested)                               |
| 132                | `0x84`             | `CUSTOM_AMPLIFY_FIELDS`            | Variable (TLV block)    | (nested)                               |
| —                  | —                  | `UNIFI_SUB_FIELDS.BLE_BRIDGE_PORT` | 2 bytes (sub-TLV)       | `0x1F41` (8001)                        | 
'''


class DiscoveryResponder:
    DISCOVERY_PORT = 10001
    VERSION = 1
    CMD_INFO = 0

    def __init__(self, settings, logger=None):
        self.settings = settings
        self.log = logger or logging.getLogger("camera_app")

    def build_field(self, field_id, data: bytes) -> bytes:
        # 1 byte id, 2 byte length (big-endian), then data
        return struct.pack(">BH", field_id, len(data)) + data

    def build_response(self):
        payload = b""

        # --- PRIMARY_ADDRESS (47): MAC(6) + IP(4)
        mac_b = self.settings.mac_bytes("mac")
        ip_b  = self.settings.ip_bytes("host")
        if not mac_b or len(mac_b) != 6:
            raise ValueError("Invalid MAC bytes for PRIMARY_ADDRESS")
        if not ip_b or len(ip_b) != 4:
            raise ValueError("Invalid IP bytes for PRIMARY_ADDRESS")
        payload += self.build_field(47, mac_b + ip_b)

        # --- HWADDR (1) 6 bytes
        payload += self.build_field(1, mac_b)

        # --- HOSTNAME (11) string
        host = self.settings.get("host", "")
        payload += self.build_field(11, host.encode())

        # --- PLATFORM (12) string
        platform = self.settings.get("platform", "")
        payload += self.build_field(12, platform.encode())

        # --- WMODE (14) 1 = wired
        payload += self.build_field(14, struct.pack("B", 1))

        # --- ESSID (13) empty
        payload += self.build_field(13, b"")

        # --- FWVERSION (3) string (ok if empty)
        fw = self.settings.get("firmwareVersion", "v4.23.8")
        payload += self.build_field(3, fw.encode())

        # --- DEVICE_ID (32) string; most devices send MAC as text
        payload += self.build_field(32, self.settings.get("mac", "").encode())

        # --- UPTIME (10) uint32 BE seconds
        uptime = int(self.settings.get("uptime", 0)) & 0xFFFFFFFF
        payload += self.build_field(10, struct.pack(">I", uptime))

        # --- WEBUI (15): protocol + port (HTTPS=1, HTTP=0)
        #     >HH  (proto_flag, port)
        payload += self.build_field(15, struct.pack(">HH", 1, 443))

        # --- SYSTEM_ID (16) uint16 LE  (e.g. "0xa573")
        sysid_raw = self.settings.get("sysid")
        if sysid_raw:
            try:
                sysid_val = int(sysid_raw, 0)  # handles "0xa573" or "42355"
                payload += self.build_field(16, struct.pack("<H", sysid_val))
            except (TypeError, ValueError):
                self.log.warning("Skipping invalid sysid %r", sysid_raw)

        # --- DEVICE_DEFAULT_CREDENTIALS (44) 1 byte (optional)
        payload += self.build_field(44, struct.pack("B", 1))

        # --- CONTROLLER_ID (38) 16 bytes UUID (optional)
        cid = self.settings.get("controllerId")
        if cid:
            try:
                payload += self.build_field(38, bytes.fromhex(cid.replace("-", "")))
            except ValueError:
                self.log.warning("Invalid controllerId %r; expected hex/uuid", cid)

        # Header: version, cmd, payload length
        header = struct.pack(">BBH", self.VERSION, self.CMD_INFO, len(payload))
        return header + payload

    def start(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", self.DISCOVERY_PORT))

        listen_host = self.settings.get("host", "0.0.0.0")
        mac_addr = self.settings.get("mac", "UNKNOWN")
        self.log.info("=" * 60)
        self.log.info(f"Discovery Responder Started")
        self.log.info(f"  Listening on: 0.0.0.0:{self.DISCOVERY_PORT} (UDP)")
        self.log.info(f"  Camera IP: {listen_host}")
        self.log.info(f"  Camera MAC: {mac_addr}")
        self.log.info(f"  Can Adopt: {self.settings.get('canAdopt', True)}")
        self.log.info("=" * 60)

        while True:
            if not self.settings.get("canAdopt", True):
                self.log.warning("Exiting discovery loop because canAdopt is False.")
                break

            sock.settimeout(1.0)
            try:
                data, addr = sock.recvfrom(1024)
            except socket.timeout:
                continue

            self.log.debug(f"Received discovery from {addr} | Raw: {data.hex()}")

            # Basic match for "\x01\x00\x00\x00" (version=1, cmd=0, length=0)
            if data[:4] == b"\x01\x00\x00\x00":
                self.log.info(f"Discovery request from {addr[0]}:{addr[1]} - Sending response")
                try:
                    response = self.build_response()
                except Exception as e:
                    self.log.error(f"Failed to build discovery response: {e}")
                    continue

                self.log.debug(f"Sending discovery response: {response.hex()}")
                sock.sendto(response, addr)