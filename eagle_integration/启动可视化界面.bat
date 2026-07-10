@echo off
chcp 65001 >nul
cd /d "%~dp0"
python eagle_thumbnail_gui.py
pause
