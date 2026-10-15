"""Главный цикл Multitool: отрисовка, ввод с подсказками, разбор команд."""

from __future__ import annotations

import difflib
import threading
import time
from dataclasses import dataclass

import psutil
from prompt_toolkit import PromptSession
from prompt_toolkit.application import get_app
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import FileHistory
from prompt_toolkit.styles import Style
from rich.console import Console

from . import config as cfg
from . import ui
from .launcher import is_running, launch, running_names
from .themes import get_theme
from .winsys import center_window, claim_instance, find_windows, set_console_title

REFRESH = "\x00refresh"
RELOAD = "\x00reload"


@dataclass
class Out:
    """Что сделать после команды: перерисовать экран, показать уведомление, выйти."""
    redraw: bool = False
    message: str = ""
    kind: str = "ok"
    exit: bool = False


class MultitoolCompleter(Completer):
    def __init__(self, app: "App"):
        self.app = app

    def get_completions(self, document, complete_event):
        from .commands import COMMANDS

        text = document.text_before_cursor
        word = document.get_word_before_cursor(WORD=True)
        low = word.lower()
        parts = text.split()
        if text.strip().replace(" ", "").isdigit() and not complete_event.completion_requested:
            return
        if len(parts) == 0 or (len(parts) == 1 and not text.endswith(" ")):
            for item in self.app.config.items:
                for alias in [item.key, *item.aliases]:
                    if alias.lower().startswith(low):
                        yield Completion(alias, -len(word), display_meta=f"{item.icon} {item.name}")
            for cmd in COMMANDS:
                for name in cmd.names[:1]:
                    if name.startswith(low):
                        yield Completion(name, -len(word), display_meta=cmd.help)
            return
        first = parts[0].lower()
        options: list[tuple[str, str]] = []
        if first in ("stop", "del", "x"):
            options = [(i.key, f"{i.icon} {i.name}") for i in self.app.config.items]
        elif first == "theme":
            from .themes import all_theme_names
            options = [(n, "тема") for n in all_theme_names(self.app.config.themes)]
        elif first == "autostart":
            options = [("on", "включить"), ("off", "выключить")]
        elif first == "off":
            options = [("30", "через 30 минут"), ("60", "через час"), ("cancel", "отменить таймер")]
        elif first == "kill":
            options = [(n, "процесс") for n in sorted(self.app.names)]
        for value, meta in options:
            if value.lower().startswith(low):
                yield Completion(value, -len(word), display_meta=meta)


class App:
    def __init__(self, autostart: bool = False, center: bool = False):
        self.console = Console(highlight=False)
        self.autostart = autostart
        self.center = center
        self.config: cfg.Config | None = None
        self.theme = get_theme("fresh")
        self.names: set[str] = set()
        self.on_dashboard = False
        self.hotkey_text = ""
        self.exit_scheduled = False
        self.last_redraw = 0.0
        self.session: PromptSession | None = None

    # ─── конфиг ──────────────────────────────────────────────────────────

    def load_config(self) -> bool:
        try:
            self.config = cfg.load()
            self.theme = get_theme(self.config.settings["theme"], self.config.themes)
            return True
        except cfg.ConfigError as exc:
            self.console.clear()
            ui.error_panel(self.console, self.theme, "config.json", str(exc))
            return False

    def wait_for_valid_config(self) -> bool:
        from .commands import open_config

        while not self.load_config():
            self.console.print("  [bold]edit[/] — открыть конфиг   ·   [bold]Enter[/] — перечитать   ·   [bold]q[/] — выход")
            try:
                answer = input("  ❯ ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                return False
            if answer in ("q", "й", "exit"):
                return False
            if answer == "edit":
                open_config(self)
        return True

    # ─── экран ───────────────────────────────────────────────────────────

    def redraw(self, message: str = "", kind: str = "ok", first: bool = False) -> None:
        self.names = running_names()
        with self.console.capture() as capture:
            ui.dashboard(self.console, self.config, self.theme, self.names, first_render=first)
            if message:
                ui.toast(self.console, self.theme, message, kind)
        content = capture.get()
        lines = content.count("\n")
        out = self.console.file
        # Без мерцания: если влезает в окно — перерисовываем поверх, иначе полная очистка.
        # Строку стираем до вывода (\x1b[2K), а не после: иначе у строк во всю ширину
        # пропадает последний символ — правая рамка панели.
        if lines < self.console.height - 1:
            out.write("\x1b[H" + "\n".join("\x1b[2K" + line for line in content.split("\n")) + "\x1b[J")
        else:
            out.write("\x1b[2J\x1b[3J\x1b[H" + content)
        out.flush()
        self.on_dashboard = True
        self.last_redraw = time.monotonic()

    def say(self, message: str, kind: str = "ok") -> None:
        ui.toast(self.console, self.theme, message, kind)

    # ─── ввод ────────────────────────────────────────────────────────────

    def _style(self) -> Style:
        t = self.theme
        accent = t["accents"][0]
        return Style.from_dict({
            "pl.cap": accent,
            "pl.seg": f"bg:{accent} {t['on_accent']} bold",
            "pl.arrow": accent,
            "arrow": f"{t['accents'][1]} bold",
            "bottom-toolbar": f"noreverse bg:#16161F {t['muted']}",
            "bottom-toolbar.key": f"bg:#16161F {t['text']} bold",
            "bottom-toolbar.ok": f"bg:#16161F {t['ok']} bold",
            "completion-menu": "bg:#1B1B29 #E6E6F0",
            "completion-menu.completion.current": f"bg:{accent} {t['on_accent']} bold",
            "completion-menu.meta.completion": "bg:#14141E #8A8AA0",
            "completion-menu.meta.completion.current": f"bg:{t['accents'][3]} {t['on_accent']}",
            "auto-suggestion": t["dim"],
        })

    def _message(self):
        return [
            ("class:pl.cap", ui.PL_ROUND_LEFT),
            ("class:pl.seg", " ⚡ multitool "),
            ("class:pl.arrow", ui.PL_RIGHT),
            ("class:arrow", " ❯ "),
        ]

    def _toolbar(self):
        self._watch()
        running = sum(1 for item in self.config.items if item.process and is_running(item, self.names))
        parts = [
            ("class:bottom-toolbar", f"  {time.strftime('%H:%M')}  │  "),
            ("class:bottom-toolbar.ok", f"● {running} "),
            ("class:bottom-toolbar", "запущено  │  "),
            ("class:bottom-toolbar.key", "Tab"), ("class:bottom-toolbar", " подсказки  "),
            ("class:bottom-toolbar.key", "↑↓"), ("class:bottom-toolbar", " история  "),
            ("class:bottom-toolbar.key", "help"), ("class:bottom-toolbar", " справка  "),
            ("class:bottom-toolbar.key", "q"), ("class:bottom-toolbar", " выход "),
        ]
        if self.hotkey_text:
            parts += [("class:bottom-toolbar", " │  "), ("class:bottom-toolbar.key", self.hotkey_text),
                      ("class:bottom-toolbar", " свернуть/открыть ")]
        return parts

    def _watch(self) -> None:
        """Вызывается при каждой перерисовке строки ввода: следит за конфигом и автообновлением."""
        app = get_app()
        if self.exit_scheduled:
            return
        result = None
        if cfg.changed_on_disk(self.config):
            result = RELOAD
        else:
            interval = float(self.config.settings.get("auto_refresh") or 0)
            if interval and self.on_dashboard and not app.current_buffer.text \
                    and time.monotonic() - self.last_redraw > interval:
                result = REFRESH
        if result:
            self.exit_scheduled = True
            app.loop.call_soon(lambda: app.exit(result=result) if app.is_running else None)

    def read_line(self) -> str:
        self.exit_scheduled = False
        return self.session.prompt(self._message, style=self._style(), bottom_toolbar=self._toolbar,
                                   refresh_interval=2)

    # ─── запуск ──────────────────────────────────────────────────────────

    def run_item(self, item: cfg.Item) -> Out:
        if item.admin:
            self.say(f"{item.name}: запрос прав администратора…", "warn")

        def on_wait(seconds: float) -> None:
            with self.console.status(f"[bold]{item.name}[/]: жду {seconds:g} с…", spinner="dots"):
                time.sleep(seconds)

        if item.kind == "command" and not item.admin and not item.raw.get("window"):
            ui.section_title(self.console, self.theme, f"{item.icon} {item.name}", self.theme["accents"][3])
            result = launch(item, self.names, on_wait)
            self.say(result.message, "ok" if result.ok else "err")
            self.on_dashboard = False
            return Out()

        with self.console.status(f"[bold]Запускаю {item.name}…", spinner="dots"):
            result = launch(item, self.names, on_wait)
            time.sleep(0.6)
        if result.ok and self.config.settings["close_after_launch"]:
            return Out(exit=True)
        return Out(redraw=True, message=result.message, kind="ok" if result.ok else "err")

    def dispatch(self, line: str) -> Out:
        from .commands import COMMANDS, find_command

        line = line.strip()
        if not line:
            return Out(redraw=True)
        if line.startswith("!"):
            from .commands import shell
            return shell(self, [line[1:]])

        words = line.replace(",", " ").split()
        items = [self.config.find(w) for w in words]
        if all(items):
            outs = [self.run_item(item) for item in items]
            messages = [o.message for o in outs if o.message]
            failed = any(o.kind == "err" for o in outs)
            if any(o.exit for o in outs):
                return Out(exit=True)
            if not any(o.redraw for o in outs):
                return Out()
            return Out(redraw=True, message="  ·  ".join(messages), kind="err" if failed else "ok")

        name, args = words[0].lower(), words[1:]
        command = find_command(name)
        if command:
            raw_args = line.split(None, 1)[1] if len(line.split(None, 1)) > 1 else ""
            return command.func(self, args if command.split_args else ([raw_args] if raw_args else []))

        # Поиск по названию пункта: «discord», «телеграм» и т.п.
        query = line.lower()
        matches = [i for i in self.config.items if query in i.name.lower()]
        exact = [i for i in matches if i.name.lower() == query]
        if len(exact) == 1 or len(matches) == 1:
            return self.run_item((exact or matches)[0])
        if matches:
            options = ", ".join(f"{i.key} — {i.name}" for i in matches)
            return Out(message=f"Уточни: {options}", kind="warn")

        known = [n for c in COMMANDS for n in c.names] + [a for i in self.config.items for a in (i.key, *i.aliases)]
        close = difflib.get_close_matches(name, known, n=1, cutoff=0.6)
        hint = f" Может, «{close[0]}»?" if close else " Список команд: help"
        return Out(message=f"Не знаю «{words[0]}».{hint}", kind="err")

    # ─── цикл ────────────────────────────────────────────────────────────

    def _center_later(self) -> None:
        def work():
            title = self.config.settings["window_title"]
            for _ in range(20):
                windows = find_windows(title)
                if windows:
                    center_window(windows[0])
                    return
                time.sleep(0.1)
        threading.Thread(target=work, daemon=True).start()

    def check_hotkey(self) -> None:
        """Поднимает фоновый процесс горячей клавиши, если он должен работать, и сообщает о проблемах."""
        from .integration import autostart_enabled, daemon_running, hotkey_status, start_daemon

        if autostart_enabled() and not daemon_running():
            start_daemon()
            time.sleep(0.8)
        status = hotkey_status()
        if status and status.get("ok"):
            self.hotkey_text = status.get("hotkey", "")
        elif status:
            self.say(f"Горячая клавиша {status.get('hotkey')} занята другой программой — "
                     "поменяй settings.hotkey в config.json", "warn")

    def run_autostart_items(self) -> None:
        items = [i for i in self.config.items if i.autostart]
        for item in items:
            if item.process and is_running(item, self.names):
                continue
            result = launch(item, self.names)
            self.say(result.message, "ok" if result.ok else "err")

    def run(self) -> None:
        claim_instance()
        if not self.wait_for_valid_config():
            return
        set_console_title(self.config.settings["window_title"])
        if self.center and self.config.settings["window"].get("center", True):
            self._center_later()
        psutil.cpu_percent(interval=None)
        self.session = PromptSession(
            history=FileHistory(str(cfg.HISTORY_PATH)),
            auto_suggest=AutoSuggestFromHistory(),
            completer=MultitoolCompleter(self),
            complete_while_typing=True,
            reserve_space_for_menu=6,
        )
        self.console.file.write("\x1b[2J\x1b[3J\x1b[H")
        self.redraw(first=True)
        self.check_hotkey()
        if self.autostart:
            self.run_autostart_items()

        while True:
            try:
                line = self.read_line()
            except KeyboardInterrupt:
                continue
            except EOFError:
                break

            if line == RELOAD:
                if self.load_config() or self.wait_for_valid_config():
                    self.redraw("Конфиг обновлён", "ok")
                    continue
                break
            if line == REFRESH:
                self.redraw()
                continue

            try:
                out = self.dispatch(line)
            except KeyboardInterrupt:
                out = Out(message="Отменено", kind="warn")
            except Exception as exc:  # команда не должна ронять всё приложение
                out = Out(message=f"Ошибка: {exc}", kind="err")
            if out.exit:
                break
            if out.redraw:
                self.redraw(out.message, out.kind)
            else:
                if out.message:
                    self.say(out.message, out.kind)
                self.on_dashboard = False


def run(autostart: bool = False, center: bool = False) -> None:
    App(autostart=autostart, center=center).run()
