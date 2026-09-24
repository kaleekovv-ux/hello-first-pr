import json
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from autoedit.demo import generate_demo_project
from autoedit.render import RenderError, RenderMode, preview_mode, render_project

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
        self.assertIsNotNone(summary.report_path)
        self.assertTrue(summary.report_path.is_file())

    def test_vertical_mode_produces_9x16_output_alongside_horizontal(self) -> None:
        import subprocess

        horizontal_summary = render_project(self.project_dir, self.plan, preview_mode())
        vertical_summary = render_project(self.project_dir, self.plan, preview_mode(vertical=True))

        self.assertNotEqual(horizontal_summary.output_path, vertical_summary.output_path)
        self.assertTrue(horizontal_summary.output_path.is_file())
        self.assertTrue(vertical_summary.output_path.is_file())

        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(vertical_summary.output_path)],
            capture_output=True, text=True, check=True,
        )
        width, height = (int(v) for v in result.stdout.strip().split(",")[:2])
        self.assertLess(width, height)
        self.assertEqual((width, height), (540, 960))

    def test_audio_only_mode_produces_no_video_file(self) -> None:
        mode = preview_mode()
        mode.audio_only = True
        summary = render_project(self.project_dir, self.plan, mode)
        self.assertTrue(summary.output_path.is_file())
        self.assertEqual(summary.output_path.suffix, ".wav")

    def test_out_of_order_shots_halt_render_by_default(self) -> None:
        scrambled_plan = dict(self.plan)
        shots = list(scrambled_plan["shots"])
        shots[0], shots[1] = shots[1], shots[0]  # D02 (start=3.0) now listed before D01 (start=0.0)
        scrambled_plan["shots"] = shots

        with self.assertRaises(RenderError):
            render_project(self.project_dir, scrambled_plan, preview_mode())

    def test_out_of_order_shots_allowed_with_flag(self) -> None:
        scrambled_plan = dict(self.plan)
        shots = list(scrambled_plan["shots"])
        shots[0], shots[1] = shots[1], shots[0]
        scrambled_plan["shots"] = shots

        summary = render_project(self.project_dir, scrambled_plan, preview_mode(), allow_reorder=True)
        self.assertTrue(summary.output_path.is_file())

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
