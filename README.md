# File Health Checker

A Windows desktop app that scans a folder, verifies each supported file is
actually openable/decodable (not just "exists"), and sorts files into
`Working Files` / `Non-Working Files` subfolders.

Built with **Python + PySide6**, packaged into a standalone `.exe` with
**PyInstaller** (no Python needed on the end user's machine).

## What's new in v12 (CyberTech 2026)

- **Full folder + file tree.** Browse to your *main folder* and every
  sub-folder (at any depth) and every file is listed with tick-boxes.
  Tick the main folder to select everything, tick a folder to select all
  files inside it, or tick individual files. Folders show a partial tick when
  only some of their files are selected. Unsupported file types are listed
  greyed-out with no tick-box and are never touched.
- **Sorted per folder.** Every selected file is sorted into `Working Files` /
  `Non-Working Files` inside **its own folder** (e.g. `Main/A/A1/Working Files`),
  not pooled into the main folder.
- **Sorted output is visible** in the tree (green = Working, red =
  Non-Working, view-only so it is never re-scanned), and a new **Results by
  folder** tab lists every folder with its Working / Non-Working files and the
  reason for each failure.
- **Black cyber theme** with neon-cyan accents and the *CyberTech 2026* badge.

## v12 validation audit (photo + video)

Tested against 94 real files (healthy + deliberately damaged: truncated, zero-filled
holes, random garbage, bit flips, fake extensions, empty files). Two checks were added:

- **Frame-loss check** (video): compares frames actually decoded with frames
  expected. MP4/MOV/AVI (exact counts) use a tight tolerance; MKV/WEBM/FLV/WMV/MPG/TS
  (estimated counts) a loose one so variable-frame-rate recordings are not
  falsely flagged. Trimmed/edit-list MP4s, VFR, HEVC, B-frame and fragmented MP4
  were verified to still pass.
- **MPEG-PS truncated-tail check** (.mpg/.mpeg/.vob): last pack/PES packet must be complete.

Known limits (no decoder-based check can see these): a single flipped byte inside
JPEG pixel data (image still decodes); small random byte damage inside AVI/FLV/WMV/
WEBM/MPEG frames (decoder silently conceals it); uncompressed BMP zero-fill (valid
pixels); an MPEG file cut exactly on a packet boundary.

## What's included

| File | Purpose |
|---|---|
| `main.py` | Entry point |
| `gui.py` | PySide6 window: folder picker, options, progress stats, log, CSV export |
| `scanner.py` | Background `QThread` + `ThreadPoolExecutor`; keeps the UI responsive and validates many files concurrently |
| `validators.py` | One real, format-appropriate check per file type (see below) |
| `file_utils.py` | Category lookup, output-folder creation, duplicate-safe move/copy |
| `models.py` | Data classes / enums (`FileResult`, `Status`, `FileMode`, `DuplicatePolicy`) |
| `signatures.py` | Header/hex "magic bytes" pre-check — fast first-pass gate that runs before the full decode |
| `report.py` | CSV report export |
| `requirements.txt` | Python dependencies |

## Header / hex signature pre-check

Before the full format-specific validator runs, each file is checked
against the known magic-byte signature for its extension (e.g. JPEG
starts `FF D8 FF`, PDF starts `%PDF`, DOCX/XLSX start `PK\x03\x04`, MP4
has `ftyp` at byte offset 4, and so on). This is the same technique
you'd use manually in a hex editor. If the header plainly doesn't match
— a mislabeled file, a completely wrong format, an empty file — it's
rejected immediately as Non-Working with a clear "header mismatch"
reason, without spending time on a full decode. This matters most on
large batches: a multi-GB video that's obviously not even the right
container gets rejected in milliseconds instead of being opened.

A matching header is **not** treated as proof the file is fine — it only
proves the first few bytes are correct, which can't catch mid-file
corruption. Files that pass the header check (or extensions with no
single reliable fixed signature, like `.txt`/`.csv`/legacy `.doc`/`.xls`)
still go through the exact same full decode as before. This step can
only make a file fail faster; it never changes what counts as Working.

All modules were syntax-checked and the core logic (category detection,
duplicate-safe transfer, and every validator) was exercised against real
valid *and* deliberately corrupted files during development — see the
transcript for the test cases used (truncated PNG, garbage-body PDF,
corrupted DOCX zip, unsynced MP3, etc.), all correctly classified.

## Validation approach (per requirement #12)

No file is "Working" just because it exists and has size > 0:

- **Video** – every video gets the full stream-level check: every video AND
  audio frame is decoded start to end (not just sampled), plus an
  independent byte-level hex check runs first and is format-specific, not a
  generic one-size-fits-all scan:
  - **MP4/MOV/M4V/3GP** (ISO-BMFF) – walks the box tree, verifies every
    frame's NAL-length chain, and scans `mdat` for zero/FF-filled holes using
    a codec-aware threshold (32 bytes for H.264/HEVC, since real NAL data can
    never contain a long literal zero run; 4096 bytes for other codecs, which
    can legitimately have longer flat runs).
  - **MKV/WEBM, FLV, WMV/ASF, AVI** – each container's own element/chunk/tag
    structure is walked and verified complete. AVI and FLV additionally get a
    tight per-frame zero-run check (512/256 bytes) scoped to each individual
    frame's own payload bounds, verified safe against real legitimate content
    (AVI's only long zero runs are in the header, never inside a frame).
    WMV/ASF's packet padding is legitimately zero-filled up to ~2800+ bytes
    by design, so tightening it further would need full ASF packet-level
    parsing, which hasn't been done — large-scale corruption is still caught,
    but a small in-place corruption inside WMV packet data can be missed.
  - **MTS/M2TS** (MPEG transport stream) – every 188-byte packet (or 192-byte
    AVCHD packet with a 4-byte timecode prefix) must start with the sync byte
    `47`; a lost sync grid is corruption.
  - **MPG/MPEG/VOB** (MPEG program stream) – these have no separate
    authoritative duration header anywhere in the file (unlike MP4's `moov`),
    so a damaged file's self-reported duration just shrinks to match
    whatever's left, making duration comparisons useless. Instead: a
    whole-file scan requires an MPEG start code (`00 00 01 xx`) at least
    every 8KB (real files have one every ~2KB) and flags any long literal
    zero-byte run. VOB additionally must be an exact multiple of 2048 bytes
    (the DVD sector size every real VOB is padded to) — this catches clean
    truncation that lands right after a valid-looking packet, which the
    start-code scan alone can miss.
- **Audio** – `mutagen` parses the real codec/container headers.
- **Supported extensions** – Photos: ARW, CR2, CR3, JPG, JPEG, PSD, PNG, NEF, TIF/TIFF, HEIC/HEIF, GIF, DNG, BMP (plus ORF, RW2, RAF, PEF, SRW, WEBP, ICO). Video: MP4, MTS/M2TS, MOV, MKV, AVI, MPG/MPEG, 3GP, WMV, VOB, WEBM (plus FLV, M4V). `.heic`/`.heif` need `pillow-heif` (in requirements.txt).
- **Image** – always strict, and (like video) every format now gets an
  independent byte-level hex check, not just "did the decoder raise an
  exception" — several decoders (see limitations below) will silently
  decode corrupted data into a wrong-but-plausible image without ever
  raising. JPEGs must pass: (1) a byte-level hex check of the whole file
  (start/middle/end, marker structure, EOI present, and a scan for
  zero-filled holes in the entropy-coded data — skipped for progressive
  JPEGs specifically, since progressive encoding can legitimately produce
  long zero runs for a smooth region like a sky, verified against realistic
  photos), (2) a strict libjpeg-turbo decode via `simplejpeg` where *any*
  decoder warning counts as damage, (3) Pillow strict decode (truncation =
  error). A pixel-level gray-block scan used to also run after a clean
  strict decode, but real photos (overcast skies, studio/product backdrops,
  grayscale photos) legitimately contain large near-128-gray regions, and it
  was flagging healthy photos as Non-Working — removed for that case, since
  a clean strict decode already proves nothing was concealed. It still runs
  as a fallback only when simplejpeg can't give a strict verdict.
  PSD and HEIC/HEIF get their own equivalent hex check: PSD walks its
  length-prefixed sections to the actual Image Data section and scans it for
  a zero-filled hole; HEIC/HEIF (ISO-BMFF, like MP4, but built on a `meta`
  box instead of `moov` since it's a still image, not a video track) walks
  its box tree and scans the `mdat` picture data the same way. Both
  thresholds were set by measuring real files of that format and confirming
  zero legitimate occurrences of a long zero run.
  **Known limitation** (not fixable without a real risk of false positives,
  so left as-is): BMP and raw/uncompressed TIFF have no error-detecting
  structure in their pixel data at all, so in-place corruption that doesn't
  change the file's size can be undetectable — same issue PNG/GIF don't have
  (their compression is self-checking) and compressed TIFF (LZW/Deflate)
  doesn't have either. WebP (lossy/VP8) and HEIC's non-zero-byte corruption
  case share a different version of the same problem: their entropy coding
  (arithmetic/range coding) can "successfully" decode genuinely corrupted
  data into a syntactically valid but wrong image, the same way some video
  codecs can. Truncation and header corruption are still always caught for
  all of these.
  To see exactly why a photo fails: `python check_image.py "<folder or file>"`.
- **PDF** – `pypdf` opens the file and walks the page tree (handles
  encrypted PDFs too).
- **Word** – `python-docx` for `.docx`; OLE compound-file structural check
  for legacy `.doc`; signature/decode checks for `.rtf`/`.txt`.
- **Excel** – `openpyxl` for `.xlsx`, `xlrd` for legacy `.xls`, `csv`
  module for `.csv`, ZIP/container check for `.ods`.

Every validator is wrapped so a single corrupted file can never crash a
scan — unexpected errors are classified as `Unknown/Error`, OS permission
errors as `Permission Error`, and Windows sharing violations as
`Locked/In Use`.

## Run it (development)

```bash
pip install -r requirements.txt
python main.py
```

## Package as a standalone Windows .exe

Run this **on a Windows machine** (PyInstaller builds for the OS it runs
on — it can't cross-compile a Windows exe from Linux/Mac):

```bash
pip install -r requirements.txt
pyinstaller --noconfirm --onedir --windowed --noupx --name "FileHealthChecker" main.py
```

Or just run `build.bat`, which does this with the full set of unused-Qt-module
exclusions already applied (see below).

The finished app is `dist/FileHealthChecker/FileHealthChecker.exe`. Hand
people the whole `dist/FileHealthChecker` folder — the exe needs the files
next to it, but they still just double-click the `.exe` inside.

**Why `--onedir`, not `--onefile`:** a `--onefile` exe silently re-extracts
the entire bundled Python runtime, Qt, OpenCV, and LibRaw into a temp
folder on *every single launch* — that's the "exe takes forever to open"
symptom. `--onedir` runs directly from its own folder with no extraction
step, so launches are near-instant after the first one. The trade-off is
a folder instead of one portable file.

`build.bat` also excludes large PySide6/Qt modules this app never uses
(WebEngine, QML/Quick, Multimedia, 3D, Charts, SQL, etc.) and disables UPX
compression, both of which further shrink the build and cut startup time.

If antivirus/Defender still adds a noticeable delay on first launch after
a fresh build, that's normal — it's re-scanning newly-written files, not
the app itself; adding an exclusion for the app's folder resolves it.

Optional flags:
- `--icon=youricon.ico` to set an app icon.

## Performance trade-offs for large/heavy files

Two validators use a fast-path/fallback pattern to keep large batches
(200GB+, mixed DSLR/iPhone footage and RAW photos) from being dominated
by a handful of slow files:

- **Video over 150MB** (typical of DSLR/iPhone 10-bit 4K HEVC footage) —
  gets a fast check: first-frame decode + container metadata (resolution)
  only. The multi-point deep seek probe (used for smaller files) is
  skipped, because seeking into high-bitrate 10-bit/HEVC footage is
  expensive (the decoder must walk forward from the last keyframe). A
  corrupted file overwhelmingly fails on the very first frame anyway, so
  this trades a small amount of deep-corruption coverage for a large,
  predictable speed win on exactly the files that were slow. The 150MB
  threshold is a constant (`VIDEO_LIGHT_CHECK_THRESHOLD_BYTES`) near the
  top of `validate_video` in `validators.py` if you want to tune it.
- **RAW images (.arw, .cr2, .nef, etc.)** — try decoding the embedded
  JPEG/bitmap thumbnail first (typically well under 1MB, even for a 50MB+
  RAW file) instead of unpacking the full sensor array. A broken/truncated
  file usually breaks its thumbnail too, so this catches the vast majority
  of real corruption almost instantly. Only if there's no usable thumbnail
  does it fall back to the slower full sensor-data unpack — so nothing
  gets less accurate, only faster in the common case.

Both fast-path results say so explicitly in the scan report's Reason
column (e.g. "fast check: large file...", "fast check: embedded thumbnail
decoded") so you can tell at a glance which files got the quick check.

## Missing-index (moov atom) check for mp4/mov/m4v/m4a

Beyond decoding, `.mp4`/`.mov`/`.m4v`/`.m4a` files get one more check: a
fast structural scan confirming a `moov` atom (the file's index --
duration, frame offsets, track info) actually exists somewhere in the
file. This targets a specific real-world corruption pattern: a file whose
transfer/download/copy was interrupted before the index finished writing
(often written last, after all the raw video data). The raw frame data
(`mdat`) is often still present and can even decode a frame or two under
a lenient decoder, but standard players -- including Windows' own
(`0xc00d36e5`, "reacquire the content") -- will refuse to play it. This
scan only reads box headers and seeks past each box's data using its
declared size; it never reads actual video bytes, so it's fast even on
huge files.

- **Recursion**: the current scanner only scans the *top level* of the
  selected folder (it deliberately skips into `Working Files` /
  `Non-Working Files` so re-running a scan is safe). Say the word and I
  can add a "include subfolders" option.
- **`.txt` validation** is intentionally lenient (it accepts anything
  decodable as UTF-8 or Latin-1) since plain text rarely has a
  meaningful "corruption" signature — flag if you want stricter rules.
- **`.odt`/`.ods`** get a structural ZIP-container check rather than full
  OpenDocument parsing (keeps dependencies minimal); can be deepened with
  `odfpy` if you want stricter validation.
- **Locked-file detection** targets Windows sharing-violation errors
  specifically (`WinError 32/33`), since that's the deployment target.
- Default duplicate-name policy in the dropdown is **Rename
  automatically** (safest); Copy mode is the default transfer mode per
  your safety requirement (#10).


## Byte-level (hex) check -- start, middle, end (photos and videos)

Every JPEG and every video is also checked directly at the byte level,
independent of the decoders (`hexcheck.py`), before any decoding:

- **JPEG start:** must begin `FF D8 FF`.
- **JPEG middle:** every marker is walked; the compressed image data of every
  scan is searched for zero-filled holes (512+ zero bytes) and FF floods;
  start/middle/end 64 KB windows must not be entirely 00 or FF.
- **JPEG end:** must finish with the end marker `FF D9`; a tail of only
  00/FF padding is rejected.
- **Video (mp4/mov/m4v...):** `ftyp` at start, every top-level box must be
  complete and end exactly at end-of-file, `moov` must exist, and the whole
  media-data (`mdat`) region is scanned for zero-filled holes (4+ KB of 00).
  A zero-filled tail is rejected. AVI: declared RIFF size must fit the file.
- All videos additionally get the existing full start-to-end frame decode.

Known trade-off: a video whose *audio* is uncompressed PCM with 4 KB+ of
digital silence could be flagged; if that happens the reason text says
exactly where, so you can review it.


## v5 changes (Made by CyberTech 2026)

- **Animated opening**: a splash screen (logo, spinning rings, loading bar)
  plays first, then fades into the main app.
- **Folder > subfolder workflow**: click *Browse*, pick a folder, and its
  subfolders appear as a tick-list. Tick one or more subfolders (or
  *Select all*). Only the ticked subfolders are scanned.
- **Per-subfolder output**: inside each ticked subfolder the app creates
  `Working Files` and `Non-Working Files` and copies/moves into them.
  Unticked folders and everything else are never touched.
- Last entry in the list, *(Files directly inside the selected folder)*,
  keeps the old behaviour of scanning loose files in the chosen folder.
- New dark UI and a "Made by CyberTech 2026" footer.
