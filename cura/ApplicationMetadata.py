# Copyright (c) 2018 Ultimaker B.V.
# Cura is released under the terms of the LGPLv3 or higher.

# ---------
# General constants used in Cura
# ---------
DEFAULT_CURA_APP_NAME = "cura"
DEFAULT_CURA_DISPLAY_NAME = "Ultimaker Cura"
DEFAULT_CURA_VERSION = "master"
DEFAULT_CURA_BUILD_TYPE = ""
DEFAULT_CURA_DEBUG_MODE = False
DEFAULT_CURA_SDK_VERSION = "6.0.0"

try:
    from cura.CuraVersion import CuraAppName  # type: ignore
    if CuraAppName == "":
        CuraAppName = DEFAULT_CURA_APP_NAME
except ImportError:
    CuraAppName = DEFAULT_CURA_APP_NAME

try:
    from cura.CuraVersion import CuraAppDisplayName  # type: ignore
    if CuraAppDisplayName == "":
        CuraAppDisplayName = DEFAULT_CURA_DISPLAY_NAME
except ImportError:
    CuraAppDisplayName = DEFAULT_CURA_DISPLAY_NAME

try:
    from cura.CuraVersion import CuraVersion  # type: ignore
    if CuraVersion == "":
        CuraVersion = DEFAULT_CURA_VERSION
except ImportError:
    CuraVersion = DEFAULT_CURA_VERSION  # [CodeStyle: Reflecting imported value]

try:
    from cura.CuraVersion import CuraBuildType  # type: ignore
except ImportError:
    CuraBuildType = DEFAULT_CURA_BUILD_TYPE

try:
    from cura.CuraVersion import CuraDebugMode  # type: ignore
except ImportError:
    CuraDebugMode = DEFAULT_CURA_DEBUG_MODE

try:
    from cura.CuraVersion import CuraSDKVersion  # type: ignore
    if CuraSDKVersion == "":
        CuraSDKVersion = DEFAULT_CURA_SDK_VERSION
except ImportError:
    CuraSDKVersion = DEFAULT_CURA_SDK_VERSION
