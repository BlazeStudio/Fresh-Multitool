"""Цветовые темы и градиенты."""

from __future__ import annotations

from rich.text import Text

# Каждая тема: gradient — цвета баннера, accents — цвета секций меню,
# bar — фоны сегментов статус-строки. Остальное — служебные цвета.
THEMES: dict[str, dict] = {
    "fresh": {
        "gradient": ["#FF2E88", "#FF7A59", "#F9E64F", "#6CBF5A", "#1BA39C"],
        "accents": ["#FF2E88", "#F9E64F", "#6CBF5A", "#1BA39C", "#A78BFA", "#38BDF8"],
        "bar": ["#FF2E88", "#F9E64F", "#6CBF5A", "#1BA39C", "#3B82F6", "#A78BFA"],
    },
    "neon": {
        "gradient": ["#FF00CC", "#9D4EDD", "#3A86FF", "#00F5D4"],
        "accents": ["#FF00CC", "#00F5D4", "#3A86FF", "#9D4EDD", "#FFBE0B", "#FB5607"],
        "bar": ["#FF00CC", "#9D4EDD", "#3A86FF", "#00B4D8", "#00F5D4", "#FFBE0B"],
    },
    "sunset": {
        "gradient": ["#7B2FF7", "#F107A3", "#FF512F", "#F09819", "#FFD200"],
        "accents": ["#FF512F", "#FFD200", "#F107A3", "#F09819", "#7B2FF7", "#FF8FAB"],
        "bar": ["#7B2FF7", "#F107A3", "#FF512F", "#F09819", "#FFD200", "#FF8FAB"],
    },
    "ocean": {
        "gradient": ["#00F5A0", "#00D9F5", "#0072FF", "#7B2FF7"],
        "accents": ["#00D9F5", "#00F5A0", "#4D9FFF", "#9B7BFF", "#5EEAD4", "#93C5FD"],
        "bar": ["#00F5A0", "#00D9F5", "#38BDF8", "#4D9FFF", "#7B8CFF", "#9B7BFF"],
    },
    "dracula": {
        "gradient": ["#FF79C6", "#BD93F9", "#8BE9FD", "#50FA7B"],
        "accents": ["#FF79C6", "#BD93F9", "#8BE9FD", "#50FA7B", "#F1FA8C", "#FFB86C"],
        "bar": ["#FF79C6", "#BD93F9", "#8BE9FD", "#50FA7B", "#F1FA8C", "#FFB86C"],
    },
    "matrix": {
        "gradient": ["#B6FFB0", "#39FF14", "#00C832", "#008F11"],
        "accents": ["#39FF14", "#00C832", "#7CFC9A", "#00E676", "#B6FFB0", "#69F0AE"],
        "bar": ["#B6FFB0", "#7CFC9A", "#39FF14", "#00E676", "#00C832", "#00A82D"],
    },
}

BASE = {
    "text": "#E6E6F0",
    "muted": "#7C7C96",
    "dim": "#4A4A60",
    "ok": "#6CDB8B",
    "warn": "#F9E64F",
    "err": "#FF4D6D",
    "on_accent": "#14141E",  # цвет текста поверх ярких фонов
}


def get_theme(name: str, custom: dict | None = None) -> dict:
    """Тема по имени; пользовательские темы из конфига дополняют встроенные."""
    themes = {**THEMES, **(custom or {})}
    chosen = themes.get(name) or THEMES["fresh"]
    return {**BASE, **THEMES["fresh"], **chosen, "name": name if name in themes else "fresh"}


def all_theme_names(custom: dict | None = None) -> list[str]:
    return list({**THEMES, **(custom or {})}.keys())


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def blend(stops: list[str], t: float) -> str:
    """Цвет в точке t (0..1) многоцветного градиента."""
    if len(stops) == 1:
        return stops[0]
    t = min(max(t, 0.0), 1.0) * (len(stops) - 1)
    i = min(int(t), len(stops) - 2)
    local = t - i
    a, b = _hex_to_rgb(stops[i]), _hex_to_rgb(stops[i + 1])
    r, g, bl = (round(a[k] + (b[k] - a[k]) * local) for k in range(3))
    return f"#{r:02X}{g:02X}{bl:02X}"


def gradient_lines(lines: list[str], stops: list[str], bold: bool = True) -> Text:
    """Диагональный градиент по многострочному тексту (для ASCII-баннера)."""
    width = max((len(line) for line in lines), default=1)
    height = max(len(lines), 1)
    out = Text()
    for y, line in enumerate(lines):
        for x, ch in enumerate(line):
            if ch == " ":
                out.append(ch)
                continue
            t = (x / max(width - 1, 1)) * 0.85 + (y / max(height - 1, 1)) * 0.15
            out.append(ch, style=f"{'bold ' if bold else ''}{blend(stops, t)}")
        if y < len(lines) - 1:
            out.append("\n")
    return out


def gradient_text(text: str, stops: list[str], bold: bool = False) -> Text:
    return gradient_lines([text], stops, bold=bold)


def bar(fraction: float, width: int, theme: dict, stops: list[str] | None = None) -> Text:
    """Полоска заполнения с градиентом от «зелёного» к «красному»."""
    fraction = min(max(fraction, 0.0), 1.0)
    filled = round(fraction * width)
    stops = stops or [theme["ok"], theme["warn"], theme["err"]]
    out = Text()
    for i in range(width):
        if i < filled:
            out.append("━", style=blend(stops, i / max(width - 1, 1)))
        else:
            out.append("━", style=theme["dim"])
    return out


def level_color(fraction: float, theme: dict) -> str:
    if fraction < 0.6:
        return theme["ok"]
    if fraction < 0.85:
        return theme["warn"]
    return theme["err"]
