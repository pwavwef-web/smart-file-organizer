from __future__ import annotations

import struct
from datetime import datetime, timezone
from pathlib import Path


_EXIF_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".tif", ".tiff"}
_DATETIME_ORIGINAL_TAG = 0x9003  # DateTimeOriginal
_DATETIME_DIGITIZED_TAG = 0x9004  # DateTimeDigitized
_DATETIME_TAG = 0x0132  # DateTime (IFD0)
_EXIF_IFD_POINTER_TAG = 0x8769


def _parse_exif_datetime(text: str) -> datetime | None:
    text = text.strip().rstrip("\x00")
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _read_ifd(data: bytes, offset: int, endian: str, wanted: set[int]) -> dict[int, object]:
    found: dict[int, object] = {}
    if offset + 2 > len(data):
        return found
    (count,) = struct.unpack(endian + "H", data[offset : offset + 2])
    entry = offset + 2
    for _ in range(count):
        if entry + 12 > len(data):
            break
        tag, typ, num = struct.unpack(endian + "HHI", data[entry : entry + 8])
        value_bytes = data[entry + 8 : entry + 12]
        if tag in wanted:
            if typ == 2:  # ASCII string
                (str_off,) = struct.unpack(endian + "I", value_bytes)
                raw = data[str_off : str_off + num]
                found[tag] = raw.split(b"\x00", 1)[0].decode("ascii", "ignore")
            elif typ == 4:  # LONG (used for the Exif IFD pointer)
                (found[tag],) = struct.unpack(endian + "I", value_bytes)
        entry += 12
    return found


def _exif_capture_datetime(path: Path) -> datetime | None:
    try:
        with path.open("rb") as handle:
            head = handle.read(2)
            if head != b"\xff\xd8":  # not a JPEG
                return None
            # Walk JPEG markers to find the APP1/Exif segment.
            while True:
                marker = handle.read(2)
                if len(marker) < 2 or marker[0] != 0xFF:
                    return None
                if marker[1] == 0xE1:  # APP1
                    (seg_len,) = struct.unpack(">H", handle.read(2))
                    segment = handle.read(seg_len - 2)
                    break
                if marker[1] in (0xD9, 0xDA):  # end of image / start of scan
                    return None
                (seg_len,) = struct.unpack(">H", handle.read(2))
                handle.seek(seg_len - 2, 1)
    except (OSError, struct.error):
        return None

    if not segment.startswith(b"Exif\x00\x00"):
        return None
    tiff = segment[6:]
    if len(tiff) < 8:
        return None
    endian = "<" if tiff[:2] == b"II" else ">" if tiff[:2] == b"MM" else None
    if endian is None:
        return None
    (ifd0_offset,) = struct.unpack(endian + "I", tiff[4:8])

    ifd0 = _read_ifd(tiff, ifd0_offset, endian, {_EXIF_IFD_POINTER_TAG, _DATETIME_TAG})
    result: str | None = None
    exif_pointer = ifd0.get(_EXIF_IFD_POINTER_TAG)
    if isinstance(exif_pointer, int):
        exif_ifd = _read_ifd(
            tiff, exif_pointer, endian, {_DATETIME_ORIGINAL_TAG, _DATETIME_DIGITIZED_TAG}
        )
        candidate = exif_ifd.get(_DATETIME_ORIGINAL_TAG) or exif_ifd.get(_DATETIME_DIGITIZED_TAG)
        if isinstance(candidate, str):
            result = candidate
    if result is None and isinstance(ifd0.get(_DATETIME_TAG), str):
        result = ifd0[_DATETIME_TAG]  # type: ignore[assignment]
    return _parse_exif_datetime(result) if result else None


def capture_datetime(path: Path) -> datetime:
    """Best-effort capture/creation date for a file.

    Prefers EXIF DateTimeOriginal for JPEG/TIFF photos so pictures group by the
    day they were taken; otherwise falls back to the file modification time.
    """
    suffix = path.suffix.casefold()
    if suffix in _EXIF_IMAGE_SUFFIXES:
        taken = _exif_capture_datetime(path)
        if taken is not None:
            return taken
    try:
        return datetime.fromtimestamp(path.stat().st_mtime)
    except (OSError, OverflowError, ValueError):
        return datetime.now(timezone.utc).replace(tzinfo=None)
