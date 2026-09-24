import sys
import unittest

from hpc_login.limits import run_bounded


class LimitTests(unittest.TestCase):
    def test_stdout_is_capped_and_flagged(self):
        result = run_bounded(
            [sys.executable, "-c", "print('x' * 5000, end='')"],
            timeout=5,
            stdout_limit=128,
            stderr_limit=128,
        )
        self.assertEqual(result.stdout, b"x" * 128)
        self.assertTrue(result.stdout_truncated)
        self.assertFalse(result.timed_out)
        self.assertEqual(result.returncode, 0)

    def test_timeout_kills_a_sleeping_process(self):
        result = run_bounded(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            timeout=0.4,
            stdout_limit=100,
            stderr_limit=100,
        )
        self.assertTrue(result.timed_out)
        self.assertNotEqual(result.returncode, 0)

    def test_stdin_is_passed_through(self):
        result = run_bounded(
            [
                sys.executable,
                "-c",
                "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())",
            ],
            timeout=5,
            stdout_limit=100,
            stderr_limit=100,
            stdin=b"hello-hpc",
        )
        self.assertEqual(result.stdout, b"hello-hpc")
        self.assertFalse(result.truncated)
        self.assertEqual(result.returncode, 0)

    def test_rejects_non_positive_timeout(self):
        with self.assertRaises(ValueError):
            run_bounded(["true"], timeout=0, stdout_limit=1, stderr_limit=1)


if __name__ == "__main__":
    unittest.main()
