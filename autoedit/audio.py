"""Звук: голос, музыка (с приглушением под голос), звуковые эффекты,
итоговый мастеринг громкости.

Модуль работает с уже вычисленными абсолютными таймингами (секунды от
начала озвучки) — привязку anchor-фраз к этим секундам делает модуль
align, отдельно от звуковой инженерии.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TARGET_LUFS = -14.0
TARGET_TRUE_PEAK = -1.0
SAMPLE_RATE = 48000


class AudioError(Exception):
    """Не удалось собрать звуковую дорожку (обычно — сбой FFmpeg)."""


@dataclass
class MusicCue:
    asset: Path
    start: float
    end: float
    volume: float = 0.18
    fade_in: float = 0.0
    fade_out: float = 0.0


@dataclass
class SfxCue:
    asset: Path
    time: float
    volume: float = 1.0


def _run_ffmpeg(args: list[str], step_name: str) -> str:
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise AudioError(f"Не удалось запустить FFmpeg ({step_name}): {exc}") from exc
    if result.returncode != 0:
        stderr_tail = "\n".join(result.stderr.strip().splitlines()[-8:])
        raise AudioError(f"FFmpeg завершился с ошибкой на шаге «{step_name}»:\n{stderr_tail}")
    return result.stderr


def _run_ffmpeg_loudnorm_pass(args: list[str], step_name: str) -> str:
    """Как _run_ffmpeg, но без -loglevel error: сводка loudnorm (JSON)
    печатается FFmpeg на уровне info и иначе была бы не видна."""
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "info", *args]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise AudioError(f"Не удалось запустить FFmpeg ({step_name}): {exc}") from exc
    if result.returncode != 0:
        stderr_tail = "\n".join(result.stderr.strip().splitlines()[-8:])
        raise AudioError(f"FFmpeg завершился с ошибкой на шаге «{step_name}»:\n{stderr_tail}")
    return result.stderr


def _parse_loudnorm_json(stderr: str, step_name: str) -> dict[str, Any]:
    start = stderr.rfind("{")
    if start == -1:
        raise AudioError(f"Не удалось прочитать результат измерения громкости ({step_name})")
    try:
        return json.loads(stderr[start:])
    except json.JSONDecodeError as exc:
        raise AudioError(f"Не удалось разобрать результат измерения громкости ({step_name}): {exc}") from exc


def _silence(duration: float, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(
        [
            "-f", "lavfi", "-i", f"anullsrc=r={SAMPLE_RATE}:cl=stereo",
            "-t", f"{max(duration, 0.05):.3f}",
            str(out_path),
        ],
        "тишина-заглушка",
    )


def build_voice_track(voice_paths: list[Path], out_path: Path) -> None:
    """Склеивает файлы озвучки и приводит их к единому звучанию: срез
    низких частот ниже 80 Гц и лёгкий компрессор."""
    if not voice_paths:
        raise AudioError("Не указано ни одного файла озвучки")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    args: list[str] = []
    for p in voice_paths:
        args.extend(["-i", str(p)])

    processing = "highpass=f=80,acompressor=threshold=0.1:ratio=3:attack=5:release=200"
    if len(voice_paths) == 1:
        filter_complex = f"[0:a]{processing}[out]"
    else:
        inputs = "".join(f"[{i}:a]" for i in range(len(voice_paths)))
        filter_complex = f"{inputs}concat=n={len(voice_paths)}:v=0:a=1[cat];[cat]{processing}[out]"

    args.extend(["-filter_complex", filter_complex, "-map", "[out]", "-ar", str(SAMPLE_RATE), str(out_path)])
    _run_ffmpeg(args, "обработка голоса")


def build_music_bed(cues: list[MusicCue], total_duration: float, out_path: Path) -> None:
    """Раскладывает музыкальные треки по их местам на общей шкале
    времени и сводит их в одну дорожку-подложку."""
    if not cues:
        _silence(total_duration, out_path)
        return

    out_path.parent.mkdir(parents=True, exist_ok=True)
    args: list[str] = []
    filter_parts: list[str] = []
    labels: list[str] = []

    for i, cue in enumerate(cues):
        args.extend(["-i", str(cue.asset)])
        span = max(cue.end - cue.start, 0.05)
        chain = [f"atrim=0:{span:.3f}", "asetpts=PTS-STARTPTS"]
        if cue.fade_in > 0:
            chain.append(f"afade=t=in:st=0:d={cue.fade_in:.3f}")
        if cue.fade_out > 0:
            fade_start = max(span - cue.fade_out, 0.0)
            chain.append(f"afade=t=out:st={fade_start:.3f}:d={cue.fade_out:.3f}")
        chain.append(f"volume={cue.volume}")
        delay_ms = max(int(round(cue.start * 1000)), 0)
        chain.append(f"adelay={delay_ms}|{delay_ms}")
        label = f"m{i}"
        filter_parts.append(f"[{i}:a]{','.join(chain)}[{label}]")
        labels.append(label)

    mix_inputs = "".join(f"[{label}]" for label in labels)
    filter_parts.append(
        f"{mix_inputs}amix=inputs={len(labels)}:duration=longest:normalize=0,apad=whole_dur={total_duration:.3f}[out]"
    )
    filter_complex = ";".join(filter_parts)
    args.extend(["-filter_complex", filter_complex, "-map", "[out]", "-t", f"{total_duration:.3f}", str(out_path)])
    _run_ffmpeg(args, "сборка музыкальной подложки")


def apply_silence_windows(in_path: Path, windows: list[tuple[float, float]], out_path: Path) -> None:
    """Приглушает музыку до тишины в заданных интервалах (silence_before
    перед раскрытием и т.п.)."""
    if not windows:
        if in_path != out_path:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            _run_ffmpeg(["-i", str(in_path), "-c", "copy", str(out_path)], "копирование музыкальной подложки")
        return

    conditions = "+".join(f"between(t,{start:.3f},{end:.3f})" for start, end in windows)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(
        [
            "-i", str(in_path),
            "-af", f"volume=enable='{conditions}':volume=0",
            str(out_path),
        ],
        "тишина перед раскрытием",
    )


def duck_music_under_voice(voice_path: Path, music_path: Path, out_path: Path) -> None:
    """Автоматически приглушает музыку, когда звучит голос (sidechain)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    filter_complex = (
        "[1:a][0:a]sidechaincompress=threshold=0.05:ratio=8:attack=5:release=300[ducked]"
    )
    _run_ffmpeg(
        [
            "-i", str(voice_path),
            "-i", str(music_path),
            "-filter_complex", filter_complex,
            "-map", "[ducked]",
            str(out_path),
        ],
        "приглушение музыки под голос",
    )


def build_sfx_bed(cues: list[SfxCue], total_duration: float, out_path: Path) -> None:
    if not cues:
        _silence(total_duration, out_path)
        return

    out_path.parent.mkdir(parents=True, exist_ok=True)
    args: list[str] = []
    filter_parts: list[str] = []
    labels: list[str] = []

    for i, cue in enumerate(cues):
        args.extend(["-i", str(cue.asset)])
        delay_ms = max(int(round(cue.time * 1000)), 0)
        chain = [f"volume={cue.volume}", f"adelay={delay_ms}|{delay_ms}"]
        label = f"s{i}"
        filter_parts.append(f"[{i}:a]{','.join(chain)}[{label}]")
        labels.append(label)

    mix_inputs = "".join(f"[{label}]" for label in labels)
    filter_parts.append(
        f"{mix_inputs}amix=inputs={len(labels)}:duration=longest:normalize=0,apad=whole_dur={total_duration:.3f}[out]"
    )
    filter_complex = ";".join(filter_parts)
    args.extend(["-filter_complex", filter_complex, "-map", "[out]", "-t", f"{total_duration:.3f}", str(out_path)])
    _run_ffmpeg(args, "сборка звуковых эффектов")


def mix_final_audio(
    voice_path: Path,
    music_path: Path,
    sfx_path: Path,
    total_duration: float,
    out_path: Path,
    work_dir: Path,
) -> float:
    """Сводит голос, музыку и эффекты и мастерит громкость для YouTube
    (-14 LUFS, true peak -1 dBTP) двухпроходным loudnorm: сначала
    измеряет реальную громкость смеси, потом применяет коррекцию по
    измеренным значениям — это заметно точнее однопроходного режима
    (однопроходный давал ошибку в 2-3 LUFS от цели).

    Возвращает фактически достигнутую интегральную громкость (LUFS) —
    её показывает второй проход loudnorm по своим же измерениям, без
    отдельного третьего прохода-проверки.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    mixed_path = work_dir / "_mixed_raw.wav"

    _run_ffmpeg(
        [
            "-i", str(voice_path),
            "-i", str(music_path),
            "-i", str(sfx_path),
            "-filter_complex", "[0:a][1:a][2:a]amix=inputs=3:duration=longest:normalize=0[out]",
            "-map", "[out]",
            "-t", f"{total_duration:.3f}",
            "-ar", str(SAMPLE_RATE),
            str(mixed_path),
        ],
        "предварительное сведение",
    )

    measure_stderr = _run_ffmpeg_loudnorm_pass(
        [
            "-i", str(mixed_path),
            "-af", f"loudnorm=I={TARGET_LUFS}:TP={TARGET_TRUE_PEAK}:LRA=11:print_format=json",
            "-f", "null", "-",
        ],
        "измерение громкости",
    )
    measured = _parse_loudnorm_json(measure_stderr, "измерение громкости")

    apply_filter = (
        f"loudnorm=I={TARGET_LUFS}:TP={TARGET_TRUE_PEAK}:LRA=11:"
        f"measured_I={measured['input_i']}:measured_TP={measured['input_tp']}:"
        f"measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}:"
        f"offset={measured['target_offset']}:linear=true:print_format=json"
    )
    apply_stderr = _run_ffmpeg_loudnorm_pass(
        ["-i", str(mixed_path), "-af", apply_filter, "-ar", str(SAMPLE_RATE), str(out_path)],
        "финальный мастеринг громкости",
    )
    achieved = _parse_loudnorm_json(apply_stderr, "финальный мастеринг громкости")

    try:
        return float(achieved["output_i"])
    except (KeyError, ValueError):
        return float(measured.get("output_i", TARGET_LUFS))


def render_audio(
    voice_paths: list[Path],
    music_cues: list[MusicCue],
    sfx_cues: list[SfxCue],
    silence_windows: list[tuple[float, float]],
    total_duration: float,
    out_path: Path,
    work_dir: Path,
) -> float:
    """Полный конвейер: голос → музыка (+тишина, +приглушение) → эффекты
    → финальное сведение с мастерингом громкости.

    Возвращает фактически достигнутую громкость (LUFS) финального файла.
    """
    work_dir.mkdir(parents=True, exist_ok=True)

    voice_path = work_dir / "voice_processed.wav"
    build_voice_track(voice_paths, voice_path)

    music_raw_path = work_dir / "music_raw.wav"
    build_music_bed(music_cues, total_duration, music_raw_path)

    music_muted_path = work_dir / "music_muted.wav"
    apply_silence_windows(music_raw_path, silence_windows, music_muted_path)

    music_ducked_path = work_dir / "music_ducked.wav"
    duck_music_under_voice(voice_path, music_muted_path, music_ducked_path)

    sfx_path = work_dir / "sfx_bed.wav"
    build_sfx_bed(sfx_cues, total_duration, sfx_path)

    return mix_final_audio(voice_path, music_ducked_path, sfx_path, total_duration, out_path, work_dir)
