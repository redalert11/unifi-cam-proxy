"""
Minimal ONVIF probe using the raw SOAP client.

Examples:
  python -m Web.onvif_probe --host 192.168.0.42 --user admin --password pass --stream-uris
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Dict

from .onvif_client import OnvifSoapClient


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe ONVIF profiles using raw SOAP.")
    parser.add_argument("--host", help="Camera host or IP")
    parser.add_argument("--port", type=int, default=80, help="Camera port (default 80)")
    parser.add_argument("--https", action="store_true", help="Use https (changes scheme only)")
    parser.add_argument("--user", help="Camera/ONVIF username")
    parser.add_argument("--password", help="Camera/ONVIF password")
    parser.add_argument("--auth", choices=["digest", "basic"], default="digest", help="Auth mode (default digest)")
    parser.add_argument("--wsse", choices=["none", "digest", "text"], default="digest", help="WS-Security UsernameToken (default digest)")
    parser.add_argument("--timeout", type=float, default=8.0, help="Timeout seconds")
    parser.add_argument("--stream-uris", action="store_true", help="Fetch GetStreamUri/GetSnapshotUri")
    parser.add_argument("--services", action="store_true", help="Include GetServices output in result")
    parser.add_argument("--services-capabilities", action="store_true", help="Include service capabilities (GetServices IncludeCapability=true)")
    parser.add_argument("--encoder-config-token", help="Fetch video encoder configuration by token")
    parser.add_argument("--encoder-options-profile", help="Fetch video encoder options for profile token")
    parser.add_argument("--set-encoder-config-json", help="JSON string for SetVideoEncoderConfiguration")
    parser.add_argument("--encoder-raw", action="store_true", help="Include raw XML for encoder queries")
    parser.add_argument("--audio-config-token", help="Fetch audio encoder configuration by token")
    parser.add_argument("--audio-options-profile", help="Fetch audio encoder options for profile token")
    parser.add_argument("--audio-raw", action="store_true", help="Include raw XML for audio queries")
    parser.add_argument("--audio-output-token", help="Fetch audio output (speaker) configuration by token")
    parser.add_argument("--audio-output-options", help="Fetch audio output options for profile or output token")
    parser.add_argument("--audio-output-raw", action="store_true", help="Include raw XML for audio output queries")
    parser.add_argument("--scopes", action="store_true", help="Include GetScopes output in result")
    parser.add_argument("--profile-claims", action="store_true", help="Include profile claims derived from scopes")
    parser.add_argument("--discover", action="store_true", help="Run WS-Discovery and exit")
    parser.add_argument("--discover-timeout", type=float, default=4.0, help="Discovery timeout seconds (default 4)")
    parser.add_argument("--discover-host", help="Run WS-Discovery unicast to a specific host and exit")
    parser.add_argument("--walk-endpoints", action="store_true", help="Probe all ONVIF service endpoints and report status")
    args = parser.parse_args()

    if args.discover:
        devices = OnvifSoapClient.discover(timeout=args.discover_timeout)
        print(json.dumps({"devices": devices}, indent=2))
        return

    if args.discover_host:
        devices = OnvifSoapClient.discover_unicast(args.discover_host, timeout=args.discover_timeout)
        print(json.dumps({"devices": devices, "host": args.discover_host}, indent=2))
        return

    if not args.host or not args.user or not args.password:
        parser.error("Provide --host, --user, --password")

    client = OnvifSoapClient(
        host=args.host,
        port=args.port,
        username=args.user,
        password=args.password,
        auth_mode=args.auth,
        wsse_mode=args.wsse,
        timeout=args.timeout,
        https=args.https,
    )

    endpoints = [
        client.endpoint("/onvif/device_service"),
        client.endpoint("/onvif/media_service"),
        client.endpoint("/device_service"),
    ]

    last_err = None
    errors: Dict[str, str] = {}
    media_override = client.get_media_xaddr(endpoints[0])
    media_endpoints = [media_override] if media_override else []
    media_endpoints += [ep for ep in endpoints if "media" in ep]
    target_list = media_endpoints + [ep for ep in endpoints if "device" in ep]

    for target in target_list:
        try:
            profiles = client.get_profiles(target, include_streams=args.stream_uris)
            dev_info = client.get_device_info(target)
            services = []
            if args.services:
                services = client.get_services(
                    client.endpoint("/onvif/device_service"),
                    include_capability=args.services_capabilities,
                )
            encoder_config = {}
            encoder_options = {}
            set_result = None
            scopes = []
            profile_claims = {}
            audio_config = {}
            audio_options = {}
            audio_output_config = {}
            audio_output_options = {}
            if args.scopes:
                scopes = client.get_scopes(client.endpoint("/onvif/device_service"))
            if args.profile_claims:
                profile_claims = client.get_profile_claims(client.endpoint("/onvif/device_service"))
            profile_by_token = {p.get("token"): p for p in profiles if p.get("token")}
            profile_by_encoder = {p.get("video_encoder_token"): p for p in profiles if p.get("video_encoder_token")}
            profile_by_audio = {p.get("audio_encoder_token"): p for p in profiles if p.get("audio_encoder_token")}
            if args.encoder_config_token:
                config_token = args.encoder_config_token
                if config_token in profile_by_token:
                    mapped = profile_by_token[config_token].get("video_encoder_token")
                    if mapped:
                        config_token = mapped
                elif config_token in profile_by_encoder:
                    config_token = profile_by_encoder[config_token].get("video_encoder_token") or config_token
                encoder_config = client.get_video_encoder_configuration(target, config_token)
                if args.encoder_raw:
                    encoder_config = {
                        "parsed": encoder_config,
                        "raw": client.get_video_encoder_configuration_raw(target, config_token),
                        "token_used": config_token,
                    }
            if args.encoder_options_profile:
                prof = profile_by_token.get(args.encoder_options_profile) or profile_by_encoder.get(args.encoder_options_profile)
                encoder_options = client.get_video_encoder_configuration_options(
                    target,
                    profile_token=args.encoder_options_profile if prof and prof.get("token") == args.encoder_options_profile else None,
                    config_token=prof.get("video_encoder_token") if prof else args.encoder_options_profile,
                )
                if args.encoder_raw:
                    encoder_options = {
                        "parsed": encoder_options,
                        "raw": client.get_video_encoder_configuration_options_raw(
                            target,
                            profile_token=args.encoder_options_profile if prof and prof.get("token") == args.encoder_options_profile else None,
                            config_token=prof.get("video_encoder_token") if prof else args.encoder_options_profile,
                        ),
                    }
            if args.set_encoder_config_json:
                try:
                    payload = json.loads(args.set_encoder_config_json)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid --set-encoder-config-json: {exc}") from exc
                token = payload.get("token")
                if token and token in profile_by_token:
                    mapped = profile_by_token[token].get("video_encoder_token")
                    if mapped:
                        payload["token"] = mapped
                set_result = client.set_video_encoder_configuration(target, payload)
            if args.audio_config_token:
                audio_token = args.audio_config_token
                if audio_token in profile_by_token:
                    mapped = profile_by_token[audio_token].get("audio_encoder_token")
                    if mapped:
                        audio_token = mapped
                elif audio_token in profile_by_encoder:
                    mapped = profile_by_encoder[audio_token].get("audio_encoder_token")
                    if mapped:
                        audio_token = mapped
                elif audio_token in profile_by_audio:
                    audio_token = profile_by_audio[audio_token].get("audio_encoder_token") or audio_token
                audio_config = client.get_audio_encoder_configuration(target, audio_token)
                if args.audio_raw:
                    audio_config = {
                        "parsed": audio_config,
                        "raw": client.get_audio_encoder_configuration_raw(target, audio_token),
                        "token_used": audio_token,
                    }
            if args.audio_options_profile:
                prof = (
                    profile_by_token.get(args.audio_options_profile)
                    or profile_by_audio.get(args.audio_options_profile)
                    or profile_by_encoder.get(args.audio_options_profile)
                )
                audio_options = client.get_audio_encoder_configuration_options(
                    target,
                    profile_token=args.audio_options_profile if prof and prof.get("token") == args.audio_options_profile else None,
                    config_token=prof.get("audio_encoder_token") if prof else args.audio_options_profile,
                )
                if args.audio_raw:
                    audio_options = {
                        "parsed": audio_options,
                        "raw": client.get_audio_encoder_configuration_options_raw(
                            target,
                            profile_token=args.audio_options_profile if prof and prof.get("token") == args.audio_options_profile else None,
                            config_token=prof.get("audio_encoder_token") if prof else args.audio_options_profile,
                        ),
                    }
            if args.audio_output_token:
                audio_output_config = client.get_audio_output_configuration(target, args.audio_output_token)
                if args.audio_output_raw:
                    audio_output_config = {
                        "parsed": audio_output_config,
                        "raw": client.get_audio_output_configuration_raw(target, args.audio_output_token),
                        "token_used": args.audio_output_token,
                    }
            if args.audio_output_options:
                prof = (
                    profile_by_token.get(args.audio_output_options)
                    or profile_by_audio.get(args.audio_output_options)
                )
                audio_output_options = client.get_audio_output_configuration_options(
                    target,
                    profile_token=args.audio_output_options if prof and prof.get("token") == args.audio_output_options else None,
                    config_token=prof.get("audio_encoder_token") if prof else args.audio_output_options,
                )
                if args.audio_output_raw:
                    audio_output_options = {
                        "parsed": audio_output_options,
                        "raw": client.get_audio_output_configuration_options_raw(
                            target,
                            profile_token=args.audio_output_options if prof and prof.get("token") == args.audio_output_options else None,
                            config_token=prof.get("audio_encoder_token") if prof else args.audio_output_options,
                        ),
                    }

            endpoint_walk = []
            if args.walk_endpoints:
                endpoint_walk = client.walk_endpoints(client.endpoint("/onvif/device_service"))

            print(
                json.dumps(
                    {
                        "via": "direct",
                        "endpoint": target,
                        "profiles": profiles,
                        "device_info": dev_info,
                        "services": services,
                        "scopes": scopes,
                        "profile_claims": profile_claims,
                        "encoder_config": encoder_config,
                        "encoder_options": encoder_options,
                        "audio_config": audio_config,
                        "audio_options": audio_options,
                        "audio_output_config": audio_output_config,
                        "audio_output_options": audio_output_options,
                        "set_encoder_result": set_result,
                        "endpoint_walk": endpoint_walk,
                        "media_endpoint": media_override,
                    },
                    indent=2,
                    default=str,
                )
            )
            return
        except Exception as exc:  # pylint: disable=broad-except
            last_err = exc
            errors[target] = str(exc)
            continue

    print(
        json.dumps(
            {
                "error": str(last_err or "probe failed"),
                "via": "direct",
                "endpoints_tried": target_list,
                "errors": errors,
            },
            indent=2,
            default=str,
        ),
        file=sys.stderr,
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
