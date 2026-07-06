"""
SpotiPi Version Information
Central version management for the SpotiPi project.
"""

from typing import Dict, Optional, Union

# Semantic Versioning: MAJOR.MINOR.PATCH
# MAJOR: Breaking changes
# MINOR: New features (backward compatible)
# PATCH: Bug fixes (backward compatible)
VERSION = "1.12.7"

# Additional version info
VERSION_INFO: Dict[str, Union[int, Optional[str]]] = {
    "major": 1,
    "minor": 12,
    "patch": 7,
    "pre_release": None,  # e.g., "alpha", "beta", "rc1"
    "build": None
}

# Application metadata
APP_NAME = "SpotiPi"
APP_DESCRIPTION = "Smart Alarm Clock & Sleep Timer with Spotify Integration"
APP_AUTHOR = "SpotiPi Team"

def get_version() -> str:
    """Get the current version string.
    
    Returns:
        str: Version string in format MAJOR.MINOR.PATCH
    """
    return VERSION

def get_full_version() -> str:
    """Get version with pre-release and build info if available.
    
    Returns:
        str: Full version string with pre-release and build metadata
    """
    version = VERSION
    if VERSION_INFO["pre_release"]:
        version += f"-{VERSION_INFO['pre_release']}"
    if VERSION_INFO["build"]:
        version += f"+{VERSION_INFO['build']}"
    return version

def get_app_info() -> str:
    """Get application name and version.
    
    Returns:
        str: Application name and version in format "AppName vX.Y.Z"
    """
    return f"{APP_NAME} v{get_version()}"

def get_version_dict() -> Dict[str, Union[str, int, Optional[str]]]:
    """Get version information as dictionary.
    
    Returns:
        dict: Complete version information including metadata
    """
    return {
        "version": VERSION,
        "full_version": get_full_version(),
        "app_name": APP_NAME,
        "description": APP_DESCRIPTION,
        "author": APP_AUTHOR,
        **VERSION_INFO
    }
