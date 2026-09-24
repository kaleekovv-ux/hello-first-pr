import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from autoedit.demo import generate_demo_project
from autoedit.plan import load_plan_file, validate_plan

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None


@unittest.skipUnless(FFMPEG_AVAILABLE, "FFmpeg не установлен в этом окружении")
class DemoProjectTests(unittest.TestCase):
    def test_generated_demo_project_passes_validation(self) -> None:
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "demo"
            generate_demo_project(target)

            self.assertTrue((target / "plan.json").is_file())
            self.assertTrue((target / "voice" / "full.mp3").is_file())

            data = load_plan_file(target / "plan.json")
            issues = validate_plan(data, target)
            errors = [i for i in issues if i.level == "error"]
            self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
