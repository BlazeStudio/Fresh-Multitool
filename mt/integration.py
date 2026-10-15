"""Интеграция с системой: открытие окна, автозапуск, установка и удаление."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from . import config as cfg
from .themes import get_theme
from .winsys import (
    APP_NAME, DAEMON_MUTEX, PROFILE_GUID, SCHEME_NAME, add_to_user_path, claim_mutex, create_shortcut,
    find_windows, focus_window, fragment_path, hotkey_loop, instance_running, mutex_exists, parse_hotkey,
    python_exe, remove_from_user_path, start_menu_dir, startup_dir, user32, wt_exe,
)

SCRIPT = cfg.ROOT / "multitool.py"
BIN_DIR = cfg.ROOT / "bin"
ICON = cfg.ROOT / "assets" / "multitool.ico"
HOTKEY_STATUS = cfg.ROOT / ".hotkey_status"


def _settings() -> dict:
    try:
        return cfg.load().settings
    except cfg.ConfigError:
        return cfg._merge_settings({})


# ─── открытие окна ───────────────────────────────────────────────────────────

def open_window(autostart: bool = False) -> None:
    """Открывает Multitool в отдельном окне Windows Terminal (или фокусирует уже открытое)."""
    s = _settings()
    title = s["window_title"]
    if s["single_instance"] and instance_running():
        windows = find_windows(title)
        if windows:
            focus_window(windows[0])
            return
        wt = wt_exe()
        if wt:
            subprocess.Popen([wt, "-w", "multitool", "focus-tab", "-t", "0"])
        return

    if autostart and s.get("autostart_delay"):
        time.sleep(float(s["autostart_delay"]))

    app_args = ["-X", "utf8", str(SCRIPT), "--here", "--center"] + (["--autostart"] if autostart else [])
    py = python_exe(windowed=False)
    wt = wt_exe()
    # Multitool — цветной интерфейс; унаследованный NO_COLOR сделал бы его серым.
    env = {k: v for k, v in os.environ.items() if k != "NO_COLOR"}
    if wt and s.get("use_windows_terminal", True):
        win = s.get("window", {})
        theme = get_theme(s["theme"])
        cmd = [wt, "-w", "multitool", "--size", f"{win.get('columns', 124)},{win.get('rows', 42)}", "new-tab"]
        if fragment_path().exists():
            cmd += ["-p", APP_NAME]
        cmd += ["--title", title, "--suppressApplicationTitle", "--tabColor", theme["accents"][0],
                "-d", str(cfg.ROOT), py, *app_args]
        subprocess.Popen(cmd, env=env)
    else:
        subprocess.Popen([py, *app_args], cwd=str(cfg.ROOT), env=env, creationflags=subprocess.CREATE_NEW_CONSOLE)


# ─── фоновый процесс горячей клавиши ─────────────────────────────────────────

def toggle_window() -> None:
    """Горячая клавиша: окно активно — свернуть, иначе открыть или вывести на передний план."""
    title = _settings()["window_title"]
    windows = find_windows(title)
    if windows and user32.GetForegroundWindow() == windows[0]:
        user32.ShowWindow(windows[0], 6)  # SW_MINIMIZE
    else:
        open_window()


def run_daemon(autostart: bool = False) -> None:
    """Живёт в фоне (pythonw), держит глобальную горячую клавишу. С --autostart ещё и открывает окно."""
    first = claim_mutex(DAEMON_MUTEX)
    if autostart and _settings().get("open_on_startup", True):
        open_window(autostart=True)
    if not first:
        return  # горячую клавишу уже держит другой экземпляр

    cache = {"mtime": None, "hotkey": None, "text": ""}

    def current_hotkey():
        try:
            mtime = cfg.CONFIG_PATH.stat().st_mtime
        except OSError:
            return cache["hotkey"]
        if mtime != cache["mtime"]:
            cache["mtime"] = mtime
            cache["text"] = _settings().get("hotkey", "")
            cache["hotkey"] = parse_hotkey(cache["text"])
        return cache["hotkey"]

    def report(hotkey, ok):
        try:
            HOTKEY_STATUS.write_text(json.dumps({"hotkey": cache["text"], "ok": ok}, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    hotkey_loop(current_hotkey, toggle_window, report)


def daemon_running() -> bool:
    return mutex_exists(DAEMON_MUTEX)


def hotkey_status() -> dict | None:
    """Что сообщил фоновый процесс: {"hotkey": "Alt+`", "ok": true}. None — процесс не запущен."""
    if not daemon_running():
        return None
    try:
        return json.loads(HOTKEY_STATUS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def start_daemon() -> None:
    if not daemon_running():
        subprocess.Popen([python_exe(windowed=True), str(SCRIPT), "--daemon"], cwd=str(cfg.ROOT),
                         creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)


def stop_daemon() -> None:
    import psutil
    for proc in psutil.process_iter(["cmdline"]):
        try:
            cmd = proc.info.get("cmdline") or []
        except psutil.Error:
            continue
        if "--daemon" in cmd and any(str(SCRIPT).lower() == c.lower() for c in cmd):
            try:
                proc.kill()
            except psutil.Error:
                pass


# ─── автозапуск и ярлыки ─────────────────────────────────────────────────────

def autostart_lnk() -> Path:
    return startup_dir() / f"{APP_NAME}.lnk"


def start_menu_lnk() -> Path:
    return start_menu_dir() / f"{APP_NAME}.lnk"


def autostart_enabled() -> bool:
    return autostart_lnk().exists()


def set_autostart(enabled: bool) -> None:
    lnk = autostart_lnk()
    if enabled:
        create_shortcut(lnk, python_exe(windowed=True), f'"{SCRIPT}" --daemon --autostart', str(cfg.ROOT),
                        str(ICON) if ICON.exists() else "", description="Fresh Multitool — автозапуск и горячая клавиша")
        start_daemon()
    elif lnk.exists():
        lnk.unlink()


def create_start_menu_shortcut() -> None:
    create_shortcut(start_menu_lnk(), python_exe(windowed=True), f'"{SCRIPT}" --open', str(cfg.ROOT),
                    str(ICON) if ICON.exists() else "", description="Fresh Multitool — быстрый запуск программ")


# ─── профиль Windows Terminal ────────────────────────────────────────────────

def write_terminal_fragment() -> Path:
    theme = get_theme("fresh")
    py = python_exe(windowed=False)
    fragment = {
        "profiles": [{
            "guid": PROFILE_GUID,
            "name": APP_NAME,
            "commandline": f'"{py}" -X utf8 "{SCRIPT}" --here',
            "startingDirectory": str(cfg.ROOT),
            "icon": str(ICON) if ICON.exists() else "⚡",
            "colorScheme": SCHEME_NAME,
            "tabTitle": "Multitool",
            "suppressApplicationTitle": True,
            "tabColor": theme["accents"][0],
            "font": {"face": "Cascadia Code", "size": 11, "builtinGlyphs": True},
            "padding": "14, 10, 14, 6",
            "opacity": 93,
            "useAcrylic": True,
            "cursorShape": "bar",
            "scrollbarState": "hidden",
            "closeOnExit": "always",
            "bellStyle": "none",
            "hidden": False,
        }],
        "schemes": [{
            "name": SCHEME_NAME,
            "background": "#0E0E16",
            "foreground": "#E6E6F0",
            "cursorColor": "#FF2E88",
            "selectionBackground": "#3A2F5A",
            "black": "#1B1B29", "brightBlack": "#4A4A60",
            "red": "#FF4D6D", "brightRed": "#FF7A90",
            "green": "#6CDB8B", "brightGreen": "#9BF0B0",
            "yellow": "#F9E64F", "brightYellow": "#FFF27A",
            "blue": "#3B82F6", "brightBlue": "#6EA8FF",
            "purple": "#A78BFA", "brightPurple": "#C4B5FD",
            "cyan": "#1BA39C", "brightCyan": "#5EEAD4",
            "white": "#C8C8D8", "brightWhite": "#FFFFFF",
        }],
    }
    path = fragment_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fragment, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# ─── установка / удаление ────────────────────────────────────────────────────

def install(autostart: bool = True) -> None:
    from rich.console import Console
    from rich.text import Text

    console = Console(highlight=False)
    theme = get_theme("fresh")
    s = _settings()

    def step(ok: bool, title: str, detail: str = "") -> None:
        line = Text()
        line.append("  ✓ " if ok else "  × ", style=f"bold {theme['ok'] if ok else theme['err']}")
        line.append(title, style=f"bold {theme['text']}")
        if detail:
            line.append(f"  {detail}", style=theme["muted"])
        console.print(line)

    console.print()
    console.print(Text("  FRESH MULTITOOL · установка", style=f"bold {theme['accents'][0]}"))
    console.print()

    try:
        added = add_to_user_path(str(BIN_DIR))
        step(True, "PATH", f"{BIN_DIR} {'добавлен' if added else 'уже был'} — команды fresh_multitool и mt")
    except OSError as exc:
        step(False, "PATH", str(exc))

    if wt_exe():
        try:
            step(True, "Профиль Windows Terminal", str(write_terminal_fragment()))
        except OSError as exc:
            step(False, "Профиль Windows Terminal", str(exc))
    else:
        step(False, "Windows Terminal не найден", "будет использоваться обычная консоль")

    try:
        create_start_menu_shortcut()
        step(True, "Ярлык в меню Пуск", f"«{APP_NAME}» — ищется через Win+S")
    except OSError as exc:
        step(False, "Ярлык в меню Пуск", str(exc))

    try:
        set_autostart(autostart)
        step(True, "Автозапуск", "включён" if autostart else "выключен")
    except OSError as exc:
        step(False, "Автозапуск", str(exc))

    hotkey = s.get("hotkey", "")
    if parse_hotkey(hotkey):
        start_daemon()
        status = None
        for _ in range(30):
            time.sleep(0.2)
            status = hotkey_status()
            if status and status.get("hotkey") == hotkey:
                break
        if status and status.get("ok"):
            step(True, "Горячая клавиша", f"{hotkey} — открыть / свернуть Multitool из любого места")
        else:
            step(False, "Горячая клавиша", f"{hotkey} занята другой программой — поменяй settings.hotkey в config.json")
    else:
        step(False, "Горячая клавиша", f"не понял «{hotkey}», пример: Alt+` или Ctrl+Alt+T")

    console.print()
    console.print(Text("  Готово! Открой новое окно cmd и введи fresh_multitool (или mt).", style=theme["ok"]))
    console.print()


def uninstall() -> None:
    from rich.console import Console

    console = Console(highlight=False)
    stop_daemon()
    remove_from_user_path(str(BIN_DIR))
    for lnk in (autostart_lnk(), start_menu_lnk()):
        if lnk.exists():
            lnk.unlink()
    frag = fragment_path()
    if frag.exists():
        frag.unlink()
        try:
            frag.parent.rmdir()
        except OSError:
            pass
    console.print("[bold]Multitool убран из PATH, автозапуска, меню Пуск и Windows Terminal, "
                  "горячая клавиша отключена.[/] Файлы проекта не тронуты.")
