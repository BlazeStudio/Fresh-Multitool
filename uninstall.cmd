@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -X utf8 multitool.py --uninstall
pause
