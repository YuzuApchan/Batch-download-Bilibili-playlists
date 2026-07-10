@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "LIBRARY_DIR="
set /P "LIBRARY_DIR=Input Eagle .library folder path: "
if "%LIBRARY_DIR%"=="" (
  echo Library folder is empty.
  pause
  exit /b 1
)
if not exist "%LIBRARY_DIR%\" (
  echo Library folder does not exist: "%LIBRARY_DIR%"
  pause
  exit /b 1
)
python import_videos_to_eagle.py --eagle-library "%LIBRARY_DIR%" --mode contact-sheet --overwrite --prepare-only --limit 10
pause
