"""Отрисовка: баннер, статус-строка в стиле powerline, меню, уведомления."""

from __future__ import annotations

import getpass
import time
from datetime import datetime
from functools import lru_cache

import psutil
from rich import box
from rich.align import Align
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .config import Config, Item, VERSION
from .launcher import is_running, item_missing
from .themes import gradient_lines, gradient_text

WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
MONTHS = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]

# Запасной баннер, если pyfiglet недоступен.
FALLBACK_BANNER = [
    "███╗   ███╗██╗   ██╗██╗  ████████╗██╗████████╗ ██████╗  ██████╗ ██╗     ",
    "████╗ ████║██║   ██║██║  ╚══██╔══╝██║╚══██╔══╝██╔═══██╗██╔═══██╗██║     ",
    "██╔████╔██║██║   ██║██║     ██║   ██║   ██║   ██║   ██║██║   ██║██║     ",
    "██║╚██╔╝██║██║   ██║██║     ██║   ██║   ██║   ██║   ██║██║   ██║██║     ",
    "██║ ╚═╝ ██║╚██████╔╝███████╗██║   ██║   ██║   ╚██████╔╝╚██████╔╝███████╗",
    "╚═╝     ╚═╝ ╚═════╝ ╚══════╝╚═╝   ╚═╝   ╚═╝    ╚═════╝  ╚═════╝ ╚══════╝",
]

PL_RIGHT = "\ue0b0"  #
PL_ROUND_LEFT = "\ue0b6"  #
PL_ROUND_RIGHT = "\ue0b4"  #


@lru_cache(maxsize=8)
def figlet(title: str, font: str) -> tuple[str, ...]:
    try:
        import pyfiglet
        art = pyfiglet.figlet_format(title, font=font, width=400)
        lines = [line.rstrip() for line in art.splitlines()]
        while lines and not lines[-1].strip():
            lines.pop()
        while lines and not lines[0].strip():
            lines.pop(0)
        return tuple(lines)
    except Exception:
        return tuple(FALLBACK_BANNER) if title.upper() == "MULTITOOL" else ()


def banner(console: Console, config: Config, theme: dict) -> None:
    s = config.settings
    lines = list(figlet(s["title"], s["banner_font"]))
    width = max((len(line) for line in lines), default=0)
    if lines and width <= console.width - 2:
        lines = [line.ljust(width) for line in lines]
        console.print(Align.center(gradient_lines(lines, theme["gradient"])))
    else:
        spaced = "  ".join(s["title"].upper())
        console.print(Align.center(gradient_text(spaced, theme["gradient"], bold=True)))
    sub = Text()
    sub.append("◆ ", style=theme["gradient"][0])
    sub.append(s["subtitle"], style=f"italic {theme['muted']}")
    sub.append(f"  ·  v{VERSION}  ", style=theme["dim"])
    sub.append("◆", style=theme["gradient"][-1])
    console.print(Align.center(sub))


def _fmt_uptime(seconds: float) -> str:
    minutes = int(seconds // 60)
    days, minutes = divmod(minutes, 60 * 24)
    hours, minutes = divmod(minutes, 60)
    if days:
        return f"{days}д {hours}ч"
    if hours:
        return f"{hours}ч {minutes:02d}м"
    return f"{minutes}м"


def status_segments(first_render: bool) -> list[str]:
    now = datetime.now()
    cpu = psutil.cpu_percent(interval=0.15 if first_render else None)
    mem = psutil.virtual_memory()
    segments = [
        f"👤 {getpass.getuser()}",
        f"🕐 {WEEKDAYS[now.weekday()]}, {now.day} {MONTHS[now.month - 1]} · {now:%H:%M}",
        f"⚡ CPU {cpu:.0f}%",
        f"🧠 RAM {mem.used / 2**30:.1f}/{mem.total / 2**30:.0f} ГБ",
    ]
    try:
        disk = psutil.disk_usage("C:\\")
        segments.append(f"💾 C: {disk.free / 2**30:.0f} ГБ своб.")
    except OSError:
        pass
    segments.append(f"⏳ {_fmt_uptime(time.time() - psutil.boot_time())}")
    return segments


def powerline(segments: list[str], theme: dict, powerline_glyphs: bool, max_width: int) -> Text:
    colors = theme["bar"]
    while segments:
        out = Text()
        for i, seg in enumerate(segments):
            bg = colors[i % len(colors)]
            if i == 0 and powerline_glyphs:
                out.append(PL_ROUND_LEFT, style=bg)
            out.append(f" {seg} ", style=f"bold {theme['on_accent']} on {bg}")
            if powerline_glyphs:
                if i + 1 < len(segments):
                    out.append(PL_RIGHT, style=f"{bg} on {colors[(i + 1) % len(colors)]}")
                else:
                    out.append(PL_ROUND_RIGHT, style=bg)
            elif i + 1 < len(segments):
                out.append(" ")
        if out.cell_len <= max_width:
            return out
        segments = segments[:-1]
    return Text()


def section_color(config: Config, theme: dict, index: int) -> str:
    color = config.sections[index].color
    return color or theme["accents"][index % len(theme["accents"])]


def _item_row(item: Item, accent: str, theme: dict, names: set[str], show_desc: bool, key_width: int) -> tuple:
    badge = Text(f" {item.key.rjust(key_width)} ", style=f"bold {theme['on_accent']} on {accent}")
    title = Text(no_wrap=True, overflow="ellipsis")
    title.append(f"{item.icon} ", style="")
    title.append(item.name, style=f"bold {theme['text']}")
    if show_desc and item.description:
        title.append(f"  {item.description}", style=theme["muted"])
    status = Text(no_wrap=True, justify="right")
    if item_missing(item):
        status.append("! нет файла", style=f"bold {theme['err']}")
    elif item.process and is_running(item, names):
        status.append("● работает", style=f"bold {theme['ok']}")
    elif item.admin:
        status.append("admin", style=theme["dim"])
    return badge, title, status


def menu(console: Console, config: Config, theme: dict, names: set[str]) -> None:
    show_desc = config.settings["show_descriptions"]
    width = console.width
    per_row = 1 if width < 96 else 2 if width < 156 else 3
    sections = [s for s in config.sections if s.items]
    key_width = max((len(i.key) for i in config.items), default=1)

    panels: list[tuple[Panel, int]] = []
    for section in sections:
        index = config.sections.index(section)
        accent = section_color(config, theme, index)
        grid = Table.grid(expand=True, padding=(0, 1))
        grid.add_column(no_wrap=True)
        grid.add_column(ratio=1, no_wrap=True)
        grid.add_column(no_wrap=True, justify="right")
        for item in section.items:
            grid.add_row(*_item_row(item, accent, theme, names, show_desc, key_width))
        rows = len(section.items)
        title = Text(f" {section.icon} {section.name.upper()} ", style=f"bold {accent}")
        panel = Panel(grid, title=title, title_align="left", border_style=accent, box=box.ROUNDED, padding=(0, 1))
        panels.append((panel, rows))

    layout = Table.grid(expand=True, padding=(0, 1))
    for _ in range(per_row):
        layout.add_column(ratio=1)
    for start in range(0, len(panels), per_row):
        chunk = panels[start:start + per_row]
        height = max(rows for _, rows in chunk) + 2
        cells = []
        for panel, _ in chunk:
            panel.height = height
            cells.append(panel)
        cells += [Text("")] * (per_row - len(cells))
        layout.add_row(*cells)
    console.print(layout)


COMMAND_GROUPS = [
    ("Система", ["sys", "top", "net", "ip", "dns", "clean"]),
    ("Питание", ["lock", "sleep", "off", "reboot"]),
    ("Меню", ["add", "del", "edit", "reload", "theme", "autostart"]),
    ("Ещё", ["stop N", "kill имя", "find", "!cmd", "help", "q"]),
]


def commands_panel(console: Console, theme: dict) -> None:
    grid = Table.grid(expand=True, padding=(0, 2))
    per_row = 2 if console.width >= 96 else 1
    for _ in range(per_row):
        grid.add_column(ratio=1)
    cells = []
    for g_index, (label, cmds) in enumerate(COMMAND_GROUPS):
        accent = theme["accents"][g_index % len(theme["accents"])]
        line = Text(no_wrap=True, overflow="ellipsis")
        line.append(f"{label:<8}", style=theme["muted"])
        for c_index, cmd in enumerate(cmds):
            if c_index:
                line.append(" · ", style=theme["dim"])
            word, _, rest = cmd.partition(" ")
            line.append(word, style=f"bold {accent}")
            if rest:
                line.append(f" {rest}", style=theme["muted"])
        cells.append(line)
    for start in range(0, len(cells), per_row):
        row = cells[start:start + per_row]
        grid.add_row(*(row + [Text("")] * (per_row - len(row))))
    console.print(Panel(grid, title=Text(" 🧰 КОМАНДЫ ", style=f"bold {theme['muted']}"), title_align="left",
                        border_style=theme["dim"], box=box.ROUNDED, padding=(0, 1)))


def hint(console: Console, theme: dict) -> None:
    text = Text()
    text.append("номер", style=f"bold {theme['text']}")
    text.append(" — запуск (можно сразу несколько: ", style=theme["muted"])
    text.append("1 3", style=f"bold {theme['text']}")
    text.append(")  ·  ", style=theme["muted"])
    text.append("Tab", style=f"bold {theme['text']}")
    text.append(" — подсказки  ·  ", style=theme["muted"])
    text.append("Enter", style=f"bold {theme['text']}")
    text.append(" — обновить", style=theme["muted"])
    console.print(Align.center(text))


def toast(console: Console, theme: dict, message: str, kind: str = "ok") -> None:
    color = {"ok": theme["ok"], "err": theme["err"], "warn": theme["warn"]}.get(kind, theme["accents"][3])
    mark = {"ok": "✓", "err": "×", "warn": "!"}.get(kind, "◆")
    text = Text()
    text.append("▌ ", style=color)
    text.append(f"{mark} ", style=f"bold {color}")
    text.append(message, style=theme["text"])
    console.print(text)


def dashboard(console: Console, config: Config, theme: dict, names: set[str], first_render: bool = False) -> None:
    s = config.settings
    console.print()
    banner(console, config, theme)
    if s["show_status_bar"]:
        console.print()
        bar = powerline(status_segments(first_render), theme, s["powerline"], console.width - 2)
        console.print(Align.center(bar))
    console.print()
    menu(console, config, theme, names)
    if s["show_commands"]:
        commands_panel(console, theme)
    hint(console, theme)


def section_title(console: Console, theme: dict, title: str, accent: str | None = None) -> None:
    accent = accent or theme["accents"][0]
    text = Text()
    text.append(PL_ROUND_LEFT, style=accent)
    text.append(f" {title} ", style=f"bold {theme['on_accent']} on {accent}")
    text.append(PL_RIGHT, style=accent)
    console.print()
    console.print(text)


def error_panel(console: Console, theme: dict, title: str, body: str) -> None:
    console.print(Panel(Text(body, style=theme["text"]), title=Text(f" {title} ", style=f"bold {theme['err']}"),
                        border_style=theme["err"], box=box.ROUNDED, padding=(1, 2)))
