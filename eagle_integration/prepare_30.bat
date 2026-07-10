@echo off
cd /d "%~dp0"
python export_to_eagle.py --prepare-only --limit 30
pause
