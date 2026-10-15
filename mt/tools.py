"""Встроенные утилиты: система, процессы, сеть, очистка, питание."""

from __future__ import annotations

import ctypes
import getpass
import os
import platform
import socket
import ssl
import subprocess
import time
import urllib.request
import winreg
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psutil
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .themes import bar, level_color
from .ui import section_title


def _gb(value: float) -> str:
    return f"{value / 2**30:.1f} ГБ"


def _reg(path: str, name: str, root=winreg.HKEY_LOCAL_MACHINE) -> str:
    try:
        with winreg.OpenKey(root, path) as key:
            return str(winreg.QueryValueEx(key, name)[0])
    except OSError:
        return ""


def _gpu_names() -> list[str]:
    base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    names = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as key:
            for i in range(winreg.QueryInfoKey(key)[0]):
                sub = winreg.EnumKey(key, i)
                name = _reg(base + "\\" + sub, "DriverDesc")
                if name and name not in names and "basic" not in name.lower() and "virtual" not in name.lower():
                    names.append(name)
    except OSError:
        pass
    return names


def _windows_version() -> str:
    nt = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
    name = _reg(nt, "ProductName") or "Windows"
    build = int(_reg(nt, "CurrentBuildNumber") or 0)
    if build >= 22000:
        name = name.replace("Windows 10", "Windows 11")
    display = _reg(nt, "DisplayVersion")
    ubr = _reg(nt, "UBR")
    return f"{name} {display} (сборка {build}.{ubr})".strip()


def system_info(console: Console, theme: dict) -> None:
    section_title(console, theme, "💻 СИСТЕМА", theme["accents"][0])
    cpu_name = _reg(r"HARDWARE\DESCRIPTION\System\CentralProcessor\0", "ProcessorNameString").strip() or platform.processor()
    freq = psutil.cpu_freq()
    cpu_load = psutil.cpu_percent(interval=0.4)
    mem = psutil.virtual_memory()
    boot = time.time() - psutil.boot_time()

    info = Table.grid(padding=(0, 2))
    info.add_column(style=theme["muted"], no_wrap=True)
    info.add_column(style=theme["text"])
    info.add_row("Компьютер", f"{platform.node()}  ·  {getpass.getuser()}")
    info.add_row("Система", _windows_version())
    info.add_row("Процессор", f"{cpu_name}  ·  {psutil.cpu_count(logical=False)} ядер / {psutil.cpu_count()} потоков"
                 + (f"  ·  {freq.current / 1000:.2f} ГГц" if freq and freq.current else ""))
    for gpu in _gpu_names():
        info.add_row("Видеокарта", gpu)
    days, rem = divmod(int(boot), 86400)
    info.add_row("Аптайм", f"{days} д {rem // 3600} ч {rem % 3600 // 60} мин" if days else f"{rem // 3600} ч {rem % 3600 // 60} мин")
    battery = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
    if battery:
        info.add_row("Батарея", f"{battery.percent:.0f}%" + (" (зарядка)" if battery.power_plugged else ""))

    meters = Table.grid(padding=(0, 2))
    meters.add_column(style=theme["muted"], no_wrap=True)
    meters.add_column(no_wrap=True)
    meters.add_column(no_wrap=True, justify="right")
    meters.add_row("CPU", bar(cpu_load / 100, 30, theme), Text(f"{cpu_load:.0f}%", style=level_color(cpu_load / 100, theme)))
    meters.add_row("RAM", bar(mem.percent / 100, 30, theme),
                   Text(f"{_gb(mem.used)} из {_gb(mem.total)}", style=level_color(mem.percent / 100, theme)))
    for part in psutil.disk_partitions(all=False):
        if "cdrom" in part.opts or not part.fstype:
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        frac = usage.percent / 100
        meters.add_row(f"Диск {part.mountpoint.rstrip(chr(92))}", bar(frac, 30, theme),
                       Text(f"свободно {_gb(usage.free)} из {_gb(usage.total)}", style=level_color(frac, theme)))

    console.print(Panel(info, border_style=theme["accents"][0], box=box.ROUNDED, padding=(0, 2)))
    console.print(Panel(meters, border_style=theme["accents"][1], box=box.ROUNDED, padding=(0, 2)))


def top_processes(console: Console, theme: dict, count: int = 12) -> None:
    section_title(console, theme, "📊 ТОП ПРОЦЕССОВ ПО ПАМЯТИ", theme["accents"][1])
    procs: dict[str, list] = {}
    for p in psutil.process_iter(["name", "memory_info"]):
        mi = p.info.get("memory_info")
        name = p.info.get("name") or "?"
        if not mi:
            continue
        entry = procs.setdefault(name, [0, 0])
        entry[0] += mi.rss
        entry[1] += 1
    total = psutil.virtual_memory().total
    rows = sorted(procs.items(), key=lambda kv: kv[1][0], reverse=True)[:count]
    table = Table(box=box.SIMPLE_HEAD, border_style=theme["dim"], header_style=f"bold {theme['muted']}", expand=False)
    table.add_column("#", justify="right", style=theme["dim"])
    table.add_column("Процесс", style=f"bold {theme['text']}")
    table.add_column("Шт.", justify="right", style=theme["muted"])
    table.add_column("Память", justify="right")
    table.add_column("", no_wrap=True)
    biggest = rows[0][1][0] if rows else 1
    for i, (name, (rss, n)) in enumerate(rows, 1):
        frac = rss / total
        table.add_row(str(i), name, str(n), Text(_gb(rss), style=level_color(frac * 4, theme)), bar(rss / biggest, 24, theme))
    console.print(table)
    console.print(Text("  завершить: kill имя.exe", style=theme["dim"]))


_ssl_context: ssl.SSLContext | None = None


def _check_url(name: str, url: str, timeout: float = 5.0) -> tuple[str, str, float | None, str]:
    start = time.perf_counter()
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0 Multitool"})
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context) as resp:
            resp.read(0)
            return name, url, (time.perf_counter() - start) * 1000, f"HTTP {resp.status}"
    except urllib.error.HTTPError as exc:
        # Сайт ответил — значит доступен, даже если HEAD не разрешён.
        return name, url, (time.perf_counter() - start) * 1000, f"HTTP {exc.code}"
    except (urllib.error.URLError, socket.timeout, TimeoutError, ssl.SSLError, ConnectionError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        text = str(reason)
        if isinstance(reason, (socket.timeout, TimeoutError)) or "timed out" in text:
            text = "тайм-аут — возможно, блокировка"
        elif "getaddrinfo" in text or "11001" in text:
            text = "DNS не разрешил адрес"
        elif "10054" in text or "reset" in text.lower():
            text = "соединение сброшено — похоже на DPI"
        return name, url, None, text[:60]


def net_check(console: Console, theme: dict, targets: list[dict]) -> None:
    global _ssl_context
    section_title(console, theme, "📡 ДОСТУПНОСТЬ САЙТОВ", theme["accents"][3])
    with console.status("[bold]Проверяю…", spinner="dots"):
        # Загрузка сертификатов Windows медленная — один раз и до замеров времени.
        _ssl_context = _ssl_context or ssl.create_default_context()
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda t: _check_url(t["name"], t["url"]), targets))
    table = Table(box=box.SIMPLE_HEAD, border_style=theme["dim"], header_style=f"bold {theme['muted']}")
    table.add_column("Сайт", style=f"bold {theme['text']}")
    table.add_column("Статус", no_wrap=True)
    table.add_column("Время", justify="right")
    table.add_column("", style=theme["muted"])
    for name, url, ms, detail in results:
        if ms is None:
            table.add_row(name, Text("● недоступен", style=f"bold {theme['err']}"), Text("—", style=theme["dim"]), detail)
        else:
            color = theme["ok"] if ms < 300 else theme["warn"] if ms < 1000 else theme["err"]
            table.add_row(name, Text("● доступен", style=f"bold {theme['ok']}"), Text(f"{ms:.0f} мс", style=color), detail)
    console.print(table)
    if any(ms is None for _, _, ms, _ in results):
        console.print(Text("  Discord/YouTube недоступны? Запусти zapret из меню.", style=theme["muted"]))


def ip_info(console: Console, theme: dict) -> None:
    section_title(console, theme, "🌐 IP-АДРЕСА", theme["accents"][4 % len(theme["accents"])])
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style=theme["muted"], no_wrap=True)
    grid.add_column(style=f"bold {theme['text']}")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            grid.add_row("Локальный", s.getsockname()[0])
    except OSError:
        grid.add_row("Локальный", "нет сети")
    for nic, addrs in psutil.net_if_addrs().items():
        stats = psutil.net_if_stats().get(nic)
        if not stats or not stats.isup:
            continue
        for a in addrs:
            if a.family == socket.AF_INET and not a.address.startswith(("127.", "169.254.")):
                grid.add_row(f"  {nic}", Text(a.address, style=theme["text"]))
    with console.status("[bold]Узнаю внешний IP…", spinner="dots"):
        try:
            with urllib.request.urlopen("https://api.ipify.org", timeout=5) as resp:
                external = resp.read().decode().strip()
        except OSError:
            external = "не удалось получить"
    grid.add_row("Внешний", external)
    console.print(Panel(grid, border_style=theme["accents"][4 % len(theme["accents"])], box=box.ROUNDED, padding=(0, 2)))


def flush_dns() -> tuple[bool, str]:
    result = subprocess.run(["ipconfig", "/flushdns"], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    return result.returncode == 0, "DNS-кэш очищен" if result.returncode == 0 else "не удалось очистить DNS-кэш"


def temp_size() -> tuple[int, int]:
    total, count = 0, 0
    for root, _, files in os.walk(os.environ.get("TEMP", "")):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
                count += 1
            except OSError:
                pass
    return total, count


def clean_temp() -> tuple[int, int]:
    """Удаляет содержимое %TEMP%; занятые файлы пропускает. Возвращает (байт, файлов)."""
    temp = Path(os.environ.get("TEMP", ""))
    if not temp.is_dir() or len(temp.parts) < 3:
        return 0, 0
    freed, removed = 0, 0
    for root, dirs, files in os.walk(temp, topdown=False):
        for name in files:
            path = os.path.join(root, name)
            try:
                size = os.path.getsize(path)
                os.remove(path)
                freed += size
                removed += 1
            except OSError:
                pass
        for name in dirs:
            try:
                os.rmdir(os.path.join(root, name))
            except OSError:
                pass
    return freed, removed


def lock() -> None:
    ctypes.windll.user32.LockWorkStation()


def sleep() -> None:
    ctypes.windll.powrprof.SetSuspendState(False, False, False)


def shutdown(seconds: int = 0) -> None:
    subprocess.run(["shutdown", "/s", "/t", str(seconds)], creationflags=subprocess.CREATE_NO_WINDOW)


def cancel_shutdown() -> bool:
    return subprocess.run(["shutdown", "/a"], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW).returncode == 0


def reboot() -> None:
    subprocess.run(["shutdown", "/r", "/t", "0"], creationflags=subprocess.CREATE_NO_WINDOW)
