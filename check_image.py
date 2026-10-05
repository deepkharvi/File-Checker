r"""
Check one photo/video, several, or a whole folder and print WHY
each is Working / Non-Working. Uses exactly the same strict checks as the app.

Usage:
    python check_image.py "D:\Graphics, Picture\JPEG Digital Camera\Working Files"
    python check_image.py photo1.jpg photo2.jpg
    python check_image.py "D:\...\Working Files" --bad-only     (list only the damaged ones)
"""
import argparse
import os
import sys

from file_utils import get_category
from models import Status
from validators import validate_file


def gather(paths):
    for p in paths:
        if os.path.isdir(p):
            for name in sorted(os.listdir(p)):
                full = os.path.join(p, name)
                if os.path.isfile(full) and get_category(name) in ("Image", "RAW Image", "Video"):
                    yield full
        elif os.path.isfile(p):
            yield p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--bad-only", action="store_true")
    args = ap.parse_args()

    total = bad = 0
    for path in gather(args.paths):
        cat = get_category(path) or "Image"
        status, reason = validate_file(path, cat, thorough=True)
        total += 1
        is_ok = status == Status.WORKING
        bad += 0 if is_ok else 1
        if args.bad_only and is_ok:
            continue
        print(f"[{'OK ' if is_ok else 'BAD'}] {os.path.basename(path)}  ->  {status.value}"
              + (f"\n        {reason}" if reason else ""))
    print(f"\nChecked {total} file(s): {total - bad} OK, {bad} damaged/problem.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
