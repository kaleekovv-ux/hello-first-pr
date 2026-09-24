"""Лист миниатюр (`autoedit sheet`): по кадру из середины каждого видео
в папке, крупно, с именем файла и длительностью под каждой миниатюрой.
Нужен, чтобы не гадать, что показывает сток с именем вроде
"13601663_1920_1080_30fps.mp4".
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .video import BUNDLED_FONT, probe_duration

VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
THUMB_WIDTH = 320
THUMB_HEIGHT = 180
CAPTION_HEIGHT = 40
PADDING = 12
COLUMNS = 5
MAX_ROWS_PER_SHEET = 6  # 30 миниатюр на один лист


class SheetError(Exception):
    """Не удалось построить лист миниатюр."""


def _find_videos(folder: Path) -> list[Path]:
    return sorted(
        p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES
    )


def _load_caption_font() -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(str(BUNDLED_FONT), 16)
    except OSError:
        return ImageFont.load_default()


def _extract_middle_frame(video_path: Path, duration: float | None, out_path: Path) -> bool:
    mid = (duration / 2) if duration else 0.0
    scale = (
        f"scale={THUMB_WIDTH}:{THUMB_HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={THUMB_WIDTH}:{THUMB_HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black"
    )
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{mid:.3f}", "-i", str(video_path),
        "-frames:v", "1", "-vf", scale,
        str(out_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and out_path.is_file()


def _truncate(name: str, limit: int = 34) -> str:
    return name if len(name) <= limit else name[: limit - 1] + "…"


def generate_contact_sheets(folder: Path, out_dir: Path) -> list[Path]:
    """Строит один или несколько листов миниатюр для всех видео в folder
    (включая вложенные папки). Возвращает пути к созданным .jpg."""
    videos = _find_videos(folder)
    if not videos:
        raise SheetError(f"В папке {folder} не найдено видеофайлов ({', '.join(sorted(VIDEO_SUFFIXES))})")

    out_dir.mkdir(parents=True, exist_ok=True)
    font = _load_caption_font()
    per_sheet = COLUMNS * MAX_ROWS_PER_SHEET
    sheet_paths: list[Path] = []

    for page_index in range(0, len(videos), per_sheet):
        page_videos = videos[page_index : page_index + per_sheet]
        rows = (len(page_videos) + COLUMNS - 1) // COLUMNS
        sheet_w = COLUMNS * (THUMB_WIDTH + PADDING) + PADDING
        sheet_h = rows * (THUMB_HEIGHT + CAPTION_HEIGHT + PADDING) + PADDING
        sheet = Image.new("RGB", (sheet_w, sheet_h), (24, 24, 24))
        draw = ImageDraw.Draw(sheet)

        for i, video_path in enumerate(page_videos):
            col, row = i % COLUMNS, i // COLUMNS
            x = PADDING + col * (THUMB_WIDTH + PADDING)
            y = PADDING + row * (THUMB_HEIGHT + CAPTION_HEIGHT + PADDING)

            duration = probe_duration(video_path)
            thumb_path = out_dir / f"_thumb_{page_index + i}.jpg"
            ok = _extract_middle_frame(video_path, duration, thumb_path)
            if ok:
                with Image.open(thumb_path) as thumb:
                    sheet.paste(thumb.convert("RGB"), (x, y))
                thumb_path.unlink(missing_ok=True)
            else:
                draw.rectangle([x, y, x + THUMB_WIDTH, y + THUMB_HEIGHT], fill=(60, 20, 20))
                draw.text((x + 10, y + THUMB_HEIGHT // 2 - 8), "не удалось прочитать", font=font, fill=(255, 140, 140))

            try:
                rel_name = video_path.relative_to(folder).as_posix()
            except ValueError:
                rel_name = video_path.name
            draw.text((x, y + THUMB_HEIGHT + 4), _truncate(rel_name), font=font, fill=(255, 255, 255))
            duration_text = f"{duration:.1f} сек" if duration else "длительность неизвестна"
            draw.text((x, y + THUMB_HEIGHT + 22), duration_text, font=font, fill=(210, 190, 90))

        page_num = page_index // per_sheet + 1
        sheet_name = "contact_sheet.jpg" if len(videos) <= per_sheet else f"contact_sheet_{page_num}.jpg"
        sheet_path = out_dir / sheet_name
        sheet.save(sheet_path, quality=88)
        sheet_paths.append(sheet_path)

    return sheet_paths
