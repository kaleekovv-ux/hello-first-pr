"""Видеомонтаж: приведение ассетов к единому формату, движение камеры,
переходы, оверлеи на хромакее, текст, LUT/зерно/виньетка, финальная сборка.

Все операции выполняются через FFmpeg. Плавный зум/панорама сделаны не
через фильтр `zoompan` (он рассчитан прежде всего на статичные картинки
и ведёт себя ненадёжно на видео с собственным движением — например,
может держать/дублировать кадры непредсказуемо), а через комбинацию
`crop` с покадровыми (eval=frame) выражениями от времени `t` и `scale`.
Это чуть более многословно, зато работает одинаково и на видео, и на
картинках, с точной привязкой к длительности кадра.
"""

from __future__ import annotations

import platform
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080
DEFAULT_FPS = 30
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
MIN_SPEED = 0.8  # медленнее не замедляем — дальше только "заморозка" последнего кадра


class VideoError(Exception):
    """Не удалось собрать/отрендерить видео (обычно — сбой FFmpeg)."""


@dataclass
class ShotRenderResult:
    path: Path
    warnings: list[str] = field(default_factory=list)


def _run_ffmpeg(args: list[str], step_name: str) -> None:
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise VideoError(f"Не удалось запустить FFmpeg ({step_name}): {exc}") from exc
    if result.returncode != 0:
        stderr_tail = "\n".join(result.stderr.strip().splitlines()[-8:])
        raise VideoError(f"FFmpeg завершился с ошибкой на шаге «{step_name}»:\n{stderr_tail}")


def probe_duration(path: Path) -> float | None:
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=nk=1:nw=1",
                str(path),
            ],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = result.stdout.strip()
    try:
        return float(text)
    except ValueError:
        return None


def has_video_stream(path: Path) -> bool:
    """Проверяет, что в файле реально есть видеопоток (не пустой/битый файл)."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=index",
                "-of", "csv=p=0",
                str(path),
            ],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(result.stdout.strip())


_FONT_CANDIDATES = [
    # Linux
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    # macOS
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    # Windows
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
]


def find_bold_font() -> str | None:
    for candidate in _FONT_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return None


def _escape_drawtext(text: str) -> str:
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\u2019")


def _ease_expr(duration: float) -> str:
    p = f"(t/{max(duration, 0.001):.6f})"
    return f"(({p})*({p})*(3-2*({p})))"


def build_motion_filter(
    motion: str | None,
    motion_amount: float | None,
    width: int,
    height: int,
    duration: float,
) -> str:
    """Строит цепочку фильтров: привести к формату кадра (cover) + плавное
    движение камеры (если задано motion). duration — длительность именно
    фазы движения (без учёта возможной "заморозки" в конце)."""
    motion = motion or "static"
    amount = max(motion_amount if motion_amount is not None else 0.08, 0.0)

    if motion == "static" or amount <= 0:
        return f"scale=w={width}:h={height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1"

    ease = _ease_expr(duration)

    if motion in ("zoom_in", "zoom_out"):
        # Плавный зум делаем через `scale` с eval=frame (он умеет менять
        # размер кадра каждый output-фрейм по выражению от `t`), а не
        # через `crop`: в этой версии FFmpeg сам crop пересчитывает по
        # времени только x/y, но не w/h (только init-вычисление) — при
        # попытке дать w/h кадра, зависящие от t, FFmpeg отказывается
        # запускаться ("Error when evaluating the expression").
        big_w = round(width * (1 + amount))
        big_h = round(height * (1 + amount))
        big_w += big_w % 2
        big_h += big_h % 2
        cover = f"scale=w={big_w}:h={big_h}:force_original_aspect_ratio=increase,crop={big_w}:{big_h}"

        if motion == "zoom_in":
            w_expr = f"{big_w}-({big_w}-{width})*{ease}"
            h_expr = f"{big_h}-({big_h}-{height})*{ease}"
        else:  # zoom_out
            w_expr = f"{width}+({big_w}-{width})*{ease}"
            h_expr = f"{height}+({big_h}-{height})*{ease}"

        zoom = f"scale=w='{w_expr}':h='{h_expr}':eval=frame"
        return f"{cover},{zoom},crop={width}:{height},setsar=1"

    if motion in ("pan_left", "pan_right", "rise"):
        # Панораму делаем через `crop` с time-varying x/y — это FFmpeg
        # поддерживает нативно (в отличие от w/h, см. выше).
        mid_amount = amount / 2
        mid_w = round(width * (1 + mid_amount))
        mid_h = round(height * (1 + mid_amount))
        mid_w += mid_w % 2
        mid_h += mid_h % 2
        cover = f"scale=w={mid_w}:h={mid_h}:force_original_aspect_ratio=increase,crop={mid_w}:{mid_h}"

        max_dx = (mid_w - width) / 2
        max_dy = (mid_h - height) / 2
        base_x = max_dx
        base_y = max_dy
        if motion == "pan_left":
            x = f"({base_x} + {max_dx}*(1-2*{ease}))"
            y = f"{base_y}"
        elif motion == "pan_right":
            x = f"({base_x} - {max_dx}*(1-2*{ease}))"
            y = f"{base_y}"
        else:  # rise
            x = f"{base_x}"
            y = f"({base_y} + {max_dy}*(1-2*{ease}))"

        motion_crop = f"crop=w={width}:h={height}:x={x}:y={y}"
        return f"{cover},{motion_crop},setsar=1"

    raise VideoError(
        f'неизвестное значение "motion": {motion!r} '
        f"(допустимо: zoom_in, zoom_out, pan_left, pan_right, rise, static)"
    )


def _plan_speed_and_freeze(
    source_duration: float | None, source_start: float, target_duration: float
) -> tuple[float, float, str | None]:
    """Возвращает (скорость воспроизведения, доп. время "заморозки" в
    конце, предупреждение или None)."""
    if source_duration is None:
        return 1.0, 0.0, None

    available = max(source_duration - source_start, 0.01)
    if available >= target_duration:
        return 1.0, 0.0, None

    slowed_span = available / MIN_SPEED
    if slowed_span >= target_duration:
        return MIN_SPEED, 0.0, None

    freeze_extra = target_duration - slowed_span
    warning = (
        f"клип короче нужной длительности даже при замедлении до {MIN_SPEED:.1f}× "
        f"— последний кадр удержан на {freeze_extra:.2f} сек"
    )
    return MIN_SPEED, freeze_extra, warning


def render_shot(
    shot: dict[str, Any],
    duration: float,
    project_dir: Path,
    out_path: Path,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    fps: int = DEFAULT_FPS,
) -> ShotRenderResult:
    """Рендерит один кадр (видео без звука) заданной длительности."""
    warnings: list[str] = []
    asset_path = project_dir / shot["asset"]
    if not asset_path.is_file():
        raise VideoError(f"Файл не найден: {asset_path}")

    source_start = float(shot.get("source_start", 0.0) or 0.0)
    is_image = asset_path.suffix.lower() in IMAGE_SUFFIXES

    if is_image:
        speed, freeze_extra, warn = 1.0, 0.0, None
    else:
        source_duration = probe_duration(asset_path)
        if source_duration is not None and source_start >= source_duration:
            clamped_start = max(source_duration - 0.1, 0.0)
            warnings.append(
                f'Кадр {shot.get("id", "?")}: source_start={source_start:.2f} сек больше длины исходника '
                f'({source_duration:.2f} сек) — начало сдвинуто на {clamped_start:.2f} сек, иначе кадр был бы пустым'
            )
            source_start = clamped_start
        speed, freeze_extra, warn = _plan_speed_and_freeze(source_duration, source_start, duration)
    if warn:
        warnings.append(f'Кадр {shot.get("id", "?")}: {warn}')

    motion_span = duration - freeze_extra

    filters: list[str] = []
    crop = shot.get("crop")
    if crop:
        cx, cy, cw, ch = crop
        filters.append(f"crop=w=iw*{cw}:h=ih*{ch}:x=iw*{cx}:y=ih*{cy}")

    if speed != 1.0:
        filters.append(f"setpts=PTS/{speed}")

    filters.append(build_motion_filter(shot.get("motion"), shot.get("motion_amount"), width, height, motion_span))

    if freeze_extra > 0:
        filters.append(f"tpad=stop_mode=clone:stop_duration={freeze_extra:.3f}")

    transition_in = shot.get("transition_in", "cut")
    transition_duration = float(shot.get("transition_duration") or 0.5)
    if transition_in == "fade_from_black" and transition_duration > 0:
        filters.append(f"fade=t=in:st=0:d={transition_duration:.3f}:color=black")

    text = shot.get("text")
    if text:
        font = find_bold_font()
        if font:
            escaped = _escape_drawtext(str(text))
            filters.append(
                "drawtext=fontfile='"
                + font.replace("\\", "/").replace(":", "\\:")
                + f"':text='{escaped}':fontsize=h*0.06:fontcolor=white:"
                "borderw=3:bordercolor=black:x=(w-text_w)/2:y=h*0.82"
            )
        else:
            warnings.append(
                f'Кадр {shot.get("id", "?")}: текст не наложен — на этой системе не найден '
                f"шрифт для drawtext (нужен .ttf-файл, например DejaVu Sans Bold)"
            )

    filters.append(f"fps={fps}")
    filters.append("format=yuv420p")

    filter_chain = ",".join(filters)

    if is_image:
        input_args = ["-loop", "1", "-t", f"{duration:.3f}", "-i", str(asset_path)]
    else:
        input_args = ["-ss", f"{source_start:.3f}", "-i", str(asset_path)]

    overlays = shot.get("overlays") or []
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not overlays:
        args = [
            *input_args,
            "-vf", filter_chain,
            "-t", f"{duration:.3f}",
            "-an",
            str(out_path),
        ]
        _run_ffmpeg(args, f"кадр {shot.get('id', '?')}")
        return ShotRenderResult(path=out_path, warnings=warnings)

    args = list(input_args)
    filter_parts = [f"[0:v]{filter_chain}[base0]"]
    last_label = "base0"
    for i, overlay in enumerate(overlays, start=1):
        overlay_path = project_dir / overlay["asset"]
        if not overlay_path.is_file():
            raise VideoError(f"Файл оверлея не найден: {overlay_path}")
        offset = float(overlay.get("offset", 0.0) or 0.0)
        chroma_key = overlay.get("chroma_key")
        args.extend(["-itsoffset", f"{offset:.3f}", "-i", str(overlay_path)])
        ov_filters = [f"scale=w={width}:h={height}"]
        if chroma_key:
            color = chroma_key.lstrip("#")
            ov_filters.append(f"colorkey=0x{color}:0.30:0.15")
        ov_filters.append("format=yuva420p")
        new_label = f"ov{i}"
        filter_parts.append(f"[{i}:v]{','.join(ov_filters)}[{new_label}]")
        combined_label = f"comb{i}"
        filter_parts.append(f"[{last_label}][{new_label}]overlay=shortest=0:eof_action=pass[{combined_label}]")
        last_label = combined_label

    filter_complex = ";".join(filter_parts)
    args.extend(
        [
            "-filter_complex", filter_complex,
            "-map", f"[{last_label}]",
            "-t", f"{duration:.3f}",
            "-an",
            str(out_path),
        ]
    )
    _run_ffmpeg(args, f"кадр {shot.get('id', '?')} (с оверлеем)")
    return ShotRenderResult(path=out_path, warnings=warnings)


def render_end_screen(
    asset_path: Path,
    duration: float,
    out_path: Path,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    fps: int = DEFAULT_FPS,
) -> None:
    filter_chain = f"scale=w={width}:h={height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1,fps={fps},format=yuv420p"
    is_image = asset_path.suffix.lower() in IMAGE_SUFFIXES
    input_args = (
        ["-loop", "1", "-t", f"{duration:.3f}", "-i", str(asset_path)]
        if is_image
        else ["-i", str(asset_path)]
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(
        [*input_args, "-vf", filter_chain, "-t", f"{duration:.3f}", "-an", str(out_path)],
        "концевая заставка",
    )


def assemble_video(
    clip_paths: list[Path],
    transitions: list[tuple[str, float]],
    out_path: Path,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    fps: int = DEFAULT_FPS,
    look: dict[str, Any] | None = None,
) -> None:
    """Склеивает уже отрендеренные кадры в один видеоряд.

    transitions[i] описывает переход ПЕРЕД clip_paths[i] (для первого
    кадра переход не используется). Значения: ("cut"|"fade_from_black", _)
    просто конкатенируются (fade_from_black уже "вшит" в сам кадр в
    render_shot), ("crossfade", duration) — склеиваются внахлёст через xfade.
    """
    if not clip_paths:
        raise VideoError("Нечего собирать: список кадров пуст")

    durations = [probe_duration(p) or 0.0 for p in clip_paths]

    args: list[str] = []
    for p in clip_paths:
        args.extend(["-i", str(p)])

    filter_parts: list[str] = []
    # xfade требует одинаковую временную базу (timebase) у обоих входов —
    # приводим каждый клип к общему fps/timebase перед склейкой.
    for i in range(len(clip_paths)):
        filter_parts.append(f"[{i}:v]fps={fps},format=yuv420p,settb=AVTB[n{i}]")

    current_label = "n0"
    accumulated = durations[0]

    for i in range(1, len(clip_paths)):
        kind, transition_duration = transitions[i]
        next_label = f"v{i}"
        if kind == "crossfade" and transition_duration > 0:
            offset = max(accumulated - transition_duration, 0.0)
            filter_parts.append(
                f"[{current_label}][n{i}]xfade=transition=fade:duration={transition_duration:.3f}:offset={offset:.3f},settb=AVTB[{next_label}]"
            )
            accumulated = accumulated + durations[i] - transition_duration
        else:
            filter_parts.append(f"[{current_label}][n{i}]concat=n=2:v=1:a=0,settb=AVTB[{next_label}]")
            accumulated = accumulated + durations[i]
        current_label = next_label

    look = look or {}
    look_filters: list[str] = []
    lut = look.get("lut")
    if lut:
        look_filters.append(f"lut3d=file='{lut}'")
    grain = look.get("grain") or 0
    if grain > 0:
        look_filters.append(f"noise=alls={grain * 40:.1f}:allf=t")
    vignette = look.get("vignette") or 0
    if vignette > 0:
        look_filters.append(f"vignette=PI/{max(5 - 4 * vignette, 1.2):.2f}")

    final_label = current_label
    if look_filters:
        look_chain = ",".join(look_filters)
        filter_parts.append(f"[{current_label}]{look_chain}[graded]")
        final_label = "graded"

    filter_complex = ";".join(filter_parts) if filter_parts else None

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if filter_complex:
        args.extend(["-filter_complex", filter_complex, "-map", f"[{final_label}]"])
    else:
        args.extend(["-map", "0:v"])
    args.extend(["-r", str(fps), "-an", str(out_path)])
    _run_ffmpeg(args, "сборка видеоряда")
