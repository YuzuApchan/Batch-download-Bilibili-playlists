@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "LIBRARY_DIR="
set /P "LIBRARY_DIR=Input copied/test Eagle .library folder path: "
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
python apply_contact_sheets_to_eagle.py --library-dir "%LIBRARY_DIR%" --limit 10
echo.
echo If the dry-run matches look correct, close Eagle and run:
echo python apply_contact_sheets_to_eagle.py --library-dir "%LIBRARY_DIR%" --limit 10 --apply
pause
