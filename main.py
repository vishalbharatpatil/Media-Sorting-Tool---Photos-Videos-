"""Interactive media sorter with restore support.

Sorting uses SOURCE/Sorted/YEAR/MONTH/MEDIA_TYPE folders.
Restore reads this script's records and compatible records made by main2.py.

Install Pillow for image EXIF capture dates: python -m pip install Pillow
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import sys
import uuid


MEDIA_EXTENSIONS = {
    "Images": {
        # Includes screenshots, photos, and common image/project formats.
        ".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".apng", ".gif",
        ".bmp", ".dib", ".tif", ".tiff", ".webp", ".heic", ".heif",
        ".avif", ".jxl", ".ico", ".cur", ".svg", ".dng", ".nef",
        ".nrw", ".cr2", ".cr3", ".arw", ".rw2", ".orf", ".raf",
        ".pef", ".srw", ".x3f", ".psd", ".xcf", ".exr", ".hdr",
        ".jng", ".pcx", ".tga", ".jp2", ".j2k", ".wmf", ".emf",
    },
    "Videos": {
        ".mp4", ".m4v", ".mov", ".avi", ".mkv", ".wmv", ".flv",
        ".webm", ".mpg", ".mpeg", ".mpe", ".m2v", ".mts", ".m2ts",
        ".ts", ".vob", ".3gp", ".3g2", ".ogv", ".dv", ".asf",
        ".rm", ".rmvb", ".f4v", ".mxf", ".gxf", ".r3d", ".braw",
    },
    "Audio": {
        ".mp3", ".m4a", ".aac", ".wav", ".wave", ".flac", ".ogg",
        ".oga", ".opus", ".wma", ".aiff", ".aif", ".alac", ".amr",
        ".mid", ".midi", ".ac3", ".eac3", ".mka", ".caf", ".ape",
        ".wv", ".tta", ".dsf", ".dff", ".m4b", ".m4p", ".voc",
    },
}
EXTENSION_TO_TYPE = {
    extension: media_type
    for media_type, extensions in MEDIA_EXTENSIONS.items()
    for extension in extensions
}
MANIFEST_PREFIX = ".sort_run_"
RESTORE_FOLDERS = ("Sorted", "All_Pictures", "Events")

try:
    from PIL import Image
except ImportError:
    Image = None


def photo_date(path: Path) -> datetime:
    """Use EXIF capture time, falling back to the file's modified time."""
    if Image is not None:
        try:
            with Image.open(path) as image:
                exif = image.getexif()
                for tag in (36867, 36868, 306):
                    value = exif.get(tag)
                    if value:
                        try:
                            return datetime.strptime(str(value), "%Y:%m:%d %H:%M:%S")
                        except ValueError:
                            continue
        except Exception:
            # Unreadable or unsupported image metadata falls back to file time.
            pass
    return datetime.fromtimestamp(path.stat().st_mtime)


def unused_path(path: Path) -> Path:
    """Return a non-existing path so restore and sorting never overwrite files."""
    if not path.exists():
        return path
    number = 1
    while True:
        candidate = path.with_name(f"{path.stem}_{number}{path.suffix}")
        if not candidate.exists():
            return candidate
        number += 1


def write_manifest(path: Path, manifest: dict) -> None:
    """Save restore progress atomically, including before every file move."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(manifest, file, indent=2, ensure_ascii=False)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, path)


def sort_media(source: Path, destination: Path, dry_run: bool = False) -> int:
    """Sort top-level images, videos, and audio into year/month folders."""
    if not source.is_dir():
        raise ValueError(f"Source folder does not exist: {source}")
    if source == destination:
        raise ValueError("Destination cannot be the source folder.")

    pictures = [
        path for path in source.iterdir()
        if path.is_file() and path.suffix.lower() in EXTENSION_TO_TYPE
    ]
    if not pictures:
        print(f"No supported image, video, or audio files found directly inside {source}")
        return 0

    manifest_path = None
    manifest = None
    if not dry_run:
        destination.mkdir(parents=True, exist_ok=True)
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f") + "_" + uuid.uuid4().hex[:8]
        manifest_path = destination / f"{MANIFEST_PREFIX}{run_id}.json"
        manifest = {"status": "running", "entries": []}
        write_manifest(manifest_path, manifest)

    moved = 0
    for picture in pictures:
        try:
            taken = photo_date(picture)
            media_type = EXTENSION_TO_TYPE[picture.suffix.lower()]
            folder = (
                destination
                / taken.strftime("%Y")
                / taken.strftime("%m - %B")
                / media_type
            )
            target = unused_path(folder / picture.name)
            if dry_run:
                print(f"{picture.name} -> {target}")
            else:
                # Record the source and target before moving to allow recovery
                # if the program or computer stops during the run.
                entry = {"source": str(picture), "destination": str(target)}
                manifest["entries"].append(entry)
                write_manifest(manifest_path, manifest)
                folder.mkdir(parents=True, exist_ok=True)
                shutil.move(str(picture), str(target))
                entry["status"] = "moved"
                write_manifest(manifest_path, manifest)
                print(f"Moved: {picture.name} -> {target.relative_to(destination)}")
            moved += 1
        except Exception as error:
            print(f"Skipped {picture.name}: {error}", file=sys.stderr)

    if manifest is not None and manifest_path is not None:
        manifest["status"] = "complete"
        write_manifest(manifest_path, manifest)
        print(f"Restore record saved: {manifest_path}")
    action = "would be sorted" if dry_run else "sorted"
    print(f"{moved} media file(s) {action}.")
    return moved


def find_manifests(source: Path, custom_destination: Path | None) -> list[Path]:
    folders = [source / name for name in RESTORE_FOLDERS]
    if custom_destination is not None:
        folders.append(custom_destination)
    folders.append(source)
    found: set[Path] = set()
    for folder in folders:
        try:
            found.update(folder.glob(f"{MANIFEST_PREFIX}*.json"))
        except OSError as error:
            print(f"Could not check restore records in {folder}: {error}", file=sys.stderr)
    return sorted(found, key=lambda path: path.name, reverse=True)


def restore_latest(source: Path, custom_destination: Path | None = None) -> int:
    manifest_path = None
    manifest = None
    for candidate in find_manifests(source, custom_destination):
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
            if data.get("status") != "restored":
                manifest_path = candidate
                manifest = data
                break
        except (OSError, json.JSONDecodeError) as error:
            print(f"Ignoring unreadable restore record {candidate}: {error}", file=sys.stderr)

    if manifest_path is None or manifest is None:
        print(f"No unfinished restore record found for {source}", file=sys.stderr)
        return 1

    entries = manifest.get("entries", [])
    restored = 0
    missing = 0
    for entry in reversed(entries):
        try:
            sorted_path = Path(entry["destination"])
            original_path = Path(entry["source"])
            if not sorted_path.exists():
                if original_path.exists():
                    entry["status"] = "restored"
                else:
                    entry["status"] = "missing"
                    missing += 1
                write_manifest(manifest_path, manifest)
                continue

            target = unused_path(original_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(sorted_path), str(target))
            entry["status"] = "restored"
            entry["restored_to"] = str(target)
            write_manifest(manifest_path, manifest)
            print(f"Restored: {sorted_path.name} -> {target}")
            restored += 1
        except Exception as error:
            print(f"Could not restore a picture: {error}", file=sys.stderr)

    complete = all(entry.get("status") == "restored" for entry in entries)
    manifest["status"] = "restored" if complete else "restore_incomplete"
    write_manifest(manifest_path, manifest)
    print(f"Restored {restored} picture(s) using {manifest_path.name}.")
    if missing:
        print(f"{missing} picture(s) were missing from both locations.", file=sys.stderr)
    return 0 if complete else 1


def ask_action() -> str:
    while True:
        print("\nWhat would you like to do?")
        print("1. Sort images, videos, and audio by year and month")
        print("2. Restore the latest sorting run")
        choice = input("Choose 1 or 2: ").strip().lower()
        if choice in ("1", "sort", "s"):
            return "sort"
        if choice in ("2", "restore", "r"):
            return "restore"
        print("Please enter 1 or 2.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sort images, videos, and audio by year/month or restore the latest run."
    )
    parser.add_argument(
        "-d", "--destination",
        help="sorting destination (default: SOURCE/Sorted); use the same path to find a custom restore record",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="preview sorting without moving media files",
    )
    args = parser.parse_args()

    action = ask_action()
    source_text = input("Folder containing your media files: ").strip().strip('"')
    if not source_text:
        print("Please provide a folder.", file=sys.stderr)
        return 2
    source = Path(source_text).expanduser().resolve()
    destination = (
        Path(args.destination).expanduser().resolve()
        if args.destination
        else source / "Sorted"
    )

    try:
        if action == "restore":
            return restore_latest(source, destination if args.destination else None)
        sort_media(source, destination, args.dry_run)
    except (OSError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
