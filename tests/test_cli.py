import argparse
import shutil
import unittest
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from autoedit import cli
from autoedit.demo import generate_demo_project

FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None


class BuildParserTests(unittest.TestCase):
    def test_build_subcommand_parses_project_and_flags(self) -> None:
        parser = cli.build_parser()
        args = parser.parse_args(["build", "/tmp/some_project", "--vertical", "--allow-reorder"])
        self.assertEqual(args.command, "build")
        self.assertEqual(args.project, "/tmp/some_project")
        self.assertTrue(args.vertical)
        self.assertTrue(args.allow_reorder)
        self.assertIs(args.func, cli.cmd_build)

    def test_render_subcommand_has_debug_and_script_flags(self) -> None:
        parser = cli.build_parser()
        args = parser.parse_args(["render", "/tmp/some_project", "--debug", "--script", "s.txt"])
        self.assertTrue(args.debug)
        self.assertEqual(args.script, "s.txt")


@unittest.skipUnless(FFMPEG_AVAILABLE, "FFmpeg не установлен в этом окружении")
class BuildCommandIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.project_dir = Path(self._tmp.name) / "demo"
        generate_demo_project(self.project_dir)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_build_runs_check_preview_final_and_produces_final_video(self) -> None:
        args = argparse.Namespace(
            project=str(self.project_dir), vertical=False, allow_reorder=False,
            model="tiny", script=None,
        )
        with patch("sys.stdout", new_callable=StringIO) as fake_out:
            code = cli.cmd_build(args)
            output = fake_out.getvalue()

        self.assertEqual(code, 0)
        self.assertIn("[1/3]", output)
        self.assertIn("[2/3]", output)
        self.assertIn("[3/3]", output)
        self.assertIn("СБОРКА ЗАВЕРШЕНА", output)
        self.assertTrue((self.project_dir / "output" / "preview.mp4").is_file())
        self.assertTrue((self.project_dir / "output" / "final.mp4").is_file())


if __name__ == "__main__":
    unittest.main()
