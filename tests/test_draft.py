import argparse
import json
import unittest
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from autoedit import cli
from autoedit.align import Word, build_timeline
from autoedit.draft import build_draft_plan


def _speak(lines: list[str], word_seconds: float = 0.4, pause: float = 0.6) -> list[Word]:
    """Синтетическая «распознанная озвучка»: слова подряд, пауза между фразами."""
    words: list[Word] = []
    t = 0.0
    for line in lines:
        for token in line.split():
            words.append(Word(text=token, start=t, end=t + word_seconds - 0.05))
            t += word_seconds
        t += pause
    return words


SCRIPT = [
    "Imagine leaving home for a short fishing trip.",
    "Fourteen months later you wash up on a distant island.",
    "Nobody believes a single word of your story.",
]


class BuildDraftPlanTests(unittest.TestCase):
    def test_one_shot_per_line_with_verbatim_anchors_that_check_finds(self) -> None:
        words = _speak(SCRIPT)
        plan, warnings = build_draft_plan(SCRIPT, words, "Демо", ["voice/full.mp3"])

        self.assertEqual(warnings, [])
        self.assertEqual(len(plan["shots"]), 3)
        self.assertEqual(plan["shots"][0]["id"], "S01")
        self.assertEqual(plan["shots"][1]["line"], SCRIPT[1])

        timeline, issues = build_timeline(plan, words)
        self.assertEqual(issues, [])
        starts = [t.start for t in timeline]
        line_starts = [words[0].start, words[8].start, words[18].start]
        for got, expected in zip(starts, line_starts):
            self.assertAlmostEqual(got, expected, places=3)

    def test_line_longer_than_8_seconds_is_split_into_several_shots(self) -> None:
        long_line = " ".join(f"word{i}" for i in range(30))  # 30 * 0.4 = 12 сек
        words = _speak([long_line, "and then the end"])
        plan, _warnings = build_draft_plan([long_line, "and then the end"], words, "Демо", ["v.mp3"])

        timeline, issues = build_timeline(plan, words)
        self.assertEqual(issues, [])
        self.assertGreater(len(plan["shots"]), 2)
        self.assertIn("часть 1/", plan["shots"][0]["line"])
        for timed in timeline:
            self.assertLessEqual(timed.duration, 8.0)

    def test_repeated_phrase_gets_longer_anchor_to_stay_unambiguous(self) -> None:
        script = [
            "no engine no radio no food",
            "no engine no radio and no hope at all",
        ]
        words = _speak(script)
        plan, warnings = build_draft_plan(script, words, "Демо", ["v.mp3"])

        self.assertEqual(warnings, [])
        timeline, issues = build_timeline(plan, words)
        self.assertEqual(issues, [])
        self.assertAlmostEqual(timeline[1].start, words[6].start, places=3)

    def test_line_missing_from_audio_is_reported(self) -> None:
        words = _speak(SCRIPT[:2])
        plan, warnings = build_draft_plan(SCRIPT, words, "Демо", ["v.mp3"])
        self.assertEqual(len(plan["shots"]), 2)
        self.assertTrue(any("не найдена в озвучке" in w for w in warnings))

    def test_no_words_raises(self) -> None:
        with self.assertRaises(ValueError):
            build_draft_plan(SCRIPT, [], "Демо", ["v.mp3"])


class DraftCommandTests(unittest.TestCase):
    def test_writes_plan_draft_json_and_refuses_to_overwrite_without_force(self) -> None:
        with TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "voice").mkdir()
            (project / "voice" / "full.mp3").write_bytes(b"fake")
            script = project / "script.txt"
            script.write_text("\n".join(SCRIPT), encoding="utf-8")
            args = argparse.Namespace(project=str(project), script=str(script), voice=None, force=False, model="small")

            with patch("autoedit.align.load_or_transcribe", return_value=_speak(SCRIPT)), patch(
                "sys.stdout", new_callable=StringIO
            ):
                self.assertEqual(cli.cmd_draft(args), 0)
                self.assertEqual(cli.cmd_draft(args), 1)

            plan = json.loads((project / "plan_draft.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["voice"], ["voice/full.mp3"])
            self.assertEqual(len(plan["shots"]), 3)
            self.assertFalse((project / "plan.json").exists())


if __name__ == "__main__":
    unittest.main()
