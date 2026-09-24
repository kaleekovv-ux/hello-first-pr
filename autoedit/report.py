"""Формирование текстового отчёта на русском (report.txt)."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .align import TimedShot
from .env import EnvStatus
from .plan import ValidationIssue

LONG_SHOT_SECONDS = 8.0
MAX_AI_SHARE = 0.30


def _format_timecode(seconds: float) -> str:
    total = max(int(seconds), 0)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


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


def write_render_report(
    path: Path,
    plan: dict[str, Any],
    timeline: list[TimedShot],
    issues: list[ValidationIssue],
    render_warnings: list[str],
    total_duration: float,
    ai_screen_time: float,
    total_screen_time: float,
) -> None:
    lines: list[str] = []
    lines.append("Отчёт сборки ролика AutoEdit")
    lines.append(f"Название ролика: {plan.get('title', '(не указано)')}")
    lines.append(f"Длительность: {total_duration:.1f} сек ({_format_timecode(total_duration)})")
    lines.append(f"Кадров собрано: {len(timeline)}")
    lines.append("")

    errors = [i for i in issues if i.level == "error"]
    warnings = [i for i in issues if i.level == "warning"]

    lines.append(f"Ошибок: {len(errors)}")
    for issue in errors:
        lines.append(f"  - {issue.format()}")
    lines.append("")

    lines.append(f"Предупреждений при рендере: {len(render_warnings) + len(warnings)}")
    for w in render_warnings:
        lines.append(f"  - {w}")
    for issue in warnings:
        lines.append(f"  - {issue.format()}")
    lines.append("")

    lines.append("Причины решений по кадрам (поле why):")
    for timed in timeline:
        why = timed.shot.get("why")
        lines.append(f"  - {timed.shot.get('id', '?')}: {why if why else '(не указано)'}")
    lines.append("")

    long_shots = [
        t for t in timeline if t.duration > LONG_SHOT_SECONDS and not t.shot.get("allow_long")
    ]
    lines.append(f"Кадры длиннее {LONG_SHOT_SECONDS:.0f} сек (без allow_long): {len(long_shots)}")
    for t in long_shots:
        lines.append(f"  - {t.shot.get('id', '?')}: {t.duration:.1f} сек")
    lines.append("")

    asset_counts = Counter(t.shot.get("asset") for t in timeline if t.shot.get("is_ai"))
    repeated = {asset: n for asset, n in asset_counts.items() if n > 1}
    lines.append(f"ИИ-клипы, использованные больше одного раза: {len(repeated)}")
    for asset, n in repeated.items():
        lines.append(f"  - {asset}: {n} раз(а)")
    lines.append("")

    ai_share = (ai_screen_time / total_screen_time * 100) if total_screen_time else 0.0
    verdict = "в норме" if ai_share <= MAX_AI_SHARE * 100 else f"выше рекомендованных {MAX_AI_SHARE*100:.0f}%"
    lines.append(f"Доля ИИ-кадров по экранному времени: {ai_share:.1f}% ({verdict})")
    lines.append("")

    lines.append(
        "Музыка приглушается автоматически под голос (sidechain-компрессия) — "
        "отдельно момент \"музыка громче голоса\" не размечается."
    )
    lines.append("")

    chapters: list[tuple[str, float]] = []
    seen_chapters: set[str] = set()
    for t in timeline:
        chapter = t.shot.get("chapter")
        if chapter and chapter not in seen_chapters:
            chapters.append((chapter, t.start))
            seen_chapters.add(chapter)
    if chapters:
        lines.append("Главы для описания на YouTube:")
        for name, start in chapters:
            lines.append(f"  {_format_timecode(start)} {name}")
        lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
