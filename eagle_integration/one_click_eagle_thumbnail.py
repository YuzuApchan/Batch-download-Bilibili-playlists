#!/usr/bin/env python3
"""One-click Eagle video thumbnail workflow.

This scans an Eagle .library, uses videos copied inside the library to generate
cover-first contact sheets, then optionally applies them as custom thumbnails.

Default is dry-run/prepare-only. Add --apply to write Eagle library files.
Close Eagle before running with --apply.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

from apply_contact_sheets_to_eagle import (
    BACKUP_ROOT,
    apply_match,
    find_library_items,
    match_manifest_to_library,
)
from export_to_eagle import EXPORT_DIR, configure_console, dedupe_videos, videos_from_cache, videos_from_cache_dir
from import_videos_to_eagle import (
    VIDEO_MANIFEST_PATH,
    build_video_manifest,
    match_eagle_library_items,
    scan_eagle_library_videos,
)


REPORT_PATH = EXPORT_DIR / "one_click_report.json"
STATE_PATH = EXPORT_DIR / "one_click_state.json"
FOLDERS_PATH = EXPORT_DIR / "eagle_folders.json"
DEFAULT_USERDATA = Path(__file__).resolve().parent.parent / "userdata"


def load_videos(cache: Path | None, cache_dir: Path) -> list:
    if cache:
        return dedupe_videos(videos_from_cache(cache))
    return dedupe_videos(videos_from_cache_dir(cache_dir))


def load_history_bvids(history: Path | None, userdata_dir: Path = DEFAULT_USERDATA) -> set[str]:
    paths = []
    if history:
        paths.append(history)
    else:
        paths.extend(userdata_dir.glob("*/history.json"))
    bvids: set[str] = set()
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, list):
            bvids.update(str(item).strip() for item in data if str(item).startswith("BV"))
    return bvids


def write_report(report: dict) -> None:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_state(state: dict) -> None:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def state_key(library_dir: Path) -> str:
    return str(library_dir.resolve()).lower()


def progress(current: int, total: int, message: str) -> None:
    width = 24
    filled = int(width * current / total) if total else width
    bar = "#" * filled + "-" * (width - filled)
    percent = int(100 * current / total) if total else 100
    print(f"[progress] {current}/{total} [{bar}] {percent:3d}% {message}")


def flatten_folders(nodes: list, prefix: str = "") -> list[dict]:
    out = []
    for node in nodes or []:
        name = str(node.get("name") or "")
        path = f"{prefix}/{name}" if prefix else name
        item = {
            "id": node.get("id"),
            "name": name,
            "path": path,
            "children": [child.get("id") for child in node.get("children", []) or []],
        }
        out.append(item)
        out.extend(flatten_folders(node.get("children", []) or [], path))
    return out


def load_library_folders(library_dir: Path) -> list[dict]:
    metadata_path = library_dir / "metadata.json"
    if not metadata_path.exists():
        return []
    try:
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return flatten_folders(data.get("folders", []) or [])


def folder_descendant_ids(folders: list[dict], folder_ids: set[str]) -> set[str]:
    by_id = {str(item.get("id")): item for item in folders if item.get("id")}
    result = set(folder_ids)
    stack = list(folder_ids)
    while stack:
        current = stack.pop()
        for child in by_id.get(current, {}).get("children", []) or []:
            child = str(child)
            if child not in result:
                result.add(child)
                stack.append(child)
    return result


def export_folder_list(library_dir: Path) -> None:
    folders = load_library_folders(library_dir)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    FOLDERS_PATH.write_text(json.dumps(folders, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[folders] exported {len(folders)} folders -> {FOLDERS_PATH}")
    for item in folders[:80]:
        print(f"[folder] {item.get('id')} | {item.get('path')}")


def filter_items_by_folders(items: list[dict], allowed_folder_ids: set[str]) -> list[dict]:
    if not allowed_folder_ids:
        return items
    filtered = []
    for item in items:
        folders = set(str(x) for x in item.get("metadata", {}).get("folders", []) or [])
        if folders & allowed_folder_ids:
            filtered.append(item)
    return filtered


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate and apply Eagle contact-sheet thumbnails in one step.")
    parser.add_argument("--library-dir", type=Path, required=True, help="Eagle .library folder.")
    parser.add_argument("--list-folders", action="store_true", help="Export and print Eagle folder ids, then exit.")
    parser.add_argument("--folder-id", action="append", default=[], help="Only process this Eagle folder id. Can be repeated.")
    parser.add_argument("--include-child-folders", action="store_true", help="Include descendants of --folder-id.")
    parser.add_argument("--cache", type=Path, help="Use one fav_*.json cache file.")
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "userdata" / "_web_cache",
    )
    parser.add_argument("--limit", type=int, default=20, help="Limit processed matches; 0 means all. Default: 20")
    parser.add_argument("--min-score", type=float, default=0.9)
    parser.add_argument("--allow-title-match", action="store_true", help="Allow fuzzy title matching. Risky for large libraries.")
    parser.add_argument("--history", type=Path, help="History JSON with downloaded BV ids. Defaults to userdata/*/history.json.")
    parser.add_argument(
        "--history-title-match",
        action="store_true",
        help="Allow title matching only among BV ids present in history. Safer than unrestricted title matching.",
    )
    parser.add_argument(
        "--history-cover-min-score",
        type=float,
        default=0.95,
        help="History title matches below this score are skipped; matches at/above use Bilibili cover. Default: 0.95",
    )
    parser.add_argument("--frames", type=int, default=8)
    parser.add_argument("--columns", type=int, default=4)
    parser.add_argument("--sheet-width", type=int, default=1920)
    parser.add_argument("--no-danmaku", action="store_true")
    parser.add_argument("--overwrite-sheets", action="store_true", help="Regenerate existing contact sheets.")
    parser.add_argument("--skip-custom", action="store_true", help="Skip Eagle items that already have custom thumbnails.")
    parser.add_argument("--force", action="store_true", help="Ignore saved state and process matched items again.")
    parser.add_argument("--apply", action="store_true", help="Actually replace Eagle thumbnails.")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    configure_console()
    args = parse_args(argv)
    report = {
        "library_dir": str(args.library_dir),
        "apply": args.apply,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "matched": [],
        "skipped": [],
        "errors": [],
    }

    try:
        state = load_state()
        lib_key = state_key(args.library_dir)
        lib_state = state.setdefault(lib_key, {"generated": {}, "applied": {}})
        if args.list_folders:
            export_folder_list(args.library_dir)
            return 0
        videos = load_videos(args.cache, args.cache_dir)
        history_bvids = load_history_bvids(args.history)
        if history_bvids:
            history_filtered = [video for video in videos if video.bvid in history_bvids]
        else:
            history_filtered = videos
        eagle_video_items = scan_eagle_library_videos(args.library_dir)
        selected_folder_ids = set(str(x) for x in args.folder_id if str(x).strip())
        if selected_folder_ids and args.include_child_folders:
            selected_folder_ids = folder_descendant_ids(load_library_folders(args.library_dir), selected_folder_ids)
        if selected_folder_ids:
            before_folder_filter = len(eagle_video_items)
            eagle_video_items = filter_items_by_folders(eagle_video_items, selected_folder_ids)
            print(f"[folder-filter] {before_folder_filter} -> {len(eagle_video_items)} items in selected folders")
        if args.skip_custom:
            library_items = find_library_items(args.library_dir)
            custom_ids = {str(item["id"]) for item in library_items if item["metadata"].get("customThumbnail")}
            eagle_video_items = [item for item in eagle_video_items if str(item["eagle_id"]) not in custom_ids]

        allow_title = args.allow_title_match or args.history_title_match
        match_videos = history_filtered if args.history_title_match else videos
        matches = match_eagle_library_items(
            match_videos,
            eagle_video_items,
            min_score=args.min_score,
            allow_title_match=allow_title,
        )
        if args.history_title_match:
            safe_matches = []
            for match in matches:
                if match.get("method") == "eagle-bvid" or float(match.get("score") or 0) >= args.history_cover_min_score:
                    safe_matches.append(match)
                else:
                    report["skipped"].append(
                        {
                            "eagle_id": match.get("eagle_id"),
                            "source_video": str(match.get("source_path")),
                            "bvid": getattr(match.get("video"), "bvid", ""),
                            "score": match.get("score"),
                            "reason": f"history title score below {args.history_cover_min_score}",
                        }
                    )
            matches = safe_matches
        matched_eagle_ids = {str(match.get("eagle_id")) for match in matches}
        for item in eagle_video_items:
            if str(item.get("eagle_id")) not in matched_eagle_ids:
                report["skipped"].append(
                    {
                        "eagle_id": item.get("eagle_id"),
                        "source_video": str(item.get("source_path")),
                        "reason": "no BV/history exact match" if not allow_title else "no safe match",
                    }
                )
        if args.limit and args.limit > 0:
            matches = matches[: args.limit]

        original_count = len(matches)
        if not args.force:
            pending = []
            for match in matches:
                eagle_id = str(match.get("eagle_id") or "")
                done_bucket = lib_state["applied"] if args.apply else lib_state["generated"]
                if eagle_id and eagle_id in done_bucket:
                    report["skipped"].append(
                        {
                            "eagle_id": eagle_id,
                            "bvid": getattr(match.get("video"), "bvid", ""),
                            "reason": "already applied" if args.apply else "already generated",
                        }
                    )
                    continue
                pending.append(match)
            matches = pending

        print(
            f"[source] cache videos={len(videos)} history bvids={len(history_bvids)} "
            f"candidate videos={len(match_videos)} eagle videos={len(eagle_video_items)} "
            f"matched={original_count} pending={len(matches)}"
        )
        if not matches:
            report["message"] = "no pending matches; use --force to process again"
            write_report(report)
            print("[done] no pending matches. Use --force to process again.")
            print(f"[report] {REPORT_PATH}")
            return 0

        generated_manifest = []
        total = len(matches)
        for index, match in enumerate(matches, 1):
            try:
                eagle_id = str(match.get("eagle_id") or "")
                bvid = getattr(match.get("video"), "bvid", "")
                progress(index, total, f"generate {bvid} {eagle_id}")
                manifest_items = build_video_manifest(
                    [match],
                    mode="contact-sheet",
                    overwrite=args.overwrite_sheets,
                    limit=1,
                    frame_count=max(1, args.frames),
                    columns=max(1, args.columns),
                    sheet_width=max(720, args.sheet_width),
                    use_danmaku=not args.no_danmaku,
                    bilibili_cover_min_score=args.history_cover_min_score if args.history_title_match else 1.0,
                )
                if manifest_items:
                    item = manifest_items[0]
                    item["eagle_id"] = match.get("eagle_id", "")
                    generated_manifest.append(item)
                    report["matched"].append(
                        {
                            "bvid": item.get("bvid"),
                            "eagle_id": item.get("eagle_id"),
                            "source_video": item.get("source_video"),
                            "contact_sheet": item.get("contact_sheet"),
                        }
                    )
                    if eagle_id:
                        lib_state["generated"][eagle_id] = {
                            "bvid": item.get("bvid"),
                            "contact_sheet": item.get("contact_sheet"),
                            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        }
                        save_state(state)
            except Exception as exc:
                video = match.get("video")
                report["errors"].append(
                    {
                        "bvid": getattr(video, "bvid", ""),
                        "source_path": str(match.get("source_path", "")),
                        "error": str(exc),
                    }
                )
                print(f"[skip-error] {getattr(video, 'bvid', '')}: {exc}")

        if not generated_manifest:
            report["errors"].append("no contact sheets generated")
            write_report(report)
            print(f"[report] {REPORT_PATH}")
            return 1

        VIDEO_MANIFEST_PATH.write_text(
            json.dumps(generated_manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        library_items = find_library_items(args.library_dir)
        apply_matches = match_manifest_to_library(generated_manifest, library_items)
        print(f"[apply-match] generated={len(generated_manifest)} eagle matched={len(apply_matches)}")
        for match in apply_matches:
            print(f"[match] {match['entry'].get('bvid')} -> {match['item']['id']}")

        if not args.apply:
            report["dry_run"] = True
            write_report(report)
            print("[dry-run] no Eagle files changed. Re-run with --apply after closing Eagle.")
            print(f"[report] {REPORT_PATH}")
            return 0

        stamp = time.strftime("%Y%m%d_%H%M%S")
        total_apply = len(apply_matches)
        for index, match in enumerate(apply_matches, 1):
            try:
                eagle_id = str(match["item"].get("id") or "")
                progress(index, total_apply, f"apply {match['entry'].get('bvid')} {eagle_id}")
                apply_match(match, args.library_dir, stamp)
                if eagle_id:
                    lib_state["applied"][eagle_id] = {
                        "bvid": match["entry"].get("bvid"),
                        "contact_sheet": match["entry"].get("contact_sheet"),
                        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    }
                    save_state(state)
            except Exception as exc:
                report["errors"].append(
                    {
                        "bvid": match["entry"].get("bvid"),
                        "eagle_id": match["item"].get("id"),
                        "error": str(exc),
                    }
                )
                print(f"[apply-error] {match['entry'].get('bvid')}: {exc}")

        report["applied"] = len(apply_matches) - len([e for e in report["errors"] if isinstance(e, dict) and e.get("eagle_id")])
        report["backup_dir"] = str(BACKUP_ROOT / stamp)
        write_report(report)
        print(f"[done] applied={report['applied']} backup={BACKUP_ROOT / stamp}")
        print(f"[report] {REPORT_PATH}")
        return 0
    except Exception as exc:
        report["errors"].append(str(exc))
        write_report(report)
        print(f"[error] {exc}", file=sys.stderr)
        print(f"[report] {REPORT_PATH}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
