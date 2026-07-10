# BiliDownloader -> Eagle integration experiment

This folder is isolated from the main downloader.

## Visual UI

Double click:

```text
启动可视化界面.bat
```

The UI can:

- choose an Eagle `.library` folder;
- read and choose Eagle folders such as `视频类`;
- run dry-run or apply mode;
- show logs and progress;
- resume from `exports/one_click_state.json`;
- open the latest report.

Keep `直接应用到 Eagle` unchecked for dry-run. Close Eagle before checking it and
running apply mode.

Current direction:

- Keep original videos unchanged.
- Generate a cover-first contact sheet image for each video.
- Import the original video into Eagle with metadata.
- Record the contact sheet path in `exports/video_manifest.json`.
- Apply the contact sheet to Eagle's internal custom thumbnail files after a dry-run.

## Contact Sheet Design

The generated thumbnail image follows this layout:

- Top: large Bilibili cover image.
- Bottom: 8 video still frames in a 4x2 grid by default.
- Canvas size follows the source video's aspect ratio, for example 1920x1080 for 16:9.
- If danmaku has a clear peak, one bottom frame is taken near that peak.
- If no peak or no danmaku exists, the script falls back to random frames.
- Candidate frames are scored to avoid very dark, very bright, or low-detail frames.

Danmaku timing is cached in:

```text
exports/danmaku_cache/
```

Use `--no-danmaku` for fully offline generation.

## Prepare Contact Sheets

If you still have the downloaded videos in a normal folder, double click:

```text
prepare_contact_sheets.bat
```

Then input the folder that contains your downloaded videos.

If old videos were already imported into Eagle and the original download folder
was deleted, use the Eagle library directly:

```text
prepare_from_eagle_library.bat
```

Then input the `.library` folder path. This works when Eagle copied the video
files into `images/<item-id>.info/`.

Command-line equivalent:

```powershell
python import_videos_to_eagle.py --video-dir "D:\YourVideoFolder" --mode contact-sheet --overwrite --prepare-only --limit 10
```

Eagle-library equivalent:

```powershell
python import_videos_to_eagle.py --eagle-library "E:\YourLibrary.library" --mode contact-sheet --overwrite --prepare-only --limit 10
```

Useful options:

```powershell
python import_videos_to_eagle.py --video-dir "D:\YourVideoFolder" --frames 8 --columns 4 --sheet-width 1920 --prepare-only --limit 10
```

`--sheet-width` is the preferred long-side/base width. The final image keeps the
source video's aspect ratio so Eagle's grid does not crop the contact sheet.

Offline/no danmaku mode:

```powershell
python import_videos_to_eagle.py --video-dir "D:\YourVideoFolder" --no-danmaku --prepare-only --limit 10
```

## Import Original Videos

After checking `exports/video_manifest.json`, open Eagle and run:

```powershell
python import_videos_to_eagle.py --import-only
```

Or double click:

```text
import_videos_to_eagle.bat
```

This imports the original videos. It does not set thumbnails through Eagle's API,
because the public API has no official thumbnail field.

## Apply Contact Sheets As Eagle Thumbnails

Eagle custom thumbnail behavior observed in a copied test library:

- replace `images/<item-id>.info/*_thumbnail.png`;
- set `customThumbnail` and `lastModified` in `metadata.json`;
- update the root `mtime.json`;
- skip deleted Eagle items.

Use a copied/test library first. Close Eagle before running `--apply`.

Dry-run:

```powershell
python apply_contact_sheets_to_eagle.py --library-dir "E:\YourCopiedTest.library" --limit 10
```

Apply:

```powershell
python apply_contact_sheets_to_eagle.py --library-dir "E:\YourCopiedTest.library" --limit 10 --apply
```

Backups are written to:

```text
exports/eagle_thumbnail_backups/
```

## One-Click Eagle Library Workflow

Recommended for old Eagle libraries where the original download folder was
deleted but Eagle copied the videos into the `.library`.

If the library contains many non-video design assets, list folder ids first:

```text
list_eagle_folders.bat
```

Example folder id:

```text
YOUR_FOLDER_ID
```

Use `--folder-id` and `--include-child-folders` to restrict processing to that
folder tree.

Dry-run:

```text
one_click_eagle_thumbnail.bat
```

Apply after checking the dry-run result and closing Eagle:

```text
one_click_eagle_thumbnail_apply.bat
```

Command-line dry-run:

```powershell
python one_click_eagle_thumbnail.py --library-dir "E:\YourLibrary.library" --limit 20
```

Folder-limited dry-run:

```powershell
python one_click_eagle_thumbnail.py --library-dir "E:\YourLibrary.library" --folder-id YOUR_FOLDER_ID --include-child-folders --limit 20
```

Command-line apply:

```powershell
python one_click_eagle_thumbnail.py --library-dir "E:\YourLibrary.library" --limit 20 --apply
```

Folder-limited apply:

```powershell
python one_click_eagle_thumbnail.py --library-dir "E:\YourLibrary.library" --folder-id YOUR_FOLDER_ID --include-child-folders --limit 20 --apply
```

Items that cannot be detected, matched, or processed are skipped and written to:

```text
exports/one_click_report.json
```

Progress and resume state are saved to:

```text
exports/one_click_state.json
```

Completed items are skipped on the next run, so interrupted batches can resume
without regenerating or reapplying completed thumbnails. To process again, add:

```powershell
--force
```

By default, the one-click workflow only accepts exact BV matches found in Eagle
metadata/path text. This avoids mixing a Bilibili cover from one video with
frames from another video. Fuzzy title matching is disabled by default.

If your imported download history contains BV ids, use history-limited title
matching for old Eagle libraries that do not store BV in metadata:

```text
one_click_eagle_thumbnail_history.bat
```

Command line:

```powershell
python one_click_eagle_thumbnail.py --library-dir "E:\YourLibrary.library" --limit 20 --history-title-match
```

This only considers BV ids present in `userdata/*/history.json`. It is safer
than unrestricted fuzzy matching. When the title score is at least `0.95`, the
top image uses the Bilibili cover for the matched BV. Lower-score matches are
skipped by default.

You can change the safety threshold:

```powershell
python one_click_eagle_thumbnail.py --library-dir "E:\YourLibrary.library" --limit 20 --history-title-match --history-cover-min-score 0.97
```

If you intentionally want unrestricted title matching for a tiny manually checked
batch, use:

```powershell
python one_click_eagle_thumbnail.py --library-dir "E:\YourLibrary.library" --limit 20 --allow-title-match
```

## Output Files

- `exports/contact_sheets/`: generated cover-first contact sheets.
- `exports/video_manifest.json`: original-video import manifest plus contact-sheet paths.
- `exports/video_match_report.json`: match report.
- `exports/danmaku_cache/`: cached danmaku timing analysis.
- `exports/eagle_thumbnail_backups/`: backups before thumbnail replacement.
- `exports/one_click_report.json`: one-click workflow report.
- `exports/one_click_state.json`: resume/progress state.

## Safety

- Original videos are not modified.
- Contact sheets are generated from local video files.
- Bilibili cover and danmaku requests are cached.
- Use `--limit` for small batches first.
- Close Eagle before applying library-file changes.
