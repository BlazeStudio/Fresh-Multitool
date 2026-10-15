"""Встроенные команды Multitool."""

from __future__ import annotations

import difflib
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from prompt_toolkit import prompt
from prompt_toolkit.styles import Style
from rich import box
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import config as cfg
from . import tools, ui
from .app import App, Out
from .integration import autostart_enabled, set_autostart
from .launcher import is_url, kill_processes
from .themes import all_theme_names, get_theme, gradient_text
from .winsys import read_shortcut, shell_execute, start_menu_shortcuts


@dataclass
class Command:
    names: list[str]
    usage: str
    help: str
    group: str
    func: Callable[[App, list[str]], Out]
    split_args: bool = True


COMMANDS: list[Command] = []
GROUPS = ["Приложения", "Система", "Питание", "Меню", "Прочее"]


def command(*names: str, usage: str = "", help: str = "", group: str = "Прочее", split_args: bool = True):
    def register(func):
        COMMANDS.append(Command(list(names), usage or names[0], help, group, func, split_args))
        return func
    return register


def find_command(name: str) -> Command | None:
    name = name.lower()
    return next((c for c in COMMANDS if name in c.names), None)


# ─── диалоги ─────────────────────────────────────────────────────────────────

YES = {"y", "yes", "д", "да", "н", "l"}  # «н» и «l» — та же клавиша в другой раскладке


def _style(app: App) -> Style:
    return Style.from_dict({"q": f"{app.theme['accents'][1]} bold", "hint": app.theme["muted"]})


def ask_text(app: App, label: str, default: str = "") -> str:
    message = [("class:q", "  ? "), ("", f"{label}"), ("class:hint", f" [{default}]" if default else ""), ("", ": ")]
    answer = prompt(message, style=_style(app))
    return answer.strip() or default


def confirm(app: App, question: str, default: bool = False) -> bool:
    message = [("class:q", "  ? "), ("", question), ("class:hint", " (y/N) " if not default else " (Y/n) ")]
    answer = prompt(message, style=_style(app)).strip().lower()
    if not answer:
        return default
    return answer in YES


def _item_arg(app: App, args: list[str]) -> cfg.Item | None:
    if not args:
        return None
    word = " ".join(args)
    item = app.config.find(word)
    if item:
        return item
    matches = [i for i in app.config.items if word.lower() in i.name.lower()]
    return matches[0] if len(matches) == 1 else None


# ─── приложения ──────────────────────────────────────────────────────────────

@command("stop", "x", usage="stop N", help="закрыть программу пункта N (по полю process)", group="Приложения")
def stop(app: App, args: list[str]) -> Out:
    item = _item_arg(app, args)
    if not item:
        return Out(message="Укажи пункт меню: stop 2", kind="warn")
    if not item.process:
        return Out(message=f"У «{item.name}» не указано поле process в config.json", kind="warn")
    result = kill_processes(item.process)
    time.sleep(0.5)
    return Out(redraw=True, message=result.message, kind="ok" if result.ok else "warn")


@command("kill", usage="kill имя", help="завершить процесс по имени (chrome, winws.exe…)", group="Приложения")
def kill(app: App, args: list[str]) -> Out:
    if not args:
        return Out(message="Укажи процесс: kill chrome", kind="warn")
    names = [a if "." in a else f"{a}.exe" for a in args]
    result = kill_processes(names)
    return Out(message=result.message, kind="ok" if result.ok else "warn")


@command("find", "search", usage="find слово", help="найти программу в меню Пуск", group="Приложения", split_args=False)
def find(app: App, args: list[str]) -> Out:
    if not args:
        return Out(message="Что искать? find obs", kind="warn")
    matches = search_shortcuts(args[0])
    if not matches:
        return Out(message=f"В меню Пуск нет «{args[0]}»", kind="warn")
    ui.section_title(app.console, app.theme, f"🔎 НАЙДЕНО В МЕНЮ ПУСК: {len(matches)}", app.theme["accents"][2])
    for i, lnk in enumerate(matches[:15], 1):
        line = Text()
        line.append(f"  {i:>2}  ", style=app.theme["dim"])
        line.append(lnk.stem, style=f"bold {app.theme['text']}")
        app.console.print(line)
    app.console.print(Text("  добавить в меню: add название", style=app.theme["muted"]))
    return Out()


# ─── система ─────────────────────────────────────────────────────────────────

@command("sys", "info", "система", usage="sys", help="процессор, видеокарта, память, диски", group="Система")
def sys_cmd(app: App, args: list[str]) -> Out:
    tools.system_info(app.console, app.theme)
    return Out()


@command("top", "ps", usage="top", help="процессы, которые едят больше всего памяти", group="Система")
def top(app: App, args: list[str]) -> Out:
    tools.top_processes(app.console, app.theme)
    return Out()


@command("net", "ping", "сеть", usage="net", help="доступны ли Discord, YouTube и др. (проверка zapret)", group="Система")
def net(app: App, args: list[str]) -> Out:
    tools.net_check(app.console, app.theme, app.config.settings["net_targets"])
    return Out()


@command("ip", usage="ip", help="локальный и внешний IP-адрес", group="Система")
def ip(app: App, args: list[str]) -> Out:
    tools.ip_info(app.console, app.theme)
    return Out()


@command("dns", usage="dns", help="сбросить DNS-кэш (ipconfig /flushdns)", group="Система")
def dns(app: App, args: list[str]) -> Out:
    ok, message = tools.flush_dns()
    return Out(message=message, kind="ok" if ok else "err")


@command("clean", "temp", usage="clean", help="очистить временные файлы (%TEMP%)", group="Система")
def clean(app: App, args: list[str]) -> Out:
    with app.console.status("[bold]Считаю размер TEMP…", spinner="dots"):
        size, count = tools.temp_size()
    if not count:
        return Out(message="Папка TEMP уже пуста")
    if not confirm(app, f"Удалить {count} файлов ({size / 2**20:.0f} МБ) из TEMP? Занятые пропущу"):
        return Out(message="Отменено", kind="warn")
    with app.console.status("[bold]Чищу…", spinner="dots"):
        freed, removed = tools.clean_temp()
    return Out(message=f"Освобождено {freed / 2**20:.0f} МБ, удалено файлов: {removed}")


# ─── питание ─────────────────────────────────────────────────────────────────

@command("lock", usage="lock", help="заблокировать компьютер (Win+L)", group="Питание")
def lock(app: App, args: list[str]) -> Out:
    tools.lock()
    return Out(message="Компьютер заблокирован")


@command("sleep", usage="sleep", help="спящий режим", group="Питание")
def sleep_cmd(app: App, args: list[str]) -> Out:
    if confirm(app, "Перевести компьютер в сон?"):
        tools.sleep()
        return Out(message="Доброе утро!")
    return Out(message="Отменено", kind="warn")


@command("off", "shutdown", usage="off [мин|cancel]", help="выключить сейчас, через N минут или отменить", group="Питание")
def off(app: App, args: list[str]) -> Out:
    if args and args[0].lower() in ("cancel", "отмена", "c", "a"):
        ok = tools.cancel_shutdown()
        return Out(message="Выключение отменено" if ok else "Таймер выключения не был запущен", kind="ok" if ok else "warn")
    if args:
        try:
            minutes = float(args[0].replace(",", "."))
        except ValueError:
            return Out(message="Пример: off 30 — выключить через 30 минут", kind="warn")
        tools.shutdown(int(minutes * 60))
        at = time.strftime("%H:%M", time.localtime(time.time() + minutes * 60))
        return Out(message=f"Выключение в {at} (через {minutes:g} мин). Отменить: off cancel")
    if confirm(app, "Выключить компьютер прямо сейчас?"):
        tools.shutdown(0)
        return Out(message="Выключаюсь…")
    return Out(message="Отменено", kind="warn")


@command("reboot", "restart", usage="reboot", help="перезагрузить компьютер", group="Питание")
def reboot(app: App, args: list[str]) -> Out:
    if confirm(app, "Перезагрузить компьютер?"):
        tools.reboot()
        return Out(message="Перезагружаюсь…")
    return Out(message="Отменено", kind="warn")


# ─── настройка меню ──────────────────────────────────────────────────────────

ICON_HINTS = [
    (("discord",), "💬"), (("telegram",), "📨"), (("steam", "epic", "battle", "ubisoft", "ea", "riot", "faceit"), "🎮"),
    (("chrome", "browser", "edge", "firefox", "opera", "яндекс", "yandex"), "🌐"), (("obs",), "🎥"),
    (("code", "studio", "idea", "pycharm", "notepad"), "💻"), (("vpn", "amnezia", "zapret"), "🔓"),
    (("spotify", "music", "музык"), "🎵"), (("photoshop", "paint"), "🎨"), (("obsidian", "word", "excel"), "📝"),
    (("afterburner", "gpu", "furmark"), "🔥"), (("torrent",), "🧲"),
]


def guess_icon(name: str, path: str) -> str:
    low = (name + " " + path).lower()
    for words, icon in ICON_HINTS:
        if any(w in low for w in words):
            return icon
    if is_url(path):
        return "🌐"
    if Path(cfg.expand(path)).is_dir():
        return "📁"
    if path.lower().endswith((".bat", ".cmd", ".ps1")):
        return "📜"
    return "🚀"


def search_shortcuts(query: str) -> list[Path]:
    shortcuts = start_menu_shortcuts()
    low = query.lower()
    matches = [p for p in shortcuts if low in p.stem.lower()]
    if not matches:
        stems = {p.stem.lower(): p for p in shortcuts}
        matches = [stems[s] for s in difflib.get_close_matches(low, list(stems), n=8, cutoff=0.5)]
    def rank(p: Path):
        stem = p.stem.lower()
        words = re.split(r"\W+", stem)
        # Точное имя → целое слово («obs» → OBS Studio, а не Obsidian) → начало имени → короче.
        return stem != low, low not in words, not stem.startswith(low), len(stem)

    matches.sort(key=rank)
    return matches


def _from_shortcut(lnk: Path) -> dict:
    info = read_shortcut(lnk)
    target = info.get("target") or ""
    if target and Path(target).exists():
        entry = {"name": lnk.stem, "path": target}
        if info.get("args"):
            entry["args"] = info["args"]
        cwd = info.get("cwd") or ""
        if cwd and Path(cwd) != Path(target).parent:
            entry["cwd"] = cwd
        return entry
    return {"name": lnk.stem, "path": str(lnk)}


def find_target(query: str) -> dict | list[Path] | None:
    """Путь/ссылка → готовый пункт; название программы → список ярлыков из меню Пуск."""
    if is_url(query):
        host = query.split("://", 1)[-1].split("/", 1)[0]
        return {"name": host.removeprefix("www."), "path": query}
    path = Path(cfg.expand(query))
    if path.exists():
        if path.suffix.lower() == ".lnk":
            return _from_shortcut(path)
        return {"name": path.stem if path.is_file() else path.name, "path": query}
    return search_shortcuts(query)[:9] or None


def pick_shortcut(app: App, matches: list[Path]) -> Path | None:
    if len(matches) > 1:
        for i, lnk in enumerate(matches, 1):
            line = Text()
            line.append(f"    {i}  ", style=f"bold {app.theme['accents'][1]}")
            line.append(lnk.stem, style=app.theme["text"])
            app.console.print(line)
        choice = ask_text(app, "Какую выбрать", "1")
        if not choice.isdigit() or not 1 <= int(choice) <= len(matches):
            return None
        lnk = matches[int(choice) - 1]
    else:
        lnk = matches[0]
    app.console.print(Text(f"    → {lnk.stem}", style=app.theme["muted"]))
    return lnk


def choose_section(app: App) -> int:
    sections = app.config.raw.setdefault("sections", [])
    for i, sec in enumerate(sections, 1):
        line = Text()
        line.append(f"    {i}  ", style=f"bold {app.theme['accents'][1]}")
        line.append(f"{sec.get('icon', '◆')} {sec.get('name', '')}", style=app.theme["text"])
        app.console.print(line)
    app.console.print(Text("    n  новая секция", style=app.theme["muted"]))
    default = str(len(sections)) if sections else "n"
    choice = ask_text(app, "В какую секцию", default).lower()
    if choice.isdigit() and 1 <= int(choice) <= len(sections):
        return int(choice) - 1
    name = ask_text(app, "Название новой секции", "Разное")
    icon = ask_text(app, "Иконка секции", "📦")
    sections.append({"name": name, "icon": icon, "items": []})
    return len(sections) - 1


@command("add", "добавить", usage="add [путь|название]", help="добавить программу, сайт или папку в меню",
         group="Меню", split_args=False)
def add(app: App, args: list[str]) -> Out:
    ui.section_title(app.console, app.theme, "➕ НОВЫЙ ПУНКТ МЕНЮ", app.theme["accents"][2])
    app.console.print(Text("  Можно перетащить файл в окно. Ctrl+C — отмена.", style=app.theme["muted"]))
    query = (args[0] if args else "").strip().strip('"')
    if not query:
        query = ask_text(app, "Путь к exe/bat/папке, ссылка или название из меню Пуск").strip('"')
    if not query:
        return Out(message="Отменено", kind="warn")
    with app.console.status("[bold]Ищу…", spinner="dots"):
        entry = find_target(query)
    if isinstance(entry, list):
        lnk = pick_shortcut(app, entry)
        if not lnk:
            return Out(message="Отменено", kind="warn")
        with app.console.status("[bold]Читаю ярлык…", spinner="dots"):
            entry = _from_shortcut(lnk)
    if not entry:
        return Out(message=f"Не нашёл «{query}» — укажи полный путь", kind="err")

    name = ask_text(app, "Название", entry["name"])
    icon = ask_text(app, "Иконка-эмодзи", guess_icon(name, entry["path"]))
    description = ask_text(app, "Описание (Enter — пропустить)")
    admin = confirm(app, "Запускать от имени администратора?")
    section = choose_section(app)

    item = {"name": name, "icon": icon}
    if description:
        item["description"] = description
    item["path"] = entry["path"]
    for key in ("args", "cwd"):
        if entry.get(key):
            item[key] = entry[key]
    if admin:
        item["admin"] = True
    exe = Path(cfg.expand(entry["path"])).name
    if exe.lower().endswith(".exe"):
        item["process"] = exe

    app.config.raw["sections"][section].setdefault("items", []).append(item)
    cfg.save(app.config.raw)
    app.load_config()
    key = app.config.sections[section].items[-1].key
    return Out(redraw=True, message=f"Добавлено: {icon} {name} — запуск клавишей {key}")


@command("del", "remove", "rm", usage="del N", help="удалить пункт N из меню", group="Меню")
def delete(app: App, args: list[str]) -> Out:
    item = _item_arg(app, args)
    if not item:
        return Out(message="Укажи пункт: del 5", kind="warn")
    if not confirm(app, f"Убрать «{item.name}» из меню? (программа не удаляется)"):
        return Out(message="Отменено", kind="warn")
    app.config.raw["sections"][item.section]["items"].remove(item.raw)
    cfg.save(app.config.raw)
    app.load_config()
    return Out(redraw=True, message=f"Убрано из меню: {item.name}  (копия старого конфига — config.json.bak)")


def open_config(app: App) -> str:
    editor = app.config.settings.get("editor", "auto") if app.config else "auto"
    path = f'"{cfg.CONFIG_PATH}"'
    if editor in ("auto", "code"):
        code = shutil.which("code")
        if code:
            exe = Path(code).parent.parent / "Code.exe"
            if exe.exists():
                shell_execute(str(exe), path)
            else:
                shell_execute(code, path, window="hidden")
            return "VS Code"
    if editor not in ("auto", "code", "notepad"):
        shell_execute(cfg.expand(editor), path)
        return Path(editor).stem
    shell_execute("notepad.exe", path)
    return "Блокнот"


@command("edit", "config", usage="edit", help="открыть config.json (изменения подхватятся сразу)", group="Меню")
def edit(app: App, args: list[str]) -> Out:
    editor = open_config(app)
    return Out(message=f"Открываю конфиг в {editor}. Сохрани файл — меню обновится само")


@command("reload", "r", usage="reload", help="перечитать config.json", group="Меню")
def reload(app: App, args: list[str]) -> Out:
    if app.load_config() or app.wait_for_valid_config():
        return Out(redraw=True, message="Конфиг перечитан")
    return Out(exit=True)


@command("theme", "тема", usage="theme [имя]", help="сменить цветовую тему", group="Меню")
def theme(app: App, args: list[str]) -> Out:
    names = all_theme_names(app.config.themes)
    if not args:
        ui.section_title(app.console, app.theme, "🎨 ТЕМЫ", app.theme["accents"][0])
        for name in names:
            t = get_theme(name, app.config.themes)
            line = Text("  ")
            line.append(gradient_text("█" * 28, t["gradient"]))
            line.append(f"  {name}", style=f"bold {t['accents'][0]}")
            if name == app.theme["name"]:
                line.append("  ← сейчас", style=app.theme["muted"])
            app.console.print(line)
        app.console.print(Text("  сменить: theme имя", style=app.theme["muted"]))
        return Out()
    name = args[0].lower()
    if name not in names:
        close = difflib.get_close_matches(name, names, n=1)
        return Out(message=f"Нет темы «{name}».{' Может, ' + close[0] + '?' if close else ''}", kind="err")
    cfg.set_setting(app.config.raw, "theme", name)
    app.load_config()
    return Out(redraw=True, message=f"Тема: {name}")


@command("autostart", "автозапуск", usage="autostart [on|off]", help="запуск вместе с Windows и горячая клавиша", group="Меню")
def autostart(app: App, args: list[str]) -> Out:
    if not args:
        state = "включён" if autostart_enabled() else "выключен"
        return Out(message=f"Автозапуск {state}. Изменить: autostart on / autostart off", kind="info")
    enable = args[0].lower() in ("on", "1", "yes", "вкл", "да")
    set_autostart(enable)
    return Out(message="Автозапуск включён" if enable else "Автозапуск выключен")


# ─── прочее ──────────────────────────────────────────────────────────────────

@command("folder", "dir", usage="folder", help="открыть папку Multitool", group="Прочее")
def folder(app: App, args: list[str]) -> Out:
    shell_execute(str(cfg.ROOT))
    return Out(message=f"Открываю {cfg.ROOT}")


@command("cls", "clear", usage="cls", help="перерисовать экран", group="Прочее")
def cls(app: App, args: list[str]) -> Out:
    return Out(redraw=True)


@command("help", "?", "помощь", "h", usage="help", help="эта справка", group="Прочее")
def help_cmd(app: App, args: list[str]) -> Out:
    t = app.theme
    ui.section_title(app.console, t, "📖 СПРАВКА", t["accents"][0])
    table = Table(box=None, show_header=False, padding=(0, 2), pad_edge=False)
    table.add_column(no_wrap=True)
    table.add_column(no_wrap=True)
    table.add_column(style=t["text"])
    table.add_column(style=t["dim"])
    for g_index, group in enumerate(GROUPS):
        accent = t["accents"][g_index % len(t["accents"])]
        first = True
        for c in (c for c in COMMANDS if c.group == group):
            table.add_row(Text(group if first else "", style=t["muted"]), Text(c.usage, style=f"bold {accent}"),
                          c.help, ", ".join(c.names[1:]))
            first = False
        table.add_row("", "", "", "")
    table.add_row(Text("Ввод", style=t["muted"]), Text("!команда", style=f"bold {t['accents'][1]}"),
                  "выполнить команду cmd прямо здесь (!ipconfig)", "")
    app.console.print(table)
    tips = Text()
    tips.append("Номер", style="bold")
    tips.append(" пункта — запуск, несколько через пробел — запуск по очереди (1 3).\n", style=t["text"])
    tips.append("Название", style="bold")
    tips.append(" или алиас тоже работают: ", style=t["text"])
    tips.append("cs", style=f"bold {t['accents'][1]}")
    tips.append(", ", style=t["text"])
    tips.append("hh", style=f"bold {t['accents'][1]}")
    tips.append(", ", style=t["text"])
    tips.append("telegram", style=f"bold {t['accents'][1]}")
    tips.append(".\nВсё меню настраивается в ", style=t["text"])
    tips.append(str(cfg.CONFIG_PATH), style=f"bold {t['accents'][3]}")
    tips.append(" — описание полей в README.md.", style=t["text"])
    app.console.print(Panel(tips, border_style=t["dim"], box=box.ROUNDED, padding=(0, 2)))
    return Out()


@command("q", "exit", "quit", "й", "выход", usage="q", help="выйти", group="Прочее")
def quit_cmd(app: App, args: list[str]) -> Out:
    return Out(exit=True)


def shell(app: App, args: list[str]) -> Out:
    line = args[0].strip() if args else ""
    if not line:
        return Out(message="Пример: !ipconfig", kind="warn")
    ui.section_title(app.console, app.theme, f"❯ {line}", app.theme["accents"][3])
    code = subprocess.call(line, shell=True)
    return Out(message=f"Код выхода: {code}", kind="ok" if code == 0 else "warn")
