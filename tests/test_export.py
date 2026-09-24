import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from autoedit.align import TimedShot
from autoedit.export import export_timeline

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None


@unittest.skipUnless(FFMPEG_AVAILABLE, "FFmpeg не установлен в этом окружении")
class ExportTimelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.project_dir = Path(self._tmp.name)
        self.asset = self.project_dir / "assets" / "clip.mp4"
        self.asset.parent.mkdir(parents=True)
        subprocess.run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=blue:size=320x180:rate=30:duration=5",
                "-pix_fmt", "yuv420p", str(self.asset),
            ],
            check=True,
        )
        self.timeline = [
            TimedShot(shot={"id": "S1", "asset": "assets/clip.mp4", "why": "test"}, start=0.0, duration=3.0),
            TimedShot(shot={"id": "S2", "asset": "assets/clip.mp4"}, start=3.0, duration=2.0),
        ]

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_otio_export_creates_file(self) -> None:
        out_path = self.project_dir / "timeline.otio"
        export_timeline("Test", self.timeline, self.project_dir, out_path)
        self.assertTrue(out_path.is_file())
        self.assertIn("S1", out_path.read_text(encoding="utf-8"))

    def test_fcpxml_export_creates_file(self) -> None:
        out_path = self.project_dir / "timeline.fcpxml"
        export_timeline("Test", self.timeline, self.project_dir, out_path)
        self.assertTrue(out_path.is_file())
        content = out_path.read_text(encoding="utf-8")
        self.assertIn("fcpxml", content)


if __name__ == "__main__":
    unittest.main()
