"""Сборка озвучки из отдельных кусков (например, от ElevenLabs) по
сценарию (`autoedit voice`).

ElevenLabs обычно даёт много отдельных файлов — по одному на фразу,
среди них попадаются повторно перегенерированные дубли. Эта команда
распознаёт каждый кусок, сопоставляет его с текстом сценария и
склеивает куски в правильном порядке в voice/full.mp3.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

from .align import DEFAULT_MODEL_SIZE

AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".flac", ".ogg"}
MATCH_THRESHOLD = 0.55
_TIMESTAMP_PATTERN = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}_\d{2}_\d{2})")


class VoiceError(Exception):
    """Не удалось собрать озвучку из кусков."""


@dataclass
class ChunkInfo:
    path: Path
    text: str
    timestamp: datetime | None


@dataclass
class MatchResult:
    script_line: str
    chunk: ChunkInfo | None


@dataclass
class VoiceMatchReport:
    matches: list[MatchResult] = field(default_factory=list)
    duplicates: list[tuple[str, Path]] = field(default_factory=list)
    unused_chunks: list[Path] = field(default_factory=list)


def _parse_timestamp(name: str) -> datetime | None:
    match = _TIMESTAMP_PATTERN.search(name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y-%m-%dT%H_%M_%S")
    except ValueError:
        return None


def _normalize(text: str) -> str:
    return re.sub(r"[^\w']+", " ", text.lower()).strip()


def load_script(script_path: Path) -> list[str]:
    """Читает сценарий — по одной фразе/предложению на строку, пустые
    строки пропускаются."""
    if not script_path.is_file():
        raise VoiceError(f"Файл сценария не найден: {script_path}")
    lines = [line.strip() for line in script_path.read_text(encoding="utf-8").splitlines()]
    return [line for line in lines if line]


def find_audio_chunks(folder: Path) -> list[Path]:
    if not folder.is_dir():
        raise VoiceError(f"Папка с кусками озвучки не найдена: {folder}")
    chunks = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_SUFFIXES)
    if not chunks:
        raise VoiceError(f"В папке {folder} не найдено аудиофайлов ({', '.join(sorted(AUDIO_SUFFIXES))})")
    return chunks


def transcribe_chunks(paths: list[Path], model_size: str = DEFAULT_MODEL_SIZE) -> dict[Path, str]:
    """Распознаёт текст каждого куска озвучки (модель загружается один
    раз на все куски, а не по одной на файл)."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise VoiceError(
            "Библиотека faster-whisper не установлена. Установите: pip install faster-whisper"
        ) from exc

    try:
        model = WhisperModel(model_size, device="cpu", compute_type="int8")
    except Exception as exc:  # noqa: BLE001 — сеть/загрузка модели может падать по-разному
        raise VoiceError(f"Не удалось загрузить модель распознавания речи «{model_size}»: {exc}") from exc

    texts: dict[Path, str] = {}
    for path in paths:
        try:
            segments, _info = model.transcribe(str(path))
            texts[path] = " ".join(seg.text.strip() for seg in segments).strip()
        except Exception as exc:  # noqa: BLE001 — не роняем весь процесс из-за одного файла
            texts[path] = ""
    return texts


def _chunk_sort_key(chunk: ChunkInfo) -> tuple[int, float]:
    if chunk.timestamp:
        return (1, chunk.timestamp.timestamp())
    try:
        return (0, chunk.path.stat().st_mtime)
    except OSError:
        return (0, 0.0)


def match_chunks_to_script(script_lines: list[str], chunk_texts: dict[Path, str]) -> VoiceMatchReport:
    normalized_script = [_normalize(line) for line in script_lines]
    candidates: dict[int, list[ChunkInfo]] = {i: [] for i in range(len(script_lines))}
    unused: list[Path] = []

    for path, text in chunk_texts.items():
        norm_text = _normalize(text)
        best_idx: int | None = None
        best_score = 0.0
        for i, norm_line in enumerate(normalized_script):
            if not norm_line:
                continue
            score = SequenceMatcher(None, norm_line, norm_text).ratio()
            if score > best_score:
                best_score = score
                best_idx = i
        if best_idx is not None and best_score >= MATCH_THRESHOLD:
            candidates[best_idx].append(ChunkInfo(path=path, text=text, timestamp=_parse_timestamp(path.name)))
        else:
            unused.append(path)

    report = VoiceMatchReport()
    for i, line in enumerate(script_lines):
        chunks = candidates[i]
        if not chunks:
            report.matches.append(MatchResult(script_line=line, chunk=None))
            continue

        if len(chunks) > 1:
            chunks.sort(key=_chunk_sort_key)
        chosen = chunks[-1]
        for dup in chunks[:-1]:
            report.duplicates.append((line, dup.path))
        report.matches.append(MatchResult(script_line=line, chunk=chosen))

    report.unused_chunks = unused
    return report


def concatenate_chunks(paths: list[Path], out_path: Path) -> None:
    """Склеивает куски как есть, без обрезки пауз в начале/конце."""
    if not paths:
        raise VoiceError("Нечего склеивать — нет ни одного сопоставленного куска")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    list_path = out_path.parent / "_voice_concat_list.txt"
    list_path.write_text(
        "\n".join(f"file '{p.resolve().as_posix()}'" for p in paths), encoding="utf-8"
    )
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(list_path),
        "-c:a", "libmp3lame", "-q:a", "2",
        str(out_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=False)
    finally:
        list_path.unlink(missing_ok=True)
    if result.returncode != 0:
        stderr_tail = "\n".join(result.stderr.strip().splitlines()[-8:])
        raise VoiceError(f"Не удалось склеить озвучку:\n{stderr_tail}")
