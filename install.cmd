@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Устанавливаю библиотеки Python...
python -m pip install --disable-pip-version-check -q --no-warn-script-location -r requirements.txt
if errorlevel 1 (
    echo Не удалось установить библиотеки. Проверь, что Python установлен и есть в PATH.
    pause
    exit /b 1
)
python -X utf8 multitool.py --install %*
pause
