import tempfile
import unittest
from pathlib import Path

from hpc_login.config import Config
from hpc_login.limits import BoundedResult
from hpc_login.ssh import (
    SshError,
    check_argv,
    compose_remote_command,
    exec_argv,
    master_argv,
    master_is_running,
    run_remote,
)


def sample_config(tmp: Path, **overrides) -> Config:
    values = dict(
        host="hpc.example.invalid",
        user="demo",
        port=None,
        identity_file=None,
        proxy_jump=None,
        server_alive_interval=15,
        server_alive_count_max=4,
        connect_timeout=5,
        http_host="127.0.0.1",
        http_port=8765,
        command_timeout=30,
        output_limit_bytes=200,
        transfer_limit_bytes=1000,
        state_dir=str(tmp),
        control_path=str(tmp / "cm.sock"),
    )
    values.update(overrides)
    return Config(**values)


class SshArgTests(unittest.TestCase):
    def test_master_uses_openssh_multiplex_and_keepalive(self):
        with tempfile.TemporaryDirectory() as raw:
            cfg = sample_config(
                Path(raw),
                identity_file="/home/demo/.ssh/id_ed25519",
                proxy_jump="jump.example.edu",
                port=22,
            )
        argv = master_argv(cfg)
        joined = " ".join(argv)
        self.assertEqual(argv[0], "ssh")
        self.assertIn("ControlMaster=yes", joined)
        self.assertIn("ControlPersist=yes", joined)
        self.assertIn(f"ControlPath={cfg.control_path}", joined)
        self.assertIn("ServerAliveInterval=15", joined)
        self.assertIn("ServerAliveCountMax=4", joined)
        self.assertIn("BatchMode=yes", joined)
        self.assertIn("ProxyJump=jump.example.edu", joined)
        self.assertIn("/home/demo/.ssh/id_ed25519", argv)
        self.assertIn("IdentitiesOnly=yes", joined)
        self.assertIn("-N", argv)
        self.assertEqual(argv[-1], "demo@hpc.example.invalid")
        self.assertNotIn("PasswordAuthentication=yes", joined)
        self.assertNotIn("StrictHostKeyChecking=no", joined)
        self.assertNotIn("password", joined.lower())

    def test_client_joins_master_and_check_does_not_open_a_session(self):
        with tempfile.TemporaryDirectory() as raw:
            cfg = sample_config(Path(raw))
        argv = exec_argv(cfg, "squeue")
        self.assertIn("ControlMaster=no", " ".join(argv))
        self.assertEqual(argv[-2:], ["--", "squeue"])
        check = check_argv(cfg)
        self.assertIn("-O", check)
        self.assertIn("check", check)
        self.assertNotIn("ControlMaster=yes", " ".join(check))

    def test_down_master_does_not_run_the_command(self):
        with tempfile.TemporaryDirectory() as raw:
            cfg = sample_config(Path(raw))

        def runner(*args, **kwargs):
            raise AssertionError("remote command should not start")

        result = run_remote(cfg, "hostname", checker=lambda _cfg: (False, "socket missing"), runner=runner)
        self.assertIsNotNone(result.error)
        self.assertIn("not connected", result.error)

    def test_runner_receives_timeout_and_output_limits(self):
        with tempfile.TemporaryDirectory() as raw:
            cfg = sample_config(Path(raw))
        seen = {}

        def runner(argv, **kwargs):
            seen["argv"] = argv
            seen["kwargs"] = kwargs
            return BoundedResult(0, b"ok\n", b"", False, False, False)

        result = run_remote(
            cfg,
            "hostname",
            timeout=999,
            checker=lambda _cfg: (True, ""),
            runner=runner,
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.stdout, "ok\n")
        self.assertEqual(seen["kwargs"]["timeout"], 30)
        self.assertEqual(seen["kwargs"]["stdout_limit"], 200)
        self.assertIn("hostname", seen["argv"][-1])

    def test_command_must_be_one_line(self):
        with self.assertRaises(SshError):
            compose_remote_command("echo hi\necho bye")

    def test_missing_socket_check_stays_local(self):
        with tempfile.TemporaryDirectory() as raw:
            cfg = sample_config(Path(raw))
        alive, detail = master_is_running(cfg, timeout=3)
        self.assertFalse(alive)
        self.assertTrue(detail)


if __name__ == "__main__":
    unittest.main()
