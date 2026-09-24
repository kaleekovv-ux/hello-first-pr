"""Сборка готового ролика: от plan.json до preview.mp4 / final.mp4.

Связывает вместе align (тайминги), video (видеоряд), audio (звук) и
report (итоговый отчёт). Кадры рендерятся по отдельности и кэшируются
по хэшу их параметров — правка одного кадра в плане не пересчитывает
весь ролик заново.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import align, audio, export, report, video
from .plan import ValidationIssue

FPS = 30
PREVIEW_SIZE = (960, 540)
FINAL_SIZE = (1920, 1080)


class RenderError(Exception):
    """Не удалось собрать ролик."""


@dataclass
class RenderMode:
    name: str  # "preview" | "final"
    width: int
    height: int
    video_preset: str
    crf: int
    audio_bitrate: str
    no_fx: bool = False
    no_sound: bool = False
    audio_only: bool = False


def preview_mode() -> RenderMode:
    return RenderMode("preview", *PREVIEW_SIZE, video_preset="veryfast", crf=28, audio_bitrate="192k")


def final_mode() -> RenderMode:
    return RenderMode("final", *FINAL_SIZE, video_preset="slow", crf=18, audio_bitrate="320k")


@dataclass
class RenderSummary:
    output_path: Path
    warnings: list[str] = field(default_factory=list)
    issues: list[ValidationIssue] = field(default_factory=list)
    total_duration: float = 0.0
    ai_screen_time: float = 0.0
    total_screen_time: float = 0.0


def _hash_payload(payload: Any) -> str:
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _progress(callback: Callable[[str], None] | None, message: str) -> None:
    if callback:
        callback(message)


def _strip_fx(shot: dict[str, Any]) -> dict[str, Any]:
    stripped = deepcopy(shot)
    stripped["motion"] = "static"
    stripped.pop("overlays", None)
    stripped.pop("text", None)
    return stripped


def _render_shots(
    timeline: list[align.TimedShot],
    project_dir: Path,
    cache_dir: Path,
    mode: RenderMode,
    progress: Callable[[str], None] | None,
) -> tuple[list[Path], list[str]]:
    warnings: list[str] = []
    clip_paths: list[Path] = []
    cache_dir.mkdir(parents=True, exist_ok=True)

    for i, timed in enumerate(timeline, start=1):
        shot = _strip_fx(timed.shot) if mode.no_fx else timed.shot
        cache_key = _hash_payload(
            {"shot": shot, "duration": round(timed.duration, 3), "w": mode.width, "h": mode.height, "fps": FPS}
        )
        clip_path = cache_dir / f"{shot.get('id', i)}_{cache_key}.mp4"
        if not clip_path.is_file():
            result = video.render_shot(shot, timed.duration, project_dir, clip_path, mode.width, mode.height, FPS)
            warnings.extend(result.warnings)
        clip_paths.append(clip_path)
        _progress(progress, f"Кадр {i}/{len(timeline)} готов ({shot.get('id', '?')})")

    return clip_paths, warnings


def _resolve_music_cues(
    plan: dict[str, Any], words: list[align.Word], project_dir: Path
) -> tuple[list[audio.MusicCue], list[ValidationIssue]]:
    cues: list[audio.MusicCue] = []
    issues: list[ValidationIssue] = []

    for i, track in enumerate(plan.get("music") or []):
        label = f"музыка №{i + 1}"
        from_match, _ = align.find_anchor(words, track.get("from_anchor", ""))
        to_match, _ = align.find_anchor(words, track.get("to_anchor", ""))
        if from_match is None or to_match is None:
            issues.append(ValidationIssue("error", label, "не удалось привязать музыку к озвучке (from_anchor/to_anchor не найдены)"))
            continue
        cues.append(
            audio.MusicCue(
                asset=project_dir / track["asset"],
                start=from_match.start,
                end=to_match.start,
                volume=float(track.get("volume", 0.18)),
                fade_in=float(track.get("fade_in", 0.0)),
                fade_out=float(track.get("fade_out", 0.0)),
            )
        )
    return cues, issues


def _resolve_sfx_cues(timeline: list[align.TimedShot], project_dir: Path) -> list[audio.SfxCue]:
    cues: list[audio.SfxCue] = []
    for timed in timeline:
        for sfx in timed.shot.get("sfx") or []:
            cues.append(
                audio.SfxCue(
                    asset=project_dir / sfx["asset"],
                    time=timed.start + float(sfx.get("offset", 0.0)),
                    volume=float(sfx.get("volume", 1.0)),
                )
            )
    return cues


def _resolve_silence_windows(timeline: list[align.TimedShot]) -> list[tuple[float, float]]:
    windows = []
    for timed in timeline:
        silence_before = timed.shot.get("silence_before")
        if silence_before:
            windows.append((max(timed.start - float(silence_before), 0.0), timed.start))
    return windows


def _mux(video_path: Path | None, audio_path: Path | None, out_path: Path, mode: RenderMode) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    args: list[str] = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]

    if video_path is not None:
        args.extend(["-i", str(video_path)])
    if audio_path is not None and not mode.no_sound:
        args.extend(["-i", str(audio_path)])

    if video_path is not None:
        args.extend(["-c:v", "libx264", "-preset", mode.video_preset, "-crf", str(mode.crf), "-pix_fmt", "yuv420p"])
    if audio_path is not None and not mode.no_sound:
        args.extend(["-c:a", "aac", "-b:a", mode.audio_bitrate, "-ar", "48000"])
    else:
        args.append("-an")

    args.extend(["-shortest", str(out_path)])
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=600, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RenderError(f"Не удалось запустить FFmpeg (сведение видео и звука): {exc}") from exc
    if result.returncode != 0:
        stderr_tail = "\n".join(result.stderr.strip().splitlines()[-8:])
        raise RenderError(f"FFmpeg завершился с ошибкой при сведении видео и звука:\n{stderr_tail}")


def render_project(
    project_dir: Path,
    plan: dict[str, Any],
    mode: RenderMode,
    model_size: str = align.DEFAULT_MODEL_SIZE,
    progress: Callable[[str], None] | None = None,
) -> RenderSummary:
    output_dir = project_dir / "output"
    cache_dir = output_dir / ".cache" / "shots"
    work_dir = output_dir / ".cache" / "audio"

    voice_files = plan.get("voice") or []
    words: list[align.Word] = []
    summary = RenderSummary(output_path=output_dir / f"{mode.name}.mp4")

    if voice_files and all((project_dir / v).is_file() for v in voice_files):
        _progress(progress, "Распознаю озвучку...")
        try:
            words = align.load_or_transcribe(project_dir, voice_files, model_size)
        except align.AlignError as exc:
            summary.warnings.append(f"привязка к озвучке пропущена: {exc}")
        else:
            align.generate_srt(words, output_dir / "subtitles.srt")

    timeline, timeline_issues = align.build_timeline(plan, words)
    summary.issues.extend(timeline_issues)
    if not timeline:
        raise RenderError("В плане нет ни одного кадра, который удалось разместить на таймлайне")

    total_duration = timeline[-1].start + timeline[-1].duration
    summary.total_duration = total_duration
    summary.total_screen_time = sum(t.duration for t in timeline)
    summary.ai_screen_time = sum(t.duration for t in timeline if t.shot.get("is_ai"))

    music_cues, music_issues = _resolve_music_cues(plan, words, project_dir)
    summary.issues.extend(music_issues)
    sfx_cues = _resolve_sfx_cues(timeline, project_dir)
    silence_windows = _resolve_silence_windows(timeline)

    audio_path: Path | None = None
    if not mode.no_sound:
        _progress(progress, "Свожу звук...")
        audio_out = output_dir / (f"{mode.name}_audio.wav" if not mode.audio_only else "audio_only.wav")
        audio.render_audio(
            voice_paths=[project_dir / v for v in voice_files],
            music_cues=music_cues,
            sfx_cues=sfx_cues,
            silence_windows=silence_windows,
            total_duration=total_duration,
            out_path=audio_out,
            work_dir=work_dir,
        )
        audio_path = audio_out

    if mode.audio_only:
        summary.output_path = audio_path or output_dir / "audio_only.wav"
        report.write_render_report(
            output_dir / "report.txt", plan, timeline, summary.issues, summary.warnings,
            total_duration, summary.ai_screen_time, summary.total_screen_time,
        )
        return summary

    _progress(progress, f"Рендерю {len(timeline)} кадров...")
    clip_paths, shot_warnings = _render_shots(timeline, project_dir, cache_dir, mode, progress)
    summary.warnings.extend(shot_warnings)

    transitions: list[tuple[str, float]] = [("cut", 0.0)]
    for timed in timeline[1:]:
        transitions.append((timed.shot.get("transition_in", "cut"), float(timed.shot.get("transition_duration") or 0.5)))

    end_screen = plan.get("end_screen")
    if end_screen:
        end_path = cache_dir / "end_screen.mp4"
        video.render_end_screen(project_dir / end_screen["asset"], float(end_screen["duration"]), end_path, mode.width, mode.height, FPS)
        clip_paths.append(end_path)
        transitions.append(("cut", 0.0))

    look = {} if mode.no_fx else (plan.get("look") or {})
    if look.get("lut"):
        look = dict(look)
        look["lut"] = str(project_dir / look["lut"])

    _progress(progress, "Собираю видеоряд...")
    assembled_path = output_dir / ".cache" / f"{mode.name}_video.mp4"
    video.assemble_video(clip_paths, transitions, assembled_path, mode.width, mode.height, FPS, look)

    _progress(progress, "Свожу видео и звук вместе...")
    _mux(assembled_path, audio_path, summary.output_path, mode)

    if not mode.no_fx:
        _progress(progress, "Экспортирую таймлайн для DaVinci Resolve...")
        try:
            export.export_timeline(plan.get("title", "AutoEdit"), timeline, project_dir, output_dir / "timeline.otio", FPS)
            export.export_timeline(plan.get("title", "AutoEdit"), timeline, project_dir, output_dir / "timeline.fcpxml", FPS)
        except export.ExportError as exc:
            summary.warnings.append(f"экспорт таймлайна пропущен: {exc}")

    report.write_render_report(
        output_dir / "report.txt", plan, timeline, summary.issues, summary.warnings,
        total_duration, summary.ai_screen_time, summary.total_screen_time,
    )

    return summary
