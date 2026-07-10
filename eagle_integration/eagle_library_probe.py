#!/usr/bin/env python3
"""Snapshot and compare an Eagle library folder.

Use this only on a copied/test Eagle library. The goal is to discover which
library files change after manually setting a custom thumbnail in Eagle.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SNAPSHOT_DIR = ROOT / "exports" / "library_snapshots"
HASH_LIMIT_BYTES = 25 * 1024 * 1024


def sha1_file(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot_library(library_dir: Path) -> dict:
    if not library_dir.exists():
        raise FileNotFoundError(f"library dir not found: {library_dir}")
    files = {}
    for path in library_dir.rglob("*"):
        if not path.is_file():
            continue
        stat = path.stat()
        rel = path.relative_to(library_dir).as_posix()
        item = {
            "size": stat.st_size,
            "mtime": int(stat.st_mtime),
            "suffix": path.suffix.lower(),
        }
        if stat.st_size <= HASH_LIMIT_BYTES:
            try:
                item["sha1"] = sha1_file(path)
            except Exception as exc:
                item["sha1_error"] = str(exc)
        files[rel] = item
    return {
        "library_dir": str(library_dir),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "file_count": len(files),
        "files": files,
    }


def write_snapshot(data: dict, name: str) -> Path:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)
    path = SNAPSHOT_DIR / f"{safe}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_snapshot(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def compare_snapshots(before: dict, after: dict) -> dict:
    a = before.get("files", {})
    b = after.get("files", {})
    added = sorted(set(b) - set(a))
    removed = sorted(set(a) - set(b))
    changed = []
    for rel in sorted(set(a) & set(b)):
        old = a[rel]
        new = b[rel]
        if old.get("size") != new.get("size") or old.get("sha1") != new.get("sha1"):
            changed.append(
                {
                    "path": rel,
                    "before": old,
                    "after": new,
                }
            )
    return {
        "before": before.get("created_at"),
        "after": after.get("created_at"),
        "added": added,
        "removed": removed,
        "changed": changed,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Snapshot/compare an Eagle library folder.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    snap = sub.add_parser("snapshot", help="Create a library snapshot.")
    snap.add_argument("--library-dir", type=Path, required=True)
    snap.add_argument("--name", required=True, help="Snapshot name, for example before or after.")

    comp = sub.add_parser("compare", help="Compare two snapshots.")
    comp.add_argument("--before", type=Path, required=True)
    comp.add_argument("--after", type=Path, required=True)
    comp.add_argument("--name", default="compare")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        if args.cmd == "snapshot":
            data = snapshot_library(args.library_dir)
            out = write_snapshot(data, args.name)
            print(f"[ok] snapshot saved: {out}")
            print(f"[ok] files: {data['file_count']}")
            return 0

        before = load_snapshot(args.before)
        after = load_snapshot(args.after)
        diff = compare_snapshots(before, after)
        out = write_snapshot(diff, args.name)
        print(f"[ok] diff saved: {out}")
        print(f"[diff] added={len(diff['added'])} removed={len(diff['removed'])} changed={len(diff['changed'])}")
        for item in diff["changed"][:20]:
            print(f"[changed] {item['path']}")
        for item in diff["added"][:20]:
            print(f"[added] {item}")
        return 0
    except Exception as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
