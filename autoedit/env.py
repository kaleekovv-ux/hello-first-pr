"""Проверка окружения: версия Python и наличие FFmpeg."""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass

MIN_PYTHON = (3, 11)


@dataclass
class EnvStatus:
    python_ok: bool
    python_version: str
    ffmpeg_ok: bool
    ffmpeg_version: str | None
    os_name: str


def check_python() -> tuple[bool, str]:
    version = ".".join(str(part) for part in sys.version_info[:3])
    ok = sys.version_info[:2] >= MIN_PYTHON
    return ok, version


def check_ffmpeg() -> tuple[bool, str | None]:
    path = shutil.which("ffmpeg")
    if not path:
        return False, None
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        first_line = result.stdout.splitlines()[0] if result.stdout else None
        return True, first_line
    except (OSError, subprocess.SubprocessError):
        return True, None


def ffmpeg_install_hint() -> str:
    system = platform.system()
    if system == "Darwin":
        return "Установите FFmpeg командой: brew install ffmpeg (нужен Homebrew, см. brew.sh)"
    if system == "Windows":
        return (
            "Установите FFmpeg командой: winget install ffmpeg "
            "(или скачайте сборку с ffmpeg.org и добавьте папку bin в PATH)"
        )
    return (
        "Установите FFmpeg командой: sudo apt install ffmpeg "
        "(для Ubuntu/Debian; в других дистрибутивах используйте свой пакетный менеджер)"
    )


def check_environment() -> EnvStatus:
    python_ok, python_version = check_python()
    ffmpeg_ok, ffmpeg_version = check_ffmpeg()
    return EnvStatus(
        python_ok=python_ok,
        python_version=python_version,
        ffmpeg_ok=ffmpeg_ok,
        ffmpeg_version=ffmpeg_version,
        os_name=platform.system(),
    )
