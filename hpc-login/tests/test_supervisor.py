import json
import tempfile
import unittest
from pathlib import Path

from hpc_login.supervisor import Supervisor
from tests.test_ssh import sample_config


class FakeProc:
    def __init__(self, code, pid=4321):
        self._code = code
        self.pid = pid

    def poll(self):
        return self._code


class FakeOps:
    def __init__(self, succeed: bool):
        self.succeed = succeed
        self.starts = 0
        self.stops = 0
        self.running = False

    def is_running(self):
        if self.running:
            return True, ""
        return False, "socket missing"

    def last_error(self):
        if self.succeed:
            return ""
        return "Permission denied (publickey)."

    def start(self):
        self.starts += 1
        if self.succeed:
            self.running = True
            return FakeProc(None)
        self.running = False
        return FakeProc(255)

    def stop(self, proc):
        self.stops += 1
        self.running = False


class SupervisorTests(unittest.TestCase):
    def test_successful_tick_publishes_connected_and_does_not_respawn(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            ops = FakeOps(succeed=True)
            supervisor = Supervisor(sample_config(tmp), ops=ops)
            supervisor.tick()
            supervisor.tick()
            status = supervisor.status()
            self.assertEqual(status["state"], "connected")
            self.assertEqual(ops.starts, 1)
            self.assertEqual(status["consecutive_failures"], 0)
            saved = json.loads((tmp / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["state"], "connected")
            self.assertEqual(saved["destination"], "demo@hpc.example.invalid")

    def test_failed_attempt_backs_off_instead_of_looping(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            ops = FakeOps(succeed=False)
            supervisor = Supervisor(sample_config(tmp), ops=ops)
            supervisor.tick()
            supervisor.tick()
            status = supervisor.status()
            self.assertEqual(ops.starts, 1)
            self.assertEqual(status["state"], "reconnecting")
            self.assertEqual(status["consecutive_failures"], 1)
            self.assertIn("Permission denied", status["last_error"])
            self.assertGreaterEqual(status["next_attempt_in_seconds"], 1)

    def test_reconnect_request_starts_a_fresh_master(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            ops = FakeOps(succeed=True)
            supervisor = Supervisor(sample_config(tmp), ops=ops)
            supervisor.tick()
            supervisor.request_reconnect()
            supervisor.tick()
            self.assertEqual(ops.starts, 2)
            self.assertGreaterEqual(ops.stops, 1)
            self.assertEqual(supervisor.status()["state"], "connected")


if __name__ == "__main__":
    unittest.main()
