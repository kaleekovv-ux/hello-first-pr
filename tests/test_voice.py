import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from autoedit import voice

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None


class LoadScriptTests(unittest.TestCase):
    def test_reads_nonempty_lines(self) -> None:
        with TemporaryDirectory() as tmp:
            script_path = Path(tmp) / "script.txt"
            script_path.write_text("Line one.\n\nLine two.\n   \nLine three.\n", encoding="utf-8")
            lines = voice.load_script(script_path)
            self.assertEqual(lines, ["Line one.", "Line two.", "Line three."])

    def test_missing_file_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaises(voice.VoiceError):
                voice.load_script(Path(tmp) / "missing.txt")


class FindAudioChunksTests(unittest.TestCase):
    def test_finds_only_audio_files(self) -> None:
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "a.mp3").write_bytes(b"\x00")
            (folder / "b.wav").write_bytes(b"\x00")
            (folder / "notes.txt").write_text("x")
            chunks = voice.find_audio_chunks(folder)
            self.assertEqual({p.name for p in chunks}, {"a.mp3", "b.wav"})

    def test_empty_folder_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaises(voice.VoiceError):
                voice.find_audio_chunks(Path(tmp))


class MatchChunksToScriptTests(unittest.TestCase):
    def test_matches_in_script_order_regardless_of_dict_order(self) -> None:
        script = ["Imagine leaving home", "Now imagine coming back", "no engine"]
        chunk_texts = {
            Path("c_back.mp3"): "now imagine coming back fourteen months later",
            Path("a_home.mp3"): "imagine leaving home for a trip",
            Path("b_engine.mp3"): "with no engine no radio",
        }
        report = voice.match_chunks_to_script(script, chunk_texts)
        self.assertEqual([m.script_line for m in report.matches], script)
        self.assertEqual(report.matches[0].chunk.path.name, "a_home.mp3")
        self.assertEqual(report.matches[1].chunk.path.name, "c_back.mp3")
        self.assertEqual(report.matches[2].chunk.path.name, "b_engine.mp3")
        self.assertEqual(report.unused_chunks, [])

    def test_missing_line_reports_no_chunk(self) -> None:
        script = ["Imagine leaving home", "A line nobody recorded"]
        chunk_texts = {Path("a.mp3"): "imagine leaving home for a trip"}
        report = voice.match_chunks_to_script(script, chunk_texts)
        self.assertIsNotNone(report.matches[0].chunk)
        self.assertIsNone(report.matches[1].chunk)

    def test_unrelated_chunk_is_unused(self) -> None:
        script = ["Imagine leaving home"]
        chunk_texts = {
            Path("a.mp3"): "imagine leaving home for a trip",
            Path("junk.mp3"): "completely unrelated recording about cats",
        }
        report = voice.match_chunks_to_script(script, chunk_texts)
        self.assertEqual([p.name for p in report.unused_chunks], ["junk.mp3"])

    def test_duplicate_takes_latest_timestamp(self) -> None:
        script = ["Imagine leaving home"]
        chunk_texts = {
            Path("ElevenLabs_2026-09-22T19_38_41_take1.mp3"): "imagine leaving home for a trip",
            Path("ElevenLabs_2026-09-22T20_10_07_take2.mp3"): "imagine leaving home for a trip",
        }
        report = voice.match_chunks_to_script(script, chunk_texts)
        self.assertEqual(report.matches[0].chunk.path.name, "ElevenLabs_2026-09-22T20_10_07_take2.mp3")
        self.assertEqual(len(report.duplicates), 1)
        self.assertEqual(report.duplicates[0][1].name, "ElevenLabs_2026-09-22T19_38_41_take1.mp3")


@unittest.skipUnless(FFMPEG_AVAILABLE, "FFmpeg не установлен в этом окружении")
class ConcatenateChunksTests(unittest.TestCase):
    def test_concatenates_without_error(self) -> None:
        with TemporaryDirectory() as tmp:
            folder = Path(tmp)
            chunk1 = folder / "c1.wav"
            chunk2 = folder / "c2.wav"
            for path, freq, dur in ((chunk1, 220, 1.0), (chunk2, 440, 1.5)):
                subprocess.run(
                    [
                        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                        "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={dur}",
                        str(path),
                    ],
                    check=True,
                )
            out_path = folder / "voice" / "full.mp3"
            voice.concatenate_chunks([chunk1, chunk2], out_path)
            self.assertTrue(out_path.is_file())

            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nk=1:nw=1", str(out_path)],
                capture_output=True, text=True, check=True,
            )
            self.assertAlmostEqual(float(result.stdout.strip()), 2.5, delta=0.2)

    def test_no_paths_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaises(voice.VoiceError):
                voice.concatenate_chunks([], Path(tmp) / "out.mp3")


class TranscribeChunksWiringTests(unittest.TestCase):
    def test_loads_model_once_for_all_chunks(self) -> None:
        with TemporaryDirectory() as tmp:
            paths = [Path(tmp) / "a.mp3", Path(tmp) / "b.mp3"]
            for p in paths:
                p.write_bytes(b"\x00")

            fake_segment = MagicMock()
            fake_segment.text = "hello world"
            mock_model = MagicMock()
            mock_model.transcribe.return_value = ([fake_segment], None)

            with patch("faster_whisper.WhisperModel", return_value=mock_model) as mock_cls:
                texts = voice.transcribe_chunks(paths, "small")

            mock_cls.assert_called_once()
            self.assertEqual(mock_model.transcribe.call_count, 2)
            self.assertEqual(texts[paths[0]], "hello world")


if __name__ == "__main__":
    unittest.main()
