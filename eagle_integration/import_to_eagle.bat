@echo off
cd /d "%~dp0"
python export_to_eagle.py --import-only
pause
