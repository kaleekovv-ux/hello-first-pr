import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from autoedit.align import (
    Cue,
    Word,
    _best_script_match,
    _enforce_min_duration,
    build_timeline,
    find_anchor,
    generate_srt,
    load_or_transcribe,
    resolve_anchor_starts,
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

    def test_out_of_order_explicit_shots_report_error(self) -> None:
        plan = {
            "shots": [
                {"id": "S1", "start": 10.0, "duration": 2.0},
                {"id": "S2", "start": 2.0, "duration": 2.0},
            ]
        }
        timeline, issues = build_timeline(plan, [])
        errors = [i for i in issues if i.level == "error"]
        self.assertTrue(any("не совпадает с порядком" in i.message for i in errors))
        self.assertEqual([t.shot["id"] for t in timeline], ["S2", "S1"])

    def test_allow_reorder_downgrades_to_warning(self) -> None:
        plan = {
            "shots": [
                {"id": "S1", "start": 10.0, "duration": 2.0},
                {"id": "S2", "start": 2.0, "duration": 2.0},
            ]
        }
        timeline, issues = build_timeline(plan, [], allow_reorder=True)
        self.assertFalse(any(i.level == "error" for i in issues))
        self.assertTrue(any(i.level == "warning" and "не совпадает с порядком" in i.message for i in issues))

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


class ResolveAnchorStartsTests(unittest.TestCase):
    def test_missing_anchor_error_reports_search_position_and_previous_shot(self) -> None:
        shots = [
            {"id": "H01", "anchor": "Imagine leaving home"},
            {"id": "H02", "anchor": "this phrase is not in the audio"},
        ]
        _starts, issues = resolve_anchor_starts(shots, SAMPLE_WORDS)
        self.assertEqual(len(issues), 1)
        message = issues[0].message
        # Сообщение должно содержать время, с которого продолжился поиск
        # после H01, и упоминать, что последним успешно нашёлся именно
        # H01, чтобы было ясно, где искать причину несовпадения.
        self.assertIn("сек", message)
        self.assertIn("H01", message)

    def test_first_anchor_missing_has_no_previous_shot_hint(self) -> None:
        shots = [{"id": "H01", "anchor": "not present at all"}]
        _starts, issues = resolve_anchor_starts(shots, SAMPLE_WORDS)
        self.assertEqual(len(issues), 1)
        self.assertNotIn("Последним успешно нашёлся", issues[0].message)


class EnforceMinDurationTests(unittest.TestCase):
    def test_short_cue_is_stretched_to_minimum(self) -> None:
        cues = [Cue(start=0.0, end=0.2, text="да")]
        _enforce_min_duration(cues)
        self.assertGreaterEqual(cues[0].end - cues[0].start, 1.0)

    def test_stretch_does_not_overlap_next_cue(self) -> None:
        cues = [
            Cue(start=0.0, end=0.2, text="да"),
            Cue(start=0.3, end=0.6, text="нет"),
        ]
        _enforce_min_duration(cues)
        self.assertLessEqual(cues[0].end, cues[1].start - 0.05 + 1e-9)

    def test_long_enough_cue_is_untouched(self) -> None:
        cues = [Cue(start=0.0, end=2.0, text="это уже достаточно долгая реплика")]
        _enforce_min_duration(cues)
        self.assertEqual(cues[0].end, 2.0)


class BestScriptMatchTests(unittest.TestCase):
    def test_close_match_is_returned(self) -> None:
        script_lines = ["Он провёл 438 дней в открытом океане."]
        result = _best_script_match("он провел четыреста тридцать восемь дней в открытом океане", script_lines)
        self.assertEqual(result, script_lines[0])

    def test_unrelated_text_returns_none(self) -> None:
        script_lines = ["Он провёл 438 дней в открытом океане."]
        result = _best_script_match("совершенно другая фраза ни о чём", script_lines)
        self.assertIsNone(result)


class GenerateSrtScriptMatchTests(unittest.TestCase):
    def test_matched_cue_uses_script_wording_with_digits(self) -> None:
        words = _words(
            ("он", 0.0, 0.2),
            ("провел", 0.2, 0.6),
            ("четыреста", 0.6, 1.0),
            ("тридцать", 1.0, 1.3),
            ("восемь", 1.3, 1.6),
            ("дней", 1.6, 1.9),
            ("в", 1.9, 2.0),
            ("открытом", 2.0, 2.4),
            ("океане.", 2.4, 2.8),
        )
        script_lines = ["Он провёл 438 дней в открытом океане."]
        with TemporaryDirectory() as tmp:
            srt_path = Path(tmp) / "subtitles.srt"
            generate_srt(words, srt_path, script_lines)
            content = srt_path.read_text(encoding="utf-8")
            self.assertIn("438", content)

    def test_unmatched_cue_keeps_recognized_text(self) -> None:
        script_lines = ["Совершенно другая строка сценария."]
        with TemporaryDirectory() as tmp:
            srt_path = Path(tmp) / "subtitles.srt"
            generate_srt(SAMPLE_WORDS, srt_path, script_lines)
            content = srt_path.read_text(encoding="utf-8")
            self.assertIn("Imagine", content)


if __name__ == "__main__":
    unittest.main()
