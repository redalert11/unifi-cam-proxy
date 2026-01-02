"""
ONVIF SOAP client helpers (raw SOAP, no external ONVIF libs).
Common ports seen:
  - Amcrest: ONVIF on 80 (device_service/media_service paths)
  - Tapo: ONVIF on 2020 (single /onvif/service endpoint)
"""

from __future__ import annotations

import base64
import hashlib
import os
import socket
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from requests.auth import HTTPBasicAuth, HTTPDigestAuth


class OnvifSoapClient:
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        *,
        auth_mode: str = "digest",
        wsse_mode: str = "digest",
        timeout: float = 8.0,
        https: bool = False,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.auth_mode = auth_mode
        self.wsse_mode = wsse_mode
        self.timeout = timeout
        self.scheme = "https" if https else "http"

    def endpoint(self, path: str) -> str:
        return f"{self.scheme}://{self.host}:{self.port}{path}"

    def get_media_xaddr(self, device_url: str) -> Optional[str]:
        body = '    <GetServices xmlns="http://www.onvif.org/ver10/device/wsdl"><IncludeCapability>false</IncludeCapability></GetServices>\n'
        try:
            xml = self._request_versions(device_url, body, "http://www.onvif.org/ver10/device/wsdl/GetServices")
            root = ET.fromstring(xml)
            for svc in root.findall(".//{*}Service"):
                ns = svc.findtext(".//{*}Namespace", default="")
                if "/media/wsdl" in ns:
                    return svc.findtext(".//{*}XAddr", default="") or None
        except Exception:
            return None
        return None

    def get_services(self, device_url: str, include_capability: bool = False) -> List[Dict[str, Any]]:
        cap = "true" if include_capability else "false"
        body = f'    <GetServices xmlns="http://www.onvif.org/ver10/device/wsdl"><IncludeCapability>{cap}</IncludeCapability></GetServices>\n'
        xml = self._request_versions(device_url, body, "http://www.onvif.org/ver10/device/wsdl/GetServices")
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            return []
        services = []
        for svc in root.findall(".//{*}Service"):
            capability = None
            cap_elem = svc.find(".//{*}Capabilities")
            if cap_elem is not None:
                capability = ET.tostring(cap_elem, encoding="unicode")
            services.append(
                {
                    "Namespace": svc.findtext(".//{*}Namespace", default=""),
                    "XAddr": svc.findtext(".//{*}XAddr", default=""),
                    "VersionMajor": svc.findtext(".//{*}Major", default=""),
                    "VersionMinor": svc.findtext(".//{*}Minor", default=""),
                    "Capabilities": capability,
                }
            )
        return services

    def walk_endpoints(self, device_url: str) -> List[Dict[str, Any]]:
        """
        Discover services and attempt a lightweight probe per endpoint.
        """
        services = self.get_services(device_url, include_capability=False)
        results: List[Dict[str, Any]] = []
        for svc in services:
            ns = svc.get("Namespace") or ""
            xaddr = svc.get("XAddr") or ""
            status = "unknown"
            detail = ""
            try:
                if ns.endswith("/device/wsdl"):
                    _ = self.get_device_info(xaddr)
                    status = "ok"
                elif ns.endswith("/media/wsdl"):
                    _ = self.get_profiles(xaddr, include_streams=False)
                    status = "ok"
                elif ns.endswith("/events/wsdl"):
                    body = '    <GetEventProperties xmlns="http://www.onvif.org/ver10/events/wsdl"/>\n'
                    _ = self._request_versions(xaddr, body, "http://www.onvif.org/ver10/events/wsdl/GetEventProperties")
                    status = "ok"
                else:
                    # Generic minimal probe: GetServiceCapabilities if available
                    body = '    <GetServiceCapabilities xmlns="http://www.onvif.org/ver10/device/wsdl"/>\n'
                    _ = self._request_versions(xaddr, body, f"{ns}/GetServiceCapabilities")
                    status = "ok"
            except Exception as exc:  # pylint: disable=broad-except
                status = "error"
                detail = str(exc)
            results.append(
                {
                    "Namespace": ns,
                    "XAddr": xaddr,
                    "status": status,
                    "error": detail,
                }
            )
        return results

    def get_scopes(self, device_url: str) -> List[str]:
        body = '    <GetScopes xmlns="http://www.onvif.org/ver10/device/wsdl"/>\n'
        xml = self._request_versions(device_url, body, "http://www.onvif.org/ver10/device/wsdl/GetScopes")
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            return []
        scopes = []
        for scope in root.findall(".//{*}Scopes"):
            val = scope.findtext(".//{*}ScopeItem", default="")
            if val:
                scopes.append(val)
        # Some cameras return ScopeItem directly under Scope
        if not scopes:
            for item in root.findall(".//{*}ScopeItem"):
                if item.text:
                    scopes.append(item.text.strip())
        return scopes

    def get_profile_claims(self, device_url: str) -> Dict[str, List[str]]:
        """
        Return profile claims derived from ONVIF scopes.
        Example: profiles=['Q'], flags=['Streaming'].
        """
        scopes = self.get_scopes(device_url)
        profiles: List[str] = []
        flags: List[str] = []
        for scope in scopes:
            if "/Profile/" not in scope:
                continue
            tail = scope.split("/Profile/", 1)[-1]
            head = tail.split("/", 1)[0]
            if head.upper() == "STREAMING":
                flags.append("Streaming")
                continue
            if head and len(head) == 1:
                profiles.append(head.upper())
        # de-dup, preserve order
        profiles = list(dict.fromkeys(profiles))
        flags = list(dict.fromkeys(flags))
        return {"profiles": profiles, "flags": flags, "scopes": scopes}

    @staticmethod
    def discover(timeout: float = 4.0) -> List[Dict[str, Any]]:
        """
        WS-Discovery probe for ONVIF devices.
        Returns list of dicts with xaddrs/scopes and source address.
        """
        message_id = f"uuid:{uuid.uuid4()}"
        probe = f"""<?xml version="1.0" encoding="utf-8"?>
<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope"
            xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing"
            xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
            xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
  <e:Header>
    <w:MessageID>{message_id}</w:MessageID>
    <w:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>
    <w:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>
  </e:Header>
  <e:Body>
    <d:Probe>
      <d:Types>dn:NetworkVideoTransmitter</d:Types>
    </d:Probe>
  </e:Body>
</e:Envelope>"""
        multicast_addr = ("239.255.255.250", 3702)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.settimeout(timeout)
        try:
            sock.sendto(probe.encode("utf-8"), multicast_addr)
        except Exception:
            sock.close()
            return []

        start = time.time()
        results: List[Dict[str, Any]] = []
        seen: set[str] = set()
        while True:
            remaining = timeout - (time.time() - start)
            if remaining <= 0:
                break
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                break
            except Exception:
                break
            if not data:
                continue
            raw = data.decode("utf-8", errors="ignore")
            try:
                root = ET.fromstring(raw)
            except ET.ParseError:
                continue
            xaddrs_text = ""
            xaddrs_elem = root.find(".//{*}XAddrs")
            if xaddrs_elem is not None and xaddrs_elem.text:
                xaddrs_text = xaddrs_elem.text.strip()
            scopes_text = ""
            scopes_elem = root.find(".//{*}Scopes")
            if scopes_elem is not None and scopes_elem.text:
                scopes_text = scopes_elem.text.strip()
            key = xaddrs_text or f"{addr[0]}:{addr[1]}"
            if key in seen:
                continue
            seen.add(key)
            results.append(
                {
                    "from": addr[0],
                    "xaddrs": xaddrs_text.split() if xaddrs_text else [],
                    "scopes": scopes_text.split() if scopes_text else [],
                }
            )
        sock.close()
        return results

    @staticmethod
    def discover_unicast(host: str, timeout: float = 4.0, port: int = 3702) -> List[Dict[str, Any]]:
        """
        WS-Discovery unicast probe to a specific host.
        """
        message_id = f"uuid:{uuid.uuid4()}"
        probe = f"""<?xml version="1.0" encoding="utf-8"?>
<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope"
            xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing"
            xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
            xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
  <e:Header>
    <w:MessageID>{message_id}</w:MessageID>
    <w:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>
    <w:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>
  </e:Header>
  <e:Body>
    <d:Probe>
      <d:Types>dn:NetworkVideoTransmitter</d:Types>
    </d:Probe>
  </e:Body>
</e:Envelope>"""
        addr = (host, port)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        try:
            sock.sendto(probe.encode("utf-8"), addr)
        except Exception:
            sock.close()
            return []
        results: List[Dict[str, Any]] = []
        try:
            data, src = sock.recvfrom(65535)
            raw = data.decode("utf-8", errors="ignore")
            root = ET.fromstring(raw)
            xaddrs_text = ""
            xaddrs_elem = root.find(".//{*}XAddrs")
            if xaddrs_elem is not None and xaddrs_elem.text:
                xaddrs_text = xaddrs_elem.text.strip()
            scopes_text = ""
            scopes_elem = root.find(".//{*}Scopes")
            if scopes_elem is not None and scopes_elem.text:
                scopes_text = scopes_elem.text.strip()
            results.append(
                {
                    "from": src[0],
                    "xaddrs": xaddrs_text.split() if xaddrs_text else [],
                    "scopes": scopes_text.split() if scopes_text else [],
                }
            )
        except socket.timeout:
            pass
        except ET.ParseError:
            pass
        finally:
            sock.close()
        return results

    def get_profiles(self, url: str, include_streams: bool = False) -> List[Dict[str, Any]]:
        body = '    <GetProfiles xmlns="http://www.onvif.org/ver10/media/wsdl"/>\n'
        xml = self._request_versions(url, body, "http://www.onvif.org/ver10/media/wsdl/GetProfiles")
        profiles = self._parse_profiles(xml)
        if include_streams:
            for prof in profiles:
                token = prof.get("token")
                if not token:
                    continue
                try:
                    prof["stream_uri"] = self.get_stream_uri(url, token)
                except Exception as exc:  # pylint: disable=broad-except
                    prof["stream_uri"] = f"error: {exc}"
                try:
                    prof["snapshot_uri"] = self.get_snapshot_uri(url, token)
                except Exception as exc:  # pylint: disable=broad-except
                    prof["snapshot_uri"] = f"error: {exc}"
        return profiles

    def get_device_info(self, url: str) -> Dict[str, Any]:
        body = '    <GetDeviceInformation xmlns="http://www.onvif.org/ver10/device/wsdl"/>\n'
        try:
            xml = self._request_versions(url, body, "http://www.onvif.org/ver10/device/wsdl/GetDeviceInformation")
            return self._parse_device_info(xml)
        except Exception as exc:  # pylint: disable=broad-except
            return {"error": str(exc)}

    def get_stream_uri(self, url: str, token: str) -> str:
        body = f"""    <GetStreamUri xmlns="http://www.onvif.org/ver10/media/wsdl">
      <StreamSetup>
        <Stream xmlns="http://www.onvif.org/ver10/schema">RTP-Unicast</Stream>
        <Transport xmlns="http://www.onvif.org/ver10/schema">
          <Protocol>RTSP</Protocol>
        </Transport>
      </StreamSetup>
      <ProfileToken>{token}</ProfileToken>
    </GetStreamUri>"""
        xml = self._request_versions(url, body, "http://www.onvif.org/ver10/media/wsdl/GetStreamUri")
        return self._parse_uri(xml)

    def get_snapshot_uri(self, url: str, token: str) -> str:
        body = f"""    <GetSnapshotUri xmlns="http://www.onvif.org/ver10/media/wsdl">
      <ProfileToken>{token}</ProfileToken>
    </GetSnapshotUri>"""
        xml = self._request_versions(url, body, "http://www.onvif.org/ver10/media/wsdl/GetSnapshotUri")
        return self._parse_uri(xml)

    def get_audio_encoder_configuration(self, url: str, token: str) -> Dict[str, Any]:
        body = f"""    <GetAudioEncoderConfiguration xmlns="http://www.onvif.org/ver10/media/wsdl">
      <ConfigurationToken>{token}</ConfigurationToken>
    </GetAudioEncoderConfiguration>"""
        xml = self._request_versions(url, body, "http://www.onvif.org/ver10/media/wsdl/GetAudioEncoderConfiguration")
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            return {}
        conf = root.find(".//{*}AudioEncoderConfiguration")
        if conf is None:
            conf = root.find(".//{*}Configuration")
        return self._parse_audio_encoder_configuration(conf) if conf is not None else {}

    def get_audio_encoder_configuration_raw(self, url: str, token: str) -> str:
        body = f"""    <GetAudioEncoderConfiguration xmlns="http://www.onvif.org/ver10/media/wsdl">
      <ConfigurationToken>{token}</ConfigurationToken>
    </GetAudioEncoderConfiguration>"""
        return self._request_versions(url, body, "http://www.onvif.org/ver10/media/wsdl/GetAudioEncoderConfiguration")

    def get_audio_encoder_configuration_options(
        self,
        url: str,
        profile_token: Optional[str] = None,
        config_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not profile_token and not config_token:
            return {}
        if profile_token:
            body = f"""    <GetAudioEncoderConfigurationOptions xmlns="http://www.onvif.org/ver10/media/wsdl">
      <ProfileToken>{profile_token}</ProfileToken>
    </GetAudioEncoderConfigurationOptions>"""
            xml = self._request_versions(url, body, "http://www.onvif.org/ver10/media/wsdl/GetAudioEncoderConfigurationOptions")
            try:
                root = ET.fromstring(xml)
                opts = root.find(".//{*}AudioEncoderConfigurationOptions") or root.find(".//{*}Options")
                parsed = self._parse_audio_encoder_options(opts) if opts is not None else {}
                if parsed:
                    return parsed
            except ET.ParseError:
                pass
        if config_token:
            body = f"""    <GetAudioEncoderConfigurationOptions xmlns="http://www.onvif.org/ver10/media/wsdl">
      <ConfigurationToken>{config_token}</ConfigurationToken>
    </GetAudioEncoderConfigurationOptions>"""
            xml = self._request_versions(url, body, "http://www.onvif.org/ver10/media/wsdl/GetAudioEncoderConfigurationOptions")
            try:
                root = ET.fromstring(xml)
                opts = root.find(".//{*}AudioEncoderConfigurationOptions") or root.find(".//{*}Options")
                return self._parse_audio_encoder_options(opts) if opts is not None else {}
            except ET.ParseError:
                return {}
        return {}

    def get_audio_encoder_configuration_options_raw(
        self,
        url: str,
        profile_token: Optional[str] = None,
        config_token: Optional[str] = None,
    ) -> str:
        if profile_token:
            body = f"""    <GetAudioEncoderConfigurationOptions xmlns="http://www.onvif.org/ver10/media/wsdl">
      <ProfileToken>{profile_token}</ProfileToken>
    </GetAudioEncoderConfigurationOptions>"""
            return self._request_versions(url, body, "http://www.onvif.org/ver10/media/wsdl/GetAudioEncoderConfigurationOptions")
        if config_token:
            body = f"""    <GetAudioEncoderConfigurationOptions xmlns="http://www.onvif.org/ver10/media/wsdl">
      <ConfigurationToken>{config_token}</ConfigurationToken>
    </GetAudioEncoderConfigurationOptions>"""
            return self._request_versions(url, body, "http://www.onvif.org/ver10/media/wsdl/GetAudioEncoderConfigurationOptions")
        return ""

    def get_audio_output_configuration(self, url: str, token: str) -> Dict[str, Any]:
        body = f"""    <GetAudioOutputConfiguration xmlns="http://www.onvif.org/ver10/media/wsdl">
      <ConfigurationToken>{token}</ConfigurationToken>
    </GetAudioOutputConfiguration>"""
        xml = self._request_versions(url, body, "http://www.onvif.org/ver10/media/wsdl/GetAudioOutputConfiguration")
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            return {}
        conf = root.find(".//{*}AudioOutputConfiguration") or root.find(".//{*}Configuration")
        return self._parse_audio_output_configuration(conf) if conf is not None else {}

    def get_audio_output_configuration_options(
        self,
        url: str,
        profile_token: Optional[str] = None,
        config_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not profile_token and not config_token:
            return {}
        if profile_token:
            body = f"""    <GetAudioOutputConfigurationOptions xmlns="http://www.onvif.org/ver10/media/wsdl">
      <ProfileToken>{profile_token}</ProfileToken>
    </GetAudioOutputConfigurationOptions>"""
            xml = self._request_versions(url, body, "http://www.onvif.org/ver10/media/wsdl/GetAudioOutputConfigurationOptions")
            try:
                root = ET.fromstring(xml)
                opts = root.find(".//{*}AudioOutputConfigurationOptions") or root.find(".//{*}Options")
                parsed = self._parse_audio_output_options(opts) if opts is not None else {}
                if parsed:
                    return parsed
            except ET.ParseError:
                pass
        if config_token:
            body = f"""    <GetAudioOutputConfigurationOptions xmlns="http://www.onvif.org/ver10/media/wsdl">
      <ConfigurationToken>{config_token}</ConfigurationToken>
    </GetAudioOutputConfigurationOptions>"""
            xml = self._request_versions(url, body, "http://www.onvif.org/ver10/media/wsdl/GetAudioOutputConfigurationOptions")
            try:
                root = ET.fromstring(xml)
                opts = root.find(".//{*}AudioOutputConfigurationOptions") or root.find(".//{*}Options")
                return self._parse_audio_output_options(opts) if opts is not None else {}
            except ET.ParseError:
                return {}
        return {}

    def get_audio_output_configuration_raw(self, url: str, token: str) -> str:
        body = f"""    <GetAudioOutputConfiguration xmlns="http://www.onvif.org/ver10/media/wsdl">
      <ConfigurationToken>{token}</ConfigurationToken>
    </GetAudioOutputConfiguration>"""
        return self._request_versions(url, body, "http://www.onvif.org/ver10/media/wsdl/GetAudioOutputConfiguration")

    def get_audio_output_configuration_options_raw(
        self,
        url: str,
        profile_token: Optional[str] = None,
        config_token: Optional[str] = None,
    ) -> str:
        if profile_token:
            body = f"""    <GetAudioOutputConfigurationOptions xmlns="http://www.onvif.org/ver10/media/wsdl">
      <ProfileToken>{profile_token}</ProfileToken>
    </GetAudioOutputConfigurationOptions>"""
            return self._request_versions(url, body, "http://www.onvif.org/ver10/media/wsdl/GetAudioOutputConfigurationOptions")
        if config_token:
            body = f"""    <GetAudioOutputConfigurationOptions xmlns="http://www.onvif.org/ver10/media/wsdl">
      <ConfigurationToken>{config_token}</ConfigurationToken>
    </GetAudioOutputConfigurationOptions>"""
            return self._request_versions(url, body, "http://www.onvif.org/ver10/media/wsdl/GetAudioOutputConfigurationOptions")
        return ""

    def get_video_encoder_configuration(self, url: str, token: str) -> Dict[str, Any]:
        body = f"""    <GetVideoEncoderConfiguration xmlns="http://www.onvif.org/ver10/media/wsdl">
      <ConfigurationToken>{token}</ConfigurationToken>
    </GetVideoEncoderConfiguration>"""
        xml = self._request_versions(url, body, "http://www.onvif.org/ver10/media/wsdl/GetVideoEncoderConfiguration")
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            return {}
        conf = root.find(".//{*}VideoEncoderConfiguration")
        if conf is None:
            conf = root.find(".//{*}Configuration")
        return self._parse_video_encoder_configuration(conf) if conf is not None else {}

    def get_video_encoder_configuration_raw(self, url: str, token: str) -> str:
        body = f"""    <GetVideoEncoderConfiguration xmlns="http://www.onvif.org/ver10/media/wsdl">
      <ConfigurationToken>{token}</ConfigurationToken>
    </GetVideoEncoderConfiguration>"""
        return self._request_versions(url, body, "http://www.onvif.org/ver10/media/wsdl/GetVideoEncoderConfiguration")

    def get_video_encoder_configuration_options(
        self,
        url: str,
        profile_token: Optional[str] = None,
        config_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not profile_token and not config_token:
            return {}
        if profile_token:
            body = f"""    <GetVideoEncoderConfigurationOptions xmlns="http://www.onvif.org/ver10/media/wsdl">
      <ProfileToken>{profile_token}</ProfileToken>
    </GetVideoEncoderConfigurationOptions>"""
            xml = self._request_versions(url, body, "http://www.onvif.org/ver10/media/wsdl/GetVideoEncoderConfigurationOptions")
            try:
                root = ET.fromstring(xml)
                opts = root.find(".//{*}VideoEncoderConfigurationOptions") or root.find(".//{*}Options")
                parsed = self._parse_video_encoder_options(opts) if opts is not None else {}
                if parsed:
                    return parsed
            except ET.ParseError:
                pass
        if config_token:
            body = f"""    <GetVideoEncoderConfigurationOptions xmlns="http://www.onvif.org/ver10/media/wsdl">
      <ConfigurationToken>{config_token}</ConfigurationToken>
    </GetVideoEncoderConfigurationOptions>"""
            xml = self._request_versions(url, body, "http://www.onvif.org/ver10/media/wsdl/GetVideoEncoderConfigurationOptions")
            try:
                root = ET.fromstring(xml)
                opts = root.find(".//{*}VideoEncoderConfigurationOptions") or root.find(".//{*}Options")
                return self._parse_video_encoder_options(opts) if opts is not None else {}
            except ET.ParseError:
                return {}
        return {}

    def get_video_encoder_configuration_options_raw(
        self,
        url: str,
        profile_token: Optional[str] = None,
        config_token: Optional[str] = None,
    ) -> str:
        if profile_token:
            body = f"""    <GetVideoEncoderConfigurationOptions xmlns="http://www.onvif.org/ver10/media/wsdl">
      <ProfileToken>{profile_token}</ProfileToken>
    </GetVideoEncoderConfigurationOptions>"""
            return self._request_versions(url, body, "http://www.onvif.org/ver10/media/wsdl/GetVideoEncoderConfigurationOptions")
        if config_token:
            body = f"""    <GetVideoEncoderConfigurationOptions xmlns="http://www.onvif.org/ver10/media/wsdl">
      <ConfigurationToken>{config_token}</ConfigurationToken>
    </GetVideoEncoderConfigurationOptions>"""
            return self._request_versions(url, body, "http://www.onvif.org/ver10/media/wsdl/GetVideoEncoderConfigurationOptions")
        return ""

    def set_video_encoder_configuration(self, url: str, config: Dict[str, Any], force_persistence: bool = True) -> Dict[str, Any]:
        # Build minimal config from dict; expects keys: token, name, encoding, width, height, quality, framerate_limit, bitrate, gov_length, profile
        token = config.get("token", "")
        if not token:
            return {"error": "missing token in config"}
        name = config.get("name", "")
        encoding = config.get("encoding", "H264")
        width = config.get("width")
        height = config.get("height")
        quality = config.get("quality")
        framerate = config.get("framerate_limit")
        bitrate = config.get("bitrate")
        gov_length = config.get("gov_length")
        profile = config.get("profile")
        persistence = "true" if force_persistence else "false"

        size_block = ""
        if width and height:
            size_block = f"<tt:Resolution><tt:Width>{int(width)}</tt:Width><tt:Height>{int(height)}</tt:Height></tt:Resolution>"
        rate_block = ""
        if framerate or bitrate:
            rate_block = (
                "<tt:RateControl>"
                f"<tt:FrameRateLimit>{int(framerate) if framerate else 0}</tt:FrameRateLimit>"
                f"<tt:BitrateLimit>{int(bitrate) if bitrate else 0}</tt:BitrateLimit>"
                "</tt:RateControl>"
            )
        h264_block = ""
        if profile or gov_length:
            gov = f"<tt:GovLength>{int(gov_length)}</tt:GovLength>" if gov_length else ""
            prof = f"<tt:H264Profile>{profile}</tt:H264Profile>" if profile else ""
            h264_block = f"<tt:H264>{gov}{prof}</tt:H264>"
        body = f"""    <SetVideoEncoderConfiguration xmlns="http://www.onvif.org/ver10/media/wsdl">
      <Configuration token="{token}" xmlns:tt="http://www.onvif.org/ver10/schema">
        <tt:Name>{name}</tt:Name>
        <tt:UseCount>1</tt:UseCount>
        <tt:Encoding>{encoding}</tt:Encoding>
        {size_block}
        <tt:Quality>{quality if quality is not None else 0}</tt:Quality>
        {rate_block}
        {h264_block}
      </Configuration>
      <ForcePersistence>{persistence}</ForcePersistence>
    </SetVideoEncoderConfiguration>"""
        xml = self._request_versions(url, body, "http://www.onvif.org/ver10/media/wsdl/SetVideoEncoderConfiguration")
        return {"ok": True, "response": xml}

    def _soap_envelope(self, body_inner: str, version: str = "1.1") -> str:
        ns = "http://schemas.xmlsoap.org/soap/envelope/" if version == "1.1" else "http://www.w3.org/2003/05/soap-envelope"
        header_inner = (
            self._build_wsse_header(self.username, self.password, mode=self.wsse_mode)
            if self.wsse_mode != "none"
            else ""
        )
        header_block = f"  <soap:Header>\n{header_inner}\n  </soap:Header>\n" if header_inner else ""
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="{ns}">
{header_block}  <soap:Body>
{body_inner}
  </soap:Body>
</soap:Envelope>"""

    def _soap_request(self, url: str, body: str, soap_action: str, version: str = "1.1") -> str:
        headers = {
            "Content-Type": "text/xml; charset=utf-8" if version == "1.1" else "application/soap+xml; charset=utf-8",
            "SOAPAction": soap_action,
        }
        auth = HTTPDigestAuth(self.username, self.password) if self.auth_mode == "digest" else HTTPBasicAuth(self.username, self.password)
        resp = requests.post(url, data=body.encode("utf-8"), headers=headers, auth=auth, timeout=self.timeout)
        resp.raise_for_status()
        return resp.text

    def _request_versions(self, url: str, body_inner: str, soap_action: str) -> str:
        last_exc: Exception | None = None
        for ver in ("1.1", "1.2"):
            try:
                xml = self._soap_request(url, self._soap_envelope(body_inner, version=ver), soap_action, version=ver)
                return xml
            except Exception as exc:  # pylint: disable=broad-except
                last_exc = exc
        raise last_exc or RuntimeError("SOAP request failed")

    @staticmethod
    def _build_wsse_header(username: str, password: str, mode: str = "digest") -> str:
        wsse_ns = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
        wsu_ns = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
        if mode == "text":
            pass_type = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordText"
            nonce = ""
            created = ""
            pwd_val = password
        else:
            pass_type = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest"
            raw_nonce = os.urandom(16)
            nonce = base64.b64encode(raw_nonce).decode("ascii")
            created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            sha1 = hashlib.sha1()
            sha1.update(raw_nonce + created.encode("utf-8") + password.encode("utf-8"))
            pwd_val = base64.b64encode(sha1.digest()).decode("ascii")
        nonce_xml = f'<wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">{nonce}</wsse:Nonce>' if nonce else ""
        created_xml = f"<wsu:Created>{created}</wsu:Created>" if created else ""
        parts = [
            f'    <wsse:Security xmlns:wsse="{wsse_ns}" xmlns:wsu="{wsu_ns}" soap:mustUnderstand="1">',
            "      <wsse:UsernameToken>",
            f"        <wsse:Username>{username}</wsse:Username>",
            f'        <wsse:Password Type="{pass_type}">{pwd_val}</wsse:Password>',
        ]
        if nonce_xml:
            parts.append(f"        {nonce_xml}")
        if created_xml:
            parts.append(f"        {created_xml}")
        parts.extend(
            [
                "      </wsse:UsernameToken>",
                "    </wsse:Security>",
            ]
        )
        return "\n".join(parts)

    @staticmethod
    def _parse_profiles(xml_str: str) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        try:
            root = ET.fromstring(xml_str)
        except ET.ParseError:
            return out
        for prof in root.findall(".//{*}Profiles"):
            entry: Dict[str, Any] = {
                "name": prof.findtext(".//{*}Name", default=""),
                "token": prof.attrib.get("token", "") or prof.attrib.get("Token", ""),
            }
            ve = prof.find(".//{*}VideoEncoderConfiguration")
            if ve is not None:
                entry["video_encoding"] = ve.findtext(".//{*}Encoding", default="")
                entry["video_encoder_token"] = ve.attrib.get("token", "") or ve.attrib.get("Token", "")
                res = ve.find(".//{*}Resolution")
                if res is not None:
                    try:
                        entry["video_width"] = int(res.findtext(".//{*}Width", default="0"))
                        entry["video_height"] = int(res.findtext(".//{*}Height", default="0"))
                    except Exception:
                        pass
            aud = prof.find(".//{*}AudioEncoderConfiguration")
            if aud is not None:
                entry["audio_encoding"] = aud.findtext(".//{*}Encoding", default="")
                entry["audio_encoder_token"] = aud.attrib.get("token", "") or aud.attrib.get("Token", "")
            out.append(entry)
        return out

    @staticmethod
    def _parse_uri(xml_str: str) -> str:
        try:
            root = ET.fromstring(xml_str)
            uri = root.find(".//{*}Uri")
            return uri.text.strip() if uri is not None and uri.text else ""
        except ET.ParseError:
            return ""

    @staticmethod
    def _parse_device_info(xml_str: str) -> Dict[str, Any]:
        fields = ["Manufacturer", "Model", "FirmwareVersion", "SerialNumber", "HardwareId"]
        out: Dict[str, Any] = {}
        try:
            root = ET.fromstring(xml_str)
        except ET.ParseError:
            return out
        for f in fields:
            out[f] = root.findtext(f".//{{*}}{f}", default="")
        return out

    @staticmethod
    def _parse_video_encoder_configuration(conf: ET.Element) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "token": conf.attrib.get("token", ""),
            "name": conf.findtext(".//{*}Name", default=""),
            "encoding": conf.findtext(".//{*}Encoding", default=""),
        }
        res = conf.find(".//{*}Resolution")
        if res is not None:
            data["width"] = int(res.findtext(".//{*}Width", default="0"))
            data["height"] = int(res.findtext(".//{*}Height", default="0"))
        data["quality"] = conf.findtext(".//{*}Quality", default="")
        rate = conf.find(".//{*}RateControl")
        if rate is not None:
            data["framerate_limit"] = rate.findtext(".//{*}FrameRateLimit", default="")
            data["bitrate_limit"] = rate.findtext(".//{*}BitrateLimit", default="")
            data["encoding_interval"] = rate.findtext(".//{*}EncodingInterval", default="")
        h264 = conf.find(".//{*}H264")
        if h264 is not None:
            data["gov_length"] = h264.findtext(".//{*}GovLength", default="")
            data["profile"] = h264.findtext(".//{*}H264Profile", default="")
        return data

    @staticmethod
    def _parse_video_encoder_options(opts: ET.Element) -> Dict[str, Any]:
        def read_range(elem: Optional[ET.Element], key: str) -> Dict[str, Any]:
            if elem is None:
                return {}
            return {
                f"{key}_min": elem.findtext(".//{*}Min", default=""),
                f"{key}_max": elem.findtext(".//{*}Max", default=""),
            }

        data: Dict[str, Any] = {}
        quality = opts.find(".//{*}QualityRange")
        data.update(read_range(quality, "quality"))
        h264 = opts.find(".//{*}H264")
        if h264 is not None:
            data.update(read_range(h264.find(".//{*}GovLengthRange"), "gov_length"))
            data.update(read_range(h264.find(".//{*}FrameRateRange"), "framerate"))
            profs = [p.text for p in h264.findall(".//{*}H264ProfilesSupported") if p.text]
            data["h264_profiles"] = profs
            res_list = []
            for res in h264.findall(".//{*}ResolutionsAvailable"):
                w = res.findtext(".//{*}Width", default="")
                h = res.findtext(".//{*}Height", default="")
                if w and h:
                    res_list.append({"width": w, "height": h})
            if res_list:
                data["resolutions"] = res_list
        return data

    @staticmethod
    def _parse_audio_encoder_configuration(conf: ET.Element) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "token": conf.attrib.get("token", ""),
            "name": conf.findtext(".//{*}Name", default=""),
            "encoding": conf.findtext(".//{*}Encoding", default=""),
        }
        data["bitrate"] = conf.findtext(".//{*}Bitrate", default="")
        data["sample_rate"] = conf.findtext(".//{*}SampleRate", default="")
        return data

    @staticmethod
    def _parse_audio_encoder_options(opts: ET.Element) -> Dict[str, Any]:
        data: Dict[str, Any] = {}
        if opts is None:
            return data
        encodings = [e.text for e in opts.findall(".//{*}Encoding") if e.text]
        if encodings:
            data["encodings"] = encodings
        bitrates = []
        for br in opts.findall(".//{*}BitrateList//{*}Bitrate"):
            if br.text:
                bitrates.append(br.text)
        for br in opts.findall(".//{*}BitrateList//{*}Items"):
            if br.text:
                bitrates.append(br.text)
        if bitrates:
            data["bitrates"] = bitrates
        rates = []
        for sr in opts.findall(".//{*}SampleRateList//{*}SampleRate"):
            if sr.text:
                rates.append(sr.text)
        for sr in opts.findall(".//{*}SampleRateList//{*}Items"):
            if sr.text:
                rates.append(sr.text)
        if rates:
            data["sample_rates"] = rates
        return data

    @staticmethod
    def _parse_audio_output_configuration(conf: ET.Element) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "token": conf.attrib.get("token", ""),
            "name": conf.findtext(".//{*}Name", default=""),
            "output_token": conf.findtext(".//{*}OutputToken", default=""),
            "send_primacy": conf.findtext(".//{*}SendPrimacy", default=""),
            "output_level": conf.findtext(".//{*}OutputLevel", default=""),
        }
        return data

    @staticmethod
    def _parse_audio_output_options(opts: ET.Element) -> Dict[str, Any]:
        data: Dict[str, Any] = {}
        if opts is None:
            return data
        levels = []
        for lvl in opts.findall(".//{*}OutputLevelRange//{*}Min"):
            if lvl.text:
                data["output_level_min"] = lvl.text
        for lvl in opts.findall(".//{*}OutputLevelRange//{*}Max"):
            if lvl.text:
                data["output_level_max"] = lvl.text
        outputs = [e.text for e in opts.findall(".//{*}OutputToken") if e.text]
        if outputs:
            data["output_tokens"] = outputs
        primacies = [e.text for e in opts.findall(".//{*}SendPrimacy") if e.text]
        if primacies:
            data["send_primacy"] = primacies
        return data
