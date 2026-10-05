r"""
Split the top-level files of a folder into batch subfolders of roughly
a target size each (default 10 GB). Files are moved whole -- never cut --
so a batch may end up slightly over the target if the last file added
pushes it past the limit.

Usage:
    python split_into_batches.py "D:\MyFiles" --size-gb 10
    python split_into_batches.py "D:\MyFiles" --size-gb 10 --copy   (copy instead of move)

After splitting, run the File Health Checker on each "Batch_XX" folder
one at a time.
"""
import argparse
import os
import shutil
import sys

RESERVED_NAMES = {"Working Files", "Non-Working Files"}


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


def main():
    parser = argparse.ArgumentParser(description="Split a folder into ~N GB batch subfolders.")
    parser.add_argument("folder", help="Folder whose top-level files should be split into batches")
    parser.add_argument("--size-gb", type=float, default=10.0, help="Target batch size in GB (default: 10)")
    parser.add_argument("--copy", action="store_true", help="Copy files instead of moving them")
    args = parser.parse_args()

    root = args.folder
    if not os.path.isdir(root):
        print(f"Not a folder: {root}")
        sys.exit(1)

    target_bytes = int(args.size_gb * (1024 ** 3))

    files = []
    with os.scandir(root) as it:
        for entry in it:
            if entry.is_file():
                files.append(entry.path)
            elif entry.is_dir() and entry.name.startswith("Batch_"):
                continue  # skip batches from a previous run
            elif entry.is_dir() and entry.name in RESERVED_NAMES:
                continue

    if not files:
        print("No top-level files found to split.")
        return

    files.sort(key=lambda p: os.path.getsize(p), reverse=True)  # largest first: packs batches tighter

    batches = []          # list of (list_of_paths, total_size)
    for path in files:
        size = os.path.getsize(path)
        placed = False
        for batch in batches:
            if batch[1] + size <= target_bytes or batch[1] == 0:
                batch[0].append(path)
                batch[1] += size
                placed = True
                break
        if not placed:
            batches.append([[path], size])

    action = shutil.copy2 if args.copy else shutil.move
    verb = "Copying" if args.copy else "Moving"

    for i, (paths, total_size) in enumerate(batches, start=1):
        batch_dir = os.path.join(root, f"Batch_{i:02d}")
        os.makedirs(batch_dir, exist_ok=True)
        print(f"\n{verb} {len(paths)} files ({human(total_size)}) into {batch_dir}")
        for p in paths:
            dest = os.path.join(batch_dir, os.path.basename(p))
            action(p, dest)

    print(f"\nDone. Created {len(batches)} batch folder(s) under {root}.")
    print("Run the File Health Checker on each Batch_XX folder one at a time.")


if __name__ == "__main__":
    main()
