import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from autoedit import audio

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None


def _make_tone(path: Path, duration: float = 3.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"sine=frequency=220:duration={duration}",
            str(path),
        ],
        check=True,
    )


class ParseLoudnormJsonTests(unittest.TestCase):
    def test_parses_json_when_nothing_follows_it(self) -> None:
        stderr = (
            '[Parsed_loudnorm_0 @ 0x0]\n'
            '{\n'
            '\t"input_i" : "-23.00",\n'
            '\t"input_tp" : "-2.00",\n'
            '\t"input_lra" : "1.00",\n'
            '\t"input_thresh" : "-33.00",\n'
            '\t"output_i" : "-14.00",\n'
            '\t"output_tp" : "-1.00",\n'
            '\t"output_lra" : "1.00",\n'
            '\t"output_thresh" : "-24.00",\n'
            '\t"normalization_type" : "dynamic",\n'
            '\t"target_offset" : "0.00"\n'
            '}\n'
        )
        result = audio._parse_loudnorm_json(stderr, "тест")
        self.assertEqual(result["input_i"], "-23.00")

    def test_parses_json_when_ffmpeg_prints_trailing_stats_after_it(self) -> None:
        # Реальный случай: на некоторых сборках FFmpeg после JSON-отчёта
        # loudnorm в stderr идёт ещё итоговая строка статистики муксинга —
        # старый парсер (json.loads до конца строки) падал на этом с
        # "Extra data".
        stderr = (
            '{\n'
            '\t"input_i" : "-23.00",\n'
            '\t"input_tp" : "-2.00",\n'
            '\t"input_lra" : "1.00",\n'
            '\t"input_thresh" : "-33.00",\n'
            '\t"output_i" : "-14.00",\n'
            '\t"output_tp" : "-1.00",\n'
            '\t"output_lra" : "1.00",\n'
            '\t"output_thresh" : "-24.00",\n'
            '\t"normalization_type" : "dynamic",\n'
            '\t"target_offset" : "0.00"\n'
            '}\n'
            'size=N/A time=00:00:12.34 bitrate=N/A speed=45.2x\n'
            'video:0kB audio:100kB subtitle:0kB other streams:0kB global headers:0kB muxing overhead: unknown\n'
        )
        result = audio._parse_loudnorm_json(stderr, "тест")
        self.assertEqual(result["output_i"], "-14.00")

    def test_no_json_object_raises_audio_error(self) -> None:
        with self.assertRaises(audio.AudioError):
            audio._parse_loudnorm_json("нет никакого json здесь", "тест")


@unittest.skipUnless(FFMPEG_AVAILABLE, "FFmpeg не установлен в этом окружении")
class RenderAudioIntegrationTests(unittest.TestCase):
    def test_full_pipeline_produces_correct_duration(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            voice = tmp_path / "voice.mp3"
            music = tmp_path / "music.mp3"
            sfx = tmp_path / "sfx.wav"
            _make_tone(voice, 6.0)
            _make_tone(music, 6.0)
            _make_tone(sfx, 0.3)

            out_path = tmp_path / "final.wav"
            achieved_lufs = audio.render_audio(
                voice_paths=[voice],
                music_cues=[audio.MusicCue(asset=music, start=0.0, end=6.0, volume=0.2, fade_in=0.2, fade_out=0.5)],
                sfx_cues=[audio.SfxCue(asset=sfx, time=3.0, volume=0.5)],
                silence_windows=[(2.0, 2.5)],
                total_duration=6.0,
                out_path=out_path,
                work_dir=tmp_path / "work",
            )

            self.assertTrue(out_path.is_file())
            duration_result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nk=1:nw=1", str(out_path)],
                capture_output=True, text=True, check=True,
            )
            self.assertAlmostEqual(float(duration_result.stdout.strip()), 6.0, delta=0.2)

            # Двухпроходный loudnorm должен попадать в цель заметно точнее
            # однопроходного (тот на реальном проекте давал -16.7 вместо -14).
            self.assertAlmostEqual(achieved_lufs, audio.TARGET_LUFS, delta=1.0)

    def test_empty_music_and_sfx_still_produce_output(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            voice = tmp_path / "voice.mp3"
            _make_tone(voice, 3.0)

            out_path = tmp_path / "final.wav"
            audio.render_audio(
                voice_paths=[voice],
                music_cues=[],
                sfx_cues=[],
                silence_windows=[],
                total_duration=3.0,
                out_path=out_path,
                work_dir=tmp_path / "work",
            )
            self.assertTrue(out_path.is_file())


if __name__ == "__main__":
    unittest.main()
