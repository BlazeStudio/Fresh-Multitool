"""Запуск пунктов меню и управление их процессами."""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import psutil

from .config import Item, expand
from .winsys import shell_execute


@dataclass
class Result:
    ok: bool
    message: str


def running_names() -> set[str]:
    names: set[str] = set()
    for proc in psutil.process_iter(["name"]):
        name = proc.info.get("name")
        if name:
            names.add(name.lower())
    return names


def is_running(item: Item, names: set[str]) -> bool:
    return any(p.lower() in names for p in item.process)


def is_url(target: str) -> bool:
    return "://" in target or target.lower().startswith(("mailto:", "steam:", "ms-settings:"))


def target_exists(target: str) -> bool:
    """Существует ли файл/папка. Для URL и команд из PATH (taskmgr.exe) — считаем, что да."""
    if not target or is_url(target):
        return True
    path = Path(expand(target))
    if path.exists():
        return True
    if not path.is_absolute() and len(path.parts) == 1:
        from shutil import which
        return which(str(path)) is not None or (Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / path).exists()
    return False


def item_missing(item: Item) -> bool:
    if item.kind == "path":
        return not target_exists(item.path)
    if item.kind == "steps":
        return any(step.get("path") and not target_exists(step["path"]) for step in item.steps)
    return False


def _as_names(value) -> list[str]:
    if not value:
        return []
    return [value] if isinstance(value, str) else list(value)


def _args_to_str(args) -> str:
    if isinstance(args, list):
        return subprocess.list2cmdline([expand(str(a)) for a in args])
    return expand(str(args or ""))


def launch_target(target: str, args="", cwd: str = "", admin: bool = False, window: str = "normal") -> None:
    target = expand(target)
    params = _args_to_str(args)
    workdir = expand(cwd) if cwd else ""
    path = Path(target)
    if not workdir and not is_url(target) and path.is_absolute() and path.exists():
        workdir = str(path if path.is_dir() else path.parent)

    # Повышенные bat-файлы Windows запускает в System32 — переходим в нужную папку сами.
    if admin and path.suffix.lower() in (".bat", ".cmd"):
        inner = f'cd /d "{workdir}" && "{target}"' + (f" {params}" if params else "")
        shell_execute("cmd.exe", f"/c {inner}", workdir or None, admin=True, window=window)
        return
    shell_execute(target, params, workdir or None, admin=admin, window=window)


def run_command(command: str, admin: bool = False, window: str = "") -> int | None:
    """Команда оболочки. Без admin/window — прямо в окне Multitool (вывод виден здесь)."""
    command = expand(command)
    if admin or window:
        shell_execute("cmd.exe", f"/c {command} & echo. & pause", None, admin=admin, window=window or "normal")
        return None
    return subprocess.call(command, shell=True)


def launch(item: Item, names: set[str] | None = None, on_wait=None) -> Result:
    try:
        if item.kind == "command":
            code = run_command(item.command, item.admin, item.raw.get("window", ""))
            if code not in (None, 0):
                return Result(False, f"{item.name}: команда завершилась с кодом {code}")
            return Result(True, f"Выполнено: {item.name}")

        if item.kind == "steps":
            names = names if names is not None else running_names()
            done = 0
            skipped_previous = False
            for step in item.steps:
                unless = [p.lower() for p in _as_names(step.get("unless_running"))]
                if unless and any(p in names for p in unless):
                    skipped_previous = True
                    continue
                if "wait" in step:
                    # Пауза нужна только если предыдущий шаг действительно что-то запустил.
                    if not skipped_previous and done:
                        (on_wait or time.sleep)(float(step["wait"]))
                    continue
                skipped_previous = False
                if step.get("command"):
                    run_command(step["command"], bool(step.get("admin")), step.get("window", ""))
                else:
                    launch_target(step["path"], step.get("args", ""), step.get("cwd", ""),
                                  bool(step.get("admin")), step.get("window", "normal"))
                done += 1
            return Result(True, f"Запущено: {item.name}" + ("" if done else " (всё уже работало)"))

        launch_target(item.path, item.args, item.cwd, item.admin, item.window)
        return Result(True, f"Запущено: {item.name}")
    except OSError as exc:
        return Result(False, f"{item.name}: {exc}")


def kill_processes(names: list[str]) -> Result:
    """Завершает процессы по именам; для повышенных — через taskkill с UAC."""
    wanted = {n.lower() for n in names}
    killed, denied = 0, set()
    for proc in psutil.process_iter(["name"]):
        name = (proc.info.get("name") or "").lower()
        if name in wanted:
            try:
                proc.kill()
                killed += 1
            except psutil.AccessDenied:
                denied.add(proc.info["name"])
            except psutil.NoSuchProcess:
                pass
    if denied:
        cmd = " & ".join(f'taskkill /F /IM "{n}"' for n in sorted(denied))
        try:
            shell_execute("cmd.exe", f"/c {cmd}", None, admin=True, window="hidden")
            return Result(True, f"Завершаю с правами администратора: {', '.join(sorted(denied))}")
        except OSError as exc:
            return Result(False, f"Не удалось завершить {', '.join(sorted(denied))}: {exc}")
    if killed:
        return Result(True, f"Завершено процессов: {killed}")
    return Result(False, "Такие процессы не запущены: " + ", ".join(names))
