import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from autoedit import video

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


if __name__ == "__main__":
    unittest.main()
