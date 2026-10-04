"""Контент для Instagram-магазина (`autoedit shop`).

Из папки вещи (фото/видео + item.txt с брендом, размером и ценой) делает
в подпапке «готово»: картинки для Stories 1080×1920, вертикальный Reels
(MP4 без звука — музыку добавляют в Instagram) и текст подписи к посту.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from . import video
from .align import TimedShot

WIDTH, HEIGHT, FPS = 1080, 1920, 30
OUTPUT_DIR_NAME = "готово"
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".avi", ".mkv"}
MAX_MEDIA_IN_REEL = 6
ACCENT = (255, 199, 44)
CROSSFADE = 0.3

_KEYS = {
    "бренд": "brand", "brand": "brand",
    "название": "name", "name": "name", "вещь": "name",
    "размер": "size", "size": "size",
    "цена": "price", "price": "price",
    "старая цена": "old_price", "было": "old_price", "old price": "old_price",
    "ник": "nick", "nick": "nick",
}


class ShopError(Exception):
    """Не удалось собрать контент для вещи."""


@dataclass
class Item:
    folder: Path
    price: str
    brand: str = ""
    name: str = ""
    size: str = ""
    old_price: str = ""
    nick: str = ""
    media: list[Path] = field(default_factory=list)


def load_item(folder: Path, default_nick: str = "") -> Item:
    info_path = folder / "item.txt"
    if not info_path.is_file():
        raise ShopError(f"В папке {folder} нет файла item.txt (бренд, размер, цена)")
    values: dict[str, str] = {}
    for raw in info_path.read_text(encoding="utf-8-sig").splitlines():
        if ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        mapped = _KEYS.get(key.strip().lower())
        if mapped and value.strip():
            values[mapped] = value.strip()
    if "price" not in values:
        raise ShopError(f'В {info_path} не указана строка "цена: ..."')

    heic = sorted(p.name for p in folder.iterdir() if p.suffix.lower() in {".heic", ".heif"})
    if heic:
        raise ShopError(
            f"Фото в формате HEIC ({', '.join(heic)}) программа не читает — "
            f"сохраните их как JPG (на iPhone: Настройки → Камера → Форматы → Наиболее совместимый)."
        )
    media = sorted(
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in video.IMAGE_SUFFIXES | VIDEO_SUFFIXES
    )
    if not media:
        raise ShopError(f"В папке {folder} нет фото или видео вещи")

    values.setdefault("nick", default_nick)
    return Item(folder=folder, media=media, **values)


def _font(size: int) -> ImageFont.FreeTypeFont:
    path = video.find_bold_font()
    if not path:
        raise ShopError("Не найден шрифт для надписей")
    return ImageFont.truetype(path, size)


def _fit_vertical(image: Image.Image) -> Image.Image:
    """Вертикальное фото — обрезка по центру; горизонтальное/квадратное —
    целиком поверх размытой копии (чтобы не отрезать вещь)."""
    image = ImageOps.exif_transpose(image).convert("RGB")
    if image.width / image.height <= 0.8:  # 9:16 и 3:4 (обычное фото с телефона)
        return ImageOps.fit(image, (WIDTH, HEIGHT), Image.LANCZOS)
    background = ImageOps.fit(image, (WIDTH, HEIGHT), Image.LANCZOS).filter(ImageFilter.GaussianBlur(40))
    background = Image.eval(background, lambda v: int(v * 0.6))
    foreground = ImageOps.contain(image, (WIDTH, HEIGHT), Image.LANCZOS)
    background.paste(foreground, ((WIDTH - foreground.width) // 2, (HEIGHT - foreground.height) // 2))
    return background


def _first_frame(path: Path, out_path: Path) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", "0.5", "-i", str(path), "-frames:v", "1", str(out_path)],
        capture_output=True, check=False,
    )
    if not out_path.is_file():
        raise ShopError(f"Не удалось взять кадр из видео {path.name}")
    return out_path


def _bottom_shade(canvas: Image.Image, height_share: float) -> None:
    top = int(HEIGHT * (1 - height_share))
    shade = Image.new("L", (1, HEIGHT - top))
    for y in range(HEIGHT - top):
        shade.putpixel((0, y), int(215 * (y / (HEIGHT - top)) ** 0.8))
    black = Image.new("RGB", (WIDTH, HEIGHT - top), (0, 0, 0))
    canvas.paste(black, (0, top), shade.resize((WIDTH, HEIGHT - top)))


def _draw_price(draw: ImageDraw.ImageDraw, item: Item, x: int, y: int, size: int) -> None:
    price_text = f"{item.price} грн"
    price_font = _font(size)
    draw.text((x, y), price_text, font=price_font, fill=ACCENT)
    if item.old_price:
        price_box = draw.textbbox((x, y), price_text, font=price_font)
        old_font = _font(size // 2)
        old_text = f"{item.old_price} грн"
        ox = price_box[2] + 30
        probe = draw.textbbox((ox, 0), old_text, font=old_font)
        oy = price_box[3] - probe[3]  # нижние края новой и старой цены на одной линии
        draw.text((ox, oy), old_text, font=old_font, fill=(200, 200, 200))
        old_box = draw.textbbox((ox, oy), old_text, font=old_font)
        line_y = (old_box[1] + old_box[3]) // 2
        draw.line((old_box[0] - 4, line_y, old_box[2] + 4, line_y), fill=(230, 60, 60), width=6)


def make_story(item: Item, photo: Image.Image, full_card: bool, out_path: Path) -> None:
    canvas = _fit_vertical(photo)
    _bottom_shade(canvas, 0.45 if full_card else 0.22)
    draw = ImageDraw.Draw(canvas)
    margin = 70

    # Верх (~200px) закрывает строка с ником аккаунта, низ (~250px) — поле
    # «Отправить сообщение»: всё важное держим между ними.
    if item.nick:
        draw.text((margin, 230), item.nick, font=_font(44), fill=(255, 255, 255), stroke_width=3, stroke_fill=(0, 0, 0))

    if full_card:
        y = HEIGHT - 780
        if item.brand:
            draw.text((margin, y), item.brand.upper(), font=_font(96), fill=(255, 255, 255))
            y += 120
        if item.name:
            draw.text((margin, y), item.name, font=_font(56), fill=(235, 235, 235))
            y += 80
        if item.size:
            draw.text((margin, y), f"Размер: {item.size}", font=_font(56), fill=(235, 235, 235))
            y += 100
        _draw_price(draw, item, margin, y, 120)
        draw.text((margin, HEIGHT - 300), "Пиши «хочу» в директ", font=_font(52), fill=(255, 255, 255))
    else:
        if item.size:
            draw.text((margin, HEIGHT - 450), f"Размер {item.size}", font=_font(56), fill=(255, 255, 255))
        _draw_price(draw, item, margin, HEIGHT - 370, 100)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, quality=92)


def _make_end_card(item: Item, out_path: Path) -> None:
    canvas = Image.new("RGB", (WIDTH, HEIGHT), (12, 12, 12))
    draw = ImageDraw.Draw(canvas)
    lines = [("ПИШИ В ДИРЕКТ", _font(110), (255, 255, 255)), (f"{item.price} грн", _font(150), ACCENT)]
    if item.nick:
        lines.append((item.nick, _font(60), (200, 200, 200)))
    total = sum(f.size + 50 for _, f, _ in lines)
    y = (HEIGHT - total) // 2
    for text, font, color in lines:
        draw.text(((WIDTH - draw.textlength(text, font=font)) / 2, y), text, font=font, fill=color)
        y += font.size + 50
    canvas.save(out_path)


def _reel_shots(item: Item, end_card_rel: str) -> list[dict]:
    media = item.media[:MAX_MEDIA_IN_REEL]
    title = item.brand.upper() or item.name
    details = "\n".join(p for p in (item.name if item.brand else "", f"размер {item.size}" if item.size else "") if p)
    price_text = f"{item.price} грн" + (f"\nвместо {item.old_price} грн" if item.old_price else "")
    motions = ["zoom_in", "zoom_out", "pan_left", "pan_right"]

    shots: list[dict] = []
    if len(media) == 1:
        # Одно фото — два разных плана с него, а не одна статичная картинка.
        media = [media[0], media[0]]
    for i, path in enumerate(media):
        last = i == len(media) - 1
        shot: dict = {
            "id": f"M{i + 1}",
            "asset": path.name,
            "motion": motions[i % len(motions)],
            "motion_amount": 0.08,
            "transition_in": "crossfade" if i else "cut",
            "transition_duration": CROSSFADE,
        }
        if i == 1 and media[0] == media[1]:
            shot["crop"] = [0.15, 0.1, 0.7, 0.7]
        if i == 0 and title:
            shot["text"] = title
            shot["text_style"] = {"position": "top", "size": 210}
        elif i == 1 and details and not last:
            shot["text"] = details
            shot["text_style"] = {"position": "lower", "size": 170}
        elif last:
            shot["text"] = price_text
            shot["text_style"] = {"position": "lower", "size": 190, "highlight": f"{item.price} грн"}
        shot["_seconds"] = 2.6 if i == 0 or last else 1.8
        shots.append(shot)

    shots.append(
        {"id": "END", "asset": end_card_rel, "motion": "static", "transition_in": "crossfade",
         "transition_duration": CROSSFADE, "_seconds": 2.0}
    )
    return shots


def make_reel(item: Item, work_dir: Path, out_path: Path) -> None:
    from .render import _layout_video  # тот же расчёт, что держит склейки на месте при crossfade

    end_card = work_dir / "end_card.png"
    _make_end_card(item, end_card)
    shots = _reel_shots(item, end_card.relative_to(item.folder).as_posix())

    timeline: list[TimedShot] = []
    t = 0.0
    for shot in shots:
        seconds = shot.pop("_seconds")
        timeline.append(TimedShot(shot=shot, start=t, duration=seconds))
        t += seconds

    durations, _gaps, transitions = _layout_video(timeline)
    clips: list[Path] = []
    for timed, seconds in zip(timeline, durations):
        clip = work_dir / f"{timed.shot['id']}.mp4"
        video.render_shot(timed.shot, seconds, item.folder, clip, WIDTH, HEIGHT, FPS)
        clips.append(clip)

    assembled = work_dir / "reel_raw.mp4"
    video.assemble_video(clips, transitions, assembled, WIDTH, HEIGHT, FPS)
    result = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(assembled),
         "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-pix_fmt", "yuv420p",
         "-movflags", "+faststart", "-an", str(out_path)],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise ShopError(f"Не удалось сохранить Reels: {result.stderr.strip()[-300:]}")


def make_caption(item: Item) -> str:
    lines = [" — ".join(p for p in (item.brand, item.name) if p)]
    if item.size:
        lines.append(f"Размер: {item.size}")
    price = f"Цена: {item.price} грн"
    if item.old_price:
        price += f" (в магазине {item.old_price} грн)"
    lines += [price, "", "Оригинал из Европы, в одном экземпляре.", "Пишите «хочу» в директ — отложим для вас."]
    tags = ["#одежда", "#брендоваяодежда", "#оригинал", "#шопинг"]
    if item.brand:
        tags.insert(0, "#" + "".join(ch for ch in item.brand.lower() if ch.isalnum()))
    lines += ["", " ".join(tags)]
    return "\n".join(line for line in lines if line is not None).strip() + "\n"


def build_item_content(folder: Path, default_nick: str = "", progress=None) -> list[Path]:
    item = load_item(folder, default_nick)
    out_dir = folder / OUTPUT_DIR_NAME
    work_dir = out_dir / ".служебное"
    work_dir.mkdir(parents=True, exist_ok=True)

    def say(message: str) -> None:
        if progress:
            progress(message)

    say("Stories...")
    outputs: list[Path] = []
    for i, path in enumerate(item.media[:MAX_MEDIA_IN_REEL], start=1):
        source = path if path.suffix.lower() in video.IMAGE_SUFFIXES else _first_frame(path, work_dir / f"frame_{i}.png")
        story = out_dir / f"story_{i}.jpg"
        with Image.open(source) as photo:
            make_story(item, photo, full_card=(i == 1), out_path=story)
        outputs.append(story)

    say("Reels (это самая долгая часть)...")
    reel = out_dir / "reels.mp4"
    make_reel(item, work_dir, reel)
    outputs.append(reel)

    caption = out_dir / "подпись.txt"
    caption.write_text(make_caption(item), encoding="utf-8")
    outputs.append(caption)
    return outputs


def find_item_folders(folder: Path) -> list[Path]:
    """Сама папка вещи или папка, в которой лежит много папок вещей."""
    if (folder / "item.txt").is_file():
        return [folder]
    return sorted(p for p in folder.iterdir() if p.is_dir() and (p / "item.txt").is_file())
