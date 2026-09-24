"""Экспорт собранного таймлайна в OTIO/FCPXML — для доработки в
DaVinci Resolve (он бесплатный и открыто читает оба формата).

CapCut использует закрытый формат проектов — писать под него ничего не
пытаемся, это стоит делать вручную, пересобирая ролик в CapCut по
report.txt и получившемуся видео как референсу.
"""

from __future__ import annotations

from pathlib import Path

import opentimelineio as otio

from .align import TimedShot
from .video import probe_duration

DEFAULT_FPS = 30.0


class ExportError(Exception):
    """Не удалось экспортировать таймлайн."""


def build_otio_timeline(
    project_title: str, timeline: list[TimedShot], project_dir: Path, fps: float = DEFAULT_FPS
) -> otio.schema.Timeline:
    otio_timeline = otio.schema.Timeline(name=project_title)
    track = otio.schema.Track(name="AutoEdit")
    otio_timeline.tracks.append(track)

    for timed in timeline:
        shot = timed.shot
        asset_path = (project_dir / shot["asset"]).resolve()
        source_start = float(shot.get("source_start", 0.0) or 0.0)

        duration_rt = otio.opentime.RationalTime(round(timed.duration * fps), fps)
        source_range = otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(round(source_start * fps), fps),
            duration=duration_rt,
        )

        media_reference = otio.schema.ExternalReference(target_url=asset_path.as_uri())
        full_duration = probe_duration(asset_path) or (source_start + timed.duration)
        media_reference.available_range = otio.opentime.TimeRange(
            start_time=otio.opentime.RationalTime(0, fps),
            duration=otio.opentime.RationalTime(round(full_duration * fps), fps),
        )

        clip = otio.schema.Clip(
            name=str(shot.get("id", asset_path.stem)),
            media_reference=media_reference,
            source_range=source_range,
        )
        if shot.get("why"):
            clip.metadata["autoedit_why"] = shot["why"]
        track.append(clip)

    return otio_timeline


def export_timeline(
    project_title: str,
    timeline: list[TimedShot],
    project_dir: Path,
    out_path: Path,
    fps: float = DEFAULT_FPS,
) -> None:
    """Пишет .otio или .fcpxml — формат определяется по расширению out_path."""
    otio_timeline = build_otio_timeline(project_title, timeline, project_dir, fps)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        otio.adapters.write_to_file(otio_timeline, str(out_path))
    except Exception as exc:  # noqa: BLE001 — адаптеры OTIO бросают разные типы ошибок
        raise ExportError(f"Не удалось записать {out_path.name}: {exc}") from exc
