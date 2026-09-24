import json
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from autoedit.demo import generate_demo_project
from autoedit.render import RenderMode, preview_mode, render_project

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None


@unittest.skipUnless(FFMPEG_AVAILABLE, "FFmpeg не установлен в этом окружении")
class RenderProjectIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.project_dir = Path(self._tmp.name) / "demo"
        generate_demo_project(self.project_dir)
        self.plan = json.loads((self.project_dir / "plan.json").read_text(encoding="utf-8"))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_preview_render_produces_video_and_audio_streams(self) -> None:
        summary = render_project(self.project_dir, self.plan, preview_mode())
        self.assertTrue(summary.output_path.is_file())
        self.assertAlmostEqual(summary.total_duration, 12.0, delta=0.01)
        self.assertTrue((self.project_dir / "output" / "report.txt").is_file())

    def test_audio_only_mode_produces_no_video_file(self) -> None:
        mode = preview_mode()
        mode.audio_only = True
        summary = render_project(self.project_dir, self.plan, mode)
        self.assertTrue(summary.output_path.is_file())
        self.assertEqual(summary.output_path.suffix, ".wav")

    def test_no_sound_mode_output_has_no_audio_stream(self) -> None:
        import subprocess

        mode = preview_mode()
        mode.no_sound = True
        summary = render_project(self.project_dir, self.plan, mode)
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "default=nw=1", str(summary.output_path)],
            capture_output=True, text=True, check=True,
        )
        self.assertNotIn("audio", result.stdout)


if __name__ == "__main__":
    unittest.main()
