"""Загрузка, проверка и сохранение config.json."""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
HISTORY_PATH = ROOT / ".history"
VERSION = "1.0"

DEFAULT_SETTINGS: dict = {
    "title": "MULTITOOL",
    "subtitle": "fresh edition",
    "theme": "fresh",
    "banner_font": "ansi_shadow",
    "powerline": True,
    "show_status_bar": True,
    "show_descriptions": True,
    "show_commands": True,
    "auto_refresh": 20,
    "close_after_launch": False,
    "single_instance": True,
    "use_windows_terminal": True,
    "window_title": "Multitool",
    "window": {"columns": 124, "rows": 42, "center": True},
    "hotkey": "Alt+`",
    "open_on_startup": True,
    "editor": "auto",
    "autostart_delay": 0,
    "net_targets": [
        {"name": "Discord", "url": "https://discord.com"},
        {"name": "YouTube", "url": "https://www.youtube.com"},
        {"name": "Google", "url": "https://www.google.com"},
        {"name": "Steam", "url": "https://store.steampowered.com"},
        {"name": "hh.ru", "url": "https://hh.ru"},
        {"name": "Яндекс", "url": "https://ya.ru"},
    ],
}

DEFAULT_CONFIG: dict = {
    "settings": {},
    "sections": [
        {
            "name": "Главное",
            "icon": "⭐",
            "items": [
                {
                    "name": "Диспетчер задач",
                    "icon": "📊",
                    "description": "пример — добавь свои программы командой add",
                    "path": "taskmgr.exe",
                }
            ],
        }
    ],
}


class ConfigError(Exception):
    pass


@dataclass
class Item:
    key: str
    name: str
    section: int
    icon: str = "◆"
    description: str = ""
    path: str = ""
    args: str | list = ""
    cwd: str = ""
    admin: bool = False
    window: str = "normal"
    process: list[str] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)
    command: str = ""
    aliases: list[str] = field(default_factory=list)
    autostart: bool = False
    raw: dict = field(default_factory=dict)

    @property
    def kind(self) -> str:
        if self.steps:
            return "steps"
        if self.command:
            return "command"
        return "path"

    def matches(self, word: str) -> bool:
        word = word.lower()
        return word == self.key.lower() or word in (a.lower() for a in self.aliases)


@dataclass
class Section:
    name: str
    icon: str
    color: str
    items: list[Item]


@dataclass
class Config:
    raw: dict
    settings: dict
    sections: list[Section]
    themes: dict
    mtime: float

    @property
    def items(self) -> list[Item]:
        return [item for section in self.sections for item in section.items]

    def find(self, word: str) -> Item | None:
        for item in self.items:
            if item.matches(word):
                return item
        return None


def expand(value: str) -> str:
    return os.path.expandvars(os.path.expanduser(value)) if value else value


def _as_list(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def _merge_settings(user: dict) -> dict:
    merged = json.loads(json.dumps(DEFAULT_SETTINGS))
    for key, value in (user or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    return merged


def _parse(raw: dict, mtime: float) -> Config:
    if not isinstance(raw, dict):
        raise ConfigError("Корень config.json должен быть объектом { ... }")
    settings = _merge_settings(raw.get("settings", {}))
    sections_raw = raw.get("sections", [])
    if not isinstance(sections_raw, list):
        raise ConfigError('"sections" должен быть списком [ ... ]')

    explicit = {
        str(item.get("key")).lower()
        for sec in sections_raw
        for item in sec.get("items", [])
        if isinstance(item, dict) and item.get("key")
    }
    counter = 1
    sections: list[Section] = []
    seen_keys: set[str] = set()
    for s_index, sec in enumerate(sections_raw):
        if not isinstance(sec, dict):
            raise ConfigError(f"Секция №{s_index + 1} должна быть объектом")
        items: list[Item] = []
        for i_index, it in enumerate(sec.get("items", [])):
            where = f'секция "{sec.get("name", s_index + 1)}", пункт №{i_index + 1}'
            if not isinstance(it, dict):
                raise ConfigError(f"{where}: пункт должен быть объектом")
            if not it.get("name"):
                raise ConfigError(f'{where}: не указано "name"')
            if not (it.get("path") or it.get("steps") or it.get("command")):
                raise ConfigError(f'{where} ({it["name"]}): нужен "path", "steps" или "command"')
            if it.get("key"):
                key = str(it["key"])
            else:
                while str(counter) in explicit or str(counter) in seen_keys:
                    counter += 1
                key = str(counter)
                counter += 1
            if key.lower() in seen_keys:
                raise ConfigError(f'{where}: ключ "{key}" уже занят другим пунктом')
            seen_keys.add(key.lower())
            items.append(
                Item(
                    key=key,
                    name=str(it["name"]),
                    section=s_index,
                    icon=str(it.get("icon") or "◆"),
                    description=str(it.get("description") or ""),
                    path=str(it.get("path") or ""),
                    args=it.get("args") or "",
                    cwd=str(it.get("cwd") or ""),
                    admin=bool(it.get("admin", False)),
                    window=str(it.get("window") or "normal"),
                    process=_as_list(it.get("process")),
                    steps=list(it.get("steps") or []),
                    command=str(it.get("command") or ""),
                    aliases=_as_list(it.get("aliases")),
                    autostart=bool(it.get("autostart", False)),
                    raw=it,
                )
            )
        sections.append(
            Section(
                name=str(sec.get("name") or f"Секция {s_index + 1}"),
                icon=str(sec.get("icon") or "◆"),
                color=str(sec.get("color") or ""),
                items=items,
            )
        )
    return Config(raw=raw, settings=settings, sections=sections, themes=raw.get("themes") or {}, mtime=mtime)


def load() -> Config:
    if not CONFIG_PATH.exists():
        save(DEFAULT_CONFIG)
    text = CONFIG_PATH.read_text(encoding="utf-8-sig")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"Ошибка в config.json, строка {exc.lineno}, столбец {exc.colno}: {exc.msg}.\n"
            "Частые причины: лишняя запятая в конце списка, одинарные кавычки, "
            "одиночный \\ в пути (пиши \\\\ или /)."
        ) from exc
    return _parse(raw, CONFIG_PATH.stat().st_mtime)


_SCALAR = r'(?:"(?:[^"\\\n]|\\.)*"|-?\d+(?:\.\d+)?|true|false|null)'
_SHORT_LIST = re.compile(r"\[\n\s+(" + _SCALAR + r"(?:,\n\s+" + _SCALAR + r")*)\n\s*\]")


def dumps(raw: dict) -> str:
    """JSON с отступами, но короткие списки строк/чисел — в одну строку: ["cs", "cs2"]."""
    text = json.dumps(raw, ensure_ascii=False, indent=2)

    def collapse(match: re.Match) -> str:
        one_line = "[" + ", ".join(part.strip() for part in match.group(1).split(",\n")) + "]"
        return one_line if len(one_line) <= 80 else match.group(0)

    return _SHORT_LIST.sub(collapse, text) + "\n"


def save(raw: dict) -> None:
    if CONFIG_PATH.exists():
        shutil.copyfile(CONFIG_PATH, CONFIG_PATH.with_suffix(".json.bak"))
    data = dumps(raw)
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, CONFIG_PATH)


def changed_on_disk(config: Config) -> bool:
    try:
        return CONFIG_PATH.stat().st_mtime != config.mtime
    except OSError:
        return False


def set_setting(raw: dict, key: str, value) -> None:
    raw.setdefault("settings", {})[key] = value
    save(raw)
