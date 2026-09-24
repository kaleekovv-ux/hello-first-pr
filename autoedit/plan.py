"""Чтение и проверка монтажного плана (plan.json).

Этап 1 проверяет план и файлы проекта на уровне схемы: обязательные
поля, допустимые значения, существование файлов. Проверка того, что
anchor-фразы действительно встречаются в озвучке (и в правильном
порядке), появится на этапе 2 вместе с распознаванием речи
(faster-whisper) — пока такие anchor-кадры пропускаются с пометкой
в отчёте.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MOTIONS = {"zoom_in", "zoom_out", "pan_left", "pan_right", "rise", "static"}
TRANSITIONS = {"cut", "crossfade", "fade_from_black"}
BEATS = {"preparation", "focus", "pause", "reveal", "consequence"}


class PlanError(Exception):
    """План не удалось загрузить (файл не найден или битый JSON)."""


@dataclass
class ValidationIssue:
    level: str  # "error" | "warning"
    shot_id: str | None
    message: str

    def format(self) -> str:
        prefix = f"Кадр {self.shot_id}: " if self.shot_id else ""
        return f"{prefix}{self.message}"


def load_plan_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise PlanError(
            f"Файл плана не найден: {path}\n"
            f"Подсказка: в папке проекта должен быть файл plan.json."
        )
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PlanError(f"Не удалось прочитать файл плана {path}: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PlanError(
            f"Файл плана {path} повреждён (невалидный JSON): "
            f"строка {exc.lineno}, столбец {exc.colno} — {exc.msg}\n"
            f"Подсказка: проверьте файл на пропущенные запятые или кавычки, "
            f"например в редакторе с подсветкой JSON."
        ) from exc
    if not isinstance(data, dict):
        raise PlanError(f"Файл плана {path} должен содержать JSON-объект (словарь), а не {type(data).__name__}")
    return data


def _asset_exists(project_dir: Path, rel_path: Any) -> bool:
    if not isinstance(rel_path, str) or not rel_path:
        return False
    return (project_dir / rel_path).is_file()


def _check_asset_field(
    project_dir: Path, shot_id: str | None, rel_path: Any, issues: list[ValidationIssue]
) -> None:
    if not isinstance(rel_path, str) or not rel_path:
        issues.append(ValidationIssue("error", shot_id, "не указан путь к файлу (asset)"))
        return
    if not _asset_exists(project_dir, rel_path):
        issues.append(ValidationIssue("error", shot_id, f"файл не найден: {rel_path}"))


def _check_crop(shot_id: str, crop: Any, issues: list[ValidationIssue]) -> None:
    if crop is None:
        return
    if not (isinstance(crop, list) and len(crop) == 4 and all(isinstance(v, (int, float)) for v in crop)):
        issues.append(ValidationIssue("error", shot_id, 'поле "crop" должно быть списком из 4 чисел [x, y, w, h]'))
        return
    x, y, w, h = crop
    if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < w <= 1 and 0 < h <= 1 and x + w <= 1.001 and y + h <= 1.001):
        issues.append(
            ValidationIssue(
                "warning",
                shot_id,
                f'поле "crop" {crop} выходит за границы кадра (значения должны быть долями от 0 до 1)',
            )
        )


def _check_overlays(
    project_dir: Path, shot_id: str, overlays: Any, issues: list[ValidationIssue]
) -> None:
    if overlays is None:
        return
    if not isinstance(overlays, list):
        issues.append(ValidationIssue("error", shot_id, 'поле "overlays" должно быть списком'))
        return
    for i, overlay in enumerate(overlays):
        label = f"{shot_id} (оверлей {i + 1})"
        if not isinstance(overlay, dict):
            issues.append(ValidationIssue("error", label, "оверлей должен быть объектом"))
            continue
        _check_asset_field(project_dir, label, overlay.get("asset"), issues)


def _check_sfx(project_dir: Path, shot_id: str, sfx_list: Any, issues: list[ValidationIssue]) -> None:
    if sfx_list is None:
        return
    if not isinstance(sfx_list, list):
        issues.append(ValidationIssue("error", shot_id, 'поле "sfx" должно быть списком'))
        return
    for i, sfx in enumerate(sfx_list):
        label = f"{shot_id} (звук {i + 1})"
        if not isinstance(sfx, dict):
            issues.append(ValidationIssue("error", label, "звуковой эффект должен быть объектом"))
            continue
        _check_asset_field(project_dir, label, sfx.get("asset"), issues)


def _check_shot(project_dir: Path, shot: Any, index: int, seen_ids: set[str], issues: list[ValidationIssue]) -> None:
    if not isinstance(shot, dict):
        issues.append(ValidationIssue("error", None, f"кадр №{index + 1} должен быть объектом"))
        return

    shot_id = shot.get("id")
    if not isinstance(shot_id, str) or not shot_id:
        issues.append(ValidationIssue("error", None, f'у кадра №{index + 1} не указано поле "id"'))
        shot_id = f"№{index + 1}"
    elif shot_id in seen_ids:
        issues.append(ValidationIssue("error", shot_id, "такой id уже используется другим кадром"))
    else:
        seen_ids.add(shot_id)

    _check_asset_field(project_dir, shot_id, shot.get("asset"), issues)

    has_anchor = isinstance(shot.get("anchor"), str) and shot.get("anchor")
    has_explicit_time = isinstance(shot.get("start"), (int, float)) and isinstance(shot.get("duration"), (int, float))
    if not has_anchor and not has_explicit_time:
        issues.append(
            ValidationIssue(
                "error",
                shot_id,
                'нужно указать либо "anchor" (привязку к словам диктора), либо оба поля "start" и "duration"',
            )
        )
    if has_anchor:
        issues.append(
            ValidationIssue(
                "warning",
                shot_id,
                'привязка по "anchor" будет проверена по озвучке на этапе 2 (распознавание речи) — пока не проверяется',
            )
        )

    motion = shot.get("motion")
    if motion is not None and motion not in MOTIONS:
        issues.append(
            ValidationIssue("error", shot_id, f'неизвестное значение "motion": {motion!r} (допустимо: {sorted(MOTIONS)})')
        )

    transition_in = shot.get("transition_in")
    if transition_in is not None and transition_in not in TRANSITIONS:
        issues.append(
            ValidationIssue(
                "error",
                shot_id,
                f'неизвестное значение "transition_in": {transition_in!r} (допустимо: {sorted(TRANSITIONS)})',
            )
        )

    beat = shot.get("beat")
    if beat is not None and beat not in BEATS:
        issues.append(
            ValidationIssue("error", shot_id, f'неизвестное значение "beat": {beat!r} (допустимо: {sorted(BEATS)})')
        )

    if not shot.get("why"):
        issues.append(
            ValidationIssue("warning", shot_id, 'не указано поле "why" — для отчёта рекомендуется объяснить причину кадра')
        )

    for field in ("audio_lead", "audio_lag", "silence_before", "handles", "min_ai_visible_seconds"):
        value = shot.get(field)
        if value is not None and (not isinstance(value, (int, float)) or value < 0):
            issues.append(ValidationIssue("error", shot_id, f'поле "{field}" должно быть неотрицательным числом'))

    _check_crop(shot_id, shot.get("crop"), issues)
    _check_overlays(project_dir, shot_id, shot.get("overlays"), issues)
    _check_sfx(project_dir, shot_id, shot.get("sfx"), issues)


def _check_music(project_dir: Path, music_list: Any, issues: list[ValidationIssue]) -> None:
    if music_list is None:
        return
    if not isinstance(music_list, list):
        issues.append(ValidationIssue("error", None, 'поле "music" должно быть списком'))
        return
    for i, track in enumerate(music_list):
        label = f"музыка №{i + 1}"
        if not isinstance(track, dict):
            issues.append(ValidationIssue("error", label, "запись о музыке должна быть объектом"))
            continue
        _check_asset_field(project_dir, label, track.get("asset"), issues)
        if not track.get("from_anchor") or not track.get("to_anchor"):
            issues.append(ValidationIssue("error", label, 'нужно указать "from_anchor" и "to_anchor"'))


def validate_plan(data: dict[str, Any], project_dir: Path) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    if not data.get("title"):
        issues.append(ValidationIssue("error", None, 'в плане не указано поле "title"'))

    voice = data.get("voice")
    if not isinstance(voice, list) or not voice:
        issues.append(ValidationIssue("error", None, 'поле "voice" должно быть непустым списком файлов озвучки'))
    else:
        for voice_file in voice:
            if not _asset_exists(project_dir, voice_file):
                issues.append(ValidationIssue("error", None, f"файл озвучки не найден: {voice_file}"))

    shots = data.get("shots")
    if not isinstance(shots, list) or not shots:
        issues.append(ValidationIssue("error", None, 'поле "shots" должно быть непустым списком кадров'))
        shots = []

    seen_ids: set[str] = set()
    for i, shot in enumerate(shots):
        _check_shot(project_dir, shot, i, seen_ids, issues)

    _check_music(project_dir, data.get("music"), issues)

    look = data.get("look")
    if look is not None:
        if not isinstance(look, dict):
            issues.append(ValidationIssue("error", None, 'поле "look" должно быть объектом'))
        else:
            lut = look.get("lut")
            if lut and not _asset_exists(project_dir, lut):
                issues.append(ValidationIssue("error", None, f"LUT-файл не найден: {lut}"))

    end_screen = data.get("end_screen")
    if end_screen is not None:
        if not isinstance(end_screen, dict):
            issues.append(ValidationIssue("error", None, 'поле "end_screen" должно быть объектом'))
        else:
            _check_asset_field(project_dir, "end_screen", end_screen.get("asset"), issues)
            if not isinstance(end_screen.get("duration"), (int, float)) or end_screen.get("duration", 0) <= 0:
                issues.append(ValidationIssue("error", "end_screen", 'поле "duration" должно быть положительным числом'))

    return issues
