import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from autoedit import sheet

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None


def _make_clip(path: Path, color: str, duration: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c={color}:s=320x180:d={duration}",
            str(path),
        ],
        check=True,
    )


@unittest.skipUnless(FFMPEG_AVAILABLE, "FFmpeg не установлен в этом окружении")
class GenerateContactSheetsTests(unittest.TestCase):
    def test_single_sheet_for_few_videos(self) -> None:
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            _make_clip(folder / "a.mp4", "red", 3)
            _make_clip(folder / "sub" / "b.mp4", "blue", 2)

            sheets = sheet.generate_contact_sheets(folder, folder / "output")
            self.assertEqual(len(sheets), 1)
            self.assertTrue(sheets[0].is_file())
            self.assertEqual(sheets[0].name, "contact_sheet.jpg")

    def test_many_videos_split_into_multiple_sheets(self) -> None:
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for i in range(35):
                _make_clip(folder / f"clip_{i}.mp4", "green", 1)

            sheets = sheet.generate_contact_sheets(folder, folder / "output")
            self.assertEqual(len(sheets), 2)
            for p in sheets:
                self.assertTrue(p.is_file())

    def test_no_videos_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "readme.txt").write_text("nothing here")
            with self.assertRaises(sheet.SheetError):
                sheet.generate_contact_sheets(folder, folder / "output")


if __name__ == "__main__":
    unittest.main()
