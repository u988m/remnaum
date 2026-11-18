"""Friendly ANSI terminal interface, with safe non-TTY fallbacks."""
from __future__ import annotations
import os
import shutil
import sys
import time

COLORS = {
    "cyan": "\x1b[96m", "blue": "\x1b[94m", "green": "\x1b[92m",
    "yellow": "\x1b[93m", "red": "\x1b[91m", "bold": "\x1b[1m",
    "dim": "\x1b[2m", "reset": "\x1b[0m",
}


def tty() -> bool:
    return sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def color(value: str, name: str) -> str:
    return COLORS.get(name, "") + value + COLORS["reset"] if tty() else value


def line() -> str:
    columns = min(max(shutil.get_terminal_size((72, 20)).columns - 2, 38), 74)
    return "─" * columns


def header(subtitle: str = "Установка · Обновления · Резервные копии") -> None:
    print()
    print(color("╭" + line() + "╮", "blue"))
    print(color("│", "blue") + "  " + color("◆ R E M N A U M", "bold") + "   •   VPN storefront")
    print(color("│", "blue") + "  " + color(subtitle, "dim"))
    print(color("╰" + line() + "╯", "blue"))
    print()


def section(current: int, total: int, title: str) -> None:
    print("\n" + color(f"  [{current}/{total}] ", "cyan") + color(title, "bold"))
    print(color("  " + line()[:55], "dim"))


def ok(message: str) -> None:
    print(color("  ✓ ", "green") + message)


def warning(message: str) -> None:
    print(color("  ! ", "yellow") + message)


def menu(title: str, options: list[tuple[str, str]]) -> None:
    print(color("  " + title, "bold"))
    for index, label in options:
        print("    " + color(f"{index:>2} ", "cyan") + "  " + label)
    print()


def animated_call(command: list[str], cwd: str, *, log_path: str) -> int:
    """No secrets in terminal logs, only status and a protected on-disk trace."""
    import subprocess
    import threading
    import itertools
    import pathlib

    target = pathlib.Path(log_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as log:
        child = subprocess.Popen(command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT)
        if not sys.stdout.isatty():
            return child.wait()
        for frame in itertools.cycle("◐◓◑◒"):
            if child.poll() is not None:
                break
            sys.stdout.write("\r  " + color(frame, "cyan") + "  Поднимаем контейнеры и проверяем зависимости…")
            sys.stdout.flush()
            time.sleep(0.17)
        code = child.wait()
        sys.stdout.write("\r" + " " * 72 + "\r")
        sys.stdout.flush()
        return code
