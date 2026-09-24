import unittest

from autoedit.env import check_environment, check_ffmpeg, check_python


class EnvTests(unittest.TestCase):
    def test_check_python_returns_tuple(self) -> None:
        ok, version = check_python()
        self.assertIsInstance(ok, bool)
        self.assertRegex(version, r"^\d+\.\d+\.\d+$")

    def test_check_ffmpeg_returns_tuple(self) -> None:
        ok, version = check_ffmpeg()
        self.assertIsInstance(ok, bool)
        if not ok:
            self.assertIsNone(version)

    def test_check_environment_returns_status(self) -> None:
        status = check_environment()
        self.assertIsInstance(status.python_ok, bool)
        self.assertIsInstance(status.ffmpeg_ok, bool)
        self.assertTrue(status.os_name)


if __name__ == "__main__":
    unittest.main()
