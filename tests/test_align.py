import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from autoedit.align import (
    Word,
    build_timeline,
    find_anchor,
    generate_srt,
    load_or_transcribe,
)


def _words(*specs: tuple[str, float, float]) -> list[Word]:
    return [Word(text=t, start=s, end=e) for t, s, e in specs]


SAMPLE_WORDS = _words(
    ("Imagine", 0.0, 0.4),
    ("leaving", 0.4, 0.8),
    ("home,", 0.8, 1.2),
    ("fourteen", 3.0, 3.4),
    ("months", 3.4, 3.8),
    ("later", 3.8, 4.2),
    ("they", 6.0, 6.2),
    ("accuse", 6.2, 6.6),
    ("you.", 6.6, 7.0),
)


class FindAnchorTests(unittest.TestCase):
    def test_exact_phrase_found(self) -> None:
        match, _next_index = find_anchor(SAMPLE_WORDS, "Imagine leaving home")
        self.assertIsNotNone(match)
        self.assertAlmostEqual(match.start, 0.0)

    def test_anchors_found_in_order(self) -> None:
        match1, next_index = find_anchor(SAMPLE_WORDS, "Imagine leaving home")
        match2, _ = find_anchor(SAMPLE_WORDS, "fourteen months later", next_index)
        self.assertIsNotNone(match1)
        self.assertIsNotNone(match2)
        self.assertGreater(match2.start, match1.start)

    def test_phrase_not_present_returns_none(self) -> None:
        match, _ = find_anchor(SAMPLE_WORDS, "the radio dies completely now")
        self.assertIsNone(match)

    def test_search_after_index_ignores_earlier_occurrence(self) -> None:
        words = _words(("hello", 0.0, 0.3), ("world", 0.3, 0.6), ("hello", 5.0, 5.3), ("world", 5.3, 5.6))
        match, _ = find_anchor(words, "hello world", search_from_index=2)
        self.assertIsNotNone(match)
        self.assertAlmostEqual(match.start, 5.0)


class BuildTimelineTests(unittest.TestCase):
    def test_anchor_shots_get_durations_from_next_shot(self) -> None:
        plan = {
            "shots": [
                {"id": "H01", "anchor": "Imagine leaving home"},
                {"id": "H02", "anchor": "fourteen months later"},
                {"id": "H03", "anchor": "they accuse you"},
            ]
        }
        timeline, issues = build_timeline(plan, SAMPLE_WORDS)
        self.assertEqual([i.format() for i in issues], [])
        self.assertEqual(len(timeline), 3)
        self.assertAlmostEqual(timeline[0].start, 0.0)
        self.assertAlmostEqual(timeline[0].duration, timeline[1].start - timeline[0].start)
        self.assertAlmostEqual(timeline[-1].start, 6.0)

    def test_missing_anchor_reports_error_and_is_skipped(self) -> None:
        plan = {
            "shots": [
                {"id": "H01", "anchor": "Imagine leaving home"},
                {"id": "H05", "anchor": "the radio dies completely now"},
            ]
        }
        timeline, issues = build_timeline(plan, SAMPLE_WORDS)
        self.assertEqual(len(timeline), 1)
        self.assertTrue(any(i.shot_id == "H05" and "не найдены" in i.message for i in issues))

    def test_explicit_timing_mixed_with_anchor(self) -> None:
        plan = {
            "shots": [
                {"id": "S1", "start": 0.0, "duration": 2.0},
                {"id": "H02", "anchor": "fourteen months later"},
            ]
        }
        timeline, issues = build_timeline(plan, SAMPLE_WORDS)
        self.assertEqual(issues, [])
        self.assertEqual(len(timeline), 2)
        self.assertAlmostEqual(timeline[0].duration, 2.0)


class LoadOrTranscribeCacheTests(unittest.TestCase):
    def test_uses_cache_when_fingerprint_matches(self) -> None:
        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            voice_dir = project_dir / "voice"
            voice_dir.mkdir()
            voice_path = voice_dir / "full.mp3"
            voice_path.write_bytes(b"\x00" * 10)

            with patch("autoedit.align.transcribe_words") as mock_transcribe:
                mock_transcribe.return_value = SAMPLE_WORDS
                words_first = load_or_transcribe(project_dir, ["voice/full.mp3"], "small")
                words_second = load_or_transcribe(project_dir, ["voice/full.mp3"], "small")

            self.assertEqual(mock_transcribe.call_count, 1)
            self.assertEqual([w.text for w in words_first], [w.text for w in words_second])

            cache_path = project_dir / "output" / "alignment.json"
            self.assertTrue(cache_path.is_file())
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            self.assertIn("fingerprint", cached)

    def test_retranscribes_when_voice_file_changes(self) -> None:
        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            voice_dir = project_dir / "voice"
            voice_dir.mkdir()
            voice_path = voice_dir / "full.mp3"
            voice_path.write_bytes(b"\x00" * 10)

            with patch("autoedit.align.transcribe_words") as mock_transcribe:
                mock_transcribe.return_value = SAMPLE_WORDS
                load_or_transcribe(project_dir, ["voice/full.mp3"], "small")
                voice_path.write_bytes(b"\x00" * 20)
                load_or_transcribe(project_dir, ["voice/full.mp3"], "small")

            self.assertEqual(mock_transcribe.call_count, 2)


class GenerateSrtTests(unittest.TestCase):
    def test_srt_file_has_expected_structure(self) -> None:
        with TemporaryDirectory() as tmp:
            srt_path = Path(tmp) / "subtitles.srt"
            generate_srt(SAMPLE_WORDS, srt_path)
            content = srt_path.read_text(encoding="utf-8")
            self.assertIn("-->", content)
            self.assertTrue(content.strip().startswith("1"))

    def test_lines_are_not_too_long(self) -> None:
        long_words = _words(*[(f"word{i}", i * 0.3, i * 0.3 + 0.2) for i in range(20)])
        with TemporaryDirectory() as tmp:
            srt_path = Path(tmp) / "subtitles.srt"
            generate_srt(long_words, srt_path)
            content = srt_path.read_text(encoding="utf-8")
            for line in content.splitlines():
                if "-->" in line or line.strip().isdigit() or not line.strip():
                    continue
                self.assertLessEqual(len(line), 42)


if __name__ == "__main__":
    unittest.main()
