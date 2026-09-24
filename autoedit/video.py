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


def probe_dimensions(path: Path) -> tuple[int, int] | None:
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "csv=p=0",
                str(path),
            ],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    parts = result.stdout.strip().split(",")
    if len(parts) < 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
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


def probe_fps(path: Path) -> float | None:
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=r_frame_rate",
                "-of", "csv=p=0",
                str(path),
            ],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = result.stdout.strip()
    if "/" not in text:
        return None
    num, _, den = text.partition("/")
    try:
        den_value = float(den)
        return float(num) / den_value if den_value else None
    except ValueError:
        return None


def sample_mean_brightness(path: Path, at_time: float) -> float | None:
    """Средняя яркость (0-255) одного кадра примерно в середине
    используемого отрезка исходника — грубая, но дешёвая проверка на
    пересвет/недосвет."""
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{max(at_time, 0.0):.3f}", "-i", str(path),
        "-frames:v", "1", "-vf", "scale=64:36,format=gray",
        "-f", "rawvideo", "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    data = result.stdout
    if not data:
        return None
    return sum(data) / len(data)


BUNDLED_FONT = Path(__file__).parent / "assets" / "fonts" / "Oswald-Bold.ttf"

_FONT_CANDIDATES = [
    str(BUNDLED_FONT),
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
    return (
        text.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\u2019")
        .replace("%", "\\%")
    )


DEFAULT_TEXT_SIZE = 96
DEFAULT_TEXT_COLOR = "#FFFFFF"
DEFAULT_TEXT_STROKE = 6
DEFAULT_HIGHLIGHT_COLOR = "#FFC72C"
TEXT_FADE_IN = 0.3
TEXT_FADE_OUT = 0.2
TEXT_REFERENCE_WIDTH = 1920  # "size" \u0432 text_style \u043a\u0430\u043b\u0438\u0431\u0440\u043e\u0432\u0430\u043d \u043f\u043e\u0434 \u0448\u0438\u0440\u0438\u043d\u0443 1920px


@dataclass
class TextSegment:
    text: str
    highlighted: bool


def _wrap_text_to_lines(text: str, font_path: str, font_size: int, max_width: float) -> list[str]:
    from PIL import ImageFont

    font = ImageFont.truetype(font_path, font_size)
    words = text.split()
    if not words:
        return []

    if font.getlength(text) <= max_width:
        return [text]

    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join(current + [word])
        if current and font.getlength(candidate) > max_width:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))

    if len(lines) <= 2:
        return lines

    # \u041d\u0435 \u043f\u043e\u043c\u0435\u0449\u0430\u0435\u0442\u0441\u044f \u0438 \u0432 2 \u0441\u0442\u0440\u043e\u043a\u0438 \u2014 \u0441\u0436\u0438\u043c\u0430\u0435\u043c \u0442\u0435\u043a\u0441\u0442 \u0434\u043e \u0434\u0432\u0443\u0445 \u0441\u0442\u0440\u043e\u043a \u043f\u0440\u0438\u043c\u0435\u0440\u043d\u043e
    # \u043f\u043e\u0440\u043e\u0432\u043d\u0443 \u043f\u043e \u043a\u043e\u043b\u0438\u0447\u0435\u0441\u0442\u0432\u0443 \u0441\u043b\u043e\u0432 (\u043b\u0443\u0447\u0448\u0435 \u0447\u0443\u0442\u044c \u0434\u043b\u0438\u043d\u043d\u0435\u0435 \u0441\u0442\u0440\u043e\u043a\u0430, \u0447\u0435\u043c 3-\u044f \u0441\u0442\u0440\u043e\u043a\u0430).
    midpoint = max(len(words) // 2, 1)
    return [" ".join(words[:midpoint]), " ".join(words[midpoint:])]


def _split_highlight(line: str, highlight: str | None) -> list[TextSegment]:
    if not highlight:
        return [TextSegment(line, False)]
    idx = line.find(highlight)
    if idx == -1:
        idx = line.lower().find(highlight.lower())
        if idx == -1:
            return [TextSegment(line, False)]
        highlight = line[idx : idx + len(highlight)]
    segments: list[TextSegment] = []
    if idx > 0:
        segments.append(TextSegment(line[:idx], False))
    segments.append(TextSegment(highlight, True))
    rest = line[idx + len(highlight) :]
    if rest:
        segments.append(TextSegment(rest, False))
    return segments


def build_text_filters(
    text: str,
    text_style: dict[str, Any] | None,
    width: int,
    height: int,
    duration: float,
) -> tuple[list[str], str | None]:
    """\u0421\u0442\u0440\u043e\u0438\u0442 drawtext-\u0444\u0438\u043b\u044c\u0442\u0440\u044b \u0434\u043b\u044f \u0442\u0435\u043a\u0441\u0442\u043e\u0432\u043e\u0439 \u043f\u043b\u0430\u0448\u043a\u0438: \u043f\u0435\u0440\u0435\u043d\u043e\u0441 \u043d\u0430 2 \u0441\u0442\u0440\u043e\u043a\u0438,
    \u0432\u044b\u0434\u0435\u043b\u0435\u043d\u0438\u0435 \u0447\u0430\u0441\u0442\u0438 \u0442\u0435\u043a\u0441\u0442\u0430 \u0446\u0432\u0435\u0442\u043e\u043c (highlight), \u043f\u043b\u0430\u0432\u043d\u043e\u0435 \u043f\u043e\u044f\u0432\u043b\u0435\u043d\u0438\u0435/\u0438\u0441\u0447\u0435\u0437\u0430\u043d\u0438\u0435.
    \u0412\u043e\u0437\u0432\u0440\u0430\u0449\u0430\u0435\u0442 (\u0441\u043f\u0438\u0441\u043e\u043a \u0444\u0438\u043b\u044c\u0442\u0440\u043e\u0432, \u043f\u0440\u0435\u0434\u0443\u043f\u0440\u0435\u0436\u0434\u0435\u043d\u0438\u0435 \u0438\u043b\u0438 None)."""
    from PIL import ImageFont

    text_style = text_style or {}
    font = find_bold_font()
    if not font:
        return [], (
            "\u0442\u0435\u043a\u0441\u0442 \u043d\u0435 \u043d\u0430\u043b\u043e\u0436\u0435\u043d \u2014 \u043d\u0430 \u044d\u0442\u043e\u0439 \u0441\u0438\u0441\u0442\u0435\u043c\u0435 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d \u043d\u0438 \u0432\u0441\u0442\u0440\u043e\u0435\u043d\u043d\u044b\u0439, \u043d\u0438 \u0441\u0438\u0441\u0442\u0435\u043c\u043d\u044b\u0439 "
            "\u0448\u0440\u0438\u0444\u0442 \u0434\u043b\u044f drawtext"
        )

    size = max(round(int(text_style.get("size", DEFAULT_TEXT_SIZE)) * width / TEXT_REFERENCE_WIDTH), 8)
    position = text_style.get("position", "center")
    color = text_style.get("color", DEFAULT_TEXT_COLOR)
    stroke = text_style.get("stroke", DEFAULT_TEXT_STROKE)
    shadow = text_style.get("shadow", True)
    highlight = text_style.get("highlight")
    highlight_color = text_style.get("highlight_color", DEFAULT_HIGHLIGHT_COLOR)

    max_line_width = width * 0.86
    try:
        pil_font = ImageFont.truetype(font, size)
        lines = _wrap_text_to_lines(str(text), font, size, max_line_width)
    except Exception as exc:  # noqa: BLE001 \u2014 \u043e\u0448\u0438\u0431\u043a\u0430 \u0441\u043e \u0448\u0440\u0438\u0444\u0442\u043e\u043c \u043d\u0435 \u0434\u043e\u043b\u0436\u043d\u0430 \u0440\u043e\u043d\u044f\u0442\u044c \u0432\u0435\u0441\u044c \u0440\u0435\u043d\u0434\u0435\u0440
        return [], f"\u0442\u0435\u043a\u0441\u0442 \u043d\u0435 \u043d\u0430\u043b\u043e\u0436\u0435\u043d \u0438\u0437-\u0437\u0430 \u043e\u0448\u0438\u0431\u043a\u0438 \u0448\u0440\u0438\u0444\u0442\u0430: {exc}"

    if not lines:
        return [], None

    line_height = size * 1.25
    block_height = line_height * len(lines)

    if position == "top":
        block_top = height * 0.08
    elif position == "bottom":
        block_top = height * 0.82 - block_height
    else:  # center
        block_top = (height - block_height) / 2

    fade_out_start = max(duration - TEXT_FADE_OUT, TEXT_FADE_IN)
    alpha_expr = (
        f"if(lt(t,{TEXT_FADE_IN}),t/{TEXT_FADE_IN},"
        f"if(gt(t,{fade_out_start:.3f}),max(({duration:.3f}-t)/{TEXT_FADE_OUT},0),1))"
    )

    filters: list[str] = []
    for line_index, line in enumerate(lines):
        segments = _split_highlight(line, highlight)
        widths = [pil_font.getlength(seg.text) for seg in segments]
        total_width = sum(widths)
        y = block_top + line_index * line_height

        cumulative = 0.0
        for seg, seg_width in zip(segments, widths):
            trimmed = seg.text.strip()
            if trimmed:
                # FFmpeg drawtext не учитывает ширину ведущих пробелов при
                # позиционировании текста — считаем её сами и сдвигаем x,
                # а рисуем уже обрезанный текст, иначе соседние сегменты
                # (например, обычный текст сразу после highlight) слипаются.
                leading_ws = seg.text[: len(seg.text) - len(seg.text.lstrip())]
                leading_ws_width = pil_font.getlength(leading_ws) if leading_ws else 0.0

                seg_color = highlight_color if seg.highlighted else color
                escaped = _escape_drawtext(trimmed)
                font_escaped = font.replace("\\", "/").replace(":", "\\:")
                parts = [
                    f"fontfile='{font_escaped}'",
                    f"text='{escaped}'",
                    f"fontsize={size}",
                    f"fontcolor={seg_color}",
                    f"alpha='{alpha_expr}'",
                ]
                if stroke:
                    parts.append(f"borderw={stroke}")
                    parts.append("bordercolor=black")
                if shadow:
                    parts.extend(["shadowx=3", "shadowy=3", "shadowcolor=black@0.6"])
                parts.append(f"x=(w-{total_width:.1f})/2+{cumulative + leading_ws_width:.1f}")
                parts.append(f"y={y:.1f}")
                filters.append("drawtext=" + ":".join(parts))
            cumulative += seg_width

    return filters, None


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


def build_look_filters(look: dict[str, Any] | None) -> list[str]:
    """Строит фильтры общего "вида" ролика (LUT/зерно/виньетка).

    Калибровка: значение 0.3 должно быть едва заметным, 1.0 — заметно,
    но не убийственно сильным (проверено визуально на тестовых кадрах).
    Применяется на уровне отдельного кадра, ДО текста — так виньетка не
    затемняет текстовую плашку.
    """
    look = look or {}
    filters: list[str] = []

    lut = look.get("lut")
    if lut:
        filters.append(f"lut3d=file='{lut}'")

    grain = look.get("grain") or 0
    if grain > 0:
        filters.append(f"noise=alls={grain * 20:.1f}:allf=t")

    vignette = look.get("vignette") or 0
    if vignette > 0:
        angle = 0.05 + 1.25 * vignette
        filters.append(f"vignette=angle={angle:.3f}")

    return filters


FIT_MODES = {"cover", "contain", "blur_fill"}


def resolve_fit_mode(shot: dict[str, Any], source_dims: tuple[int, int] | None, width: int, height: int) -> str:
    """Определяет, как вписывать исходник в кадр нужного размера.

    Явное поле "fit" в кадре всегда побеждает. Иначе, если ориентация
    исходника не совпадает с ориентацией ролика (например, вертикальное
    видео в горизонтальном ролике), по умолчанию используется
    "blur_fill" (размытая подложка + чёткая картинка по центру) вместо
    обычной обрезки по краям, которая в этом случае съедала бы почти
    всё изображение.
    """
    explicit = shot.get("fit")
    if explicit in FIT_MODES:
        return explicit
    if source_dims:
        source_w, source_h = source_dims
        if source_w > 0 and source_h > 0:
            source_vertical = source_h > source_w
            target_vertical = height > width
            if source_vertical != target_vertical:
                return "blur_fill"
    return "cover"


def _blur_fill_complex(input_label: str, width: int, height: int, output_label: str) -> str:
    return (
        f"[{input_label}]split=2[bg_src][fg_src];"
        f"[bg_src]scale=w={width}:h={height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},gblur=sigma=25,eq=brightness=-0.08[bg];"
        f"[fg_src]scale=w={width}:h={height}:force_original_aspect_ratio=decrease,setsar=1[fg];"
        f"[bg][fg]overlay=x=(W-w)/2:y=(H-h)/2[{output_label}]"
    )


BRIGHTNESS_OVEREXPOSED = 235.0
BRIGHTNESS_UNDEREXPOSED = 20.0
MIN_SOURCE_HEIGHT = 1080
SAME_SOURCE_MAX_SECONDS = 12.0


def check_source_quality(
    timeline: list[Any], project_dir: Path, target_width: int, target_height: int
) -> list[str]:
    """Проверяет исходники кадров на типичные проблемы: пересвет/недосвет,
    слишком долгий показ одного и того же исходника подряд, вертикальный
    исходник в горизонтальном ролике (и наоборот), низкое разрешение,
    нестандартную частоту кадров. timeline — список TimedShot из align.py
    (не импортируем сам класс, чтобы не плодить циклические импорты).
    Возвращает список готовых строк-предупреждений на русском.
    """
    warnings: list[str] = []
    dims_cache: dict[str, tuple[int, int] | None] = {}

    for timed in timeline:
        shot = timed.shot
        shot_id = shot.get("id", "?")
        asset_rel = shot.get("asset")
        if not asset_rel:
            continue
        asset_path = project_dir / asset_rel
        if not asset_path.is_file() or asset_path.suffix.lower() in IMAGE_SUFFIXES:
            continue

        dims = dims_cache.setdefault(asset_rel, probe_dimensions(asset_path))
        if dims:
            src_w, src_h = dims
            if min(src_w, src_h) < MIN_SOURCE_HEIGHT:
                warnings.append(
                    f"Кадр {shot_id}: исходник {asset_rel} — {src_w}x{src_h}, меньше 1080p, "
                    f"в кадре может быть заметно мыльным"
                )
            src_vertical = src_h > src_w
            target_vertical = target_height > target_width
            if src_vertical != target_vertical and (shot.get("fit") or "auto") == "auto":
                warnings.append(
                    f"Кадр {shot_id}: исходник {asset_rel} — {'вертикальный' if src_vertical else 'горизонтальный'}, "
                    f"а ролик {'вертикальный' if target_vertical else 'горизонтальный'} "
                    f"(вписан через blur_fill автоматически; переопределите полем \"fit\", если нужно иначе)"
                )

        fps = probe_fps(asset_path)
        if fps and abs(fps - 30) > 0.5:
            warnings.append(
                f"Кадр {shot_id}: исходник {asset_rel} снят с частотой {fps:.2f} к/с — "
                f"при приведении к 30 к/с возможны небольшие рывки в движении"
            )

        source_start = float(shot.get("source_start", 0.0) or 0.0)
        mid_time = source_start + timed.duration / 2
        brightness = sample_mean_brightness(asset_path, mid_time)
        if brightness is not None:
            if brightness >= BRIGHTNESS_OVEREXPOSED:
                warnings.append(f"Кадр {shot_id}: исходник {asset_rel} выглядит пересвеченным (почти белый кадр)")
            elif brightness <= BRIGHTNESS_UNDEREXPOSED:
                warnings.append(f"Кадр {shot_id}: исходник {asset_rel} выглядит слишком тёмным (почти чёрный кадр)")

    # Один и тот же исходник подряд дольше SAME_SOURCE_MAX_SECONDS
    run_asset: str | None = None
    run_seconds = 0.0
    run_start_id = None
    for timed in [*timeline, None]:
        asset_rel = timed.shot.get("asset") if timed else None
        if asset_rel == run_asset and timed is not None:
            run_seconds += timed.duration
        else:
            if run_asset and run_seconds > SAME_SOURCE_MAX_SECONDS:
                warnings.append(
                    f'Один и тот же исходник "{run_asset}" показан подряд {run_seconds:.1f} сек '
                    f"(начиная с кадра {run_start_id}) — может выглядеть однообразно"
                )
            run_asset = asset_rel
            run_seconds = timed.duration if timed else 0.0
            run_start_id = timed.shot.get("id", "?") if timed else None

    return warnings


def render_shot(
    shot: dict[str, Any],
    duration: float,
    project_dir: Path,
    out_path: Path,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    fps: int = DEFAULT_FPS,
    look: dict[str, Any] | None = None,
    debug_label: str | None = None,
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

    source_dims = probe_dimensions(asset_path)
    fit_mode = resolve_fit_mode(shot, source_dims, width, height)
    use_blur_fill = fit_mode == "blur_fill"

    filters: list[str] = []
    crop = shot.get("crop")
    if crop:
        cx, cy, cw, ch = crop
        filters.append(f"crop=w=iw*{cw}:h=ih*{ch}:x=iw*{cx}:y=ih*{cy}")

    if fit_mode == "contain":
        filters.append(
            f"scale=w={width}:h={height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"
        )

    if speed != 1.0:
        filters.append(f"setpts=PTS/{speed}")

    filters.append(build_motion_filter(shot.get("motion"), shot.get("motion_amount"), width, height, motion_span))

    if freeze_extra > 0:
        filters.append(f"tpad=stop_mode=clone:stop_duration={freeze_extra:.3f}")

    transition_in = shot.get("transition_in", "cut")
    transition_duration = float(shot.get("transition_duration") or 0.5)
    if transition_in == "fade_from_black" and transition_duration > 0:
        filters.append(f"fade=t=in:st=0:d={transition_duration:.3f}:color=black")

    filters.extend(build_look_filters(look))

    text = shot.get("text")
    if text:
        text_filters, text_warn = build_text_filters(str(text), shot.get("text_style"), width, height, duration)
        filters.extend(text_filters)
        if text_warn:
            warnings.append(f'Кадр {shot.get("id", "?")}: {text_warn}')

    if debug_label:
        font_path = find_bold_font()
        if font_path:
            escaped = _escape_drawtext(debug_label)
            filters.append(
                f"drawtext=fontfile='{font_path}':text='{escaped}':x=10:y=10:fontsize=20:"
                f"fontcolor=yellow:box=1:boxcolor=black@0.6:boxborderw=6"
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

    if not overlays and not use_blur_fill:
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
    if use_blur_fill:
        filter_parts = [_blur_fill_complex("0:v", width, height, "fitted0"), f"[fitted0]{filter_chain}[base0]"]
    else:
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
    look: dict[str, Any] | None = None,
) -> None:
    look_filters = build_look_filters(look)
    look_chain = ("," + ",".join(look_filters)) if look_filters else ""
    filter_chain = (
        f"scale=w={width}:h={height}:force_original_aspect_ratio=increase,crop={width}:{height},"
        f"setsar=1{look_chain},fps={fps},format=yuv420p"
    )
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


def render_chapters_video(
    chapters: list[tuple[str, float, float]],
    total_duration: float,
    out_path: Path,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    fps: int = DEFAULT_FPS,
) -> None:
    """Чёрный фон с названием текущей главы — картинка для режима
    --check-audio-only: если история держится на голосе и звуке, по
    такому "видео" (по сути — просто подписи глав) должно быть понятно,
    что происходит."""
    font = find_bold_font()
    filters: list[str] = []
    if font:
        font_escaped = font.replace("\\", "/").replace(":", "\\:")
        for name, start, end in chapters:
            escaped = _escape_drawtext(name)
            filters.append(
                f"drawtext=fontfile='{font_escaped}':text='{escaped}':fontsize=h*0.06:"
                f"fontcolor=white:borderw=3:bordercolor=black:x=(w-text_w)/2:y=(h-text_h)/2:"
                f"enable='between(t,{start:.3f},{end:.3f})'"
            )
    filter_chain = ",".join(filters) if filters else "null"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(
        [
            "-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r={fps}:d={total_duration:.3f}",
            "-vf", filter_chain,
            "-t", f"{total_duration:.3f}",
            "-an",
            str(out_path),
        ],
        "чёрный фон с главами",
    )


def assemble_video(
    clip_paths: list[Path],
    transitions: list[tuple[str, float]],
    out_path: Path,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    fps: int = DEFAULT_FPS,
) -> None:
    """Склеивает уже отрендеренные кадры в один видеоряд.

    transitions[i] описывает переход ПЕРЕД clip_paths[i] (для первого
    кадра переход не используется). Значения: ("cut"|"fade_from_black", _)
    просто конкатенируются (fade_from_black уже "вшит" в сам кадр в
    render_shot), ("crossfade", duration) — склеиваются внахлёст через xfade.
    "Вид" (LUT/зерно/виньетка) уже применён на уровне каждого кадра в
    render_shot/render_end_screen — так виньетка не затемняет текст,
    который рисуется поверх неё, и здесь его применять повторно не нужно.
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

    filter_complex = ";".join(filter_parts) if filter_parts else None

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if filter_complex:
        args.extend(["-filter_complex", filter_complex, "-map", f"[{current_label}]"])
    else:
        args.extend(["-map", "0:v"])
    args.extend(["-r", str(fps), "-an", str(out_path)])
    _run_ffmpeg(args, "сборка видеоряда")
