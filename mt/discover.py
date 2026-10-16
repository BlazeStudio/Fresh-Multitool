"""Поиск установленных программ: ярлыки меню Пуск, игры Steam, первичная сборка config.json."""

from __future__ import annotations

import difflib
import json
import re
import winreg
from pathlib import Path

from . import config as cfg
from .winsys import read_shortcuts, start_menu_shortcuts

EXAMPLE_PATH = cfg.ROOT / "config.example.json"

# Служебные поля config.example.json — нужны только для поиска и в config.json не попадают.
DISCOVERY_FIELDS = ("find", "detect", "steam_app")


# ─── меню Пуск ───────────────────────────────────────────────────────────────

def search_shortcuts(query: str, shortcuts: list[Path] | None = None, fuzzy: bool = True) -> list[Path]:
    """Ярлыки, похожие на запрос: точное имя → целое слово («obs» → OBS Studio, а не Obsidian) → начало имени.
    fuzzy — если точных совпадений нет, искать похожие (для add, где человек выбирает сам)."""
    shortcuts = shortcuts if shortcuts is not None else start_menu_shortcuts()
    low = query.lower()
    matches = [p for p in shortcuts if low in p.stem.lower()]
    if not matches and fuzzy:
        stems = {p.stem.lower(): p for p in shortcuts}
        matches = [stems[s] for s in difflib.get_close_matches(low, list(stems), n=8, cutoff=0.5)]

    def rank(p: Path):
        stem = p.stem.lower()
        return stem != low, low not in re.split(r"\W+", stem), not stem.startswith(low), len(stem)

    matches.sort(key=rank)
    return matches


def shortcut_entry(lnk: Path, info: dict | None = None) -> dict:
    """Ярлык → поля пункта меню (path/args/cwd). Ярлыки без файла-цели (UWP) запускаем как есть."""
    info = info if info is not None else read_shortcuts([lnk]).get(str(lnk), {})
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


# ─── Steam ───────────────────────────────────────────────────────────────────

def steam_libraries() -> list[Path]:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            steam = Path(winreg.QueryValueEx(key, "SteamPath")[0])
    except OSError:
        return []
    libraries = [steam]
    vdf = steam / "steamapps" / "libraryfolders.vdf"
    try:
        for raw in re.findall(r'"path"\s+"([^"]+)"', vdf.read_text(encoding="utf-8", errors="replace")):
            path = Path(raw.replace("\\\\", "\\"))
            if path not in libraries:
                libraries.append(path)
    except OSError:
        pass
    return libraries


def steam_app_installed(app_id: int) -> bool:
    return any((lib / "steamapps" / f"appmanifest_{app_id}.acf").exists() for lib in steam_libraries())


# ─── сборка config.json из config.example.json ───────────────────────────────

def _exists(path: str) -> bool:
    from .launcher import target_exists
    return target_exists(path)


def _resolve(item: dict, shortcuts: list[Path], lnk_info: dict) -> dict | None:
    """Пункт из примера → пункт для этого компьютера или None, если программы нет."""
    if item.get("steam_app") is not None and not steam_app_installed(int(item["steam_app"])):
        return None
    if item.get("detect"):
        candidates = [item["detect"]] if isinstance(item["detect"], str) else item["detect"]
        if not any(Path(cfg.expand(c)).exists() for c in candidates):
            return None

    resolved = {k: v for k, v in item.items() if k not in DISCOVERY_FIELDS}
    if item.get("path") and _exists(item["path"]):
        return resolved
    if item.get("steps") or item.get("command"):
        return resolved
    if item.get("find"):
        matches = search_shortcuts(item["find"], shortcuts, fuzzy=False)
        if matches:
            entry = shortcut_entry(matches[0], lnk_info.get(str(matches[0])))
            resolved["path"] = entry["path"]
            for key in ("args", "cwd"):
                if entry.get(key) and key not in resolved:
                    resolved[key] = entry[key]
            exe = Path(entry["path"]).name
            if "process" not in resolved and exe.lower().endswith(".exe"):
                resolved["process"] = exe
            return resolved
    return None


def build_from_example() -> tuple[dict, list[str]]:
    """Собирает личный конфиг: оставляет из примера только то, что установлено. → (конфиг, найденные имена)."""
    example = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8-sig"))
    shortcuts = start_menu_shortcuts()

    # Все нужные ярлыки читаем одним вызовом PowerShell — это самая медленная часть.
    wanted = []
    for sec in example.get("sections", []):
        for item in sec.get("items", []):
            if item.get("find") and not (item.get("path") and _exists(item["path"])):
                wanted += search_shortcuts(item["find"], shortcuts, fuzzy=False)[:1]
    lnk_info = read_shortcuts(wanted)

    found: list[str] = []
    sections = []
    for sec in example.get("sections", []):
        items = [r for r in (_resolve(i, shortcuts, lnk_info) for i in sec.get("items", [])) if r]
        if items:
            sections.append({**sec, "items": items})
            found += [i["name"] for i in items]
    built = {k: v for k, v in example.items() if k != "sections"}
    built["sections"] = sections or cfg.DEFAULT_CONFIG["sections"]
    return built, found


def ensure_config() -> list[str] | None:
    """Нет config.json — создаёт его (из примера с автопоиском). Возвращает найденные программы или None."""
    if cfg.CONFIG_PATH.exists():
        return None
    if EXAMPLE_PATH.exists():
        try:
            built, found = build_from_example()
        except (OSError, ValueError):
            built, found = cfg.DEFAULT_CONFIG, []
    else:
        built, found = cfg.DEFAULT_CONFIG, []
    cfg.save(built)
    return found
