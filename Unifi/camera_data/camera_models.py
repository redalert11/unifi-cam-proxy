class CameraModelDatabase:
    """
    Central mappings for camera model <-> platform <-> sysid,
    plus small helpers for lookups/packing.
    """

    # Model (marketName) -> platform code (lowercase)
    CameraPlatformsByType = {
        # Early cameras
        "UVC": "a5s",
        "UVC_PRO": "a5s",
        "UVC_DOME": "a5s",
        "UVC_MICRO": "a5s",

        # G3
        "UVC_G3": "s2l",
        "UVC_G3_DOME": "s2l",
        "UVC_G3_MICRO": "s2lm",
        "UVC_G3_MINI": "sav532q",
        "UVC_G3_INSTANT": "sav532q",
        "UVC_G3_PRO": "s2l",
        "UVC_G3_FLEX": "s2l",

        # G4
        "UVC_G4_BULLET": "s5l",
        "UVC_G4_PRO": "s5l",
        "UVC_G4_PTZ": "s5l",
        "UVC_G4_DOORBELL": "s5l",
        "UVC_G4_DOORBELL_PRO": "s5l",
        "UVC_G4_DOORBELL_PRO_WHITE": "s5l",
        "UVC_G4_DOORBELL_PRO_POE": "s5l",
        "UVC_G4_DOORBELL_PRO_POE_WHITE": "s5l",
        "UVC_G4_DOME": "s5l",
        "UVC_G4_INSTANT": "sav530q",

        # G5
        "UVC_G5_BULLET": "sav530q",
        "UVC_G5_DOME": "sav530q",
        "UVC_G5_FLEX": "sav530q",
        "UVC_G5_PRO": "sav837gw",
        "UVC_G5_PTZ": "sav530q",
        "UVC_G5_DOME_ULTRA": "sav530q",
        "UVC_G5_DOME_ULTRA_BLACK": "sav530q",
        "UVC_G5_TURRET_ULTRA": "sav530q",
        "UVC_G5_TURRET_ULTRA_BLACK": "sav530q",

        # G6
        "UVC_G6_BULLET": "sav539g",
        "UVC_G6_BULLET_BLACK": "sav539g",
        "UVC_G6_DOME": "sav539g",
        "UVC_G6_DOME_BLACK": "sav539g",
        "UVC_G6_TURRET": "sav539g",
        "UVC_G6_TURRET_BLACK": "sav539g",
        "UVC_G6_INSTANT": "sav539g",
        "UVC_G6_PTZ": "sav539gp",
        "UVC_G6_PTZ_BLACK": "sav539gp",
        "UVC_G6_PRO_360": "sav539gp",
        "UVC_G6_PRO_360_BLACK": "sav539gp",
        "UVC_G6_PRO_BULLET": "sav539gp",
        "UVC_G6_180": "sav539gp",

        # AI Series
        "UVC_AI_360": "cv2x",
        "UVC_AI_360_WHITE": "cv2x",
        "UVC_AI_BULLET": "cv2x",
        "UVC_AI_DSLR": "cv22",
        "UVC_AI_PRO": "cv2x",
        "UVC_AI_PRO_WHITE": "cv2x",
        "UVC_AI_PRO_LPR": "cv2x",
        "UVC_AI_LPR": "cv2x",
        "UVC_AI_THETA": "cv2x",
        "UVC_AI_DOME": "cv2x",
        "UVC_AI_TURRET": "cv2x",
        "UVC_AI_PTZ": "cv25z",
        "UVC_AI_PTZ_WHITE": "cv25z",
        "UVC_AI_PTZ_PRECISION": "cv25z",
        "UVC_AI_PTZ_PRECISION_WHITE": "cv25z",

        # Doorbell Lite
        "UVC_DOORBELL_LITE": "sav530q",
        "UVC_DOORBELL_LITE_WHITE": "sav530q",

        # Other
        "AFI_VC": "s2lm",
        "VISION_PRO": "s2lm",
    }

    # Model (marketName) -> sysid hex string (keep as string for JSON readability)
    CameraSysIds = {
        "AFI_VC": "0xa553",
        "UVC": "0xa524",
        "UVC_AI_360": "0xa5a0",
        "UVC_AI_BULLET": "0xa5a2",
        "UVC_AI_THETA": "0xa5a3",
        "UVC_AI_DSLR": "0xa5b0",
        "UVC_AI_PRO": "0xa5a4",
        "UVC_AI_DOME": "0xa5a5",
        "UVC_AI_TURRET": "0xa5a6",
        "UVC_AI_LPR": "0xa5a7",
        "UVC_DOME": "0xa525",
        "UVC_G3": "0xa531",
        "UVC_G3_DOME": "0xa533",
        "UVC_G3_FLEX": "0xa534",
        "UVC_G3_MICRO": "0xa552",
        "UVC_G3_INSTANT": "0xa590",
        "UVC_G3_PRO": "0xa532",
        "UVC_G4_PRO": "0xa563",
        "UVC_G4_PTZ": "0xa564",
        "UVC_G4_DOORBELL": "0xa571",
        "UVC_G4_DOORBELL_PRO": "0xa574",
        "UVC_G4_DOORBELL_PRO_WHITE": "0xa576",
        "UVC_G4_DOORBELL_PRO_POE": "0xa575",
        "UVC_G4_BULLET": "0xa572",
        "UVC_G4_DOME": "0xa573",
        "UVC_G4_INSTANT": "0xa595",
        "UVC_G5_BULLET": "0xa591",
        "UVC_G5_DOME": "0xa592",
        "UVC_G5_FLEX": "0xa593",
        "UVC_G5_PRO": "0xa598",
        "UVC_G5_PTZ": "0xa59b",
        "UVC_G5_DOME_ULTRA": "0xa59d",
        "UVC_G5_TURRET_ULTRA": "0xa59c",
        "UVC_MICRO": "0xa526",
        "UVC_PRO": "0xa521",
        "VISION_PRO": "0xa551",
        "UVC_G6_BULLET": "0xa600",
        "UVC_G6_BULLET_BLACK": "0xa06a",
        "UVC_G6_TURRET": "0xa601",
        "UVC_G6_TURRET_BLACK": "0xa06b",
        "UVC_G6_DOME": "0xa602",
        "UVC_G6_DOME_BLACK": "0xa06c",
        "UVC_G6_INSTANT": "0xa603",
        "UVC_AI_PTZ": "0xa604",
        "UVC_AI_PTZ_WHITE": "0xa065",
        "UVC_G6_PTZ": "0xa605",
        "UVC_G6_PTZ_BLACK": "0xa606",
        "UVC_DOORBELL_LITE": "0xa061",
        "UVC_DOORBELL_LITE_WHITE": "0xa062",
        "UVC_G6_PRO_360": "0xa60f",
        "UVC_G6_PRO_360_BLACK": "0xa060",
        "UVC_G6_PRO_BULLET": "0xa607",
        "UVC_G6_180": "0xa60e",
        "UVC_AI_PTZ_PRECISION": "0xa067",
        "UVC_AI_PTZ_PRECISION_WHITE": "0xa066",
    }

    # Inverse: sysid hex string -> model (marketName)
    CameraTypesBySysId = {
        v.lower(): k
        for k, v in CameraSysIds.items()
    }

    # End-of-life models
    EOLCameraTypes = [
        "UVC",
        "UVC_PRO",
        "UVC_DOME",
        "UVC_MICRO",
    ]

    # ------------ Helpers ------------

    @classmethod
    def get_platform(cls, model_name: str) -> str | None:
        """Return platform string (e.g., 'sav530q') for a marketName, or None."""
        return cls.CameraPlatformsByType.get(model_name)

    @classmethod
    def get_sysid(cls, model_name: str) -> str | None:
        """Return sysid hex string (e.g., '0xa573') for a marketName, or None."""
        return cls.CameraSysIds.get(model_name)

    @classmethod
    def get_type_by_sysid(cls, sysid: str) -> str | None:
        """Return marketName for a sysid hex string. Accepts '0xa573' or 'A573'."""
        s = sysid.lower()
        if not s.startswith("0x"):
            s = f"0x{s}"
        return cls.CameraTypesBySysId.get(s)

    @staticmethod
    def sysid_le_bytes(sysid: str) -> bytes:
        """
        Pack sysid string ('0xa573' or 'a573' or decimal) into 2 bytes (little-endian).
        Raises ValueError if invalid.
        """
        val = int(sysid, 0) if isinstance(sysid, str) else int(sysid)
        if not (0 <= val <= 0xFFFF):
            raise ValueError(f"sysid out of range: {val}")
        import struct
        return struct.pack("<H", val)
