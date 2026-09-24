"""Генератор демо-проекта: фальшивые ассеты и небольшой план.json,
чтобы можно было проверить программу без ваших реальных файлов.

Тайминги кадров в демо-плане заданы явно ("start"/"duration"), а не
через anchor — привязка к словам диктора появится на этапе 2, когда
будет готово распознавание речи.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


class DemoError(Exception):
    """Не удалось создать демо-проект (обычно — сбой FFmpeg)."""


def _run_ffmpeg(args: list[str], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args, str(out_path)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise DemoError(f"Не удалось запустить FFmpeg для {out_path.name}: {exc}") from exc
    if result.returncode != 0:
        stderr_tail = "\n".join(result.stderr.strip().splitlines()[-5:])
        raise DemoError(f"FFmpeg не смог создать {out_path.name}:\n{stderr_tail}")


def _make_color_clip(out_path: Path, color: str, duration: float, pattern: str = "color") -> None:
    if pattern == "testsrc":
        source = f"testsrc2=size=1280x720:rate=30:duration={duration}"
    else:
        source = f"color=c={color}:size=1280x720:rate=30:duration={duration}"
    _run_ffmpeg(["-f", "lavfi", "-i", source, "-pix_fmt", "yuv420p"], out_path)


def _make_tone(out_path: Path, frequency: int, duration: float) -> None:
    _run_ffmpeg(["-f", "lavfi", "-i", f"sine=frequency={frequency}:duration={duration}"], out_path)


def _build_demo_plan() -> dict:
    return {
        "title": "AutoEdit Demo",
        "voice": ["voice/full.mp3"],
        "look": {"grain": 0.0, "vignette": 0.0},
        "shots": [
            {
                "id": "D01",
                "start": 0.0,
                "duration": 3.0,
                "asset": "assets/ai/I1_test_clip.mp4",
                "motion": "zoom_in",
                "motion_amount": 0.06,
                "transition_in": "fade_from_black",
                "why": "Демо: открытие ролика, плавный наезд",
                "beat": "preparation",
                "chapter": "Intro",
                "is_ai": True,
            },
            {
                "id": "D02",
                "start": 3.0,
                "duration": 2.5,
                "asset": "assets/stock/00_test_stock.mp4",
                "motion": "pan_right",
                "transition_in": "cut",
                "why": "Демо: стоковая перебивка обычной склейкой",
                "beat": "focus",
                "chapter": "Intro",
                "is_ai": False,
            },
            {
                "id": "D03",
                "start": 5.5,
                "duration": 3.0,
                "asset": "assets/ai/I1_test_clip.mp4",
                "motion": "static",
                "crop": [0.1, 0.1, 0.6, 0.6],
                "silence_before": 0.5,
                "why": "Демо: второй план с той же ИИ-картинки через crop, без слайдшоу",
                "beat": "pause",
                "chapter": "Chapter 1",
                "is_ai": True,
            },
            {
                "id": "D04",
                "start": 8.5,
                "duration": 3.5,
                "asset": "assets/stock/00_test_stock.mp4",
                "motion": "rise",
                "overlays": [
                    {
                        "asset": "assets/graphics/G1_test_greenscreen.mp4",
                        "chroma_key": "#00FF00",
                        "offset": 0.2,
                    }
                ],
                "sfx": [
                    {"asset": "assets/sfx/test_beep.wav", "offset": 0.3, "volume": 0.6}
                ],
                "why": "Демо: оверлей на хромакее и звуковой эффект",
                "beat": "reveal",
                "chapter": "Chapter 1",
                "is_ai": False,
            },
        ],
        "end_screen": {"asset": "assets/graphics/C1_test_endscreen.mp4", "duration": 4},
    }


def generate_demo_project(target_dir: Path) -> None:
    if target_dir.exists() and any(target_dir.iterdir()):
        raise DemoError(
            f"Папка {target_dir} уже существует и не пуста. "
            f"Укажите другую папку или удалите/очистите эту вручную."
        )

    _make_tone(target_dir / "voice" / "full.mp3", frequency=220, duration=12.0)
    _make_color_clip(target_dir / "assets" / "ai" / "I1_test_clip.mp4", "0x224488", 5.0, pattern="testsrc")
    _make_color_clip(target_dir / "assets" / "stock" / "00_test_stock.mp4", "0x3355AA", 5.0)
    _make_color_clip(target_dir / "assets" / "graphics" / "G1_test_greenscreen.mp4", "0x00FF00", 3.0)
    _make_color_clip(target_dir / "assets" / "graphics" / "C1_test_endscreen.mp4", "0x111133", 4.0)
    _make_tone(target_dir / "assets" / "sfx" / "test_beep.wav", frequency=880, duration=0.5)

    (target_dir / "assets" / "maps").mkdir(parents=True, exist_ok=True)
    (target_dir / "assets" / "music").mkdir(parents=True, exist_ok=True)
    (target_dir / "lut").mkdir(parents=True, exist_ok=True)

    plan_path = target_dir / "plan.json"
    plan_path.write_text(json.dumps(_build_demo_plan(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
