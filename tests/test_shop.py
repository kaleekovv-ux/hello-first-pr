import argparse
import shutil
import subprocess
import unittest
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image

from autoedit import cli, shop, video

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None

ITEM_TXT = "бренд: Tommy Hilfiger\nназвание: Худи оверсайз\nразмер: M\nцена: 2499\nстарая цена: 4200\nник: @shop\n"


def _make_item(folder: Path, photos: list[tuple[int, int]], text: str = ITEM_TXT) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for i, size in enumerate(photos, start=1):
        Image.new("RGB", size, (40, 70, 140)).save(folder / f"{i}.jpg")
    (folder / "item.txt").write_text(text, encoding="utf-8")
    return folder


class LoadItemTests(unittest.TestCase):
    def test_reads_fields_even_with_windows_bom_and_mixed_case(self) -> None:
        with TemporaryDirectory() as tmp:
            folder = _make_item(Path(tmp), [(800, 1200)], text="")
            (folder / "item.txt").write_text("﻿Бренд: Zara\nЦена: 999\nразмер: S\n", encoding="utf-8")
            item = shop.load_item(folder, default_nick="@default")
            self.assertEqual((item.brand, item.price, item.size, item.nick), ("Zara", "999", "S", "@default"))
            self.assertEqual(len(item.media), 1)

    def test_missing_price_is_a_clear_error(self) -> None:
        with TemporaryDirectory() as tmp:
            folder = _make_item(Path(tmp), [(800, 1200)], text="бренд: Zara\n")
            with self.assertRaisesRegex(shop.ShopError, "цена"):
                shop.load_item(folder)

    def test_heic_photos_are_rejected_with_a_hint(self) -> None:
        with TemporaryDirectory() as tmp:
            folder = _make_item(Path(tmp), [(800, 1200)])
            (folder / "IMG_0001.HEIC").write_bytes(b"fake")
            with self.assertRaisesRegex(shop.ShopError, "JPG"):
                shop.load_item(folder)


class StoryAndCaptionTests(unittest.TestCase):
    def test_story_is_1080x1920_for_vertical_and_horizontal_photos(self) -> None:
        with TemporaryDirectory() as tmp:
            folder = _make_item(Path(tmp), [(1200, 1600)])
            item = shop.load_item(folder)
            for size in [(1200, 1600), (1600, 1200)]:
                out = Path(tmp) / f"story_{size[0]}.jpg"
                shop.make_story(item, Image.new("RGB", size, (40, 70, 140)), full_card=True, out_path=out)
                with Image.open(out) as story:
                    self.assertEqual(story.size, (1080, 1920))

    def test_caption_has_price_size_and_brand_hashtag(self) -> None:
        with TemporaryDirectory() as tmp:
            item = shop.load_item(_make_item(Path(tmp), [(800, 1200)]))
            caption = shop.make_caption(item)
            self.assertIn("Размер: M", caption)
            self.assertIn("2499 грн", caption)
            self.assertIn("4200 грн", caption)
            self.assertIn("#tommyhilfiger", caption)

    def test_reel_puts_details_and_price_on_two_lines(self) -> None:
        with TemporaryDirectory() as tmp:
            item = shop.load_item(_make_item(Path(tmp), [(800, 1200), (800, 1200), (800, 1200)]))
            shots = shop._reel_shots(item, "end.png")
            self.assertEqual(shots[0]["text"], "TOMMY HILFIGER")
            self.assertEqual(shots[1]["text"], "Худи оверсайз\nразмер M")
            self.assertEqual(shots[2]["text"], "2499 грн\nвместо 4200 грн")
            self.assertEqual(shots[-1]["id"], "END")


@unittest.skipUnless(FFMPEG_AVAILABLE, "FFmpeg не установлен в этом окружении")
class ShopCommandIntegrationTests(unittest.TestCase):
    def test_batch_folder_builds_stories_reel_and_caption_and_reports_bad_item(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            good = _make_item(root / "01_hoodie", [(1200, 1600), (1600, 1200)])
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=720x1280:rate=30:duration=2",
                 "-pix_fmt", "yuv420p", str(good / "3_tryon.mp4")],
                check=True,
            )
            _make_item(root / "02_broken", [(800, 1200)], text="бренд: без цены\n")

            with patch("sys.stdout", new_callable=StringIO) as out:
                code = cli.cmd_shop(argparse.Namespace(folder=str(root), nick=None))

            self.assertEqual(code, 1)
            self.assertIn("02_broken", out.getvalue())
            done = good / shop.OUTPUT_DIR_NAME
            self.assertEqual(sorted(p.name for p in done.glob("story_*.jpg")), ["story_1.jpg", "story_2.jpg", "story_3.jpg"])
            self.assertTrue((done / "подпись.txt").is_file())
            reel = done / "reels.mp4"
            self.assertEqual(video.probe_dimensions(reel), (1080, 1920))
            # 2.6 (первый) + 1.8 + 2.6 (последний) + 2.0 (концовка); crossfade не укорачивает ролик
            self.assertAlmostEqual(video.probe_duration(reel), 9.0, delta=0.15)


if __name__ == "__main__":
    unittest.main()
