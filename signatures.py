"""
File signature ("magic bytes" / hex header) pre-check.

This is a fast first-pass gate that runs BEFORE the full format-specific
validator: it reads only the first ~16 bytes of a file and compares them
against the known signature for that extension. If the header plainly
doesn't match what the extension claims, the file is rejected immediately
as Non-Working -- there's no point spending time fully decoding a
multi-GB video that isn't even the right container format.

If the header DOES match (or the format has no single reliable fixed
signature, e.g. .txt/.csv/legacy .doc & .xls which share a generic OLE
header), this step passes the file through to the real, full validator
in validators.py unchanged. A matching header only proves the first few
bytes are right -- it can't catch mid-file corruption, so it is
deliberately a pre-filter, never a replacement for the full decode.
"""
import os


def _read_head(path: str, n: int = 16) -> bytes:
    with open(path, "rb") as fh:
        return fh.read(n)


def _hex(b: bytes) -> str:
    return " ".join(f"{byte:02X}" for byte in b)


def _riff_check(head: bytes, expected_form: bytes):
    """RIFF containers (WAV, AVI, WEBP): 'RIFF' + 4-byte size + form type."""
    if len(head) < 12:
        return False
    return head[0:4] == b"RIFF" and head[8:12] == expected_form


def _ftyp_check(head: bytes):
    """ISO base media containers (MP4/MOV/M4A/M4V/CR3): 'ftyp' at offset 4."""
    return len(head) >= 8 and head[4:8] == b"ftyp"


def _tiff_check(head: bytes):
    """TIFF and most TIFF-based camera RAW formats (ARW, DNG, NEF, ORF, PEF, RW2)."""
    return head[:4] in (b"II*\x00", b"MM\x00*")


ASF_GUID = bytes.fromhex("3026B2758E66CF11A6D900AA0062CE6C")  # WMV/WMA container


def check_header(path: str, ext: str):
    """Returns:
    - None            -> no reliable fixed signature for this extension; skip (fall through to full validator)
    - (True, "")       -> header matches expectation
    - (False, reason)  -> header does NOT match; caller should fail fast without full decode
    """
    ext = ext.lower()
    try:
        head = _read_head(path, 16)
    except Exception:
        return None  # let the full validator surface the real I/O error

    if len(head) == 0:
        return False, "File is empty (0 bytes)"

    def fail(expected_desc: str):
        return False, f"Header signature mismatch -- expected {expected_desc}, found {_hex(head[:8])}"

    if ext in (".jpg", ".jpeg"):
        return (True, "") if head[:3] == b"\xFF\xD8\xFF" else fail("JPEG (FF D8 FF)")
    if ext == ".png":
        return (True, "") if head[:8] == bytes.fromhex("89504E470D0A1A0A") else fail("PNG (89 50 4E 47 0D 0A 1A 0A)")
    if ext == ".gif":
        return (True, "") if head[:6] in (b"GIF87a", b"GIF89a") else fail("GIF (GIF87a/GIF89a)")
    if ext == ".bmp":
        return (True, "") if head[:2] == b"BM" else fail("BMP (42 4D)")
    if ext == ".ico":
        return (True, "") if head[:4] == bytes.fromhex("00000100") else fail("ICO (00 00 01 00)")
    if ext in (".tif", ".tiff"):
        return (True, "") if _tiff_check(head) else fail("TIFF (II*/MM*)")
    if ext == ".psd":
        return (True, "") if head[:4] == b"8BPS" else fail("Photoshop PSD (8BPS)")
    if ext in (".heic", ".heif"):
        return (True, "") if _ftyp_check(head) else fail("HEIF/HEIC ISO-BMFF 'ftyp' container")
    if ext == ".webp":
        return (True, "") if _riff_check(head, b"WEBP") else fail("RIFF/WEBP container")

    if ext in (".arw", ".dng", ".nef", ".orf", ".pef", ".rw2"):
        return (True, "") if _tiff_check(head) else fail("TIFF-based RAW header (II*/MM*)")
    if ext == ".raf":
        return (True, "") if head[:8] == b"FUJIFILM" else fail("Fujifilm RAF (FUJIFILM)")
    if ext == ".cr2":
        return (True, "") if _tiff_check(head) else fail("TIFF-based RAW header (II*/MM*)")
    if ext == ".cr3":
        return (True, "") if _ftyp_check(head) else fail("ISO-BMFF/ftyp container")

    if ext == ".pdf":
        return (True, "") if head[:4] == b"%PDF" else fail("PDF (%PDF)")

    if ext in (".docx", ".xlsx", ".odt", ".ods"):
        return (True, "") if head[:4] == b"PK\x03\x04" else fail("ZIP/OOXML container (PK\\x03\\x04)")

    # Legacy .doc / .xls share the same generic OLE compound-file header --
    # it confirms "this is *an* OLE file" but can't distinguish doc vs xls,
    # so we only reject an outright non-match and let the real validator do
    # the format-specific work.
    if ext in (".doc", ".xls"):
        return (True, "") if head[:8] == bytes.fromhex("D0CF11E0A1B11AE1") else fail("OLE compound file (D0 CF 11 E0 A1 B1 1A E1)")

    if ext == ".mp3":
        if head[:3] == b"ID3":
            return True, ""
        if len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:
            return True, ""  # raw MPEG frame sync, no ID3 tag
        return fail("MP3 (ID3 tag or MPEG frame sync)")
    if ext == ".wav":
        return (True, "") if _riff_check(head, b"WAVE") else fail("RIFF/WAVE container")
    if ext == ".flac":
        return (True, "") if head[:4] == b"fLaC" else fail("FLAC (fLaC)")
    if ext == ".ogg":
        return (True, "") if head[:4] == b"OggS" else fail("OGG (OggS)")
    if ext in (".wma",):
        return (True, "") if head[:16] == ASF_GUID else fail("ASF/WMA container")

    if ext in (".mp4", ".m4v", ".m4a", ".mov", ".3gp"):
        return (True, "") if _ftyp_check(head) else fail("ISO-BMFF/ftyp container")
    if ext in (".mkv", ".webm"):
        return (True, "") if head[:4] == bytes.fromhex("1A45DFA3") else fail("EBML/Matroska header (1A 45 DF A3)")
    if ext == ".avi":
        return (True, "") if _riff_check(head, b"AVI ") else fail("RIFF/AVI container")
    if ext == ".flv":
        return (True, "") if head[:3] == b"FLV" else fail("FLV (46 4C 56)")
    if ext == ".wmv":
        return (True, "") if head[:16] == ASF_GUID else fail("ASF/WMV container")
    if ext in (".mpg", ".mpeg", ".vob"):
        # MPEG program stream: pack header 00 00 01 BA. Some raw MPEG-1/2
        # video elementary streams start with a sequence header 00 00 01 B3
        # instead, and some MPEG-TS captures use 0x47 sync -- accept those too.
        if head[:4] in (b"\x00\x00\x01\xBA", b"\x00\x00\x01\xB3"):
            return True, ""
        if head[0:1] == b"\x47":
            return True, ""
        return fail("MPEG program stream (00 00 01 BA / 00 00 01 B3)")
    if ext in (".mts", ".m2ts"):
        # MPEG-TS: every packet starts with sync byte 0x47. Plain .mts/.m2ts
        # use 188-byte packets (sync at offset 0); AVCHD/BDAV camcorder files
        # prefix each packet with a 4-byte timecode, so sync lands at offset 4.
        if len(head) >= 1 and head[0] == 0x47:
            return True, ""
        if len(head) >= 5 and head[4] == 0x47:
            return True, ""
        return fail("MPEG-TS sync byte (47) at offset 0 or 4")

    # .csv, .txt, .rtf, .aac: no single reliable fixed signature worth
    # gating on here -- .rtf's own "{\rtf" check already lives in the full
    # document validator, and .aac frame sync is too ambiguous to be a
    # useful fast filter. Fall through to the full validator.
    return None
