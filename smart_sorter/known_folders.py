from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from functools import lru_cache
from pathlib import Path


# KNOWNFOLDERID GUIDs (see Microsoft "KNOWNFOLDERID" documentation).
# Mapped to friendly root tokens used inside destination templates.
_KNOWN_FOLDER_GUIDS: dict[str, str] = {
    "Home": "{5E6C858F-0E22-4760-9AFE-EA3317B67173}",  # user profile
    "Desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
    "Downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
    "Documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
    "Pictures": "{33E28130-4E1E-4676-835A-98395C3BC476}",
    "Music": "{4BD8D571-6D19-48D3-BE97-422220080E43}",
    "Videos": "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}",
}

# Where each token lands when the Win32 API is unavailable (non-Windows, tests).
_PROFILE_FALLBACK: dict[str, str] = {
    "Home": "",
    "Desktop": "Desktop",
    "Downloads": "Downloads",
    "Documents": "Documents",
    "Pictures": "Pictures",
    "Music": "Music",
    "Videos": "Videos",
}


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    def __init__(self, text: str) -> None:
        super().__init__()
        text = text.strip("{}")
        parts = text.split("-")
        self.Data1 = int(parts[0], 16)
        self.Data2 = int(parts[1], 16)
        self.Data3 = int(parts[2], 16)
        rest = parts[3] + parts[4]
        for index in range(8):
            self.Data4[index] = int(rest[index * 2 : index * 2 + 2], 16)


def _query_windows(guid_text: str) -> Path | None:
    try:
        shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]
        ole32 = ctypes.windll.ole32  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return None
    guid = _GUID(guid_text)
    out = ctypes.c_wchar_p()
    # SHGetKnownFolderPath(rfid, dwFlags=0, hToken=0, ppszPath)
    result = shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, 0, ctypes.byref(out))
    try:
        if result != 0 or not out.value:
            return None
        return Path(out.value)
    finally:
        if out.value:
            ole32.CoTaskMemFree(out)


@lru_cache(maxsize=None)
def known_folder(token: str) -> Path:
    """Resolve a friendly root token to its real folder, honouring relocation.

    Falls back to a path under the user profile when the Win32 API is not
    available (e.g. on non-Windows hosts used for testing).
    """
    guid = _KNOWN_FOLDER_GUIDS.get(token)
    if guid and os.name == "nt":
        resolved = _query_windows(guid)
        if resolved is not None:
            return resolved
    home = Path(os.path.expanduser("~"))
    tail = _PROFILE_FALLBACK.get(token, token)
    return home / tail if tail else home


def known_folder_map() -> dict[str, Path]:
    """All friendly root tokens mapped to their resolved folders."""
    return {token: known_folder(token) for token in _KNOWN_FOLDER_GUIDS}
