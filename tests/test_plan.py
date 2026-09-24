import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from autoedit.plan import PlanError, load_plan_file, validate_plan


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00")


class LoadPlanFileTests(unittest.TestCase):
    def test_missing_file_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaises(PlanError):
                load_plan_file(Path(tmp) / "plan.json")

    def test_invalid_json_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text("{not valid json", encoding="utf-8")
            with self.assertRaises(PlanError):
                load_plan_file(plan_path)

    def test_valid_json_loads(self) -> None:
        with TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(json.dumps({"title": "T"}), encoding="utf-8")
            data = load_plan_file(plan_path)
            self.assertEqual(data["title"], "T")


class ValidatePlanTests(unittest.TestCase):
    def test_minimal_valid_plan_has_no_errors(self) -> None:
        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            _touch(project_dir / "voice" / "full.mp3")
            _touch(project_dir / "assets" / "ai" / "clip.mp4")

            data = {
                "title": "Test",
                "voice": ["voice/full.mp3"],
                "shots": [
                    {
                        "id": "S1",
                        "start": 0.0,
                        "duration": 3.0,
                        "asset": "assets/ai/clip.mp4",
                        "why": "test",
                    }
                ],
            }
            issues = validate_plan(data, project_dir)
            errors = [i for i in issues if i.level == "error"]
            self.assertEqual(errors, [])

    def test_missing_asset_reports_error(self) -> None:
        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            _touch(project_dir / "voice" / "full.mp3")

            data = {
                "title": "Test",
                "voice": ["voice/full.mp3"],
                "shots": [
                    {
                        "id": "S1",
                        "start": 0.0,
                        "duration": 3.0,
                        "asset": "assets/ai/missing.mp4",
                    }
                ],
            }
            issues = validate_plan(data, project_dir)
            errors = [i for i in issues if i.level == "error"]
            self.assertTrue(any("не найден" in i.message for i in errors))
            self.assertTrue(any(i.shot_id == "S1" for i in errors))

    def test_duplicate_shot_id_reports_error(self) -> None:
        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            _touch(project_dir / "voice" / "full.mp3")
            _touch(project_dir / "assets" / "ai" / "clip.mp4")

            shot = {
                "id": "S1",
                "start": 0.0,
                "duration": 3.0,
                "asset": "assets/ai/clip.mp4",
            }
            data = {
                "title": "Test",
                "voice": ["voice/full.mp3"],
                "shots": [shot, dict(shot)],
            }
            issues = validate_plan(data, project_dir)
            errors = [i for i in issues if i.level == "error"]
            self.assertTrue(any("id уже используется" in i.message for i in errors))

    def test_missing_timing_reports_error(self) -> None:
        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            _touch(project_dir / "voice" / "full.mp3")
            _touch(project_dir / "assets" / "ai" / "clip.mp4")

            data = {
                "title": "Test",
                "voice": ["voice/full.mp3"],
                "shots": [{"id": "S1", "asset": "assets/ai/clip.mp4"}],
            }
            issues = validate_plan(data, project_dir)
            errors = [i for i in issues if i.level == "error"]
            self.assertTrue(any("anchor" in i.message for i in errors))

    def test_unknown_motion_reports_error(self) -> None:
        with TemporaryDirectory() as tmp:
            project_dir = Path(tmp)
            _touch(project_dir / "voice" / "full.mp3")
            _touch(project_dir / "assets" / "ai" / "clip.mp4")

            data = {
                "title": "Test",
                "voice": ["voice/full.mp3"],
                "shots": [
                    {
                        "id": "S1",
                        "start": 0.0,
                        "duration": 3.0,
                        "asset": "assets/ai/clip.mp4",
                        "motion": "spin_wildly",
                    }
                ],
            }
            issues = validate_plan(data, project_dir)
            errors = [i for i in issues if i.level == "error"]
            self.assertTrue(any("motion" in i.message for i in errors))


if __name__ == "__main__":
    unittest.main()
