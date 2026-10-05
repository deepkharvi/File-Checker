"""
File-type-specific validation.

Each validator performs a real, format-appropriate check rather than
just confirming the file exists / has non-zero size:

- Video   -> OpenCV opens the container and decodes probe frames
             (start / middle / near-end) without loading the whole file.
- Audio   -> mutagen parses the container/codec headers.
- Image   -> Pillow verifies structure, then decodes pixel data.
- PDF     -> pypdf opens the file and walks the page tree.
- Word    -> python-docx for .docx; OLE structural check for legacy .doc;
             signature + decode check for .rtf/.txt.
- Excel   -> openpyxl for .xlsx, xlrd for legacy .xls, csv module for
             .csv, zip/mimetype check for .ods.

Every validator returns (Status, reason). Validators never raise -- any
unexpected exception is caught and classified as UNKNOWN_ERROR so one bad
file can never crash a scan.
"""
import csv
import os
import sys
import tempfile
import threading
import zipfile

from models import Status
from signatures import check_header


def _is_permission_error(exc: Exception) -> bool:
    return isinstance(exc, PermissionError)


def _is_locked_error(exc: Exception) -> bool:
    # Windows raises WinError 32 (ERROR_SHARING_VIOLATION) for locked/in-use files.
    if isinstance(exc, OSError):
        winerr = getattr(exc, "winerror", None)
        if winerr in (32, 33):
            return True
    return False


def _classify_os_exception(exc: Exception):
    if _is_permission_error(exc):
        return Status.PERMISSION_ERROR, f"Permission denied: {exc}"
    if _is_locked_error(exc):
        return Status.LOCKED, f"File is locked/in use: {exc}"
    return None


# ---------------------------------------------------------------- Video ----
# Above this size, video validation switches to a fast path: first-frame
# decode + container metadata sanity check only, skipping the additional
# seek-based probes. Seeking into large, high-bitrate footage (typical of
# DSLR/iPhone 10-bit HEVC 4K) is expensive -- the decoder has to walk
# forward from the last keyframe, which for long GOPs on this kind of
# footage can take seconds per seek. A corrupted file overwhelmingly fails
# on the very first frame anyway, so this trades a small amount of
# deep-corruption coverage for a large, predictable speed win on exactly
# the files that were slow.
VIDEO_LIGHT_CHECK_THRESHOLD_BYTES = 150 * 1024 * 1024  # 150 MB


def _get_video_track_duration_ms(path: str):
    """Parse moov to find the VIDEO track specifically, rather than using
    mvhd's movie-level duration.

    mvhd's duration reflects the LONGEST track in the file -- which is very
    often the audio track. It's completely normal for a phone/camera
    recording's audio to run a second or two longer or shorter than its
    video track; that's not corruption. Comparing decoded video frames
    against the movie-level duration causes false positives on ordinary,
    perfectly valid files. Comparing against the video track's own mdhd
    duration instead avoids that.

    Returns the video track's declared duration in milliseconds, or None if
    it can't be determined (never a failure signal by itself -- callers
    fall back to the movie-level duration when this isn't available).
    """
    try:
        with open(path, "rb") as f:
            file_size = os.fstat(f.fileno()).st_size

            def iter_boxes(start, end):
                pos = start
                while pos + 8 <= end:
                    f.seek(pos)
                    header = f.read(8)
                    if len(header) < 8:
                        return
                    box_size = int.from_bytes(header[0:4], "big")
                    box_type = header[4:8]
                    header_len = 8
                    if box_size == 1:
                        ext = f.read(8)
                        if len(ext) < 8:
                            return
                        box_size = int.from_bytes(ext, "big")
                        header_len = 16
                    elif box_size == 0:
                        box_size = end - pos
                    if box_size < header_len:
                        return
                    yield box_type, pos + header_len, min(pos + box_size, end)
                    pos += box_size

            def read_duration_ms(header_start):
                f.seek(header_start)
                version_flags = f.read(4)
                if len(version_flags) < 4:
                    return None
                if version_flags[0] == 1:
                    body = f.read(28)
                    if len(body) < 28:
                        return None
                    timescale = int.from_bytes(body[16:20], "big")
                    duration = int.from_bytes(body[20:28], "big")
                else:
                    body = f.read(16)
                    if len(body) < 16:
                        return None
                    timescale = int.from_bytes(body[8:12], "big")
                    duration = int.from_bytes(body[12:16], "big")
                if timescale <= 0 or duration <= 0:
                    return None
                return (duration / timescale) * 1000

            moov_range = None
            for box_type, data_start, data_end in iter_boxes(0, file_size):
                if box_type == b"moov":
                    moov_range = (data_start, data_end)
                    break
            if moov_range is None:
                return None

            best_video_duration_ms = None
            for box_type, trak_start, trak_end in iter_boxes(*moov_range):
                if box_type != b"trak":
                    continue
                mdia_range = None
                for sub_type, sub_start, sub_end in iter_boxes(trak_start, trak_end):
                    if sub_type == b"mdia":
                        mdia_range = (sub_start, sub_end)
                        break
                if mdia_range is None:
                    continue

                is_video = False
                mdhd_ms = None
                for m_type, m_start, m_end in iter_boxes(*mdia_range):
                    if m_type == b"hdlr":
                        f.seek(m_start + 8)  # skip version+flags(4) + pre_defined(4)
                        if f.read(4) == b"vide":
                            is_video = True
                    elif m_type == b"mdhd":
                        mdhd_ms = read_duration_ms(m_start)

                if is_video and mdhd_ms:
                    if best_video_duration_ms is None or mdhd_ms > best_video_duration_ms:
                        best_video_duration_ms = mdhd_ms

            return best_video_duration_ms
    except Exception:
        return None


def _get_mp4_duration_ms(path: str):
    """Parse the moov/mvhd box directly to read the container's own declared
    duration, in milliseconds -- independent of OpenCV/FFmpeg's frame-count
    and fps estimation, which is unreliable for many real-world files
    (variable frame rate, certain phone/social-media encoders, and -- most
    importantly for corruption detection -- the exact truncated/broken files
    we're trying to catch).

    mvhd's duration+timescale fields are written once at file creation and
    give an authoritative "this file is supposed to span N seconds" ground
    truth to compare an actual decode against.

    Returns None if the box tree couldn't be parsed. Never a failure signal
    by itself -- callers fall back to other checks when this is unavailable.
    """
    try:
        with open(path, "rb") as f:
            file_size = os.fstat(f.fileno()).st_size

            def iter_boxes(start, end):
                pos = start
                while pos + 8 <= end:
                    f.seek(pos)
                    header = f.read(8)
                    if len(header) < 8:
                        return
                    box_size = int.from_bytes(header[0:4], "big")
                    box_type = header[4:8]
                    header_len = 8
                    if box_size == 1:
                        ext = f.read(8)
                        if len(ext) < 8:
                            return
                        box_size = int.from_bytes(ext, "big")
                        header_len = 16
                    elif box_size == 0:
                        box_size = end - pos
                    if box_size < header_len:
                        return
                    yield box_type, pos + header_len, min(pos + box_size, end)
                    pos += box_size

            for box_type, data_start, data_end in iter_boxes(0, file_size):
                if box_type != b"moov":
                    continue
                for sub_type, sub_start, sub_end in iter_boxes(data_start, data_end):
                    if sub_type != b"mvhd":
                        continue
                    f.seek(sub_start)
                    version_flags = f.read(4)
                    if len(version_flags) < 4:
                        return None
                    if version_flags[0] == 1:
                        body = f.read(28)  # 2x8 (times) + 4 (timescale) + 8 (duration)
                        if len(body) < 28:
                            return None
                        timescale = int.from_bytes(body[16:20], "big")
                        duration = int.from_bytes(body[20:28], "big")
                    else:
                        body = f.read(16)  # 2x4 (times) + 4 (timescale) + 4 (duration)
                        if len(body) < 16:
                            return None
                        timescale = int.from_bytes(body[8:12], "big")
                        duration = int.from_bytes(body[12:16], "big")
                    if timescale <= 0 or duration <= 0:
                        return None
                    return (duration / timescale) * 1000
                return None  # moov found but no mvhd inside it
        return None
    except Exception:
        return None


def _has_top_level_atom(path: str, target: bytes, max_atoms: int = 100_000):
    """Walk top-level ISO-BMFF boxes (mp4/mov/m4v/m4a structure) looking for
    a box of type `target` (e.g. b'moov'). Only reads 8-16 byte box headers
    and seeks past each box's data using its declared size -- it never reads
    actual video/audio bytes, so this is fast even on huge files.

    Returns True if found, False if the file was fully walked without
    finding it, or None if the structure looked malformed/unreadable
    (inconclusive -- caller should not fail the file on that alone).
    """
    try:
        with open(path, "rb") as f:
            file_size = os.fstat(f.fileno()).st_size
            pos = 0
            count = 0
            while pos < file_size and count < max_atoms:
                f.seek(pos)
                header = f.read(8)
                if len(header) < 8:
                    break
                box_size = int.from_bytes(header[0:4], "big")
                box_type = header[4:8]
                if box_type == target:
                    return True

                if box_size == 1:
                    ext_size_bytes = f.read(8)
                    if len(ext_size_bytes) < 8:
                        break
                    box_size = int.from_bytes(ext_size_bytes, "big")
                elif box_size == 0:
                    # Box extends to end of file -- nothing follows it.
                    return False

                if box_size < 8:
                    return None  # malformed box size -- inconclusive
                pos += box_size
                count += 1
        return False
    except Exception:
        return None  # any I/O oddity -- inconclusive, don't fail the file on this alone


def _probe_container_duration_ms(path: str):
    """Format-agnostic duration ground truth: ask FFmpeg itself (via a
    throwaway VideoCapture, kept completely separate from the one used for
    the real frame-by-frame decode) for its own internal duration estimate
    by seeking to ~99.9% of the stream and reading the timestamp it lands
    on. This works across mp4, mkv, avi, webm, wmv, flv and anything else
    FFmpeg can open -- unlike the hand-written mp4 box parser above, which
    only understands ISO-BMFF containers, and unlike CAP_PROP_FRAME_COUNT,
    which is well known to be wrong or unavailable for many real-world
    files regardless of format.

    Returns None if the probe seek didn't produce a usable result (never a
    failure signal by itself).
    """
    probe = None
    try:
        import cv2
        probe = cv2.VideoCapture(path)
        if not probe.isOpened():
            return None
        probe.set(cv2.CAP_PROP_POS_AVI_RATIO, 0.999)
        ts = probe.get(cv2.CAP_PROP_POS_MSEC)
        if ts and ts > 0:
            return ts / 0.999  # scale the 99.9%-mark timestamp back up to ~100%
        return None
    except Exception:
        return None
    finally:
        if probe is not None:
            probe.release()


_video_stderr_lock = threading.Lock()

# FFmpeg (the engine behind OpenCV's video decoding) prints these directly
# to the OS-level stderr stream -- bypassing Python's sys.stderr entirely,
# since it's native C library output -- every time it hits genuinely
# corrupted, missing, or malformed data. Crucially, this includes the exact
# moment FFmpeg silently CONCEALS a broken frame (repeats the last good one
# instead of failing outright) to keep playback going -- which is precisely
# the "plays fine, then glitches/stutters" case that a pure success/failure
# check on cap.read() can never see, because concealment makes read()
# report success. This is the single most direct, authoritative signal of
# real corruption available: FFmpeg's own decoder telling us something is
# actually wrong, rather than us inferring it indirectly.
_FFMPEG_ERROR_MARKERS = (
    "concealing", "corrupt", "error while decoding", "decode_slice_header",
    "invalid data found", "invalid nal", "missing picture",
    "moov atom not found", "error splitting", "damaged", "truncat",
    "incomplete frame", "decode error", "decode_error", "referenced qscale",
    "co located pocs unavailable", "non-existing pps", "cabac decode",
)


def _scan_for_ffmpeg_errors(text: str):
    """Return the list of distinct FFmpeg error markers found in captured
    native stderr text, or an empty list if none. Never raises."""
    if not text:
        return []
    lower = text.lower()
    return [m for m in _FFMPEG_ERROR_MARKERS if m in lower]


class _NativeStderrCapture:
    """Redirects the OS-level (file descriptor 2) stderr stream to a temp
    file for the duration of the `with` block, so native/C-library output
    -- FFmpeg's warnings and errors, which print directly and bypass
    Python's sys.stderr entirely -- can be captured and inspected
    afterward. `.text` is populated once the block exits.

    stderr is a single process-wide resource, so any use of this MUST be
    serialized across threads -- always acquire `_video_stderr_lock` for
    the entire duration this is in use.
    """

    def __enter__(self):
        self.text = ""
        self._active = False
        try:
            self._stderr_fd = sys.stderr.fileno()
        except (AttributeError, ValueError, OSError):
            # Windowed (no-console) build: sys.stderr is None -> skip capture.
            return self
        self._saved_fd = os.dup(self._stderr_fd)
        self._tmp = tempfile.TemporaryFile(mode="w+b")
        sys.stderr.flush()
        os.dup2(self._tmp.fileno(), self._stderr_fd)
        self._active = True
        return self

    def __exit__(self, exc_type, exc, tb):
        if not self._active:
            return False
        sys.stderr.flush()
        os.dup2(self._saved_fd, self._stderr_fd)
        os.close(self._saved_fd)
        self._tmp.seek(0)
        self.text = self._tmp.read().decode("utf-8", errors="replace")
        self._tmp.close()
        return False


def validate_video(path: str, thorough: bool = True):
    try:
        import cv2
    except ImportError as e:
        return Status.UNKNOWN_ERROR, f"OpenCV (opencv-python-headless) failed to load: {e}"

    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    light_mode = size > VIDEO_LIGHT_CHECK_THRESHOLD_BYTES and not thorough
    ext = os.path.splitext(path)[1].lower()

    try:
        with _video_stderr_lock, _NativeStderrCapture() as _early_stderr:
            cap = cv2.VideoCapture(path)
            if not cap.isOpened():
                cap.release()
                return Status.NON_WORKING, "Video container could not be opened"

            # The first sequential frame decode is the reliable corruption signal --
            # if this fails, the file genuinely can't be played.
            ok, _ = cap.read()
        if not ok:
            cap.release()
            return Status.NON_WORKING, "First video frame could not be decoded"

        # ISO-BMFF containers (mp4/mov/m4v/m4a) need a 'moov' index atom to
        # be playable in standard players. A lenient decoder (FFmpeg, used
        # here) can often still read raw frames straight out of 'mdat' even
        # when 'moov' never finished writing -- e.g. an interrupted
        # download/copy/transfer -- so "frame 1 decodes" alone is not
        # sufficient proof the file is actually playable. This check closes
        # that gap.
        if ext in (".mp4", ".mov", ".m4v", ".m4a", ".m4b", ".m4p", ".3gp"):
            has_moov = _has_top_level_atom(path, b"moov")
            if has_moov is False:
                cap.release()
                return Status.NON_WORKING, (
                    "Missing 'moov' index atom -- file is truncated/incomplete "
                    "(the index never finished writing, typically from an "
                    "interrupted download, copy, or transfer). Raw frame data "
                    "is present and lenient decoders can still read it, but "
                    "standard players -- including Windows' built-in player -- "
                    "will refuse to play this file (error 0xc00d36e5)."
                )
            # True -> confirmed fine. None -> inconclusive; don't fail on this
            # check alone, fall through to the normal frame-probe result.

        if thorough:
            # Decode every single frame sequentially from start to end -- the
            # only way to guarantee corruption ANYWHERE in the file (not
            # just at the start, a handful of sampled points, or the end)
            # is caught. Slower by design; the user has explicitly opted
            # into full accuracy over speed.
            #
            # Ground truth for "how far should this have gotten": for
            # ISO-BMFF containers, prefer parsing the real duration straight
            # out of the file's own box structure (most precise, and
            # verified independent of OpenCV's own estimation). For every
            # format (including mkv/avi/webm/wmv/flv, which the box parser
            # above can't read at all), fall back to asking FFmpeg directly
            # for its internal duration estimate -- format-agnostic and far
            # more reliable than CAP_PROP_FRAME_COUNT, which is used only as
            # a last resort since it's well known to be wrong or missing
            # for many real-world files.
            declared_duration_ms = None
            _probe_stderr_text = ""
            if ext in (".mp4", ".mov", ".m4v", ".m4a", ".m4b", ".m4p", ".3gp"):
                declared_duration_ms = _get_video_track_duration_ms(path)
                if declared_duration_ms is None:
                    declared_duration_ms = _get_mp4_duration_ms(path)
            if declared_duration_ms is None:
                # This probe opens its own separate VideoCapture and seeks
                # near the end of the file -- on a corrupted file that can
                # trigger the same native FFmpeg errors as the main decode,
                # so it needs the same stderr capture to avoid missing (or
                # leaking to the console) a genuine corruption signal here.
                with _video_stderr_lock, _NativeStderrCapture() as _probe_stderr:
                    declared_duration_ms = _probe_container_duration_ms(path)
                _probe_stderr_text = _probe_stderr.text

            fps = cap.get(cv2.CAP_PROP_FPS) or 0
            frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
            if declared_duration_ms is None and fps > 0 and frame_count > 1:
                declared_duration_ms = (frame_count / fps) * 1000
            expected_interval_ms = (1000.0 / fps) if fps > 0 else None

            # Stutter/freeze detection: a file can decode every frame
            # successfully and STILL play back laggy/frozen in a real
            # player. Two independent technical signals catch that, and we
            # only flag a file when BOTH show up together in the same
            # stretch of the file -- deliberately conservative, because
            # either signal alone is ambiguous: content can be legitimately
            # static (security cam, screen recording, slideshow) without
            # being broken, and timing can jitter slightly on perfectly
            # normal variable-frame-rate video.
            #   1. Frozen content: consecutive decoded frames that are
            #      near-identical -- can mean the decoder silently repeated
            #      the last good frame after hitting damaged data.
            #   2. Irregular timing: a frame arriving far later than the
            #      expected interval -- a genuine playback stall.
            FREEZE_DIFF_THRESHOLD = 2.0       # mean abs diff on a 0-255 scale, tiny = "same picture"
            FREEZE_MIN_SECONDS = 4.0          # ignore short freezes -- normal encoder behavior
            GAP_MULTIPLIER = 8.0              # a gap this many times the expected interval = irregular
            IRREGULAR_GAP_FRACTION = 0.20     # require pervasive irregularity, not occasional VFR jitter

            prev_small = None
            freeze_run_frames = 0
            worst_freeze_frames = 0
            worst_freeze_had_gap = False
            current_run_has_gap = False
            irregular_gap_count = 0

            decoded = 1  # first frame already decoded above
            last_good_timestamp_ms = cap.get(cv2.CAP_PROP_POS_MSEC) or 0
            prev_timestamp_ms = last_good_timestamp_ms
            with _video_stderr_lock, _NativeStderrCapture() as _loop_stderr:
                while True:
                    ok, frame = cap.read()
                    if not ok:
                        break
                    decoded += 1

                    ts = cap.get(cv2.CAP_PROP_POS_MSEC) or last_good_timestamp_ms
                    gap_ms = ts - prev_timestamp_ms
                    is_irregular_gap = expected_interval_ms is not None and gap_ms > expected_interval_ms * GAP_MULTIPLIER
                    if is_irregular_gap:
                        irregular_gap_count += 1

                    try:
                        small = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (16, 16))
                        is_frozen = (
                            prev_small is not None
                            and cv2.absdiff(small, prev_small).mean() < FREEZE_DIFF_THRESHOLD
                        )
                    except Exception:
                        small = None
                        is_frozen = False

                    if is_frozen:
                        freeze_run_frames += 1
                        current_run_has_gap = current_run_has_gap or is_irregular_gap
                    else:
                        if freeze_run_frames > worst_freeze_frames:
                            worst_freeze_frames = freeze_run_frames
                            worst_freeze_had_gap = current_run_has_gap
                        freeze_run_frames = 0
                        current_run_has_gap = False

                    prev_small = small
                    prev_timestamp_ms = ts
                    last_good_timestamp_ms = ts

            if freeze_run_frames > worst_freeze_frames:
                worst_freeze_frames = freeze_run_frames
                worst_freeze_had_gap = current_run_has_gap

            cap.release()

            ffmpeg_errors = _scan_for_ffmpeg_errors(_early_stderr.text + "\n" + _probe_stderr_text + "\n" + _loop_stderr.text)

            if declared_duration_ms and declared_duration_ms > 0:
                reached_fraction = last_good_timestamp_ms / declared_duration_ms
                if reached_fraction < 0.90:
                    return Status.NON_WORKING, (
                        f"Playback stopped early: decoded only "
                        f"{last_good_timestamp_ms / 1000:.1f}s of a declared "
                        f"{declared_duration_ms / 1000:.1f}s "
                        f"({reached_fraction * 100:.0f}%) -- file starts fine "
                        f"but is corrupted or truncated partway through"
                    )

            # Frame-count cross-check: a damaged file can "reach the end" in
            # time while whole stretches of frames were silently dropped (the
            # decoder skips unreadable data without raising an error).
            # Containers whose frame count is stored EXACTLY (MP4/MOV sample
            # table, AVI header) are held to a tight tolerance; containers
            # where the count is only an estimate (duration x fps -- mkv, webm,
            # flv, wmv, mpg, ts) get a loose one so variable-frame-rate
            # recordings (screen captures etc.) are never falsely flagged.
            if fps > 0 and 0 < frame_count < 50_000_000 and decoded > 0:
                # Expected = the SMALLER of (a) the index's frame count and
                # (b) declared playing time x fps. (a) alone over-counts for
                # trimmed MP4s (edit lists hide pre-roll frames); (b) alone
                # over-counts for variable-frame-rate video. The minimum is
                # safe for both, and still exposes silently dropped frames.
                expected = float(frame_count)
                if declared_duration_ms and declared_duration_ms > 0:
                    expected = min(expected, declared_duration_ms / 1000.0 * fps)
                if ext in (".mp4", ".mov", ".m4v", ".3gp"):
                    # movie-level duration already has edit lists applied
                    mvhd_ms = _get_mp4_duration_ms(path)
                    if mvhd_ms and mvhd_ms > 0:
                        expected = min(expected, mvhd_ms / 1000.0 * fps)
                missing = int(round(expected)) - decoded
                if ext in (".mp4", ".mov", ".m4v", ".3gp", ".avi"):
                    lost = missing >= max(4, 0.015 * expected)
                else:
                    lost = missing >= max(15, 0.25 * expected)
                if lost:
                    return Status.NON_WORKING, (
                        f"Frames missing: about {int(round(expected))} frames expected but only "
                        f"{decoded} could be decoded ({missing} lost, "
                        f"{100.0 * missing / expected:.1f}%) -- damaged data was "
                        f"skipped, which shows up as jumps/glitches in playback"
                    )

            if ffmpeg_errors:
                # FFmpeg's own decoder reported real problems during decode
                # (including silent error concealment, which lets cap.read()
                # keep returning True while actually repeating/patching
                # broken frames) -- this is a direct, authoritative signal
                # of corruption, independent of whether duration or frame
                # count happened to look normal.
                return Status.NON_WORKING, (
                    f"FFmpeg reported decode problems while reading this file "
                    f"({', '.join(ffmpeg_errors)}) -- frames may be silently "
                    f"repaired/concealed or corrupted, which will show up as "
                    f"glitching, freezing, or lag during real playback even "
                    f"though the file technically finished decoding"
                )

            worst_freeze_seconds = (worst_freeze_frames / fps) if fps > 0 else 0
            total_frame_gaps = max(decoded - 1, 1)
            irregular_fraction = (irregular_gap_count / total_frame_gaps) if expected_interval_ms is not None else 0

            # NOTE: freeze/stutter and irregular-timing detection are kept
            # as INFORMATIONAL notes only, appended to the reason text below
            # -- they no longer change the Working/Non-Working verdict.
            # They were causing real, playable videos to be misclassified
            # (a heuristic tuned only against synthetic test clips, not
            # against real-world footage, is too risky to use as a hard
            # pass/fail signal). The verdict is now driven solely by the
            # declared-duration check above, which has been directly
            # verified against real truncated files.
            stutter_note = ""
            if worst_freeze_had_gap and worst_freeze_seconds >= FREEZE_MIN_SECONDS:
                stutter_note = (
                    f" [note: ~{worst_freeze_seconds:.1f}s stretch of frozen "
                    f"frames + irregular timing detected -- worth a manual "
                    f"look, not treated as failing on its own]"
                )
            elif irregular_fraction > IRREGULAR_GAP_FRACTION:
                stutter_note = (
                    f" [note: irregular frame timing at {irregular_gap_count} "
                    f"of {total_frame_gaps} transitions -- worth a manual "
                    f"look, not treated as failing on its own]"
                )

            if declared_duration_ms and declared_duration_ms > 0:
                return Status.WORKING, (
                    f"Playable (thorough check: decoded start to end, "
                    f"{last_good_timestamp_ms / 1000:.1f}s of "
                    f"{declared_duration_ms / 1000:.1f}s declared duration, "
                    f"{decoded} frames){stutter_note}"
                )

            # No independent duration ground truth available at all (rare --
            # typically an unusual container OpenCV can't introspect well).
            # We still decoded every frame without a single failure, which
            # is itself a meaningful result; just note duration couldn't be
            # cross-checked.
            return Status.WORKING, (
                f"Playable (thorough check: all {decoded} frames decoded "
                f"start to end with no failures; declared duration could not "
                f"be independently verified for this file){stutter_note}"
            )

        if light_mode:
            width = cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0
            height = cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0
            if width <= 0 or height <= 0:
                cap.release()
                return Status.NON_WORKING, "Video metadata invalid (no readable resolution)"

            # Even for large files we skip the full deep scan, but a single
            # cheap seek-and-decode near the midpoint catches the common
            # "starts fine, breaks partway through" case that metadata alone
            # can't reveal -- without the cost of scanning the whole file.
            fps = cap.get(cv2.CAP_PROP_FPS) or 0
            frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
            mid_ok = True
            if fps > 0 and frame_count > 1:
                duration_ms = (frame_count / fps) * 1000
                cap.set(cv2.CAP_PROP_POS_MSEC, duration_ms * 0.5)
                mid_ok, _ = cap.read()
            cap.release()

            if not mid_ok:
                return Status.NON_WORKING, (
                    "Frame decode failed partway through the file (~50% mark) -- "
                    "the file starts fine but appears corrupted or truncated in "
                    "the middle"
                )
            return Status.WORKING, (
                "Playable (fast check: large file -- first frame, midpoint "
                "frame, and metadata verified; full deep scan skipped for speed)"
            )

        # Smaller/lighter files: probe further into the file WITHOUT decoding
        # everything, using time-based seeking. Frame-count/seek metadata is
        # unreliable for web-optimized video (VP9/webm, variable frame rate),
        # so a single isolated failed seek is recorded as inconclusive rather
        # than a failure. Multiple failures across spread-out points, though,
        # are a much stronger corruption signal and are treated as real.
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        seek_issues = []
        if fps > 0 and frame_count > 1:
            duration_ms = (frame_count / fps) * 1000
            for fraction in (0.25, 0.5, 0.75, 0.9):
                cap.set(cv2.CAP_PROP_POS_MSEC, duration_ms * fraction)
                ok, _ = cap.read()
                if not ok:
                    seek_issues.append(f"{int(fraction * 100)}%")

        cap.release()

        if len(seek_issues) >= 2:
            return Status.NON_WORKING, (
                f"Frame decode failed at multiple points in the file "
                f"({', '.join(seek_issues)} of duration) -- likely corrupted "
                f"or truncated partway through, not just an isolated seek quirk"
            )
        if seek_issues:
            return Status.WORKING, (
                f"Playable (first frame decoded fine); seek probe inconclusive at "
                f"{', '.join(seek_issues)} of duration -- common for web-optimized "
                f"video with imprecise frame-count metadata, not necessarily corruption"
            )
        return Status.WORKING, ""
    except Exception as exc:  # noqa: BLE001
        classified = _classify_os_exception(exc)
        if classified:
            return classified
        return Status.NON_WORKING, f"Video validation failed: {exc}"


# ---------------------------------------------------------------- Audio ----
def validate_audio(path: str, thorough: bool = False):
    try:
        import mutagen
    except ImportError:
        return Status.UNKNOWN_ERROR, "mutagen is not installed"

    try:
        f = mutagen.File(path)
        if f is None:
            return Status.NON_WORKING, "Audio headers could not be parsed"
        # A sane audio file reports a positive duration when info is available.
        info = getattr(f, "info", None)
        length = getattr(info, "length", None)
        if length is not None and length <= 0:
            return Status.NON_WORKING, "Audio file reports zero length"
        return Status.WORKING, ""
    except Exception as exc:  # noqa: BLE001
        classified = _classify_os_exception(exc)
        if classified:
            return classified
        return Status.NON_WORKING, f"Audio validation failed: {exc}"


# ---------------------------------------------------------------- Image ----
# Strictness is a PROCESS-WIDE setting, applied once here and never toggled
# again. Pillow's LOAD_TRUNCATED_IMAGES is a global flag; the scanner runs
# many files on many threads at once, so flipping it per call (as older code
# did, and as the RAW validator used to) lets one thread silently switch
# strict mode OFF for an image being checked on another thread. That race is
# one way damaged photos ended up in "Working Files".
try:
    from PIL import ImageFile as _PILImageFile
    _PILImageFile.LOAD_TRUNCATED_IMAGES = False
except ImportError:  # reported properly inside validate_image()
    pass

import threading as _threading

# A 12 MP JPEG is ~36 MB decoded, and we decode it more than once. With 32
# scanner threads that would be gigabytes of RAM, so bound concurrent decodes.
_JPEG_DECODE_SLOTS = _threading.BoundedSemaphore(max(2, min(8, (os.cpu_count() or 4))))

# Words libjpeg puts in its messages when the compressed data is damaged.
_JPEG_CORRUPTION_MARKERS = (
    "corrupt", "premature", "extraneous", "truncat", "invalid", "bogus",
    "unexpected", "not enough", "bad ", "insufficient", "missing",
)


def _strict_jpeg_decode(data: bytes):
    """Decode a JPEG with libjpeg-turbo *warnings promoted to errors*.

    Why Pillow alone is not enough: when JPEG data is damaged INSIDE the
    compressed stream (a chunk zeroed out, bad sectors, an interrupted copy
    that still ends in a valid FF D9, bit flips), libjpeg only raises a
    non-fatal WARNING ("Corrupt JPEG data: premature end of data segment"),
    fills the unreadable part with flat gray and carries on. Pillow never
    sees that warning, load() succeeds, and the file is reported Working --
    while Windows Explorer shows the gray block. `simplejpeg` bundles the same
    decoder but raises on those warnings.

    Returns (state, payload):
      ("ok",         ndarray)  decoded start-to-end with zero warnings
      ("damaged",    reason)   the JPEG data is damaged
      ("inconclusive", why)    check could not run (library missing or an
                               exotic JPEG variant it doesn't support)
    """
    try:
        import simplejpeg
    except ImportError:
        return "inconclusive", "simplejpeg not installed"
    try:
        header = simplejpeg.decode_jpeg_header(data)
        cs = str(header[2]).upper() if len(header) > 2 else ""
        colorspace = "GRAY" if cs.startswith("GRAY") else "CMYK" if cs == "CMYK" else "RGB"
        arr = simplejpeg.decode_jpeg(data, colorspace=colorspace,
                                     fastdct=False, fastupsample=False)
        return "ok", arr
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if any(m in msg.lower() for m in _JPEG_CORRUPTION_MARKERS):
            return "damaged", (
                f"JPEG image data is damaged (decoder reported: {msg}). "
                "The damaged area shows as gray/garbage in viewers."
            )
        return "inconclusive", msg


def _gray_fill_reason(arr, strict_decoder_passed: bool):
    """Safety net on the DECODED pixels.

    When libjpeg cannot read part of a baseline JPEG it fills those 16x16
    blocks with exact mid-gray (128,128,128). Real photos essentially never
    contain large regions of *exactly* flat 128 gray, so a sizeable run of
    them means damage. Blocks are checked in raster order, which is the order
    a JPEG is stored in, so truncation shows up as a run reaching the end.

    If the strict decoder already passed with no warnings, gray blocks are
    possibly genuine content (gray card, screenshot), but a flat gray patch
    must never be listed as Working, so it is still flagged -- at a slightly
    higher bar than when the strict decoder was unavailable.
    Returns a reason string if damage is found, else "".
    """
    try:
        import numpy as np
        if arr.ndim == 2:
            arr = arr[:, :, None]
        h, w, c = arr.shape
        b = 16
        hh, ww = (h // b) * b, (w // b) * b
        if hh == 0 or ww == 0:
            return ""
        blocks = arr[:hh, :ww].reshape(hh // b, b, ww // b, b, c)
        lo = blocks.min(axis=(1, 3, 4))
        hi = blocks.max(axis=(1, 3, 4))
        flat = ((lo >= 126) & (hi <= 130)).reshape(-1)
        total = flat.size
        count = int(flat.sum())
        if count == 0:
            return ""
        nz = np.flatnonzero(~flat)
        tail = total - 1 - int(nz[-1]) if nz.size else total

        if strict_decoder_passed:
            suspicious = tail >= max(8, int(total * 0.005)) or count >= max(16, int(total * 0.02))
        else:
            suspicious = tail >= max(4, int(total * 0.004)) or count >= max(8, int(total * 0.02))
        if suspicious:
            return (f"Decoded image has {100.0 * count / total:.1f}% flat mid-gray blocks "
                    f"({100.0 * tail / total:.1f}% at the end of the file) -- the typical "
                    f"fill for unreadable JPEG data")
    except Exception:  # noqa: BLE001
        pass
    return ""


def validate_image(path: str, thorough: bool = False):
    """Image checks are ALWAYS strict, whatever `thorough` says: a
    viewer-tolerant "looks fine" answer is exactly what lets gray-block photos
    slip into Working Files.

    For JPEG (the common camera format) three independent layers must all
    pass before a file is called Working:
      1. strict libjpeg decode (warnings = errors)   -> catches damaged data
      2. Pillow strict decode (truncation = error)   -> catches cut-off files
      3. decoded-pixel gray-fill scan                -> catches what's left
    """
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError:
        return Status.UNKNOWN_ERROR, "Pillow is not installed"

    ext = os.path.splitext(path)[1].lower()
    is_jpeg = ext in (".jpg", ".jpeg")

    if ext in (".heic", ".heif"):
        try:
            from pillow_heif import register_heif_opener
            register_heif_opener()
        except ImportError:
            return Status.UNKNOWN_ERROR, (
                "The 'pillow-heif' package is not installed, so HEIC/HEIF files cannot be "
                "verified. Run setup.bat again (or: pip install pillow-heif)."
            )
    if ext == ".psd":
        # Photoshop files can be far larger than Pillow's decompression-bomb limit.
        Image.MAX_IMAGE_PIXELS = None

    try:
        strict_passed = False
        arr = None
        if is_jpeg:
            try:
                import simplejpeg  # noqa: F401
            except ImportError:
                # Refuse to say "Working" on a weaker check -- that is exactly
                # how gray-block photos ended up in Working Files.
                return Status.UNKNOWN_ERROR, (
                    "The 'simplejpeg' package is not installed, so JPEGs cannot be "
                    "verified to 100%. Run setup.bat again (or: pip install simplejpeg)."
                )
            with open(path, "rb") as fh:
                data = fh.read()
            with _JPEG_DECODE_SLOTS:
                state, payload = _strict_jpeg_decode(data)
                if state == "damaged":
                    return Status.NON_WORKING, payload
                if state == "ok":
                    # simplejpeg/libjpeg promotes every concealment warning to
                    # an exception (see _strict_jpeg_decode), so reaching here
                    # with state == "ok" already proves zero bytes were
                    # concealed/gray-filled -- there is nothing left for a
                    # pixel-level gray-block guess to usefully add, and real
                    # photos routinely contain large near-128 regions (skies,
                    # shadows, seamless studio backdrops, grayscale photos).
                    # Running it anyway was flagging perfectly healthy photos
                    # as Non-Working. Trust the authoritative decoder result.
                    strict_passed = True
            arr = None
            data = None

        with _JPEG_DECODE_SLOTS if is_jpeg else _NullCtx():
            with Image.open(path) as img:
                img.load()  # full pixel decode; strict mode raises on truncation
                img.convert("RGB").load()
                if is_jpeg and not strict_passed:
                    import numpy as np
                    reason = _gray_fill_reason(np.asarray(img.convert("RGB")), False)
                    if reason:
                        return Status.NON_WORKING, reason
        return Status.WORKING, ""
    except UnidentifiedImageError:
        return Status.NON_WORKING, "Image format not recognized / header corrupted"
    except OSError as exc:
        if "truncated" in str(exc).lower():
            return Status.NON_WORKING, f"Image file is truncated/incomplete: {exc}"
        classified = _classify_os_exception(exc)
        if classified:
            return classified
        return Status.NON_WORKING, f"Image could not be decoded: {exc}"
    except Exception as exc:  # noqa: BLE001
        classified = _classify_os_exception(exc)
        if classified:
            return classified
        return Status.NON_WORKING, f"Image could not be decoded: {exc}"


class _NullCtx:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ------------------------------------------------------------ RAW Image ----
def validate_raw_image(path: str, thorough: bool = False):
    """Sensor RAW formats (.arw, .cr2, .nef, .dng, etc.) aren't standard
    image containers -- Pillow can't read them. rawpy wraps LibRaw, which
    can.

    Fast path: every RAW file normally carries a small embedded JPEG/bitmap
    preview. Decoding just that is dramatically faster than unpacking the
    full sensor array (a 50MB ARW's embedded thumbnail is typically well
    under 1MB) and still catches the vast majority of real corruption,
    since a broken/truncated file usually breaks the thumbnail too.

    Fallback: if there's no usable embedded thumbnail (or decoding it fails
    for any reason), we fall through to the slower full sensor-data unpack
    rather than failing the file outright -- so nothing gets less accurate,
    only faster in the common case.

    Thorough mode always does the full sensor unpack and skips the
    thumbnail shortcut entirely: a valid embedded thumbnail is not proof the
    actual sensor data is intact, since thumbnail and raw data live in
    different parts of the file.
    """
    try:
        import rawpy
    except ImportError:
        return Status.UNKNOWN_ERROR, "rawpy is not installed"

    try:
        with rawpy.imread(path) as raw:
            if not thorough:
                try:
                    thumb = raw.extract_thumb()
                    if thumb.format == rawpy.ThumbFormat.JPEG:
                        import io
                        from PIL import Image
                        with Image.open(io.BytesIO(thumb.data)) as img:
                            img.load()
                        return Status.WORKING, "Playable (fast check: embedded thumbnail decoded)"
                    if thumb.format == rawpy.ThumbFormat.BITMAP:
                        _ = thumb.data.shape  # forces the bitmap thumbnail to actually unpack
                        return Status.WORKING, "Playable (fast check: embedded thumbnail decoded)"
                except rawpy.LibRawNoThumbnailError:
                    pass  # no embedded thumbnail -- fall through to the full check below
                except Exception:
                    pass  # thumbnail path failed for any reason -- don't fail the file on
                          # that alone, fall through to the authoritative full check

            # Full (slower) sensor unpack -- either because thorough mode
            # requested it directly, or the fast thumbnail path wasn't usable.
            _ = raw.raw_image_visible.shape
            if thorough:
                # postprocess() forces LibRaw to fully demosaic the sensor
                # data into an actual image, the most complete check
                # available -- catches corruption the raw array shape check
                # alone can miss.
                _ = raw.postprocess()
                return Status.WORKING, "Playable (thorough check: full sensor data demosaiced)"
            return Status.WORKING, ""
    except rawpy.LibRawError as exc:
        return Status.NON_WORKING, f"RAW file could not be decoded: {exc}"
    except Exception as exc:  # noqa: BLE001
        classified = _classify_os_exception(exc)
        if classified:
            return classified
        return Status.NON_WORKING, f"RAW image validation failed: {exc}"


# ----------------------------------------------------------------- PDF -----
def validate_pdf(path: str, thorough: bool = False):
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError:
        return Status.UNKNOWN_ERROR, "pypdf is not installed"

    try:
        reader = PdfReader(path)
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                return Status.NON_WORKING, "PDF is encrypted and could not be opened"
        n_pages = len(reader.pages)
        if n_pages == 0:
            return Status.NON_WORKING, "PDF structure has no readable pages"

        if thorough:
            # Touch every page (not just the first) and force its content
            # stream to be parsed via extract_text() -- catches corruption
            # anywhere in the document, not just in the first page's objects.
            for i in range(n_pages):
                page = reader.pages[i]
                try:
                    page.extract_text()
                except Exception as exc:
                    return Status.NON_WORKING, f"Page {i + 1} of {n_pages} could not be parsed: {exc}"
            return Status.WORKING, f"Playable (thorough check: all {n_pages} pages parsed)"

        _ = reader.pages[0]  # touch the first page object to force parsing
        return Status.WORKING, ""
    except PdfReadError as exc:
        return Status.NON_WORKING, f"PDF structure could not be read: {exc}"
    except Exception as exc:  # noqa: BLE001
        classified = _classify_os_exception(exc)
        if classified:
            return classified
        return Status.NON_WORKING, f"PDF validation failed: {exc}"


# ------------------------------------------------------------- Document ----
def validate_document(path: str, thorough: bool = False):
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".docx":
            import docx
            d = docx.Document(path)
            _ = len(d.paragraphs)
            if thorough:
                # Force every paragraph's runs and every table cell to be
                # touched, not just the paragraph list itself.
                for p in d.paragraphs:
                    _ = p.text
                for t in d.tables:
                    for row in t.rows:
                        for cell in row.cells:
                            _ = cell.text
            return Status.WORKING, ""

        if ext == ".doc":
            import olefile
            if not olefile.isOleFile(path):
                return Status.NON_WORKING, "Legacy .doc is not a valid OLE compound file"
            ole = olefile.OleFileIO(path)
            has_stream = ole.exists("WordDocument")
            ole.close()
            if not has_stream:
                return Status.NON_WORKING, "OLE file has no WordDocument stream"
            return Status.WORKING, ""

        if ext == ".rtf":
            with open(path, "rb") as fh:
                head = fh.read(6)
            if not head.startswith(b"{\\rtf"):
                return Status.NON_WORKING, "Missing RTF signature ({\\rtf1)"
            return Status.WORKING, ""

        if ext == ".txt":
            with open(path, "rb") as fh:
                data = fh.read()
            try:
                data.decode("utf-8")
            except UnicodeDecodeError:
                try:
                    data.decode("latin-1")
                except UnicodeDecodeError:
                    return Status.NON_WORKING, "Text file could not be decoded with common encodings"
            return Status.WORKING, ""

        if ext == ".odt":
            return _validate_opendocument_zip(path)

        return Status.UNSUPPORTED, f"No validator implemented for {ext}"
    except ImportError as exc:
        return Status.UNKNOWN_ERROR, f"Missing dependency for {ext}: {exc}"
    except Exception as exc:  # noqa: BLE001
        classified = _classify_os_exception(exc)
        if classified:
            return classified
        return Status.NON_WORKING, f"Document validation failed: {exc}"


# ----------------------------------------------------------- Spreadsheet ---
def validate_spreadsheet(path: str, thorough: bool = False):
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".xlsx":
            import openpyxl
            wb = openpyxl.load_workbook(path, read_only=True)
            if not wb.sheetnames:
                return Status.NON_WORKING, "Workbook has no sheets"
            if thorough:
                # Iterate every row of every sheet -- read_only mode streams
                # from disk, so this doesn't load the whole file into RAM,
                # it just forces every cell's XML to actually be parsed.
                for sheet in wb.worksheets:
                    for _row in sheet.iter_rows():
                        pass
            wb.close()
            return Status.WORKING, ""

        if ext == ".xls":
            import xlrd
            wb = xlrd.open_workbook(path)
            if wb.nsheets == 0:
                return Status.NON_WORKING, "Workbook has no sheets"
            if thorough:
                for sheet in wb.sheets():
                    for r in range(sheet.nrows):
                        _ = sheet.row(r)
            return Status.WORKING, ""

        if ext == ".csv":
            with open(path, newline="", encoding="utf-8", errors="strict") as fh:
                reader = csv.reader(fh)
                if thorough:
                    for _row in reader:
                        pass  # read every row, not just a sample
                else:
                    for i, _row in enumerate(reader):
                        if i >= 20:  # sample enough rows to catch structural corruption
                            break
            return Status.WORKING, ""

        if ext == ".ods":
            return _validate_opendocument_zip(path)

        return Status.UNSUPPORTED, f"No validator implemented for {ext}"
    except UnicodeDecodeError as exc:
        return Status.NON_WORKING, f"CSV could not be decoded: {exc}"
    except ImportError as exc:
        return Status.UNKNOWN_ERROR, f"Missing dependency for {ext}: {exc}"
    except Exception as exc:  # noqa: BLE001
        classified = _classify_os_exception(exc)
        if classified:
            return classified
        return Status.NON_WORKING, f"Spreadsheet validation failed: {exc}"


def _validate_opendocument_zip(path: str):
    """.odt / .ods are ZIP containers with a 'mimetype' entry -- validate the
    container structure since full OpenDocument parsing needs extra deps."""
    if not zipfile.is_zipfile(path):
        return Status.NON_WORKING, "Not a valid ZIP-based OpenDocument container"
    try:
        with zipfile.ZipFile(path) as zf:
            bad_entry = zf.testzip()
            if bad_entry:
                return Status.NON_WORKING, f"Corrupted archive entry: {bad_entry}"
            if "mimetype" not in zf.namelist() and "content.xml" not in zf.namelist():
                return Status.NON_WORKING, "Missing expected OpenDocument entries"
        return Status.WORKING, ""
    except zipfile.BadZipFile as exc:
        return Status.NON_WORKING, f"Corrupted ZIP container: {exc}"


VALIDATORS = {
    "Video": validate_video,
    "Audio": validate_audio,
    "Image": validate_image,
    "RAW Image": validate_raw_image,
    "PDF": validate_pdf,
    "Document": validate_document,
    "Spreadsheet": validate_spreadsheet,
}


def validate_file(path: str, category: str, thorough: bool = True):
    """Dispatch to the correct validator. Never raises.

    Runs a fast header/hex signature pre-check first: if the file's magic
    bytes plainly don't match what its extension claims, it's rejected
    immediately without spending time on a full decode. Files that pass
    (or whose format has no single reliable fixed signature) proceed to
    the full, format-specific validator exactly as before -- the header
    check only ever adds a fast-fail path, it never changes what counts
    as Working.

    thorough=True asks each validator to do the most complete check it is
    capable of (full start-to-end decode for video, strict truncation
    detection for images, full sensor demosaic for RAW, every page/row for
    documents and spreadsheets) instead of the faster sampling-based checks.
    It trades scan speed for maximum accuracy.
    """
    ext = os.path.splitext(path)[1].lower()
    header_result = check_header(path, ext)
    if header_result is not None:
        matched, reason = header_result
        if not matched:
            return Status.NON_WORKING, reason

    # Byte-level (hex) check of START / MIDDLE / END of the whole file --
    # independent of the decoders, runs for every JPEG and every video.
    hex_note = ""
    from hexcheck import check_jpeg_bytes, check_video_bytes, _check_psd, _check_heic
    try:
        if category == "Image" and ext in (".jpg", ".jpeg"):
            hex_ok, hex_msg = check_jpeg_bytes(path)
        elif category == "Image" and ext == ".psd":
            hex_ok, hex_msg = _check_psd(path, os.path.getsize(path))
        elif category == "Image" and ext in (".heic", ".heif"):
            hex_ok, hex_msg = _check_heic(path, os.path.getsize(path))
        elif category == "Video":
            hex_ok, hex_msg = check_video_bytes(path)
        else:
            hex_ok, hex_msg = True, ""
    except Exception as exc:  # noqa: BLE001 - never crash a scan; decoders still run
        hex_ok, hex_msg = True, ""
    if not hex_ok:
        return Status.NON_WORKING, hex_msg
    hex_note = hex_msg

    validator = VALIDATORS.get(category)
    if validator is None:
        return Status.UNSUPPORTED, f"No validator for category '{category}'"
    try:
        status, reason = validator(path, thorough=thorough)
        if status == Status.WORKING and hex_note:
            reason = (reason + " | " if reason else "") + hex_note
        return status, reason
    except Exception as exc:  # noqa: BLE001 - absolute last-resort safety net
        classified = _classify_os_exception(exc)
        if classified:
            return classified
        return Status.UNKNOWN_ERROR, f"Unexpected error: {exc}"
