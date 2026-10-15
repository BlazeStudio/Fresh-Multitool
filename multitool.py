"""Fresh Multitool — красивый лаунчер программ для Windows Terminal.

    python multitool.py              запустить в текущей консоли
    python multitool.py --open       открыть в отдельном окне (так работает команда fresh_multitool)
    python multitool.py --install    PATH, меню Пуск, горячая клавиша, автозапуск, профиль Терминала
    python multitool.py --uninstall  убрать всё это из системы
"""

from __future__ import annotations

import argparse
import sys
import traceback
from datetime import datetime
from pathlib import Path

LOG = Path(__file__).resolve().parent / "multitool.log"


def _log_crash() -> str:
    text = traceback.format_exc()
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}]\n{text}")
    except OSError:
        pass
    return text


def main() -> None:
    parser = argparse.ArgumentParser(prog="fresh_multitool", description="Fresh Multitool — быстрый запуск программ")
    parser.add_argument("--open", action="store_true", help="открыть в отдельном окне или переключиться на уже открытое")
    parser.add_argument("--here", action="store_true", help="запустить прямо в текущей консоли")
    parser.add_argument("--install", action="store_true", help="установить: PATH, меню Пуск, автозапуск, профиль Терминала")
    parser.add_argument("--no-autostart", action="store_true", help="при установке не включать автозапуск")
    parser.add_argument("--uninstall", action="store_true", help="убрать Multitool из PATH, автозапуска и меню Пуск")
    parser.add_argument("--daemon", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--autostart", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--center", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.install:
        from mt.integration import install
        install(autostart=not args.no_autostart)
        return
    if args.uninstall:
        from mt.integration import uninstall
        uninstall()
        return
    if args.open or args.daemon:
        try:
            from mt.integration import open_window, run_daemon
            if args.daemon:
                run_daemon(autostart=args.autostart)
            else:
                open_window(autostart=args.autostart)
        except Exception:
            text = _log_crash()
            import ctypes
            hint = "Запусти install.cmd, чтобы поставить зависимости.\n\n" if "ModuleNotFoundError" in text else ""
            ctypes.windll.user32.MessageBoxW(None, f"{hint}{text[-1500:]}", "Fresh Multitool — ошибка", 0x10)
        return

    try:
        from mt.app import run
        run(autostart=args.autostart, center=args.center)
    except KeyboardInterrupt:
        pass
    except Exception:
        text = _log_crash()
        print("\n\x1b[1;31mMultitool упал с ошибкой:\x1b[0m\n")
        print(text)
        print(f"Лог: {LOG}")
        if "ModuleNotFoundError" in text:
            print("Похоже, не хватает библиотек — запусти install.cmd")
        try:
            input("\nEnter — закрыть")
        except (EOFError, KeyboardInterrupt):
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
