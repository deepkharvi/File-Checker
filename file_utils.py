"""Filesystem helpers: category lookup, duplicate-safe move/copy."""
import os
import shutil

from models import EXT_TO_CATEGORY, DuplicatePolicy

WORKING_DIR_NAME = "Working Files"
NON_WORKING_DIR_NAME = "Non-Working Files"
RESERVED_DIR_NAMES = {WORKING_DIR_NAME, NON_WORKING_DIR_NAME}


def get_category(filename: str):
    ext = os.path.splitext(filename)[1].lower()
    return EXT_TO_CATEGORY.get(ext)


def ensure_output_dirs(root: str):
    working = os.path.join(root, WORKING_DIR_NAME)
    non_working = os.path.join(root, NON_WORKING_DIR_NAME)
    os.makedirs(working, exist_ok=True)
    os.makedirs(non_working, exist_ok=True)
    return working, non_working


def list_subfolders(root: str):
    """Immediate subfolders of root (sorted), excluding the two output folders
    and hidden/system folders."""
    names = []
    with os.scandir(root) as it:
        for entry in it:
            if not entry.is_dir():
                continue
            if entry.name in RESERVED_DIR_NAMES or entry.name.startswith((".", "$")):
                continue
            names.append(entry.name)
    return sorted(names, key=str.lower)


def count_scannable_files(folder: str, recursive: bool = True) -> int:
    """Number of files the scan would visit in a folder (used for the picker).

    recursive=True counts files in nested sub-folders too."""
    try:
        return len(list_scan_targets(folder, recursive=recursive))
    except OSError:
        return 0


def _skip_dir(name: str) -> bool:
    return name in RESERVED_DIR_NAMES or name.startswith((".", "$"))


def list_scan_targets(root: str, recursive: bool = True):
    """Files to scan under root.

    recursive=True walks every nested sub-folder; recursive=False returns only
    files directly inside root. The two output folders ('Working Files' /
    'Non-Working Files') and hidden/system folders are never entered, so
    already-sorted files are not scanned again."""
    targets = []
    if not recursive:
        with os.scandir(root) as it:
            for entry in it:
                if entry.is_dir():
                    continue
                targets.append(entry.path)
        return targets

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not _skip_dir(d)]  # prune in place
        for name in filenames:
            targets.append(os.path.join(dirpath, name))
    return targets


def list_dir_entries(path: str, include_output: bool = False):
    """(subfolders, files) directly inside `path`, each as a sorted list of
    (name, full_path). Hidden/system folders are skipped, and symlinked folders
    are not followed (no infinite loops). The 'Working Files' / 'Non-Working
    Files' output folders are skipped unless include_output=True (the tree
    picker shows them read-only so you can see where files ended up)."""
    dirs, files = [], []
    with os.scandir(path) as it:
        for entry in it:
            try:
                if entry.is_dir(follow_symlinks=False):
                    if include_output and entry.name in RESERVED_DIR_NAMES:
                        dirs.append((entry.name, entry.path))
                    elif not _skip_dir(entry.name):
                        dirs.append((entry.name, entry.path))
                else:
                    files.append((entry.name, entry.path))
            except OSError:
                continue
    dirs.sort(key=lambda x: x[0].lower())
    files.sort(key=lambda x: x[0].lower())
    return dirs, files


def _unique_path(dest_dir: str, filename: str) -> str:
    base, ext = os.path.splitext(filename)
    candidate = os.path.join(dest_dir, filename)
    counter = 1
    while os.path.exists(candidate):
        candidate = os.path.join(dest_dir, f"{base} ({counter}){ext}")
        counter += 1
    return candidate


def resolve_destination(dest_dir: str, filename: str, policy: DuplicatePolicy):
    """Return (dest_path, action) where action is 'proceed' or 'skip'."""
    target = os.path.join(dest_dir, filename)
    if not os.path.exists(target):
        return target, "proceed"

    if policy == DuplicatePolicy.SKIP:
        return target, "skip"
    if policy == DuplicatePolicy.REPLACE:
        return target, "proceed"
    # RENAME (default / safest)
    return _unique_path(dest_dir, filename), "proceed"


def transfer_file(src_path: str, dest_dir: str, mode, policy: DuplicatePolicy):
    """Move or copy src_path into dest_dir, honoring the duplicate policy.
    Returns (dest_path_or_None, note)."""
    filename = os.path.basename(src_path)
    dest_path, action = resolve_destination(dest_dir, filename, policy)
    if action == "skip":
        return None, "Skipped: destination filename already exists"

    from models import FileMode
    if mode == FileMode.COPY:
        shutil.copy2(src_path, dest_path)
    else:
        shutil.move(src_path, dest_path)
    return dest_path, ""
