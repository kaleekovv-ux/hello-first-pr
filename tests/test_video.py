import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from autoedit import video
from autoedit.align import TimedShot

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None


class BuildMotionFilterTests(unittest.TestCase):
    def test_static_motion_returns_cover_fit_chain(self) -> None:
        chain = video.build_motion_filter("static", 0.0, 1920, 1080, 3.0)
        self.assertIn("crop=1920:1080", chain)
        self.assertNotIn("scale=w='", chain)

    def test_zoom_in_uses_scale_eval_frame(self) -> None:
        chain = video.build_motion_filter("zoom_in", 0.08, 1920, 1080, 3.0)
        self.assertIn("eval=frame", chain)
        self.assertIn("t/3.000000", chain)

    def test_pan_left_uses_crop_with_time_varying_x(self) -> None:
        chain = video.build_motion_filter("pan_left", 0.08, 1920, 1080, 3.0)
        self.assertIn("crop=w=1920:h=1080:x=", chain)
        self.assertIn("t/3.000000", chain)

    def test_unknown_motion_raises(self) -> None:
        with self.assertRaises(video.VideoError):
            video.build_motion_filter("spin", 0.1, 1920, 1080, 3.0)


class PlanSpeedAndFreezeTests(unittest.TestCase):
    def test_long_enough_clip_keeps_normal_speed(self) -> None:
        speed, freeze, warn = video._plan_speed_and_freeze(10.0, 0.0, 5.0)
        self.assertEqual(speed, 1.0)
        self.assertEqual(freeze, 0.0)
        self.assertIsNone(warn)

    def test_short_clip_slows_down(self) -> None:
        speed, freeze, warn = video._plan_speed_and_freeze(4.0, 0.0, 4.5)
        self.assertAlmostEqual(speed, 0.8)
        self.assertEqual(freeze, 0.0)
        self.assertIsNone(warn)

    def test_very_short_clip_freezes_with_warning(self) -> None:
        speed, freeze, warn = video._plan_speed_and_freeze(2.0, 0.0, 5.0)
        self.assertAlmostEqual(speed, 0.8)
        self.assertGreater(freeze, 0.0)
        self.assertIsNotNone(warn)


@unittest.skipUnless(FFMPEG_AVAILABLE, "FFmpeg не установлен в этом окружении")
class RenderShotIntegrationTests(unittest.TestCase):
    def test_render_shot_produces_file_with_expected_duration(self) -> None:
        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            asset = project_dir / "assets" / "ai" / "clip.mp4"
            asset.parent.mkdir(parents=True)
            import subprocess

            subprocess.run(
                [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "color=c=red:size=640x360:rate=30:duration=5",
                    "-pix_fmt", "yuv420p", str(asset),
                ],
                check=True,
            )

            shot = {"id": "T1", "asset": "assets/ai/clip.mp4", "motion": "zoom_in", "motion_amount": 0.1}
            out_path = project_dir / "out.mp4"
            result = video.render_shot(shot, 2.0, project_dir, out_path, width=320, height=180, fps=30)

            self.assertTrue(out_path.is_file())
            duration = video.probe_duration(out_path)
            self.assertAlmostEqual(duration, 2.0, delta=0.15)
            self.assertEqual(result.warnings, [])

    def test_render_shot_with_text_and_look_does_not_crash(self) -> None:
        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            asset = project_dir / "assets" / "ai" / "clip.mp4"
            asset.parent.mkdir(parents=True)
            import subprocess

            subprocess.run(
                [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "color=c=red:size=640x360:rate=30:duration=5",
                    "-pix_fmt", "yuv420p", str(asset),
                ],
                check=True,
            )

            shot = {
                "id": "T2",
                "asset": "assets/ai/clip.mp4",
                "motion": "static",
                "text": "3 DAYS WITHOUT WATER",
                "text_style": {"highlight": "3 DAYS", "position": "top"},
            }
            out_path = project_dir / "out.mp4"
            result = video.render_shot(
                shot, 2.0, project_dir, out_path, width=320, height=180, fps=30,
                look={"grain": 0.3, "vignette": 0.3},
            )

            self.assertTrue(out_path.is_file())
            self.assertTrue(video.has_video_stream(out_path))
            self.assertEqual(result.warnings, [])

    def test_missing_asset_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            shot = {"id": "T1", "asset": "assets/ai/missing.mp4"}
            with self.assertRaises(video.VideoError):
                video.render_shot(shot, 2.0, project_dir, project_dir / "out.mp4")

    def test_source_start_beyond_clip_end_still_produces_video(self) -> None:
        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            asset = project_dir / "assets" / "stock" / "clip.mp4"
            asset.parent.mkdir(parents=True)
            import subprocess

            subprocess.run(
                [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=12",
                    "-pix_fmt", "yuv420p", str(asset),
                ],
                check=True,
            )

            # source_start (19s) намного больше длины исходника (12s) —
            # раньше это давало пустой (0 кадров) выходной файл.
            shot = {"id": "T1", "asset": "assets/stock/clip.mp4", "source_start": 19.0, "motion": "zoom_in"}
            out_path = project_dir / "out.mp4"
            result = video.render_shot(shot, 3.0, project_dir, out_path, width=320, height=180, fps=30)

            self.assertTrue(out_path.is_file())
            duration = video.probe_duration(out_path)
            self.assertAlmostEqual(duration, 3.0, delta=0.15)
            self.assertTrue(any("source_start" in w for w in result.warnings))


@unittest.skipUnless(FFMPEG_AVAILABLE, "FFmpeg не установлен в этом окружении")
class HasVideoStreamTests(unittest.TestCase):
    def test_broken_empty_file_reports_false(self) -> None:
        with TemporaryDirectory() as tmp:
            broken = Path(tmp) / "broken.mp4"
            broken.write_bytes(b"\x00" * 304)
            self.assertFalse(video.has_video_stream(broken))

    def test_real_clip_reports_true(self) -> None:
        import subprocess

        with TemporaryDirectory() as tmp:
            clip = Path(tmp) / "clip.mp4"
            subprocess.run(
                [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "color=c=blue:size=320x180:rate=30:duration=1",
                    "-pix_fmt", "yuv420p", str(clip),
                ],
                check=True,
            )
            self.assertTrue(video.has_video_stream(clip))


class ResolveFitModeTests(unittest.TestCase):
    def test_explicit_fit_wins(self) -> None:
        self.assertEqual(video.resolve_fit_mode({"fit": "contain"}, (1920, 1080), 1920, 1080), "contain")

    def test_matching_orientation_defaults_to_cover(self) -> None:
        self.assertEqual(video.resolve_fit_mode({}, (1920, 1080), 1920, 1080), "cover")
        self.assertEqual(video.resolve_fit_mode({}, (1080, 1920), 1080, 1920), "cover")

    def test_mismatched_orientation_defaults_to_blur_fill(self) -> None:
        self.assertEqual(video.resolve_fit_mode({}, (1080, 1920), 1920, 1080), "blur_fill")
        self.assertEqual(video.resolve_fit_mode({}, (1920, 1080), 1080, 1920), "blur_fill")

    def test_unknown_dimensions_defaults_to_cover(self) -> None:
        self.assertEqual(video.resolve_fit_mode({}, None, 1920, 1080), "cover")


@unittest.skipUnless(FFMPEG_AVAILABLE, "FFmpeg не установлен в этом окружении")
class FitModeRenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.project_dir = Path(self._tmp.name)
        self.asset = self.project_dir / "vertical.mp4"
        subprocess.run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=red:size=608x1080:rate=30:duration=3",
                "-pix_fmt", "yuv420p", str(self.asset),
            ],
            check=True,
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_vertical_source_in_horizontal_project_auto_blur_fill(self) -> None:
        shot = {"id": "V1", "asset": "vertical.mp4", "motion": "static"}
        out_path = self.project_dir / "out.mp4"
        result = video.render_shot(shot, 2.0, self.project_dir, out_path, width=640, height=360, fps=30)
        self.assertTrue(out_path.is_file())
        self.assertEqual(video.probe_dimensions(out_path), (640, 360))
        self.assertEqual(result.warnings, [])

    def test_explicit_contain_letterboxes(self) -> None:
        shot = {"id": "V2", "asset": "vertical.mp4", "motion": "static", "fit": "contain"}
        out_path = self.project_dir / "out.mp4"
        result = video.render_shot(shot, 2.0, self.project_dir, out_path, width=640, height=360, fps=30)
        self.assertTrue(out_path.is_file())
        self.assertEqual(video.probe_dimensions(out_path), (640, 360))
        self.assertEqual(result.warnings, [])


class BuildTextFiltersTests(unittest.TestCase):
    def test_bundled_font_is_found(self) -> None:
        self.assertTrue(video.BUNDLED_FONT.is_file())
        self.assertEqual(video.find_bold_font(), str(video.BUNDLED_FONT))

    def test_simple_text_produces_one_drawtext(self) -> None:
        filters, warn = video.build_text_filters("HELLO", None, 1920, 1080, 3.0)
        self.assertIsNone(warn)
        self.assertEqual(len(filters), 1)
        self.assertIn("text='HELLO'", filters[0])

    def test_highlight_splits_into_two_drawtext_with_gap_preserved(self) -> None:
        filters, warn = video.build_text_filters(
            "3 DAYS WITHOUT WATER", {"highlight": "3 DAYS"}, 1920, 1080, 3.0
        )
        self.assertIsNone(warn)
        self.assertEqual(len(filters), 2)
        self.assertIn("text='3 DAYS'", filters[0])
        self.assertIn(video.DEFAULT_HIGHLIGHT_COLOR, filters[0])
        self.assertIn("text='WITHOUT WATER'", filters[1])

    def test_long_text_wraps_to_two_lines(self) -> None:
        long_text = "THIS IS A VERY LONG TITLE THAT SHOULD NOT FIT ON ONE LINE AT ALL"
        filters, warn = video.build_text_filters(long_text, {"size": 96}, 1920, 1080, 3.0)
        self.assertIsNone(warn)
        y_values = set()
        for f in filters:
            for part in f.split(":"):
                if part.startswith("y="):
                    y_values.add(part)
        self.assertGreaterEqual(len(y_values), 2)

    def test_fade_in_out_alpha_expression_present(self) -> None:
        filters, _warn = video.build_text_filters("HI", None, 1920, 1080, 3.0)
        self.assertIn(f"alpha='if(lt(t,{video.TEXT_FADE_IN})", filters[0])


@unittest.skipUnless(FFMPEG_AVAILABLE, "FFmpeg не установлен в этом окружении")
class CheckSourceQualityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.project_dir = Path(self._tmp.name)

        def make(name: str, color: str, size: str, duration: float, fps: int = 30) -> None:
            path = self.project_dir / name
            subprocess.run(
                [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", f"color=c={color}:s={size}:rate={fps}:d={duration}",
                    "-pix_fmt", "yuv420p", str(path),
                ],
                check=True,
            )

        make("dark.mp4", "black", "1920x1080", 3)
        make("bright.mp4", "white", "1920x1080", 3)
        make("small.mp4", "gray", "640x360", 3)
        make("fps24.mp4", "gray", "1920x1080", 3, fps=24)
        make("normal.mp4", "gray", "1920x1080", 20)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_flags_underexposed_source(self) -> None:
        timeline = [TimedShot(shot={"id": "S1", "asset": "dark.mp4"}, start=0.0, duration=2.0)]
        warnings = video.check_source_quality(timeline, self.project_dir, 1920, 1080)
        self.assertTrue(any("слишком тёмным" in w for w in warnings))

    def test_flags_overexposed_source(self) -> None:
        timeline = [TimedShot(shot={"id": "S1", "asset": "bright.mp4"}, start=0.0, duration=2.0)]
        warnings = video.check_source_quality(timeline, self.project_dir, 1920, 1080)
        self.assertTrue(any("пересвеченным" in w for w in warnings))

    def test_flags_low_resolution_source(self) -> None:
        timeline = [TimedShot(shot={"id": "S1", "asset": "small.mp4"}, start=0.0, duration=2.0)]
        warnings = video.check_source_quality(timeline, self.project_dir, 1920, 1080)
        self.assertTrue(any("меньше 1080p" in w for w in warnings))

    def test_flags_non_30fps_source(self) -> None:
        timeline = [TimedShot(shot={"id": "S1", "asset": "fps24.mp4"}, start=0.0, duration=2.0)]
        warnings = video.check_source_quality(timeline, self.project_dir, 1920, 1080)
        self.assertTrue(any("24" in w and "к/с" in w for w in warnings))

    def test_flags_same_source_over_12_seconds_in_a_row(self) -> None:
        timeline = [
            TimedShot(shot={"id": "S1", "asset": "normal.mp4"}, start=0.0, duration=7.0),
            TimedShot(shot={"id": "S2", "asset": "normal.mp4"}, start=7.0, duration=7.0),
        ]
        warnings = video.check_source_quality(timeline, self.project_dir, 1920, 1080)
        self.assertTrue(any("подряд" in w and "14.0" in w for w in warnings))

    def test_no_warnings_for_clean_short_normal_source(self) -> None:
        timeline = [TimedShot(shot={"id": "S1", "asset": "normal.mp4"}, start=0.0, duration=5.0)]
        warnings = video.check_source_quality(timeline, self.project_dir, 1920, 1080)
        self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
