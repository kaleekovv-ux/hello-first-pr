"""Черновик монтажного плана (`autoedit draft`).

Расставляет кадры по фразам сценария: для каждой фразы находит в
распознанной озвучке место, где она звучит, и берёт anchor ДОСЛОВНО из
распознанного текста — так `autoedit check` гарантированно найдёт его на
том же месте. Фразы длиннее MAX_SHOT_SECONDS делятся на несколько кадров.

Творческих решений черновик не принимает: asset, motion, why и прочее
остаются пустыми — их заполняет человек (или Claude по навыку
montage-plan).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .align import Word, find_anchor

MAX_SHOT_SECONDS = 8.0
TARGET_SPLIT_SECONDS = 6.0
PROBE_WORDS = 5
MIN_ANCHOR_WORDS = 4
MAX_ANCHOR_WORDS = 8


@dataclass
class _ShotStart:
    word_index: int
    line: str


def _locate_lines(script_lines: list[str], words: list[Word]) -> tuple[list[tuple[str, int]], list[str]]:
    """Находит индекс первого слова каждой фразы сценария в озвучке."""
    located: list[tuple[str, int]] = []
    missing: list[str] = []
    pointer = 0
    for line in script_lines:
        tokens = line.split()
        if not tokens:
            continue
        match, next_index = find_anchor(words, " ".join(tokens[:PROBE_WORDS]), pointer)
        if match is None:
            missing.append(line)
            continue
        start_index = next_index - 1
        located.append((line, start_index))
        # Следующую фразу ищем не сразу со следующего слова, а через
        # половину текущей — иначе похожее начало следующей фразы может
        # совпасть внутри ещё не закончившейся текущей.
        pointer = min(start_index + max(1, len(tokens) // 2), len(words))
    return located, missing


def _split_long_segments(located: list[tuple[str, int]], words: list[Word]) -> list[_ShotStart]:
    starts: list[_ShotStart] = []
    for i, (line, start_index) in enumerate(located):
        end_index = located[i + 1][1] if i + 1 < len(located) else len(words)
        seg_start = words[start_index].start
        seg_end = words[end_index].start if end_index < len(words) else words[-1].end
        span = seg_end - seg_start
        parts = math.ceil(span / TARGET_SPLIT_SECONDS) if span > MAX_SHOT_SECONDS else 1

        chosen = [start_index]
        for j in range(1, parts):
            target = seg_start + j * span / parts
            candidates = range(chosen[-1] + 1, end_index)
            if not candidates:
                break
            chosen.append(min(candidates, key=lambda k: abs(words[k].start - target)))

        for part, word_index in enumerate(chosen, start=1):
            label = line if len(chosen) == 1 else f"{line} (часть {part}/{len(chosen)})"
            starts.append(_ShotStart(word_index=word_index, line=label))
    return starts


def _phrase(words: list[Word], start: int, count: int) -> str:
    text = " ".join(w.text for w in words[start : start + count])
    return text.strip(" ,.;:!?\"'«»—-")


def _pick_anchors(starts: list[_ShotStart], words: list[Word]) -> tuple[list[str], list[str]]:
    """Подбирает для каждого кадра anchor, который поиск `check` (строго
    вперёд, как в align.resolve_anchor_starts) найдёт ровно на нужном слове.
    При повторяющихся фразах anchor удлиняется, пока не станет однозначным."""
    anchors: list[str] = []
    warnings: list[str] = []
    check_pointer = 0
    for shot in starts:
        target_start = words[shot.word_index].start
        chosen: str | None = None
        for count in range(MIN_ANCHOR_WORDS, MAX_ANCHOR_WORDS + 1):
            candidate = _phrase(words, shot.word_index, count)
            match, _ = find_anchor(words, candidate, check_pointer)
            if match is not None and abs(match.start - target_start) < 1e-6:
                chosen = candidate
                break
        if chosen is None:
            chosen = _phrase(words, shot.word_index, MIN_ANCHOR_WORDS)
            warnings.append(
                f'anchor "{chosen}" может найтись не на своём месте (похожая фраза встречается раньше) — проверьте его'
            )
        anchors.append(chosen)
        match, next_index = find_anchor(words, chosen, check_pointer)
        if match is not None:
            check_pointer = next_index
    return anchors, warnings


def build_draft_plan(
    script_lines: list[str], words: list[Word], title: str, voice_files: list[str]
) -> tuple[dict[str, Any], list[str]]:
    """Возвращает (черновик plan.json, предупреждения)."""
    if not words:
        raise ValueError("Озвучка не распознана — нет ни одного слова, черновик построить не из чего")

    located, missing = _locate_lines(script_lines, words)
    starts = _split_long_segments(located, words)
    anchors, warnings = _pick_anchors(starts, words)

    for line in missing:
        warnings.append(f'фраза сценария не найдена в озвучке, кадр для неё не создан: "{line}"')

    width = max(2, len(str(len(starts))))
    shots = [
        {
            "id": f"S{i:0{width}d}",
            "anchor": anchor,
            "asset": "",
            "motion": "static",
            "why": "",
            "line": shot.line,
        }
        for i, (shot, anchor) in enumerate(zip(starts, anchors), start=1)
    ]
    plan = {"title": title, "voice": voice_files, "shots": shots}
    return plan, warnings
