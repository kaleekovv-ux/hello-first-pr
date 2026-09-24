import json
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from autoedit.demo import generate_demo_project
from autoedit.render import RenderError, RenderMode, _build_debug_label, preview_mode, render_project

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

    def test_audio_only_mode_produces_black_screen_video_with_real_audio(self) -> None:
        import subprocess

        mode = preview_mode()
        mode.audio_only = True
        summary = render_project(self.project_dir, self.plan, mode)
        self.assertTrue(summary.output_path.is_file())
        self.assertEqual(summary.output_path.suffix, ".mp4")

        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "default=nw=1", str(summary.output_path)],
            capture_output=True, text=True, check=True,
        )
        self.assertIn("video", result.stdout)
        self.assertIn("audio", result.stdout)

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

    def test_music_silence_before_and_fades_are_audible_in_mix(self) -> None:
        """Демо-план: музыка 0-12с (fade_in=0.5, fade_out=1.0), у кадра
        D03 (start=5.5) есть silence_before=0.5 — музыка должна почти
        стихнуть в окне [5.0, 5.5] и звучать заметно тише в первые/
        последние доли секунды (фейды), чем в середине."""
        import subprocess

        def mean_volume_db(path: Path, start: float, dur: float) -> float:
            result = subprocess.run(
                [
                    "ffmpeg", "-ss", f"{start}", "-t", f"{dur}", "-i", str(path),
                    "-af", "volumedetect", "-f", "null", "-",
                ],
                capture_output=True, text=True, check=True,
            )
            for line in result.stderr.splitlines():
                if "mean_volume" in line:
                    return float(line.split(":")[1].strip().rstrip(" dB"))
            raise AssertionError(f"mean_volume не найден в выводе ffmpeg для {path}")

        render_project(self.project_dir, self.plan, preview_mode())
        music_muted = self.project_dir / "output" / ".cache" / "audio" / "music_muted.wav"
        self.assertTrue(music_muted.is_file())

        steady_db = mean_volume_db(music_muted, 2.0, 1.0)
        silence_db = mean_volume_db(music_muted, 5.05, 0.4)
        fade_in_db = mean_volume_db(music_muted, 0.0, 0.15)

        self.assertLess(silence_db, steady_db - 5, "музыка должна заметно стихать перед D03 (silence_before)")
        self.assertLess(fade_in_db, steady_db - 5, "музыка должна начинаться тише из-за fade_in")


class BuildDebugLabelTests(unittest.TestCase):
    def test_label_includes_id_timecode_and_anchor(self) -> None:
        label = _build_debug_label({"id": "D03", "anchor": "они меня не поверят"}, 65.5)
        self.assertIn("id=D03", label)
        self.assertIn("01:05.50", label)
        self.assertIn('anchor="они меня не поверят"', label)

    def test_label_without_anchor_omits_it(self) -> None:
        label = _build_debug_label({"id": "D01"}, 0.0)
        self.assertIn("id=D01", label)
        self.assertNotIn("anchor", label)


@unittest.skipUnless(FFMPEG_AVAILABLE, "FFmpeg не установлен в этом окружении")
class DebugModeIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.project_dir = Path(self._tmp.name) / "demo"
        generate_demo_project(self.project_dir)
        self.plan = json.loads((self.project_dir / "plan.json").read_text(encoding="utf-8"))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_debug_mode_renders_without_colliding_with_normal_preview(self) -> None:
        normal_summary = render_project(self.project_dir, self.plan, preview_mode())

        debug_mode = preview_mode()
        debug_mode.debug = True
        debug_summary = render_project(self.project_dir, self.plan, debug_mode)

        self.assertNotEqual(normal_summary.output_path, debug_summary.output_path)
        self.assertTrue(normal_summary.output_path.is_file())
        self.assertTrue(debug_summary.output_path.is_file())


if __name__ == "__main__":
    unittest.main()
