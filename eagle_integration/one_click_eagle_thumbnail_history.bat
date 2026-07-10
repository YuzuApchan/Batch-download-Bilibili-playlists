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
python one_click_eagle_thumbnail.py --library-dir "%LIBRARY_DIR%" --limit 20 --history-title-match
echo.
echo This is a dry-run using history-limited title matching.
echo If the report looks correct, close Eagle and run:
echo python one_click_eagle_thumbnail.py --library-dir "%LIBRARY_DIR%" --limit 20 --history-title-match --apply
pause
