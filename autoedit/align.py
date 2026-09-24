"""Привязка кадров к словам диктора.

Распознаёт озвучку через faster-whisper (таймкоды по словам), ищет
anchor-фразы нечётким поиском по порядку, кэширует результат в
project/output/alignment.json и строит из этого таймлайн (во сколько
секунд от начала озвучки начинается и сколько длится каждый кадр).
Также генерирует subtitles.srt.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from .plan import ValidationIssue

DEFAULT_MODEL_SIZE = "small"
ANCHOR_MATCH_THRESHOLD = 0.6
PAUSE_GAP_SECONDS = 0.5
SUBTITLE_MAX_CHARS_PER_LINE = 42
SUBTITLE_MAX_LINES = 2
SUBTITLE_MIN_SECONDS = 1.0
SCRIPT_MATCH_THRESHOLD = 0.75


class AlignError(Exception):
    """Не удалось распознать озвучку."""


@dataclass
class Word:
    text: str
    start: float
    end: float


@dataclass
class AnchorMatch:
    start: float
    end: float
    score: float


@dataclass
class TimedShot:
    shot: dict[str, Any]
    start: float
    duration: float


def _normalize(text: str) -> str:
    return re.sub(r"[^\w']+", " ", text.lower()).strip()


_RUSSIAN_NUMBER_WORDS = {
    "ноль": 0, "один": 1, "одна": 1, "одно": 1, "одни": 1, "два": 2, "две": 2,
    "три": 3, "четыре": 4, "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9,
    "десять": 10, "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14,
    "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18, "девятнадцать": 19,
    "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50, "шестьдесят": 60,
    "семьдесят": 70, "восемьдесят": 80, "девяносто": 90,
    "сто": 100, "двести": 200, "триста": 300, "четыреста": 400, "пятьсот": 500,
    "шестьсот": 600, "семьсот": 700, "восемьсот": 800, "девятьсот": 900,
}
_RUSSIAN_SCALE_WORDS = {
    "тысяча": 1000, "тысячи": 1000, "тысяч": 1000,
    "миллион": 1_000_000, "миллиона": 1_000_000, "миллионов": 1_000_000,
}


def _parse_number_words(tokens: list[str], start: int) -> tuple[int, int] | None:
    """Пытается разобрать русские числительные, начиная с tokens[start]
    (например ["четыреста", "тридцать", "восемь"] -> 438). Возвращает
    (значение, индекс следующего токена после числительного) или None."""
    total = 0
    current = 0
    matched = False
    i = start
    while i < len(tokens):
        token = tokens[i]
        if token in _RUSSIAN_NUMBER_WORDS:
            current += _RUSSIAN_NUMBER_WORDS[token]
            matched = True
            i += 1
        elif token in _RUSSIAN_SCALE_WORDS:
            current = current or 1
            total += current * _RUSSIAN_SCALE_WORDS[token]
            current = 0
            matched = True
            i += 1
        else:
            break
    if not matched:
        return None
    return total + current, i


def _numbers_to_digits(text: str) -> str:
    """Заменяет русские числительные словами на цифры — только для
    нечёткого сравнения текста, чтобы "четыреста тридцать восемь" и
    "438" считались похожими."""
    tokens = text.split()
    result: list[str] = []
    i = 0
    while i < len(tokens):
        parsed = _parse_number_words(tokens, i)
        if parsed is not None:
            value, next_i = parsed
            result.append(str(value))
            i = next_i
        else:
            result.append(tokens[i])
            i += 1
    return " ".join(result)


def _voice_files_fingerprint(voice_paths: list[Path], model_size: str) -> str:
    hasher = hashlib.sha256()
    hasher.update(model_size.encode("utf-8"))
    for path in sorted(voice_paths, key=str):
        stat = path.stat()
        hasher.update(str(path).encode("utf-8"))
        hasher.update(str(stat.st_size).encode("utf-8"))
        hasher.update(str(int(stat.st_mtime)).encode("utf-8"))
    return hasher.hexdigest()


def transcribe_words(voice_paths: list[Path], model_size: str = DEFAULT_MODEL_SIZE) -> list[Word]:
    """Распознаёт речь во всех файлах озвучки по порядку, склеивая тайминги."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise AlignError(
            "Библиотека faster-whisper не установлена. Установите её командой: "
            "pip install faster-whisper"
        ) from exc

    for voice_path in voice_paths:
        if not voice_path.is_file():
            raise AlignError(f"Файл озвучки не найден: {voice_path}")

    try:
        model = WhisperModel(model_size, device="cpu", compute_type="int8")
        words: list[Word] = []
        offset = 0.0
        for voice_path in voice_paths:
            segments, _info = model.transcribe(str(voice_path), word_timestamps=True)
            last_end = 0.0
            for segment in segments:
                for w in segment.words or []:
                    text = w.word.strip()
                    if not text:
                        continue
                    words.append(Word(text=text, start=w.start + offset, end=w.end + offset))
                    last_end = max(last_end, w.end)
            offset += last_end
    except AlignError:
        raise
    except Exception as exc:
        raise AlignError(
            f"Не удалось распознать озвучку моделью «{model_size}»: {exc}\n"
            f"Подсказка: при первом запуске нужен интернет — модель скачивается один раз "
            f"с Hugging Face и затем работает офлайн."
        ) from exc
    return words


def load_or_transcribe(
    project_dir: Path, voice_files: list[str], model_size: str = DEFAULT_MODEL_SIZE
) -> list[Word]:
    """Берёт распознанные слова из кэша (output/alignment.json), если файлы
    озвучки не изменились, иначе распознаёт заново и сохраняет кэш."""
    voice_paths = [project_dir / v for v in voice_files]
    cache_path = project_dir / "output" / "alignment.json"
    fingerprint = _voice_files_fingerprint(voice_paths, model_size)

    if cache_path.is_file():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cached = None
        if cached and cached.get("fingerprint") == fingerprint:
            return [Word(**w) for w in cached["words"]]

    words = transcribe_words(voice_paths, model_size)

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(
            {
                "fingerprint": fingerprint,
                "model_size": model_size,
                "words": [asdict(w) for w in words],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return words


def find_anchor(
    words: list[Word], phrase: str, search_from_index: int = 0
) -> tuple[AnchorMatch | None, int]:
    """Ищет фразу phrase среди words начиная с индекса search_from_index
    (чтобы anchor-ы находились по порядку). Возвращает совпадение (или
    None) и индекс, с которого нужно продолжать поиск следующего anchor.
    """
    phrase_tokens = _normalize(phrase).split()
    if not phrase_tokens or search_from_index >= len(words):
        return None, search_from_index

    target = " ".join(phrase_tokens)
    window_sizes = sorted({n for n in (len(phrase_tokens) - 1, len(phrase_tokens), len(phrase_tokens) + 1) if n > 0})

    best: AnchorMatch | None = None
    best_next_index = search_from_index

    for window in window_sizes:
        for i in range(search_from_index, len(words) - window + 1):
            chunk = words[i : i + window]
            candidate = " ".join(_normalize(w.text) for w in chunk)
            score = SequenceMatcher(None, target, candidate).ratio()
            if best is None or score > best.score:
                best = AnchorMatch(start=chunk[0].start, end=chunk[-1].end, score=score)
                best_next_index = i + 1

    if best is not None and best.score >= ANCHOR_MATCH_THRESHOLD:
        return best, best_next_index
    return None, search_from_index


def resolve_anchor_starts(
    shots: list[dict[str, Any]], words: list[Word]
) -> tuple[dict[str, float], list[ValidationIssue]]:
    """Находит время начала для всех кадров, заданных через anchor."""
    issues: list[ValidationIssue] = []
    starts: dict[str, float] = {}
    search_index = 0

    for shot in shots:
        anchor = shot.get("anchor")
        if not anchor:
            continue
        shot_id = shot.get("id", "?")
        match, search_index = find_anchor(words, anchor, search_index)
        if match is None:
            issues.append(
                ValidationIssue(
                    "error",
                    shot_id,
                    f'слова "{anchor}" не найдены в озвучке (или встречаются не по порядку)',
                )
            )
            continue
        starts[shot_id] = match.start

    return starts, issues


def _describe_shot_position(shot: dict[str, Any]) -> str:
    anchor = shot.get("anchor")
    if anchor:
        return f'"{anchor}"'
    return f'start={shot.get("start")}'


def build_timeline(
    plan: dict[str, Any], words: list[Word], allow_reorder: bool = False
) -> tuple[list[TimedShot], list[ValidationIssue]]:
    """Строит финальный таймлайн: во сколько секунд начинается и сколько
    длится каждый кадр плана. Кадры сортируются по времени начала.

    Если кадр в плане идёт раньше другого, а по факту звучит позже него
    (порядок в plan.json не совпадает с порядком в озвучке) — это
    считается ошибкой (программа раньше молча переставляла такие кадры,
    и несовпадение легко было не заметить). allow_reorder=True снижает
    это до предупреждения.
    """
    shots = plan.get("shots", [])
    anchor_starts, issues = resolve_anchor_starts(shots, words)

    resolved: list[tuple[dict[str, Any], float]] = []
    for shot in shots:
        shot_id = shot.get("id", "?")
        if shot.get("anchor"):
            start = anchor_starts.get(shot_id)
            if start is None:
                continue
        else:
            start = shot.get("start")
            if start is None:
                continue
        resolved.append((shot, float(start)))

    for i in range(1, len(resolved)):
        prev_shot, prev_start = resolved[i - 1]
        shot, start = resolved[i]
        if start < prev_start:
            message = (
                f"Кадр {shot.get('id', '?')} ({_describe_shot_position(shot)}) найден на {start:.1f} сек — "
                f"это раньше кадра {prev_shot.get('id', '?')} ({prev_start:.1f} сек), хотя в плане "
                f"{shot.get('id', '?')} идёт после {prev_shot.get('id', '?')}. Порядок кадров в plan.json "
                f"не совпадает с порядком в озвучке — проверьте, в каком порядке склеены куски голоса, "
                f"или переставьте кадр в plan.json (или запустите с --allow-reorder, если это осознанный выбор)."
            )
            issues.append(ValidationIssue("warning" if allow_reorder else "error", shot.get("id", "?"), message))

    resolved.sort(key=lambda pair: pair[1])

    timed: list[TimedShot] = []
    for index, (shot, start) in enumerate(resolved):
        explicit_duration = shot.get("duration")
        if explicit_duration is not None:
            duration = float(explicit_duration)
        elif index + 1 < len(resolved):
            duration = resolved[index + 1][1] - start
        elif words:
            duration = max(words[-1].end - start, 0.1)
        else:
            duration = 0.1

        if duration <= 0:
            issues.append(
                ValidationIssue(
                    "error",
                    shot.get("id", "?"),
                    f"вычисленная длительность кадра не положительна ({duration:.2f} сек) — "
                    f"проверьте порядок anchor-фраз или явные start/duration",
                )
            )
            duration = 0.1

        timed.append(TimedShot(shot=shot, start=start, duration=duration))

    return timed, issues


# ---------------------------------------------------------------------------
# Субтитры
# ---------------------------------------------------------------------------


@dataclass
class Cue:
    start: float
    end: float
    text: str


def _format_timestamp(seconds: float) -> str:
    millis = max(round(seconds * 1000), 0)
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _wrap_two_lines(text: str) -> str:
    if len(text) <= SUBTITLE_MAX_CHARS_PER_LINE:
        return text
    words = text.split()
    line1: list[str] = []
    length = 0
    for w in words:
        add = len(w) + (1 if line1 else 0)
        if length + add > SUBTITLE_MAX_CHARS_PER_LINE:
            break
        line1.append(w)
        length += add
    if not line1:
        line1 = [words[0]]
    line2 = " ".join(words[len(line1) :])
    if len(line2) > SUBTITLE_MAX_CHARS_PER_LINE:
        line2 = line2[: SUBTITLE_MAX_CHARS_PER_LINE - 1].rstrip() + "…"
    return "\n".join([" ".join(line1), line2]) if line2 else " ".join(line1)


def _group_into_cues(words: list[Word]) -> list[Cue]:
    """Группирует слова в реплики субтитров (пока без переноса строк —
    text здесь ещё "сырой", в одну строку)."""
    cues: list[Cue] = []
    current: list[Word] = []

    def flush() -> None:
        if not current:
            return
        text = " ".join(w.text for w in current)
        cues.append(Cue(start=current[0].start, end=current[-1].end, text=text))

    for word in words:
        if current:
            gap = word.start - current[-1].end
            ends_sentence = current[-1].text.rstrip()[-1:] in ".!?"
            candidate_len = len(" ".join(w.text for w in current + [word]))
            too_long = candidate_len > SUBTITLE_MAX_CHARS_PER_LINE * SUBTITLE_MAX_LINES
            if gap > PAUSE_GAP_SECONDS or ends_sentence or too_long:
                flush()
                current = [word]
                continue
        current.append(word)

    flush()
    return cues


def _enforce_min_duration(cues: list[Cue]) -> None:
    """Не даёт реплике держаться на экране короче SUBTITLE_MIN_SECONDS,
    не залезая при этом на начало следующей реплики."""
    for i, cue in enumerate(cues):
        if cue.end - cue.start >= SUBTITLE_MIN_SECONDS:
            continue
        desired_end = cue.start + SUBTITLE_MIN_SECONDS
        next_start = cues[i + 1].start if i + 1 < len(cues) else None
        if next_start is not None:
            desired_end = min(desired_end, max(next_start - 0.05, cue.end))
        cue.end = max(cue.end, desired_end)


def _best_script_match(cue_text: str, script_lines: list[str]) -> str | None:
    # Числа сравниваем в цифрах: whisper обычно распознаёт их словами
    # ("четыреста тридцать восемь"), а в сценарии они чаще написаны
    # цифрами ("438") — без этого такие реплики никогда бы не совпали.
    normalized_cue = _numbers_to_digits(_normalize(cue_text))
    best_line: str | None = None
    best_score = 0.0
    for line in script_lines:
        normalized_line = _numbers_to_digits(_normalize(line))
        score = SequenceMatcher(None, normalized_line, normalized_cue).ratio()
        if score > best_score:
            best_score = score
            best_line = line
    return best_line if best_score >= SCRIPT_MATCH_THRESHOLD else None


def generate_srt(words: list[Word], path: Path, script_lines: list[str] | None = None) -> None:
    """Пишет subtitles.srt. Если передан script_lines (текст сценария,
    по фразе на строку — как в autoedit voice), реплика, уверенно
    совпавшая с целой строкой сценария, показывается словами именно из
    сценария (в т.ч. с цифрами вроде "438", а не "four hundred and
    thirty-eight", если так написано в сценарии) — распознанный текст
    используется только для таймингов и переноса такой реплики не
    трогает."""
    cues = _group_into_cues(words)
    _enforce_min_duration(cues)

    lines: list[str] = []
    for i, cue in enumerate(cues, start=1):
        matched = _best_script_match(cue.text, script_lines) if script_lines else None
        display_text = _wrap_two_lines(matched if matched is not None else cue.text)
        lines.append(str(i))
        lines.append(f"{_format_timestamp(cue.start)} --> {_format_timestamp(cue.end)}")
        lines.append(display_text)
        lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
