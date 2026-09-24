import os
import unittest
from pathlib import Path

from hpc_login.config import ConfigError, load_config

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
EXAMPLE = ROOT / "config.example.toml"


class ConfigTests(unittest.TestCase):
    def test_example_file_is_a_placeholder_without_secrets(self):
        text = EXAMPLE.read_text(encoding="utf-8")
        self.assertNotIn("BEGIN OPENSSH PRIVATE KEY", text)
        for line in text.splitlines():
            self.assertFalse(line.strip().lower().startswith("password"))
        env = {"HOME": "/tmp/hpc-login-config-home"}
        cfg = load_config(env, EXAMPLE)
        self.assertEqual(cfg.host, "hpc.example.edu")
        self.assertEqual(cfg.user, "your-username")
        self.assertEqual(cfg.destination, "your-username@hpc.example.edu")
        self.assertIsNone(cfg.identity_file)
        self.assertIsNone(cfg.proxy_jump)
        self.assertEqual(cfg.http_host, "127.0.0.1")
        self.assertEqual(cfg.server_alive_interval, 30)
        self.assertEqual(cfg.server_alive_count_max, 3)
        self.assertEqual(cfg.command_timeout, 120)
        self.assertTrue(cfg.control_path.endswith("cm.sock"))

    def test_env_overrides_file_and_empty_env_does_not(self):
        env = {
            "HOME": "/tmp/hpc-login-config-home",
            "HPC_LOGIN_USER": "realuser",
            "HPC_LOGIN_PORT": "2222",
            "HPC_LOGIN_PROXY_JUMP": "",
            "HPC_LOGIN_IDENTITY_FILE": "~/keys/id_ed25519",
        }
        cfg = load_config(env, EXAMPLE)
        self.assertEqual(cfg.user, "realuser")
        self.assertEqual(cfg.port, 2222)
        self.assertIsNone(cfg.proxy_jump)
        self.assertTrue(cfg.identity_file.endswith("/keys/id_ed25519"))
        self.assertNotIn("~", cfg.identity_file)

    def test_rejects_missing_host_and_non_loopback_bind(self):
        with self.assertRaises(ConfigError):
            load_config({"HOME": "/tmp/hpc-login-none", "HPC_LOGIN_USER": "demo"})
        with self.assertRaises(ConfigError) as caught:
            load_config(
                {
                    "HOME": "/tmp/hpc-login-none",
                    "HPC_LOGIN_HOST": "hpc.example.edu",
                    "HPC_LOGIN_HTTP_HOST": "0.0.0.0",
                }
            )
        self.assertIn("127.0.0.1", str(caught.exception))

    def test_rejects_option_injection_and_out_of_range_timeout(self):
        base = {"HOME": "/tmp/hpc-login-none", "HPC_LOGIN_HOST": "hpc.example.edu"}
        with self.assertRaises(ConfigError):
            load_config({**base, "HPC_LOGIN_PROXY_JUMP": "jump.example.edu\nProxyCommand=evil"})
        with self.assertRaises(ConfigError):
            load_config({**base, "HPC_LOGIN_USER": "bad user"})
        with self.assertRaises(ConfigError):
            load_config({**base, "HPC_LOGIN_COMMAND_TIMEOUT": "99999"})

    def test_gitignore_covers_local_config_and_environments(self):
        text = (REPO / ".gitignore").read_text(encoding="utf-8")
        for entry in (
            "hpc-login/config.toml",
            "hpc-login/.venv/",
            "node_modules/",
            "hpc-login/**/*.sock",
        ):
            self.assertIn(entry, text)
        self.assertFalse((ROOT / "config.toml").exists())


if __name__ == "__main__":
    unittest.main()
