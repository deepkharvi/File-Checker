"""Data models used throughout the File Health Checker."""
from dataclasses import dataclass, field
from enum import Enum


class Status(str, Enum):
    WORKING = "Working"
    NON_WORKING = "Non-Working"
    UNSUPPORTED = "Unsupported"
    PERMISSION_ERROR = "Permission Error"
    LOCKED = "Locked/In Use"
    UNKNOWN_ERROR = "Unknown/Error"


class FileMode(str, Enum):
    MOVE = "Move"
    COPY = "Copy"


class DuplicatePolicy(str, Enum):
    RENAME = "Rename automatically"
    SKIP = "Skip"
    REPLACE = "Replace existing file"


# Category -> tuple of supported extensions (lower-case, with dot)
CATEGORY_EXTENSIONS = {
    "Video": (".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".mts", ".m2ts",
              ".mpg", ".mpeg", ".3gp", ".vob"),
    "Audio": (".mp3", ".wav", ".aac", ".flac", ".ogg", ".m4a", ".wma"),
    "Image": (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".tif", ".ico",
              ".psd", ".heic", ".heif"),
    "RAW Image": (".arw", ".cr2", ".cr3", ".nef", ".dng", ".orf", ".rw2", ".raf", ".pef", ".srw"),
    "PDF": (".pdf",),
    "Document": (".doc", ".docx", ".rtf", ".odt", ".txt"),
    "Spreadsheet": (".xls", ".xlsx", ".csv", ".ods"),
}

EXT_TO_CATEGORY = {
    ext: category
    for category, exts in CATEGORY_EXTENSIONS.items()
    for ext in exts
}


@dataclass
class FileResult:
    path: str
    filename: str
    category: str
    size: int
    status: Status = Status.UNKNOWN_ERROR
    reason: str = ""
    scan_duration: float = 0.0
    dest_path: str = ""


@dataclass
class ScanSummary:
    total: int = 0
    scanned: int = 0
    working: int = 0
    non_working: int = 0
    unsupported: int = 0
    errors: int = 0
    elapsed_seconds: float = 0.0
    results: list = field(default_factory=list)
