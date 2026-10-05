"""Background scan orchestration.

Runs on a QThread so the GUI stays responsive. File validation is
I/O-bound (opening/parsing files), so a ThreadPoolExecutor is used to
validate many files concurrently without loading large media files fully
into RAM -- each validator reads only what it needs (headers, probe
frames, page objects, etc.).

Moving/copying happens only after a file has been successfully
validated, and only from the single worker thread (not from the pool)
to keep filesystem operations race-free.
"""
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from PySide6.QtCore import QThread, Signal

from file_utils import ensure_output_dirs, get_category, list_scan_targets, transfer_file
from models import FileMode, DuplicatePolicy, FileResult, ScanSummary, Status
from validators import validate_file

PROGRESS_THROTTLE_SECONDS = 0.1
DEFAULT_MAX_WORKERS = min(32, (os.cpu_count() or 4) * 4)


class ScannerWorker(QThread):
    progress = Signal(dict)     # periodic UI update payload
    finished_scan = Signal(object)  # ScanSummary

    def __init__(self, folders, mode: FileMode, policy: DuplicatePolicy,
                 max_workers: int = DEFAULT_MAX_WORKERS, thorough: bool = True,
                 non_recursive=(), files=(), parent=None):
        """`folders` is a folder path or a list of folder paths (scanned whole).
        `files` is an explicit list of individual file paths (from the tree
        picker). Either way, each file is sorted into the 'Working Files' /
        'Non-Working Files' folders of the folder it was found in."""
        super().__init__(parent)
        self.folders = [folders] if isinstance(folders, str) else list(folders)
        self.files = list(files)
        self.mode = mode
        self.policy = policy
        self.max_workers = max_workers
        self.thorough = thorough
        # folders in this set are scanned top-level only; all others recursively
        self.non_recursive = {os.path.normpath(f) for f in non_recursive}
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def run(self):
        start_time = time.time()
        targets = []
        out_dirs = {}  # file path -> (working_dir, non_working_dir)
        # Each file's Working/Non-Working destination lives in the SAME folder
        # the file was found in (not the top-level folder the user selected),
        # so scanning a parent folder recursively sorts each subfolder's files
        # into that subfolder, instead of pooling everything into the root.
        dir_cache = {}  # folder -> (working_dir, non_working_dir), built lazily
        seen = set()

        def add_target(p):
            if p in seen or not os.path.isfile(p):
                return
            seen.add(p)
            targets.append(p)
            parent = os.path.dirname(p)
            dirs = dir_cache.get(parent)
            if dirs is None:
                dirs = ensure_output_dirs(parent)
                dir_cache[parent] = dirs
            out_dirs[p] = dirs

        for p in self.files:
            add_target(p)
        for folder in self.folders:
            recursive = os.path.normpath(folder) not in self.non_recursive
            for p in list_scan_targets(folder, recursive=recursive):
                add_target(p)

        summary = ScanSummary(total=len(targets))
        last_emit = 0.0
        current_file_name = ""

        def validate_one(path):
            filename = os.path.basename(path)
            category = get_category(filename)
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0

            if category is None:
                return FileResult(path=path, filename=filename, category="Unsupported",
                                   size=size, status=Status.UNSUPPORTED,
                                   reason="File extension not in a supported category")

            t0 = time.time()
            status, reason = validate_file(path, category, thorough=self.thorough)
            duration = time.time() - t0
            return FileResult(path=path, filename=filename, category=category, size=size,
                               status=status, reason=reason, scan_duration=duration)

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            future_to_path = {pool.submit(validate_one, p): p for p in targets}

            for future in as_completed(future_to_path):
                if self._stop_event.is_set():
                    for f in future_to_path:
                        f.cancel()
                    break

                result = future.result()
                current_file_name = result.filename
                summary.scanned += 1

                if result.status == Status.WORKING:
                    summary.working += 1
                    dest_dir = out_dirs[result.path][0]
                elif result.status == Status.UNSUPPORTED:
                    summary.unsupported += 1
                    dest_dir = None
                elif result.status in (Status.NON_WORKING,):
                    summary.non_working += 1
                    dest_dir = out_dirs[result.path][1]
                else:  # PERMISSION_ERROR, LOCKED, UNKNOWN_ERROR
                    summary.errors += 1
                    dest_dir = out_dirs[result.path][1]

                if dest_dir is not None:
                    try:
                        dest_path, note = transfer_file(result.path, dest_dir, self.mode, self.policy)
                        if dest_path:
                            result.dest_path = dest_path
                        elif note:
                            result.reason = (result.reason + " | " if result.reason else "") + note
                    except Exception as exc:  # noqa: BLE001
                        result.reason = (result.reason + " | " if result.reason else "") + \
                            f"Transfer failed: {exc}"

                summary.results.append(result)

                now = time.time()
                if now - last_emit >= PROGRESS_THROTTLE_SECONDS or summary.scanned == summary.total:
                    elapsed = now - start_time
                    speed = summary.scanned / elapsed if elapsed > 0 else 0.0
                    remaining = summary.total - summary.scanned
                    eta = remaining / speed if speed > 0 else 0.0
                    self.progress.emit({
                        "total": summary.total,
                        "scanned": summary.scanned,
                        "working": summary.working,
                        "non_working": summary.non_working,
                        "unsupported": summary.unsupported,
                        "errors": summary.errors,
                        "current_file": current_file_name,
                        "speed": speed,
                        "eta_seconds": eta,
                    })
                    last_emit = now

        summary.elapsed_seconds = time.time() - start_time
        self.finished_scan.emit(summary)
