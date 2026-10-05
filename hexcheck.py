"""
Byte-level ("hex") integrity checks: START, MIDDLE and END of the file.

These run BEFORE the decoders and are independent of them. They read the raw
bytes of the whole file and reject anything whose start, middle or end is
structurally wrong -- zero-filled holes, missing end marker, truncated
container, zero-padded tails.

Each check returns (ok: bool, message: str). On success the message is a short
human-readable summary of what was verified (shown in the report); on failure
it says exactly where and why (with byte offsets and hex).
"""
import os
import re

_CHUNK = 8 * 1024 * 1024

# Inside JPEG entropy-coded (scan) data a real file never contains long runs of
# identical 0x00 bytes (Huffman codes always contain 1-bits) or 0xFF bytes
# (FF must be byte-stuffed as FF 00). A zeroed disk sector is 512+ bytes.
JPEG_ZERO_RUN = 512
JPEG_FF_RUN = 256

# Video: H.264/HEVC payload can never contain 00 00 00 (emulation prevention),
# so long zero runs inside media data mean a zero-filled hole.
VIDEO_ZERO_RUN = 4 * 1024
VIDEO_TAIL_ZERO = 4 * 1024        # zero-filled tail = pre-allocated / interrupted copy
WINDOW = 64 * 1024

_JPEG_MARKER_IN_SCAN = re.compile(rb"\xff[^\x00\xd0-\xd7\xff]")


def _hex(b: bytes, n: int = 8) -> str:
    return " ".join(f"{x:02X}" for x in b[:n])


def _blank(b: bytes) -> bool:
    return len(b) > 0 and (b.count(0) == len(b) or b.count(255) == len(b))


def _read_at(f, off: int, n: int) -> bytes:
    f.seek(max(0, off))
    return f.read(n)


def _window_check(path: str, size: int):
    """Start / middle / end 64 KB windows must not be entirely 00 or FF."""
    with open(path, "rb") as f:
        for name, off in (("start", 0), ("middle", max(0, size // 2 - WINDOW // 2)),
                          ("end", max(0, size - WINDOW))):
            w = _read_at(f, off, WINDOW)
            if _blank(w):
                return False, (f"{name} of file (offset {off}, {len(w)} bytes) is entirely "
                               f"{'00' if w[0] == 0 else 'FF'} -- data missing/zero-filled")
    return True, ""


def _long_run(f, start: int, end: int, byte: int, min_len: int):
    """Offset of the first run of >= min_len identical `byte`s in [start, end), else -1."""
    pat = re.compile(re.escape(bytes([byte])) + b"{%d,}" % min_len)
    f.seek(start)
    pos = start
    carry = 0           # length of the run continuing from the previous chunk
    carry_off = -1
    while pos < end:
        chunk = f.read(min(_CHUNK, end - pos))
        if not chunk:
            break
        lead = len(chunk) - len(chunk.lstrip(bytes([byte])))
        if carry and carry + lead >= min_len:
            return carry_off
        if lead == len(chunk):
            if not carry:
                carry_off = pos
            carry += lead
            if carry >= min_len:
                return carry_off
            pos += len(chunk)
            continue
        m = pat.search(chunk)
        if m:
            return pos + m.start()
        trail = len(chunk) - len(chunk.rstrip(bytes([byte])))
        carry = trail
        carry_off = pos + len(chunk) - trail
        pos += len(chunk)
    return -1


# ------------------------------------------------------------------ JPEG ----
PSD_ZERO_RUN = 512   # measured: real PSD image-data sections never contain a run this long


def _check_psd(path: str, size: int):
    """Photoshop (.psd/.psb): walk the four top-level sections in file order
    (Header, Color Mode Data, Image Resources, Layer and Mask Info -- each of
    the last three is length-prefixed, so each can be skipped exactly without
    needing to understand what's inside it) to find where the final Image
    Data section starts; everything from there to EOF is the actual pixel
    data (PackBits-RLE or raw). A zero-filled hole there is real corruption:
    measured zero such runs in genuine PSD files. Pillow's PSD reader doesn't
    reliably raise on this (RLE happens to tolerate some zero-run patterns),
    so this is an independent check, the same way check_jpeg_bytes is for JPEG.
    """
    with open(path, "rb") as f:
        head = _read_at(f, 0, 26)
        if len(head) < 26 or head[:4] != b"8BPS":
            return False, "START hex wrong: no '8BPS' signature"
        version = int.from_bytes(head[4:6], "big")
        if version not in (1, 2):
            return False, f"START hex wrong: unknown PSD version {version} (expected 1 or 2)"
        length_size = 4 if version == 1 else 8   # PSB (version 2) uses 64-bit section lengths
        pos = 26
        for section in ("Color Mode Data", "Image Resources", "Layer and Mask Info"):
            hdr = _read_at(f, pos, length_size)
            if len(hdr) < length_size:
                return False, f"END hex wrong: file cut off before the {section} section length"
            seclen = int.from_bytes(hdr, "big")
            pos += length_size + seclen
            if pos > size:
                return False, (f"END hex wrong: {section} section declares {seclen} bytes but "
                               f"runs past the end of the file -- file truncated")
        # `pos` now points at the Image Data section (2-byte compression method + data to EOF)
        if pos + 2 > size:
            return False, "END hex wrong: file cut off before the Image Data section"
        img_data_start = pos + 2
        if img_data_start >= size:
            return False, "MIDDLE hex wrong: Image Data section is empty -- no pixel data"
        z = re.search(b"\x00{%d,}" % PSD_ZERO_RUN,
                      _read_at(f, img_data_start, size - img_data_start))
        if z:
            at = img_data_start + z.start()
            return False, (f"MIDDLE hex wrong: {PSD_ZERO_RUN}+ zero bytes in a row at offset {at} "
                           f"(~{100.0 * at / size:.0f}% into the file) inside the image data -- "
                           f"zero-filled hole")
    return True, "Hex check OK -- all sections complete, no zero-filled holes in image data"


HEIC_ZERO_RUN = 512   # measured: real HEIC/HEIF coded picture data never contains a run this long


def _check_heic(path: str, size: int):
    """HEIC/HEIF: ISO-BMFF container (same box format as mp4), but a still
    image instead of a video track -- stored via a 'meta' box ('iloc'/'iinf',
    not 'moov'/'trak') pointing into 'mdat'. The existing box walker already
    validates the box tree generically; this adds a zero-run scan over the
    actual coded picture bytes in 'mdat', mirroring the JPEG/PSD/video checks
    (measured zero such runs in genuine HEIC files -- HEVC's entropy coding
    doesn't always error out on corruption the way the box structure does).
    """
    ok, msg, boxes = _walk_isobmff(path, size, require_moov=False)
    if not ok:
        return False, msg
    types = [b[0] for b in boxes]
    if b"meta" not in types:
        return False, "MIDDLE hex wrong: no 'meta' box -- not a valid HEIC/HEIF still image"
    with open(path, "rb") as f:
        for bt, pos, bsize, hlen in boxes:
            if bt == b"mdat":
                z = re.search(b"\x00{%d,}" % HEIC_ZERO_RUN,
                               _read_at(f, pos + hlen, bsize - hlen))
                if z:
                    at = pos + hlen + z.start()
                    return False, (f"MIDDLE hex wrong: {HEIC_ZERO_RUN}+ zero bytes in a row at "
                                   f"offset {at} (~{100.0 * at / size:.0f}% into the file) inside "
                                   f"the coded picture data -- zero-filled hole")
    return True, "Hex check OK -- all boxes complete, no zero-filled holes in picture data"


def check_jpeg_bytes(path: str):
    try:
        size = os.path.getsize(path)
        if size < 4:
            return False, f"File too small to be a JPEG ({size} bytes)"
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return True, ""          # let the real validator report the I/O error

    # ---- START --------------------------------------------------------
    if data[:3] != b"\xff\xd8\xff":
        return False, f"START hex wrong: expected FF D8 FF, found {_hex(data)}"

    ok, msg = _window_check(path, size)
    if not ok:
        return False, "HEX: " + msg

    # ---- walk every marker; check the entropy-coded data of every scan ----
    pos = 2
    n = len(data)
    scans = 0
    eoi_seen = False
    last_eoi_end = 0
    # Progressive JPEG (SOF2, and its rarer arithmetic-coding/differential
    # siblings) encodes AC coefficients across multiple successive-approximation
    # scans. For a smooth/flat region (sky, a studio backdrop, any plain-color
    # area -- all extremely common in real photos) a scan can legitimately
    # pack long literal runs of 0x00 this way; baseline JPEG's single full
    # scan doesn't do this. Verified empirically: realistic progressive JPEGs
    # with ordinary smooth content triggered the zero-run check below ~90% of
    # the time even though every one of them decoded perfectly. So that
    # specific heuristic is skipped for progressive files -- real corruption
    # there is still caught by the strict simplejpeg decode layer (and EOI/
    # marker-structure/truncation checks below are unaffected either way).
    is_progressive = False
    while pos < n:
        if data[pos] != 0xFF:
            # Not a marker where one must be.
            if eoi_seen:
                break            # trailer after a complete image
            return False, (f"MIDDLE hex wrong: expected a JPEG marker (FF xx) at offset {pos}, "
                           f"found {_hex(data[pos:pos + 8])} -- data corrupted")
        while pos < n and data[pos] == 0xFF:      # skip fill bytes
            pos += 1
        if pos >= n:
            break
        marker = data[pos]
        pos += 1
        if marker == 0xD8:                          # SOI of an embedded/MPF image
            continue
        if marker == 0xD9:                          # EOI
            eoi_seen = True
            last_eoi_end = pos
            if pos < n and data[pos:pos + 2] != b"\xff\xd8":
                break
            continue
        if marker == 0x01 or 0xD0 <= marker <= 0xD7 or marker == 0x00:
            continue
        if marker in (0xC2, 0xC6, 0xCA, 0xCE):   # SOF2/6/10/14: progressive variants
            is_progressive = True
        if pos + 2 > n:
            return False, f"END hex wrong: file cut off inside a marker header at offset {pos}"
        seglen = int.from_bytes(data[pos:pos + 2], "big")
        if seglen < 2 or pos + seglen > n:
            return False, (f"MIDDLE/END hex wrong: marker FF {marker:02X} at offset {pos - 2} "
                           f"declares {seglen} bytes but the file ends first -- truncated/corrupted")
        pos += seglen
        if marker == 0xDA:                          # SOS -> entropy-coded data follows
            scans += 1
            m = _JPEG_MARKER_IN_SCAN.search(data, pos)
            scan_end = m.start() if m else n
            seg = data[pos:scan_end]
            if not is_progressive:
                z = re.search(b"\x00{%d,}" % JPEG_ZERO_RUN, seg)
                if z:
                    return False, (f"MIDDLE hex wrong: {JPEG_ZERO_RUN}+ zero bytes in a row at "
                                   f"offset {pos + z.start()} inside the image data "
                                   f"(~{100.0 * (pos + z.start()) / n:.0f}% into the file) -- "
                                   f"zero-filled hole -> shows as gray patch")
            f_ = re.search(b"\xff{%d,}" % JPEG_FF_RUN, seg)
            if f_:
                return False, (f"MIDDLE hex wrong: {JPEG_FF_RUN}+ FF bytes in a row at offset "
                               f"{pos + f_.start()} inside the image data -- corrupted")
            pos = scan_end
            if not m:
                return False, ("END hex wrong: image data runs to the end of the file with no "
                               "end marker (FF D9) -- file is truncated")

    if not eoi_seen or scans == 0:
        return False, f"END hex wrong: no end-of-image marker (FF D9); file ends with {_hex(data[-8:], 8)}"

    # ---- END: what follows the last EOI? --------------------------------
    trailer = data[last_eoi_end:]
    if trailer and (trailer.count(0) == len(trailer) or trailer.count(255) == len(trailer)):
        return False, (f"END hex wrong: file ends with {len(trailer)} padding bytes "
                       f"({_hex(trailer, 4)} ...) after FF D9 -- incomplete/padded copy")

    return True, (f"Hex check OK -- start {_hex(data, 3)}, middle & all {scans} scan(s) clean, "
                  f"end {'FF D9' if not trailer else 'FF D9 + ' + str(len(trailer)) + 'B trailer'}")


# ----------------------------------------------------------------- VIDEO ----
# Strategy: find every zero-filled run in the file, then decide with the MP4's
# own sample table whether the run lies INSIDE the bytes of a video frame
# (real damage) or in audio silence / padding / free space (harmless).
import bisect
import struct

_ISOBMFF_EXT = (".mp4", ".mov", ".m4v", ".m4a", ".m4b", ".m4p", ".3gp")
ZERO_RUN_MIN = 512                 # a wiped disk sector or more
_MAX_MOOV = 256 * 1024 * 1024
# In these codecs a valid frame can never contain a long run of 00 bytes
# (emulation prevention), so even a short run inside a frame = damage.
_STRICT_CODECS = (b"avc1", b"avc3", b"hvc1", b"hev1", b"hvc2", b"dvh1", b"dvhe")
STRICT_OVERLAP = 32                # zero bytes inside one H.264/HEVC frame
LOOSE_OVERLAP = 4096               # other codecs (MPEG-4, ProRes, ...)


def _walk_isobmff(path: str, size: int, require_moov: bool = True):
    """Walk every top-level box. Returns (ok, msg, boxes[(type,pos,size,hlen)]).

    require_moov=True (the default, used for video: mp4/mov/m4v/3gp/...)
    requires a 'moov' track-index box, which every real video file has.
    HEIC/HEIF still images use this same box format but are built on 'meta'
    instead -- they never have a 'moov' at all, by spec, not as a sign of
    damage. An earlier version of this function required 'moov'
    unconditionally, which made _check_heic (the only other caller) report
    EVERY HEIC/HEIF file as truncated/damaged, including perfectly healthy
    ones. The HEIC caller passes require_moov=False and checks for 'meta'
    itself instead.
    """
    boxes = []
    with open(path, "rb") as f:
        pos = 0
        while pos < size:
            hdr = _read_at(f, pos, 16)
            if len(hdr) < 8:
                return False, (f"END hex wrong: {size - pos} stray byte(s) at offset {pos} "
                               f"({_hex(hdr, 8)}) -- last box is cut off"), boxes
            bsize = int.from_bytes(hdr[:4], "big")
            btype = hdr[4:8]
            hlen = 8
            if bsize == 1:
                if len(hdr) < 16:
                    return False, f"END hex wrong: 64-bit box header cut off at offset {pos}", boxes
                bsize = int.from_bytes(hdr[8:16], "big")
                hlen = 16
            elif bsize == 0:
                bsize = size - pos
            if not all(32 <= c < 127 for c in btype) or bsize < hlen:
                return False, (f"MIDDLE hex wrong: invalid box at offset {pos}: "
                               f"{_hex(hdr, 8)} -- container structure corrupted"), boxes
            if pos + bsize > size:
                return False, (f"END hex wrong: box '{btype.decode('latin-1')}' at offset {pos} "
                               f"declares {bsize} bytes but only {size - pos} remain -- "
                               f"file is truncated (missing {pos + bsize - size} bytes)"), boxes
            boxes.append((btype, pos, bsize, hlen))
            pos += bsize
    types = [b[0] for b in boxes]
    if not boxes or types[0] not in (b"ftyp", b"moov", b"free", b"skip", b"wide", b"styp"):
        return False, "START hex wrong: no 'ftyp' box at the start of the file", boxes
    if require_moov and b"moov" not in types:
        return False, ("END hex wrong: 'moov' index box missing -- file is truncated/incomplete "
                       "(interrupted download/copy)"), boxes
    if b"mdat" not in types:
        return False, "MIDDLE hex wrong: no 'mdat' media-data box", boxes
    return True, "", boxes


def _children(buf: bytes, start: int, end: int):
    """Yield (type, payload_start, box_end) for boxes packed in buf[start:end]."""
    pos = start
    while pos + 8 <= end:
        bsize = int.from_bytes(buf[pos:pos + 4], "big")
        btype = buf[pos + 4:pos + 8]
        hlen = 8
        if bsize == 1:
            if pos + 16 > end:
                return
            bsize = int.from_bytes(buf[pos + 8:pos + 16], "big")
            hlen = 16
        elif bsize == 0:
            bsize = end - pos
        if bsize < hlen or pos + bsize > end:
            return
        yield btype, pos + hlen, pos + bsize
        pos += bsize


def _find(buf, start, end, path):
    """Descend through a list of box types; return (payload_start, box_end) or None."""
    cur = (start, end)
    for t in path:
        nxt = None
        for bt, ps, be in _children(buf, cur[0], cur[1]):
            if bt == t:
                nxt = (ps, be)
                break
        if nxt is None:
            return None
        cur = nxt
    return cur


def _video_samples(path: str, boxes):
    """Return (sorted [(offset,size)] of all VIDEO samples, codec fourcc, n_tracks).
    Returns (None, None, 0) if the sample table can't be read (e.g. fragmented mp4)."""
    moov = next((b for b in boxes if b[0] == b"moov"), None)
    if not moov or moov[2] > _MAX_MOOV:
        return None, None, 0, None, None
    with open(path, "rb") as f:
        f.seek(moov[1])
        buf = f.read(moov[2])
    samples = []
    codec = None
    lsize = None
    stts_bad = None
    tracks = 0
    for bt, ps, be in _children(buf, moov[3], len(buf)):
        if bt != b"trak":
            continue
        hdlr = _find(buf, ps, be, [b"mdia", b"hdlr"])
        if not hdlr or buf[hdlr[0] + 8:hdlr[0] + 12] != b"vide":
            continue
        stbl = _find(buf, ps, be, [b"mdia", b"minf", b"stbl"])
        if not stbl:
            continue
        tab = {}
        for t, p, e in _children(buf, stbl[0], stbl[1]):
            tab[t] = (p, e)
        try:
            if b"stsz" not in tab or b"stsc" not in tab or not (b"stco" in tab or b"co64" in tab):
                continue
            # sample sizes
            p, e = tab[b"stsz"]
            fixed, count = struct.unpack(">II", buf[p + 4:p + 12])
            if fixed:
                sizes = [fixed] * count
            else:
                sizes = list(struct.unpack(f">{count}I", buf[p + 12:p + 12 + 4 * count]))
            # chunk offsets
            if b"stco" in tab:
                p, e = tab[b"stco"]
                n = struct.unpack(">I", buf[p + 4:p + 8])[0]
                offs = list(struct.unpack(f">{n}I", buf[p + 8:p + 8 + 4 * n]))
            else:
                p, e = tab[b"co64"]
                n = struct.unpack(">I", buf[p + 4:p + 8])[0]
                offs = list(struct.unpack(f">{n}Q", buf[p + 8:p + 8 + 8 * n]))
            # sample-to-chunk
            p, e = tab[b"stsc"]
            n = struct.unpack(">I", buf[p + 4:p + 8])[0]
            stsc = [struct.unpack(">III", buf[p + 8 + 12 * i:p + 20 + 12 * i]) for i in range(n)]
            if b"stsd" in tab and codec is None:
                sp, se = tab[b"stsd"]
                codec = buf[sp + 12:sp + 16]
                i = buf.find(b"avcC", sp, se)
                if i >= 0 and i + 9 <= se:
                    lsize = (buf[i + 8] & 3) + 1
                else:
                    i = buf.find(b"hvcC", sp, se)
                    if i >= 0 and i + 26 <= se:
                        lsize = (buf[i + 25] & 3) + 1
            if b"stts" in tab:
                p, e = tab[b"stts"]
                n = struct.unpack(">I", buf[p + 4:p + 8])[0]
                total = sum(struct.unpack(">I", buf[p + 8 + 8 * i:p + 12 + 8 * i])[0] for i in range(n))
                if total != len(sizes):
                    stts_bad = (total, len(sizes))
        except (struct.error, ValueError):
            continue
        tracks += 1
        si = 0
        for i, (first, spc, _d) in enumerate(stsc):
            last = (stsc[i + 1][0] - 1) if i + 1 < len(stsc) else len(offs)
            for ch in range(first, last + 1):
                if ch - 1 >= len(offs):
                    break
                o = offs[ch - 1]
                for _ in range(spc):
                    if si >= len(sizes):
                        break
                    samples.append((o, sizes[si]))
                    o += sizes[si]
                    si += 1
    if not tracks or not samples:
        return None, codec, tracks, None, None
    samples.sort()
    return samples, codec, tracks, lsize, stts_bad


def _zero_runs(f, a: int, b: int, min_len: int, fill: int = 0):
    """All runs of >= min_len 00 bytes in [a, b) as [(offset, length)]."""
    fb = bytes([fill])
    pat = re.compile(re.escape(fb) + b"{%d,}" % min_len)
    runs = []
    pos = a
    carry_start, carry_len = 0, 0
    f.seek(a)
    while pos < b:
        chunk = f.read(min(_CHUNK, b - pos))
        if not chunk:
            break
        base = pos
        i = 0
        if carry_len:
            lead = len(chunk) - len(chunk.lstrip(fb))
            carry_len += lead
            if lead == len(chunk):
                pos += len(chunk)
                continue
            if carry_len >= min_len:
                runs.append((carry_start, carry_len))
            carry_len = 0
            i = lead
        for m in pat.finditer(chunk, i):
            if m.end() == len(chunk):
                carry_start, carry_len = base + m.start(), m.end() - m.start()
            else:
                runs.append((base + m.start(), m.end() - m.start()))
        if not carry_len:
            trail = len(chunk) - len(chunk.rstrip(fb))
            if trail:
                carry_start, carry_len = base + len(chunk) - trail, trail
        pos += len(chunk)
    if carry_len >= min_len:
        runs.append((carry_start, carry_len))
    return runs


def _damaged_video_sample(runs, samples, need):
    """First zero run that overlaps a video sample by >= need bytes, as
    (run_offset, overlap_bytes, sample_offset, sample_size), else None."""
    starts = [s[0] for s in samples]
    for ro, rl in runs:
        re_ = ro + rl
        i = max(0, bisect.bisect_right(starts, ro) - 1)
        while i < len(samples) and samples[i][0] < re_:
            so, ss = samples[i]
            ov = min(re_, so + ss) - max(ro, so)
            if ov >= min(need, ss):
                return ro, ov, so, ss
            i += 1
    return None


# ------------------------------------------------- extra index sanity (MP4) ----
def _mp4_extra_checks(path: str, boxes, size: int):
    """Header / timing / keyframe / overlap sanity. Returns failure message or None."""
    moov = next((b for b in boxes if b[0] == b"moov"), None)
    if not moov or moov[2] > _MAX_MOOV:
        return None
    with open(path, "rb") as f:
        f.seek(moov[1])
        buf = f.read(moov[2])
    try:
        mv = _find(buf, moov[3], len(buf), [b"mvhd"])
        movie_s = 0.0
        if mv:
            ver = buf[mv[0]]
            ts, dur = (struct.unpack(">II", buf[mv[0] + 12:mv[0] + 20]) if ver == 0
                       else (struct.unpack(">I", buf[mv[0] + 20:mv[0] + 24])[0],
                             struct.unpack(">Q", buf[mv[0] + 24:mv[0] + 32])[0]))
            if ts == 0:
                return "MIDDLE hex wrong: movie header has timescale 0 -- header corrupted"
            movie_s = dur / ts
        mdats = [(b[1] + b[3], b[1] + b[2]) for b in boxes if b[0] == b"mdat"]
        allsamples = []
        for bt, ps, be in _children(buf, moov[3], len(buf)):
            if bt != b"trak":
                continue
            hd = _find(buf, ps, be, [b"mdia", b"hdlr"])
            kind = buf[hd[0] + 8:hd[0] + 12] if hd else b""
            md = _find(buf, ps, be, [b"mdia", b"mdhd"])
            if md:
                ver = buf[md[0]]
                if ver == 0:
                    mts, mdur = struct.unpack(">II", buf[md[0] + 12:md[0] + 20])
                else:
                    mts = struct.unpack(">I", buf[md[0] + 20:md[0] + 24])[0]
                    mdur = struct.unpack(">Q", buf[md[0] + 24:md[0] + 32])[0]
                if mts == 0:
                    return "MIDDLE hex wrong: track header has timescale 0 -- header corrupted"
            else:
                mts = 0
            if kind == b"vide":
                tk = _find(buf, ps, be, [b"tkhd"])
                if tk:
                    off = 76 if buf[tk[0]] == 0 else 88
                    w, hgt = struct.unpack(">II", buf[tk[0] + off:tk[0] + off + 8])
                    if (w >> 16) == 0 or (hgt >> 16) == 0:
                        return "MIDDLE hex wrong: video track has zero width/height -- header corrupted"
            stbl = _find(buf, ps, be, [b"mdia", b"minf", b"stbl"])
            if not stbl:
                continue
            tab = {t: (p, e) for t, p, e in _children(buf, stbl[0], stbl[1])}
            if kind == b"vide" and b"stts" in tab and mts and movie_s > 0:
                p, e = tab[b"stts"]
                n = struct.unpack(">I", buf[p + 4:p + 8])[0]
                tot = sum(struct.unpack(">II", buf[p + 8 + 8 * i:p + 16 + 8 * i])[0] *
                          struct.unpack(">II", buf[p + 8 + 8 * i:p + 16 + 8 * i])[1] for i in range(n))
                trk_s = tot / mts
                if trk_s > 0 and abs(trk_s - movie_s) > 2 and (trk_s < movie_s * 0.5 or trk_s > movie_s * 2):
                    return (f"MIDDLE hex wrong: video track lasts {trk_s:.1f}s but the file header "
                            f"says {movie_s:.1f}s -- index inconsistent/corrupted")
            if kind == b"vide" and b"stss" in tab:
                p, e = tab[b"stss"]
                n = struct.unpack(">I", buf[p + 4:p + 8])[0]
                if n > 0 and struct.unpack(">I", buf[p + 8:p + 12])[0] != 1:
                    return "MIDDLE hex wrong: first video frame is not a keyframe -- start of video cannot be decoded"
            # all samples of this track (for bounds / overlap)
            if not (b"stsz" in tab and b"stsc" in tab and (b"stco" in tab or b"co64" in tab)):
                continue
            p, e = tab[b"stsz"]
            fixed, count = struct.unpack(">II", buf[p + 4:p + 12])
            sizes = [fixed] * count if fixed else list(struct.unpack(f">{count}I", buf[p + 12:p + 12 + 4 * count]))
            if b"stco" in tab:
                p, e = tab[b"stco"]
                n = struct.unpack(">I", buf[p + 4:p + 8])[0]
                offs = list(struct.unpack(f">{n}I", buf[p + 8:p + 8 + 4 * n]))
            else:
                p, e = tab[b"co64"]
                n = struct.unpack(">I", buf[p + 4:p + 8])[0]
                offs = list(struct.unpack(f">{n}Q", buf[p + 8:p + 8 + 8 * n]))
            p, e = tab[b"stsc"]
            n = struct.unpack(">I", buf[p + 4:p + 8])[0]
            stsc = [struct.unpack(">III", buf[p + 8 + 12 * i:p + 20 + 12 * i]) for i in range(n)]
            si = 0
            for i, (first, spc, _d) in enumerate(stsc):
                last = (stsc[i + 1][0] - 1) if i + 1 < len(stsc) else len(offs)
                for ch in range(first, last + 1):
                    if ch - 1 >= len(offs):
                        break
                    o = offs[ch - 1]
                    for _ in range(spc):
                        if si >= len(sizes):
                            break
                        if sizes[si] > 0:
                            allsamples.append((o, sizes[si], kind))
                        o += sizes[si]
                        si += 1
        if mdats:
            for o, s, k in allsamples:
                if not any(a <= o and o + s <= z for a, z in mdats):
                    return (f"END hex wrong: a {k.decode('latin-1')} sample at offset {o} lies outside "
                            f"the media data -- index points to missing data")
        allsamples.sort()
        for i in range(len(allsamples) - 1):
            o, s, k = allsamples[i]
            if o + s > allsamples[i + 1][0]:
                return (f"MIDDLE hex wrong: frame/sample at offset {o} overlaps the next one -- "
                        f"index table corrupted")
    except (struct.error, IndexError, ValueError):
        return None
    return None


# ------------------------------------------------ other container structures --
def _ebml_vint(buf, pos, is_id):
    if pos >= len(buf) or buf[pos] == 0:
        return None
    ln = 8 - buf[pos].bit_length() + 1
    if pos + ln > len(buf):
        return None
    raw = int.from_bytes(buf[pos:pos + ln], "big")
    if is_id:
        return raw, ln, False
    mask = (1 << (7 * ln)) - 1
    val = raw & mask
    return val, ln, val == mask


def _check_mkv(path: str, size: int):
    with open(path, "rb") as f:
        hd = _read_at(f, 0, 64)
        if hd[:4] != b"\x1a\x45\xdf\xa3":
            return False, "START hex wrong: no EBML header (1A 45 DF A3) -- not a valid MKV/WebM"
        s = _ebml_vint(hd, 4, False)
        if not s:
            return False, "START hex wrong: EBML header size unreadable"
        pos = 4 + s[1] + s[0]
        seg = _read_at(f, pos, 16)
        if seg[:4] != b"\x18\x53\x80\x67":
            return False, f"MIDDLE hex wrong: 'Segment' element not found at offset {pos}"
        ss = _ebml_vint(seg, 4, False)
        if not ss:
            return False, "MIDDLE hex wrong: Segment size unreadable"
        start = pos + 4 + ss[1]
        end = size if ss[2] else start + ss[0]
        if end > size:
            return False, (f"END hex wrong: Segment declares {end - start} bytes but only "
                           f"{size - start} exist -- file is truncated (missing {end - size} bytes)")
        pos = start
        clusters = 0
        while pos < end:
            b = _read_at(f, pos, 16)
            i = _ebml_vint(b, 0, True)
            if not i:
                return False, f"MIDDLE hex wrong: invalid element ID at offset {pos} -- data corrupted"
            sz = _ebml_vint(b, i[1], False)
            if not sz:
                return False, f"MIDDLE hex wrong: invalid element size at offset {pos} -- data corrupted"
            if sz[2]:
                return True, ""      # unknown-size (live) element: cannot walk further
            nxt = pos + i[1] + sz[1] + sz[0]
            if nxt > end:
                return False, (f"END hex wrong: element at offset {pos} runs {nxt - end} bytes past the "
                               f"end -- file truncated/corrupted")
            if i[0] == 0x1F43B675:
                clusters += 1
            pos = nxt
        if clusters == 0:
            return False, "MIDDLE hex wrong: no video/audio clusters found -- no media data"
    return True, ""


FLV_VIDEO_ZERO_RUN = 256   # measured: real FLV video tag payloads never contain a run this long


def _check_flv(path: str, size: int):
    with open(path, "rb") as f:
        hd = _read_at(f, 0, 13)
        if hd[:3] != b"FLV":
            return False, "START hex wrong: no 'FLV' signature"
        pos = int.from_bytes(hd[5:9], "big")
        tags = 0
        while pos + 4 < size:
            t = _read_at(f, pos + 4, 11)
            if len(t) < 11:
                return False, f"END hex wrong: last FLV tag header cut off at offset {pos} -- truncated"
            tag_type = t[0] & 0x1F
            if tag_type not in (8, 9, 18):
                return False, f"MIDDLE hex wrong: invalid FLV tag type {t[0]} at offset {pos + 4} -- data corrupted"
            payload_len = int.from_bytes(t[1:4], "big")
            payload_start = pos + 4 + 11
            nxt = payload_start + payload_len
            if nxt > size:
                return False, (f"END hex wrong: FLV tag at offset {pos + 4} runs {nxt - size} bytes past "
                               f"the end -- file truncated")
            # Each video tag's exact boundaries are already known here (unlike
            # a generic whole-file scan), so a per-frame zero-run check can
            # use a far smaller, still-safe threshold than the generic
            # cross-format fallback (measured: real FLV video payloads never
            # contain a long literal 0x00 run at all).
            if tag_type == 9 and payload_len >= FLV_VIDEO_ZERO_RUN:
                payload = _read_at(f, payload_start, payload_len)
                z = re.search(b"\x00{%d,}" % FLV_VIDEO_ZERO_RUN, payload)
                if z:
                    at = payload_start + z.start()
                    return False, (f"MIDDLE hex wrong: {FLV_VIDEO_ZERO_RUN}+ zero bytes in a row "
                                   f"inside a video frame at offset {at} "
                                   f"(~{100.0 * at / size:.0f}% into the file) -- zero-filled hole")
            pos = nxt
            tags += 1
        if tags == 0:
            return False, "MIDDLE hex wrong: no FLV tags -- no media data"
    return True, ""


_ASF_HEADER = bytes.fromhex("3026B2758E66CF11A6D900AA0062CE6C")
_ASF_DATA = bytes.fromhex("3626B2758E66CF11A6D900AA0062CE6C")


def _check_asf(path: str, size: int):
    with open(path, "rb") as f:
        if _read_at(f, 0, 16) != _ASF_HEADER:
            return False, "START hex wrong: no ASF/WMV header signature"
        pos, seen_data = 0, False
        while pos + 24 <= size:
            o = _read_at(f, pos, 24)
            osz = int.from_bytes(o[16:24], "little")
            if osz < 24:
                return False, f"MIDDLE hex wrong: invalid ASF object size at offset {pos} -- data corrupted"
            if o[:16] == _ASF_DATA:
                seen_data = True
            if pos + osz > size:
                return False, (f"END hex wrong: ASF object at offset {pos} declares {osz} bytes but only "
                               f"{size - pos} remain -- file truncated")
            pos += osz
        if not seen_data:
            return False, "MIDDLE hex wrong: no ASF data object -- no media data"
    return True, ""


def _check_avi(path: str, size: int):
    with open(path, "rb") as f:
        hd = _read_at(f, 0, 12)
        if hd[:4] != b"RIFF" or hd[8:12] != b"AVI ":
            return False, "START hex wrong: no 'RIFF....AVI ' header"
        pos, movi, idx1, hdrl = 12, None, False, False
        while pos + 8 <= size:
            c = _read_at(f, pos, 12)
            cid, csz = c[:4], int.from_bytes(c[4:8], "little")
            if not all(32 <= x < 127 for x in cid):
                return False, f"MIDDLE hex wrong: invalid AVI chunk ID at offset {pos} -- data corrupted"
            nxt = pos + 8 + csz + (csz & 1)
            if cid == b"LIST" and c[8:12] == b"movi":
                if pos + 8 + csz > size:
                    return False, (f"END hex wrong: 'movi' data declares {csz} bytes but only "
                                   f"{size - pos - 8} remain -- file truncated")
                movi = (pos + 12, pos + 8 + csz)
            elif cid == b"LIST" and c[8:12] == b"hdrl":
                hdrl = True
            elif cid == b"idx1":
                idx1 = True
            if nxt > size and cid != b"LIST":
                return False, f"END hex wrong: AVI chunk '{cid.decode()}' at {pos} runs past end -- truncated"
            pos = nxt
        if not hdrl or movi is None:
            return False, "MIDDLE hex wrong: AVI is missing its header list or 'movi' data"
        head_blob = _read_at(f, 0, min(size, 1 << 20))
        if not idx1 and b"indx" not in head_blob:
            return False, "END hex wrong: AVI 'idx1' index missing -- recording was not finished (truncated)"
        # walk the media chunks; they must tile the movi list exactly
        AVI_VIDEO_ZERO_RUN = 512   # measured: good.avi's only long zero runs are in
                                   # the header, before 'movi' -- none inside a frame
        pos, end = movi
        n = 0
        while pos + 8 <= end:
            c = _read_at(f, pos, 8)
            cid, csz = c[:4], int.from_bytes(c[4:8], "little")
            if not all(32 <= x < 127 for x in cid):
                return False, (f"MIDDLE hex wrong: invalid AVI frame header at offset {pos} "
                               f"(after {n} good chunks) -- data corrupted")
            # cid is e.g. "00dc"/"00db" (stream 0 compressed/uncompressed video),
            # "01wb" (stream 1 audio), etc. -- the last two bytes identify the
            # stream's data kind; check video chunks for a zero-filled hole,
            # using a far tighter threshold than the generic whole-file
            # fallback now that the exact frame boundaries are known.
            if cid[2:4] in (b"dc", b"db") and csz >= AVI_VIDEO_ZERO_RUN:
                payload = _read_at(f, pos + 8, csz)
                z = re.search(b"\x00{%d,}" % AVI_VIDEO_ZERO_RUN, payload)
                if z:
                    at = pos + 8 + z.start()
                    return False, (f"MIDDLE hex wrong: {AVI_VIDEO_ZERO_RUN}+ zero bytes in a row "
                                   f"inside a video frame at offset {at} "
                                   f"(~{100.0 * at / size:.0f}% into the file; after {n} good "
                                   f"chunks) -- zero-filled hole")
            pos += 8 + csz + (csz & 1)
            n += 1
        if pos > end + 1:
            return False, "MIDDLE hex wrong: AVI media chunks overrun the data list -- data corrupted"
        if n == 0:
            return False, "MIDDLE hex wrong: AVI contains no frames"
    return True, ""


_TS_SYNC = 0x47
_TS_PACKET_LAYOUTS = ((188, 0), (192, 4), (204, 0))  # (packet size, sync offset)


def _detect_ts_layout(head: bytes):
    for pkt_size, sync_off in _TS_PACKET_LAYOUTS:
        n_try = min(50, (len(head) - sync_off) // pkt_size)
        if n_try < 3:
            continue
        if all(head[sync_off + i * pkt_size] == _TS_SYNC for i in range(n_try)):
            return pkt_size, sync_off
    return None


def _check_mts(path: str, size: int):
    """MPEG transport stream (.mts/.m2ts): no box structure to walk -- instead
    every packet (188 bytes, or 192 with a 4-byte AVCHD timecode prefix) must
    start with the sync byte 0x47. A corrupted/misaligned stream loses that
    grid at the damage point, which is what actually makes playback stop
    there. Null packets are legitimately zero-padded, so (unlike other
    containers) no generic zero-run scan is applied here -- it would just
    flag normal padding as damage.
    """
    with open(path, "rb") as f:
        head = _read_at(f, 0, 4096)
        if len(head) < 188:
            return False, f"File too small to contain a single MPEG-TS packet ({len(head)} bytes)"
        layout = _detect_ts_layout(head)
        if layout is None:
            return False, f"START hex wrong: no MPEG-TS sync byte (47) every 188/192 bytes, found {_hex(head[:8])}"
        pkt_size, sync_off = layout
        # Packets tile the file starting at byte 0 -- sync_off is WHERE the
        # sync byte sits inside each packet (0 for plain 188-byte TS, 4 for
        # the 192-byte AVCHD layout with a per-packet timecode prefix), not a
        # one-time offset to skip before packet-counting starts. (An earlier
        # version subtracted sync_off from `size` before dividing, which
        # undercounted packets by one and miscomputed "leftover" for every
        # 192-byte file -- including perfectly complete ones.)
        n_packets = size // pkt_size
        if n_packets == 0:
            return False, "File too small to contain a full MPEG-TS packet"

        pos, checked = 0, 0
        bad_at = None
        step = 4096 * pkt_size
        while pos + pkt_size <= size:
            chunk = _read_at(f, pos, step)
            cnt = len(chunk) // pkt_size
            if cnt == 0:
                break
            for k in range(cnt):
                if chunk[k * pkt_size + sync_off] != _TS_SYNC:
                    bad_at = pos + k * pkt_size + sync_off
                    break
            if bad_at is not None:
                break
            pos += cnt * pkt_size
            checked += cnt
        if bad_at is not None:
            return False, (f"MIDDLE hex wrong: MPEG-TS sync byte (47) missing at packet offset {bad_at} "
                           f"(~{100.0 * bad_at / size:.0f}% into the file, packet {checked + 1} of "
                           f"~{n_packets}) -- stream data corrupted/misaligned from there")

        leftover = size - n_packets * pkt_size
        if leftover > 0:
            return False, (f"END hex wrong: last {leftover} bytes do not form a complete "
                           f"{pkt_size}-byte TS packet -- file is truncated mid-packet")
    return True, (f"Hex check OK -- valid MPEG-TS, {n_packets} packets of {pkt_size} bytes, "
                  f"sync byte (47) intact on every packet")


_MPEG_PS_MAX_GAP = 8 * 1024   # real muxed packs/PES packets appear ~every 2KB (measured); generous margin
_MPEG_PS_ZERO_RUN = 512       # real MPEG-PS data (measured) never contains a long literal 0x00 run


def _check_mpeg_ps(path: str, size: int):
    """MPEG program stream (.mpg/.mpeg/.vob): packs and PES packets are
    variable length, so (unlike mp4/mkv/avi) there's no box/element tree to
    walk, and (unlike .mts) there's no fixed packet grid either. Worse, this
    format has no separate authoritative duration header anywhere in the file
    (nothing like mp4's moov) -- so once the file is damaged, FFmpeg's own
    duration estimate just shrinks to match whatever little valid data is
    left, and a "does decoded length match declared length" check can't tell
    anything is wrong (both numbers shrink together). A whole-file byte scan
    is the only thing that actually catches this:
      1. a long stretch with no MPEG start code (00 00 01 xx) at all -- real
         packs/PES packets appear roughly every ~2KB in practice (measured
         against real muxed output), so an extended gap means that stretch of
         the file is no longer valid MPEG data;
      2. a long literal run of identical 0x00 bytes -- real compressed
         MPEG-PS data (measured) never contains one; this catches smaller,
         localized damage (e.g. a wiped block) the gap scan alone would miss.
    """
    with open(path, "rb") as f:
        pos = 0
        carry = b""                 # tail of previous chunk, for matches spanning a chunk boundary
        OVERLAP = max(_MPEG_PS_ZERO_RUN - 1, 2)
        last_start_code = -1
        first_seen = False
        trailing_zeros = 0
        CH = 1 << 20
        while True:
            chunk = f.read(CH)
            if not chunk:
                break
            buf = carry + chunk
            base = pos - len(carry)

            # 1. start-code gap scan
            idx = 0
            while True:
                i = buf.find(b"\x00\x00\x01", idx)
                if i == -1:
                    break
                abs_i = base + i
                if last_start_code >= 0 and abs_i - last_start_code > _MPEG_PS_MAX_GAP:
                    return False, (
                        f"MIDDLE hex wrong: no MPEG start code (00 00 01) for "
                        f"{abs_i - last_start_code} bytes before offset {abs_i} "
                        f"(~{100.0 * abs_i / size:.0f}% into the file) -- "
                        f"stream data missing/corrupted there")
                last_start_code = abs_i
                first_seen = True
                idx = i + 1

            # 2. literal zero-run scan (covers the boundary via `carry`)
            z = re.search(b"\x00{%d,}" % _MPEG_PS_ZERO_RUN, buf)
            if z:
                at = base + z.start()
                return False, (
                    f"MIDDLE hex wrong: {_MPEG_PS_ZERO_RUN}+ zero bytes in a row at offset {at} "
                    f"(~{100.0 * at / size:.0f}% into the file) -- zero-filled hole")

            carry = buf[-OVERLAP:]
            pos += len(chunk)

    # 3. Truncated-tail check. Program streams have no index, so a file cut
    #    mid-packet is otherwise invisible. Find the LAST pack / PES packet
    #    header (start codes BA..FF; ES-level codes 00..B8 are skipped because
    #    they sit inside payloads) and confirm it is complete.
    try:
        with open(path, "rb") as f:
            n = min(size, 262144)
            f.seek(size - n)
            t = f.read(n)
        hits = list(re.finditer(b"\x00\x00\x01([\xba-\xff])", t))
        if hits:
            m = hits[-1]
            code, off = m.group(1)[0], m.start()
            remain = len(t) - off
            if code == 0xB9:
                pass                                   # program end code: complete
            elif code == 0xBA:
                need = 14 if remain > 4 and (t[off + 4] & 0xC0) == 0x40 else 12
                if remain < need:
                    return False, ("END hex wrong: file ends inside an MPEG pack header "
                                   "-- file is truncated")
            elif remain >= 6:
                plen = int.from_bytes(t[off + 4:off + 6], "big")
                if plen and remain < 6 + plen:
                    return False, (f"END hex wrong: last MPEG packet declares {plen} bytes but "
                                   f"only {remain - 6} remain -- file is truncated")
            else:
                return False, "END hex wrong: file ends inside an MPEG packet header -- truncated"
    except OSError:
        pass

    if not first_seen:
        return False, "START hex wrong: no MPEG start code (00 00 01) found anywhere in file"
    if size - last_start_code > _MPEG_PS_MAX_GAP:
        return False, (
            f"END hex wrong: no MPEG start code found in the last {size - last_start_code} "
            f"bytes -- file is truncated/corrupted at the end")
    return True, (f"Hex check OK -- MPEG start codes present throughout "
                  f"(no gap over {_MPEG_PS_MAX_GAP // 1024}KB), no zero-filled holes")


_STRUCT_CHECKS = {".mkv": _check_mkv, ".webm": _check_mkv, ".flv": _check_flv,
                  ".wmv": _check_asf, ".avi": _check_avi,
                  ".mpg": _check_mpeg_ps, ".mpeg": _check_mpeg_ps, ".vob": _check_mpeg_ps}


def _check_nal_chain(f, samples, lsize: int):
    """Every H.264/HEVC frame is a chain of [length][NAL]; the lengths must add up
    exactly to the frame size. Catches garbage/overwritten data, not just zeros.
    Returns (frame_index, sample, reason) for the first bad frame, else None."""
    def bad(smp):
        o, s = smp
        pos, end = o, o + s
        while pos < end:
            f.seek(pos)
            b = f.read(lsize + 1)
            if len(b) < lsize + 1:
                return "frame is cut off"
            n = int.from_bytes(b[:lsize], "big")
            if n == 0 or pos + lsize + n > end:
                return f"NAL length {n} does not fit inside the {s}-byte frame"
            if b[lsize] & 0x80:
                return "invalid NAL header (forbidden bit set)"
            pos += lsize + n
        return None
    # Only enforce when the stream really is length-prefixed (first frames valid).
    probe = [bad(s) for s in samples[:5] if s[1] > 0]
    if probe and all(probe):
        return None
    for i, smp in enumerate(samples):
        if smp[1] <= 0:
            continue
        r = bad(smp)
        if r:
            return i, smp, r
    return None


def check_video_bytes(path: str):
    try:
        size = os.path.getsize(path)
        if size < 16:
            return False, f"File too small to be a video ({size} bytes)"
        ext = os.path.splitext(path)[1].lower()
        with open(path, "rb") as f:
            head = f.read(16)

            if ext in _ISOBMFF_EXT:
                ok, msg, boxes = _walk_isobmff(path, size)
                if not ok:
                    return False, msg
                samples, codec, ntr, lsize, stts_bad = _video_samples(path, boxes)
                if samples:
                    need = STRICT_OVERLAP if codec in _STRICT_CODECS else LOOSE_OVERLAP
                    runs = []
                    for bt, pos, bsize, hlen in boxes:
                        if bt == b"mdat":
                            runs += _zero_runs(f, pos + hlen, pos + bsize, ZERO_RUN_MIN)
                    bad = _damaged_video_sample(runs, samples, need)
                    if bad:
                        ro, ov, so, ss = bad
                        return False, (f"MIDDLE hex wrong: {ov} zero bytes (00) inside a video frame "
                                       f"at offset {ro} (~{100.0 * ro / size:.0f}% into the file; "
                                       f"frame at {so}, {ss} bytes) -- zero-filled hole, "
                                       f"video data missing")
                    # garbage/erased flash (0xFF) holes inside video frames
                    ff = []
                    for bt, pos, bsize, hlen in boxes:
                        if bt == b"mdat":
                            ff += _zero_runs(f, pos + hlen, pos + bsize, ZERO_RUN_MIN, 0xFF)
                    badff = _damaged_video_sample(ff, samples, need)
                    if badff:
                        ro, ov, so, ss = badff
                        return False, (f"MIDDLE hex wrong: {ov} bytes of FF inside a video frame at "
                                       f"offset {ro} (~{100.0 * ro / size:.0f}% into the file) -- "
                                       f"erased/garbage hole, video data missing")
                    # frame structure (catches overwritten / scrambled data)
                    if lsize:
                        nb = _check_nal_chain(f, samples, lsize)
                        if nb:
                            i, (so, ss), why = nb
                            return False, (f"MIDDLE hex wrong: video frame #{i + 1} of {len(samples)} "
                                           f"at offset {so} (~{100.0 * so / size:.0f}% into the file) "
                                           f"is corrupted -- {why}")
                    if stts_bad:
                        return False, (f"MIDDLE hex wrong: frame timing table lists {stts_bad[0]} "
                                       f"frames but the index has {stts_bad[1]} -- index inconsistent")
                    mdats = [(b[1] + b[3], b[1] + b[2]) for b in boxes if b[0] == b"mdat"]
                    for so, ss in samples:
                        if not any(a <= so and so + ss <= z for a, z in mdats):
                            return False, (f"END hex wrong: a video frame at offset {so} lies outside "
                                           f"the media-data box -- index points to missing data")
                    extra = _mp4_extra_checks(path, boxes, size)
                    if extra:
                        return False, extra
                    # frames that lie beyond the end of the file
                    last = samples[-1]
                    if last[0] + last[1] > size:
                        return False, "END hex wrong: video frames point past the end of the file -- truncated"
                    return True, (f"Hex check OK -- 'ftyp' start, all boxes complete, 'moov' present, "
                                  f"{len(samples)} video frames verified (no holes, frame structure valid)")
                # Sample table unreadable (fragmented / unusual): generic scan of mdat/moof data.
                runs = []
                for bt, pos, bsize, hlen in boxes:
                    if bt == b"mdat":
                        runs += _zero_runs(f, pos + hlen, pos + bsize, 16 * 1024)
                if runs:
                    ro, rl = runs[0]
                    return False, (f"MIDDLE hex wrong: {rl // 1024}+ KB of 00 bytes in a row at "
                                   f"offset {ro} (~{100.0 * ro / size:.0f}% into the file) "
                                   f"inside the media data -- zero-filled hole")
                return True, "Hex check OK -- boxes complete, no large zero-filled holes in media data"

            # ---- MPEG transport stream: own packet-grid check, see _check_mts ----
            if ext in (".mts", ".m2ts"):
                return _check_mts(path, size)

            # DVD-authored VOBs are always padded to exact 2048-byte sectors
            # (part of the DVD-Video spec) -- a size that isn't a multiple of
            # 2048 means the file was cut or appended to, which a byte-level
            # scan alone can miss if the cut happens to land shortly after a
            # start code. This check is VOB-specific: ordinary .mpg/.mpeg
            # files have no such alignment guarantee, so it's not applied there.
            if ext == ".vob" and size % 2048 != 0:
                return False, (f"END hex wrong: file is {size} bytes, not a multiple of the "
                               f"2048-byte DVD sector size -- truncated or appended to")

            # ---- non-MP4 containers (avi, mkv, ...) ----
            fn = _STRUCT_CHECKS.get(ext)
            if fn:
                ok, msg = fn(path, size)
                if not ok:
                    return False, msg
            ok, msg = _window_check(path, size)
            if not ok:
                return False, "HEX: " + msg
            tail = _read_at(f, max(0, size - VIDEO_TAIL_ZERO), VIDEO_TAIL_ZERO)
            if len(tail) == VIDEO_TAIL_ZERO and tail.count(0) == len(tail):
                return False, (f"END hex wrong: last {VIDEO_TAIL_ZERO} bytes are all 00 -- "
                               f"zero-filled tail (incomplete file)")
            summary = "start/middle/end windows clean"
            if ext == ".avi":
                riff_size = int.from_bytes(head[4:8], "little") + 8
                if riff_size > size:
                    return False, (f"END hex wrong: AVI header says {riff_size} bytes but file "
                                   f"is only {size} -- truncated")
                summary = "start 'RIFF', declared size fits, windows clean"
            runs = _zero_runs(f, 0, size, 16 * 1024)
            if runs:
                ro, rl = runs[0]
                return False, (f"MIDDLE hex wrong: {rl // 1024}+ KB of 00 bytes in a row at offset "
                               f"{ro} (~{100.0 * ro / size:.0f}% into the file) -- zero-filled hole")
        return True, f"Hex check OK -- {summary}, no zero-filled holes"
    except OSError:
        return True, ""
