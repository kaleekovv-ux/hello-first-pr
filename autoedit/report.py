"""Формирование текстового отчёта на русском (report.txt)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .env import EnvStatus
from .plan import ValidationIssue


def write_check_report(
    path: Path,
    data: dict[str, Any],
    issues: list[ValidationIssue],
    status: EnvStatus,
) -> None:
    lines: list[str] = []
    lines.append("Отчёт проверки проекта AutoEdit")
    lines.append(f"Название ролика: {data.get('title', '(не указано)')}")
    lines.append("")

    lines.append("Окружение:")
    lines.append(f"  Python {status.python_version} — {'OK' if status.python_ok else 'нужна версия 3.11 или новее'}")
    if status.ffmpeg_ok:
        lines.append(f"  FFmpeg найден ({status.ffmpeg_version or 'версия не определена'})")
    else:
        lines.append("  FFmpeg НЕ найден — рендер работать не будет, см. подсказку в консоли")
    lines.append("")

    errors = [i for i in issues if i.level == "error"]
    warnings = [i for i in issues if i.level == "warning"]

    lines.append(f"Ошибок: {len(errors)}")
    for issue in errors:
        lines.append(f"  - {issue.format()}")
    lines.append("")

    lines.append(f"Предупреждений: {len(warnings)}")
    for issue in warnings:
        lines.append(f"  - {issue.format()}")
    lines.append("")

    shots = data.get("shots", [])
    lines.append(f"Всего кадров в плане: {len(shots)}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
