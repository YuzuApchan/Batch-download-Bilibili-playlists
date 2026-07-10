#!/usr/bin/env python3
"""Export BiliDownloader favorite covers and metadata into Eagle.

This tool is intentionally standalone. It reads the Web UI cache or live state,
downloads missing cover images with gentle pacing, then sends local cover paths
to Eagle's localhost API.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

import requests


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
DEFAULT_CACHE_DIR = PROJECT_ROOT / "userdata" / "_web_cache"
EXPORT_DIR = Path(os.environ.get("BILI_EAGLE_EXPORT_DIR") or (ROOT / "exports"))
COVER_DIR = EXPORT_DIR / "covers"
MANIFEST_PATH = EXPORT_DIR / "manifest.json"

EAGLE_API = "http://localhost:41595"
BILI_REFERER = "https://www.bilibili.com/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
)


def configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


@dataclass
class VideoItem:
    title: str
    bvid: str
    date: str = ""
    month: str = ""
    duration: int | str = ""
    cover: str = ""
    source: str = ""

    @property
    def website(self) -> str:
        return f"https://www.bilibili.com/video/{self.bvid}" if self.bvid else ""


def clean_filename(value: str, fallback: str = "item") -> str:
    value = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", value or "").strip()
    value = re.sub(r"\s+", " ", value)
    return (value[:120] or fallback).strip()


def normalize_url(url: str) -> str:
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http://"):
        return "https://" + url[len("http://") :]
    return url


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def videos_from_cache(path: Path) -> list[VideoItem]:
    raw = load_json(path)
    if not isinstance(raw, list):
        raise ValueError(f"{path} is not a favorite-list JSON array")
    return [video_from_dict(item, source=path.stem) for item in raw if isinstance(item, dict)]


def videos_from_cache_dir(cache_dir: Path) -> list[VideoItem]:
    videos: list[VideoItem] = []
    for path in sorted(cache_dir.glob("fav_*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        videos.extend(videos_from_cache(path))
    return videos


def videos_from_state(url: str = "http://127.0.0.1:8765/api/state") -> list[VideoItem]:
    resp = requests.get(url, timeout=12)
    resp.raise_for_status()
    data = resp.json()
    videos = []
    for key, source in (("favVideos", "web_state_favorite"), ("manualVideos", "web_state_manual")):
        for item in data.get(key, []) or []:
            if isinstance(item, dict):
                videos.append(video_from_dict(item, source=source))
    return videos


def video_from_dict(item: dict, source: str = "") -> VideoItem:
    return VideoItem(
        title=str(item.get("title") or item.get("name") or item.get("bvid") or "Bilibili item"),
        bvid=str(item.get("bvid") or item.get("bv") or "").strip(),
        date=str(item.get("date") or ""),
        month=str(item.get("month") or ""),
        duration=item.get("duration") or "",
        cover=normalize_url(str(item.get("cover") or item.get("pic") or "")),
        source=source,
    )


def dedupe_videos(videos: Iterable[VideoItem]) -> list[VideoItem]:
    seen: set[str] = set()
    out: list[VideoItem] = []
    for video in videos:
        key = video.bvid or video.website or video.cover or video.title
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(video)
    return out


def cover_extension(url: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return ".jpg" if suffix == ".jpeg" else suffix
    return ".jpg"


def cover_path_for(video: VideoItem) -> Path:
    stem = clean_filename(f"{video.bvid} {video.title}", fallback=video.bvid or "cover")
    return COVER_DIR / f"{stem}{cover_extension(video.cover)}"


def download_cover(video: VideoItem, overwrite: bool = False) -> Path | None:
    if not video.cover:
        return None
    COVER_DIR.mkdir(parents=True, exist_ok=True)
    path = cover_path_for(video)
    if path.exists() and path.stat().st_size > 1024 and not overwrite:
        return path

    headers = {"Referer": BILI_REFERER, "User-Agent": USER_AGENT}
    resp = requests.get(video.cover, headers=headers, timeout=20)
    if resp.status_code in {403, 404, 412, 429}:
        raise RuntimeError(f"cover HTTP {resp.status_code}; stop and retry later")
    resp.raise_for_status()
    path.write_bytes(resp.content)
    return path


def prepare_manifest(videos: list[VideoItem], limit: int = 0, overwrite: bool = False) -> list[dict]:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    selected = videos[:limit] if limit and limit > 0 else videos
    manifest: list[dict] = []

    for index, video in enumerate(selected, 1):
        try:
            cover_path = download_cover(video, overwrite=overwrite)
            if index < len(selected):
                time.sleep(random.uniform(0.35, 0.9))
        except Exception as exc:
            print(f"[skip] {video.bvid or video.title}: {exc}")
            continue

        if not cover_path:
            print(f"[skip] {video.bvid or video.title}: no cover url")
            continue

        tags = ["Bilibili", "收藏夹"]
        if video.bvid:
            tags.append(video.bvid)
        if video.month:
            tags.append(video.month)

        manifest.append(
            {
                "path": str(cover_path),
                "name": video.title,
                "website": video.website,
                "annotation": build_annotation(video),
                "tags": tags,
                "bvid": video.bvid,
                "source": video.source,
            }
        )
        print(f"[cover] {index}/{len(selected)} {video.bvid} {video.title}")

    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ok] manifest saved: {MANIFEST_PATH}")
    return manifest


def build_annotation(video: VideoItem) -> str:
    lines = []
    if video.bvid:
        lines.append(f"BV: {video.bvid}")
    if video.date:
        lines.append(f"收藏/发布时间: {video.date}")
    if video.duration != "":
        lines.append(f"时长: {video.duration} 秒")
    if video.website:
        lines.append(video.website)
    return "\n".join(lines)


def eagle_available(api: str = EAGLE_API) -> bool:
    for endpoint in ("/api/application/info", "/api/library/info"):
        try:
            resp = requests.get(api + endpoint, timeout=3)
            if resp.ok:
                return True
        except requests.RequestException:
            pass
    return False


def import_to_eagle(manifest: list[dict], api: str = EAGLE_API, batch_size: int = 20) -> None:
    if not eagle_available(api):
        raise RuntimeError("Eagle API is not reachable. Please open Eagle first.")

    endpoint = api + "/api/item/addFromPaths"
    total = len(manifest)
    for start in range(0, total, batch_size):
        batch = manifest[start : start + batch_size]
        payload = {
            "items": [
                {
                    "path": item["path"],
                    "name": item.get("name") or item.get("bvid") or "Bilibili",
                    "website": item.get("website") or "",
                    "annotation": item.get("annotation") or "",
                    "tags": item.get("tags") or ["Bilibili"],
                }
                for item in batch
            ]
        }
        resp = requests.post(endpoint, json=payload, timeout=60)
        if not resp.ok:
            raise RuntimeError(f"Eagle import failed: HTTP {resp.status_code} {resp.text[:300]}")
        print(f"[eagle] imported {min(start + batch_size, total)}/{total}")
        time.sleep(0.25)


def read_manifest(path: Path = MANIFEST_PATH) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"manifest not found: {path}")
    data = load_json(path)
    if not isinstance(data, list):
        raise ValueError("manifest must be a JSON list")
    return data


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import BiliDownloader favorites into Eagle.")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--cache", type=Path, help="Use one fav_*.json cache file.")
    source.add_argument("--cache-dir", type=Path, default=None, help="Use every fav_*.json in this directory.")
    source.add_argument("--source-state", action="store_true", help="Read the running Web UI state.")
    parser.add_argument("--limit", type=int, default=30, help="Limit items during prepare; 0 means all. Default: 30")
    parser.add_argument("--overwrite-covers", action="store_true", help="Re-download covers even if cached.")
    parser.add_argument("--prepare-only", action="store_true", help="Only download covers and write manifest.")
    parser.add_argument("--import-only", action="store_true", help="Import an existing manifest without downloading.")
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH, help="Manifest path for --import-only.")
    parser.add_argument("--eagle-api", default=EAGLE_API, help="Eagle API base URL. Default: http://localhost:41595")
    parser.add_argument("--batch-size", type=int, default=20, help="Eagle import batch size. Default: 20")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    configure_console()
    args = parse_args(argv)

    try:
        if args.import_only:
            manifest = read_manifest(args.manifest)
        else:
            if args.source_state:
                videos = videos_from_state()
            elif args.cache:
                videos = videos_from_cache(args.cache)
            else:
                videos = videos_from_cache_dir(args.cache_dir or DEFAULT_CACHE_DIR)

            videos = dedupe_videos(videos)
            print(f"[source] loaded {len(videos)} unique videos")
            manifest = prepare_manifest(videos, limit=args.limit, overwrite=args.overwrite_covers)

        if args.prepare_only:
            print("[done] prepare-only mode; Eagle import skipped")
            return 0

        import_to_eagle(manifest, api=args.eagle_api, batch_size=max(1, args.batch_size))
        print("[done] Eagle import completed")
        return 0
    except KeyboardInterrupt:
        print("\n[cancelled]")
        return 130
    except Exception as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
