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
python one_click_eagle_thumbnail.py --library-dir "%LIBRARY_DIR%" --list-folders
pause
