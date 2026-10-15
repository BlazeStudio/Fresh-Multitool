"""Низкоуровневая работа с Windows: запуск, окна, PATH, ярлыки, Windows Terminal."""

from __future__ import annotations

import base64
import ctypes
import json
import os
import shutil
import subprocess
import sys
import winreg
from ctypes import wintypes
from pathlib import Path

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

shell32.ShellExecuteW.argtypes = [
    wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int,
]
shell32.ShellExecuteW.restype = ctypes.c_void_p
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.OpenMutexW.restype = wintypes.HANDLE
user32.GetForegroundWindow.restype = wintypes.HWND

MUTEX_NAME = "Local\\FreshMultitool.Instance"
DAEMON_MUTEX = "Local\\FreshMultitool.Hotkey"
APP_NAME = "Fresh Multitool"
PROFILE_GUID = "{6d1f3c2a-8b4e-4f6a-9c1d-2e7f5a9b0c3d}"
SCHEME_NAME = "Fresh Multitool"

SHOW = {"normal": 1, "hidden": 0, "minimized": 7, "maximized": 3}

SHELL_ERRORS = {
    0: "недостаточно памяти",
    2: "файл не найден",
    3: "путь не найден",
    5: "доступ запрещён или запуск от администратора отменён",
    8: "недостаточно памяти",
    11: "повреждённый exe-файл",
    26: "нарушение общего доступа",
    27: "неполное имя ассоциации",
    28: "тайм-аут DDE",
    29: "ошибка DDE",
    30: "DDE занят",
    31: "нет программы, связанной с этим типом файла",
    32: "DLL не найдена",
}

_mutex_handles: dict[str, int] = {}


# ─── запуск ──────────────────────────────────────────────────────────────────

def shell_execute(target: str, params: str = "", cwd: str | None = None,
                  admin: bool = False, window: str = "normal") -> None:
    """Запуск как двойной клик в Проводнике (exe, bat, lnk, URL, папка). Бросает OSError."""
    code = shell32.ShellExecuteW(
        None, "runas" if admin else "open", target, params or None, cwd or None, SHOW.get(window, 1)
    )
    code = int(code or 0)
    if code <= 32:
        raise OSError(SHELL_ERRORS.get(code, f"код ошибки {code}"))


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


# ─── единственный экземпляр ──────────────────────────────────────────────────

def mutex_exists(name: str = MUTEX_NAME) -> bool:
    handle = kernel32.OpenMutexW(0x00100000, False, name)  # SYNCHRONIZE
    if handle:
        kernel32.CloseHandle(handle)
        return True
    return False


def claim_mutex(name: str = MUTEX_NAME) -> bool:
    """Держим именованный мьютекс, пока жив процесс, — по нему видно, что экземпляр запущен.
    Возвращает False, если мьютекс уже был у другого процесса."""
    _mutex_handles[name] = kernel32.CreateMutexW(None, False, name)
    return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS


def instance_running() -> bool:
    return mutex_exists(MUTEX_NAME)


def claim_instance() -> None:
    claim_mutex(MUTEX_NAME)


# ─── глобальная горячая клавиша ──────────────────────────────────────────────

MODIFIERS = {"ctrl": 0x2, "control": 0x2, "alt": 0x1, "shift": 0x4, "win": 0x8}
NAMED_KEYS = {"space": 0x20, "`": 0xC0, "~": 0xC0, "tilde": 0xC0, "ё": 0xC0, "tab": 0x09, "enter": 0x0D}


def parse_hotkey(text: str) -> tuple[int, int] | None:
    """«Ctrl+Alt+M» → (модификаторы, виртуальный код клавиши) для RegisterHotKey."""
    mods, vk = 0, None
    for part in (p.strip().lower() for p in (text or "").split("+") if p.strip()):
        if part in MODIFIERS:
            mods |= MODIFIERS[part]
        elif part in NAMED_KEYS:
            vk = NAMED_KEYS[part]
        elif part.startswith("f") and part[1:].isdigit() and 1 <= int(part[1:]) <= 24:
            vk = 0x6F + int(part[1:])
        elif len(part) == 1 and part.isascii() and part.isalnum():
            vk = ord(part.upper())
    if vk is None or not mods:
        return None
    return mods | 0x4000, vk  # MOD_NOREPEAT


def hotkey_loop(get_hotkey, on_press, on_register=None) -> None:
    """Регистрирует горячую клавишу и крутит цикл сообщений. Раз в 5 с проверяет конфиг:
    клавиша поменялась или была занята — пробует зарегистрировать снова."""
    current = None
    last_report = object()

    def register(hotkey):
        nonlocal current, last_report
        if current:
            user32.UnregisterHotKey(None, 1)
            current = None
        if hotkey and user32.RegisterHotKey(None, 1, hotkey[0], hotkey[1]):
            current = hotkey
        report = (hotkey, current is not None)
        if on_register and report != last_report:
            on_register(*report)
            last_report = report

    register(get_hotkey())
    user32.SetTimer(None, 0, 5000, None)
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == 0x0312:  # WM_HOTKEY
            on_press()
        elif msg.message == 0x0113:  # WM_TIMER
            wanted = get_hotkey()
            if wanted != current:
                register(wanted)


# ─── окна ────────────────────────────────────────────────────────────────────

class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT), ("rcWork", RECT), ("dwFlags", wintypes.DWORD)]


def find_windows(title: str) -> list[int]:
    found: list[int] = []
    buf = ctypes.create_unicode_buffer(512)

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            user32.GetWindowTextW(hwnd, buf, 512)
            if buf.value == title:
                found.append(hwnd)
        return True

    user32.EnumWindows(callback, 0)
    return found


def process_windows(pids: set[int]) -> list[int]:
    """Главные окна процессов: видимые (или свёрнутые), без владельца, с заголовком."""
    found: list[int] = []
    pid = wintypes.DWORD()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _):
        if user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, 4) and user32.GetWindowTextLengthW(hwnd):
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids:
                found.append(hwnd)
        return True

    user32.EnumWindows(callback, 0)
    return found


def focus_window(hwnd: int) -> None:
    if user32.IsIconic(hwnd):
        # Async: обычный ShowWindow ждёт, пока чужая программа обработает запрос, —
        # зависшее приложение подвесило бы и Multitool.
        user32.ShowWindowAsync(hwnd, 9)  # SW_RESTORE
    # Нажатие Alt снимает запрет Windows на перехват фокуса фоновым процессом.
    user32.keybd_event(0x12, 0, 0, 0)
    user32.keybd_event(0x12, 0, 2, 0)
    user32.SetForegroundWindow(hwnd)


def center_window(hwnd: int) -> None:
    rect = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    monitor = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
    info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
    user32.GetMonitorInfoW(monitor, ctypes.byref(info))
    work = info.rcWork
    w, h = rect.right - rect.left, rect.bottom - rect.top
    x = work.left + max((work.right - work.left - w) // 2, 0)
    y = work.top + max((work.bottom - work.top - h) // 2, 0)
    user32.SetWindowPos(hwnd, None, x, y, 0, 0, 0x0001 | 0x0004)  # NOSIZE | NOZORDER


def set_console_title(title: str) -> None:
    kernel32.SetConsoleTitleW(title)


def message_box(text: str, title: str = APP_NAME, error: bool = True) -> None:
    user32.MessageBoxW(None, text, title, 0x10 if error else 0x40)


# ─── python / пути ───────────────────────────────────────────────────────────

def python_exe(windowed: bool = False) -> str:
    """Путь к python.exe (консольный) или pythonw.exe (без окна) этой же установки."""
    exe = Path(sys.executable)
    name = "pythonw.exe" if windowed else "python.exe"
    candidate = exe.with_name(name)
    if candidate.exists():
        return str(candidate)
    found = shutil.which(name)
    return found or str(exe)


def wt_exe() -> str | None:
    return shutil.which("wt.exe") or shutil.which("wt")


def fragment_path() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "Windows Terminal" / "Fragments" / APP_NAME / "multitool.json"


# ─── PATH пользователя ───────────────────────────────────────────────────────

def _broadcast_env_change() -> None:
    result = ctypes.c_size_t()
    user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0x0002, 3000, ctypes.byref(result))


def _read_user_path() -> tuple[str, int]:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ) as key:
        try:
            value, kind = winreg.QueryValueEx(key, "Path")
            return value, kind
        except FileNotFoundError:
            return "", winreg.REG_EXPAND_SZ


def path_contains(folder: str) -> bool:
    value, _ = _read_user_path()
    norm = os.path.normcase(os.path.normpath(folder))
    return any(os.path.normcase(os.path.normpath(os.path.expandvars(p))) == norm for p in value.split(";") if p)


def add_to_user_path(folder: str) -> bool:
    if path_contains(folder):
        return False
    value, kind = _read_user_path()
    parts = [p for p in value.split(";") if p]
    parts.append(folder)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "Path", 0, kind or winreg.REG_EXPAND_SZ, ";".join(parts))
    _broadcast_env_change()
    return True


def remove_from_user_path(folder: str) -> bool:
    value, kind = _read_user_path()
    norm = os.path.normcase(os.path.normpath(folder))
    parts = [p for p in value.split(";") if p]
    kept = [p for p in parts if os.path.normcase(os.path.normpath(os.path.expandvars(p))) != norm]
    if len(kept) == len(parts):
        return False
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "Path", 0, kind, ";".join(kept))
    _broadcast_env_change()
    return True


# ─── ярлыки ──────────────────────────────────────────────────────────────────

def run_powershell(script: str, timeout: int = 30) -> subprocess.CompletedProcess:
    encoded = base64.b64encode(script.encode("utf-16-le")).decode()
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
        capture_output=True, timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW,
    )


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def create_shortcut(lnk: Path, target: str, args: str = "", workdir: str = "", icon: str = "",
                    hotkey: str = "", description: str = "", window_style: int = 1) -> None:
    lnk.parent.mkdir(parents=True, exist_ok=True)
    if lnk.exists():
        lnk.unlink()  # CreateShortcut дописывает поверх старого ярлыка и сохраняет его лишние поля
    script = f"""
$s = (New-Object -ComObject WScript.Shell).CreateShortcut({_ps_quote(str(lnk))})
$s.TargetPath = {_ps_quote(target)}
$s.Arguments = {_ps_quote(args)}
$s.WorkingDirectory = {_ps_quote(workdir)}
$s.Description = {_ps_quote(description)}
$s.WindowStyle = {window_style}
{f"$s.IconLocation = {_ps_quote(icon + ',0')}" if icon else ""}
{f"$s.Hotkey = {_ps_quote(hotkey)}" if hotkey else ""}
$s.Save()
"""
    result = run_powershell(script)
    if result.returncode != 0 or not lnk.exists():
        raise OSError(result.stderr.decode("utf-8", "replace").strip() or "не удалось создать ярлык")


def read_shortcut(lnk: Path) -> dict:
    script = f"""
$s = (New-Object -ComObject WScript.Shell).CreateShortcut({_ps_quote(str(lnk))})
[Console]::OutputEncoding = [Text.Encoding]::UTF8
@{{target=$s.TargetPath; args=$s.Arguments; cwd=$s.WorkingDirectory}} | ConvertTo-Json -Compress
"""
    result = run_powershell(script)
    try:
        return json.loads(result.stdout.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        return {}


def startup_dir() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def start_menu_dir() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def start_menu_shortcuts() -> list[Path]:
    """Все ярлыки меню Пуск (пользовательские и общие) — для поиска программ."""
    roots = [start_menu_dir(), Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"]
    skip = ("uninstall", "удал", "деинстал", "readme", "help", "справка", "website", "web site", "documentation", "license")
    found: dict[str, Path] = {}
    for root in roots:
        if root.exists():
            for lnk in root.rglob("*.lnk"):
                if not any(word in lnk.stem.lower() for word in skip):
                    found.setdefault(lnk.stem.lower(), lnk)
    return sorted(found.values(), key=lambda p: p.stem.lower())
