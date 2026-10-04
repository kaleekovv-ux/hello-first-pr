"""Цветной вывод для консоли: зелёный — всё хорошо, жёлтый —
предупреждения, красный — ошибки. Работает и в Windows PowerShell/cmd
(включает поддержку ANSI-кодов через WinAPI), а если раскраска
недоступна (вывод перенаправлен в файл и т.п.) — просто печатает
обычный текст без кодов."""

from __future__ import annotations

import sys

_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


def _enable_windows_ansi() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        pass


def _colors_supported() -> bool:
    try:
        if not sys.stdout.isatty():
            return False
    except Exception:
        return False
    _enable_windows_ansi()
    return True


_ENABLED = _colors_supported()


def _wrap(code: str, text: str) -> str:
    if not _ENABLED:
        return text
    return f"{code}{text}{_RESET}"


def ok(text: str) -> str:
    return _wrap(_GREEN, text)


def warn(text: str) -> str:
    return _wrap(_YELLOW, text)


def err(text: str) -> str:
    return _wrap(_RED, text)


def bold(text: str) -> str:
    return _wrap(_BOLD, text)
