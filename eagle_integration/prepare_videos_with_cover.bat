@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "VIDEO_DIR="
set /P "VIDEO_DIR=Input local video folder path: "
if "%VIDEO_DIR%"=="" (
  echo Video folder is empty.
  pause
  exit /b 1
)
if not exist "%VIDEO_DIR%\" (
  echo Video folder does not exist: "%VIDEO_DIR%"
  pause
  exit /b 1
)
python import_videos_to_eagle.py --video-dir "%VIDEO_DIR%" --mode contact-sheet --overwrite --prepare-only --limit 10
pause
