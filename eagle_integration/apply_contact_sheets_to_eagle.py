#!/usr/bin/env python3
"""Apply generated contact sheets as Eagle custom thumbnails.

Use this on a copied/test Eagle library first. Close Eagle before running with
--apply so Eagle does not overwrite metadata while the script is writing files.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parent
DEFAULT_MANIFEST = ROOT / "exports" / "video_manifest.json"
BACKUP_ROOT = ROOT / "exports" / "eagle_thumbnail_backups"


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def item_search_text(metadata: dict) -> str:
    fields = [
        metadata.get("id", ""),
        metadata.get("name", ""),
        metadata.get("url", ""),
        metadata.get("website", ""),
        metadata.get("annotation", ""),
        metadata.get("ext", ""),
    ]
    return "\n".join(str(x) for x in fields if x)


def find_library_items(library_dir: Path) -> list[dict]:
    images_dir = library_dir / "images"
    if not images_dir.exists():
        raise FileNotFoundError(f"Eagle images dir not found: {images_dir}")

    items = []
    for info_dir in images_dir.glob("*.info"):
        metadata_path = info_dir / "metadata.json"
        if not metadata_path.exists():
            continue
        try:
            metadata = load_json(metadata_path)
        except Exception:
            continue
        if metadata.get("isDeleted") is True:
            continue
        thumbs = sorted(info_dir.glob("*_thumbnail.*"))
        items.append(
            {
                "id": metadata.get("id") or info_dir.stem,
                "info_dir": info_dir,
                "metadata_path": metadata_path,
                "metadata": metadata,
                "thumbnail_path": thumbs[0] if thumbs else None,
                "search_text": item_search_text(metadata),
            }
        )
    return items


def match_manifest_to_library(manifest: list[dict], library_items: list[dict]) -> list[dict]:
    used_ids: set[str] = set()
    matches = []
    for entry in manifest:
        bvid = str(entry.get("bvid") or "").strip()
        contact_sheet = Path(str(entry.get("contact_sheet") or ""))
        if not bvid or not contact_sheet.exists():
            continue

        candidates = sorted(library_items, key=lambda x: bool(x["metadata"].get("customThumbnail")))
        best = None
        for item in candidates:
            if item["id"] in used_ids:
                continue
            if bvid in item["search_text"]:
                best = item
                break

        if best is None:
            source_video = Path(str(entry.get("source_video") or ""))
            for item in candidates:
                if item["id"] in used_ids:
                    continue
                if source_video.stem and source_video.stem in item["search_text"]:
                    best = item
                    break

        if best is None:
            name = str(entry.get("name") or "")
            for item in candidates:
                if item["id"] in used_ids:
                    continue
                if name and name == str(item["metadata"].get("name") or ""):
                    best = item
                    break

        if best is not None:
            used_ids.add(best["id"])
            matches.append({"entry": entry, "item": best, "contact_sheet": contact_sheet})
    return matches


def backup_item(item: dict, backup_dir: Path) -> None:
    backup_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(item["metadata_path"], backup_dir / "metadata.json")
    if item["thumbnail_path"] and item["thumbnail_path"].exists():
        shutil.copy2(item["thumbnail_path"], backup_dir / item["thumbnail_path"].name)


def write_thumbnail_png(source_image: Path, target_thumbnail: Path) -> None:
    target_thumbnail.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source_image) as image:
        image = image.convert("RGB")
        image.save(target_thumbnail, "PNG", optimize=True)


def apply_match(match: dict, library_dir: Path, backup_stamp: str) -> None:
    item = match["item"]
    item_id = str(item["id"])
    thumbnail_path = item["thumbnail_path"]
    if thumbnail_path is None:
        name = str(item["metadata"].get("name") or item_id)
        thumbnail_path = item["info_dir"] / f"{name}_thumbnail.png"

    backup_item(item, BACKUP_ROOT / backup_stamp / item_id)
    write_thumbnail_png(match["contact_sheet"], thumbnail_path)

    now_ms = int(time.time() * 1000)
    metadata = item["metadata"]
    metadata["customThumbnail"] = True
    metadata["lastModified"] = now_ms
    write_json(item["metadata_path"], metadata)

    mtime_path = library_dir / "mtime.json"
    if mtime_path.exists():
        try:
            mtime = load_json(mtime_path)
            if not isinstance(mtime, dict):
                mtime = {}
        except Exception:
            mtime = {}
    else:
        mtime = {}
    mtime[item_id] = now_ms
    mtime["all"] = 1
    write_json(mtime_path, mtime)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply contact sheets as Eagle custom thumbnails.")
    parser.add_argument("--library-dir", type=Path, required=True, help="Path to a .library folder.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--limit", type=int, default=0, help="Limit matched items; 0 means all.")
    parser.add_argument("--apply", action="store_true", help="Actually write Eagle library files.")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        manifest = load_json(args.manifest)
        if not isinstance(manifest, list):
            raise ValueError("manifest must be a JSON list")

        library_items = find_library_items(args.library_dir)
        matches = match_manifest_to_library(manifest, library_items)
        if args.limit and args.limit > 0:
            matches = matches[: args.limit]

        print(f"[source] manifest items={len(manifest)} eagle items={len(library_items)} matched={len(matches)}")
        for match in matches:
            entry = match["entry"]
            item = match["item"]
            thumb = item["thumbnail_path"] or "(will create thumbnail)"
            print(f"[match] {entry.get('bvid')} -> {item['id']} | {thumb}")

        if not args.apply:
            print("[dry-run] no files changed. Re-run with --apply after closing Eagle.")
            return 0

        stamp = time.strftime("%Y%m%d_%H%M%S")
        for match in matches:
            apply_match(match, args.library_dir, stamp)
        print(f"[done] applied={len(matches)} backup={BACKUP_ROOT / stamp}")
        return 0
    except Exception as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
