@echo off
rem Fresh Multitool — эта папка добавлена в PATH, поэтому команда работает из любого места.
rem   fresh_multitool          открыть в отдельном окне (или переключиться на уже открытое)
rem   fresh_multitool --here   запустить прямо в текущей консоли
set "MT_HOME=%~dp0.."
if /i "%~1"=="--here" (
    python -X utf8 "%MT_HOME%\multitool.py" %*
    exit /b
)
if /i "%~1"=="--install" goto console
if /i "%~1"=="--uninstall" goto console
if /i "%~1"=="--help" goto console
if /i "%~1"=="-h" goto console
start "" pythonw "%MT_HOME%\multitool.py" --open %*
exit /b

:console
python -X utf8 "%MT_HOME%\multitool.py" %*
