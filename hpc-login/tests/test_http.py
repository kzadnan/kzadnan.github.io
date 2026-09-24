import json
import threading
import unittest
import urllib.error
import urllib.request

from hpc_login.config import ConfigError
from hpc_login.http_status import make_server


class HttpTests(unittest.TestCase):
    def test_refuses_non_loopback_bind(self):
        with self.assertRaises(ConfigError):
            make_server("0.0.0.0", 9, lambda: {}, lambda: {})

    def test_status_page_and_reconnect_stay_on_loopback(self):
        calls = {"reconnects": 0}

        def status():
            calls["reconnects"]
            return {
                "state": "reconnecting",
                "destination": "demo@hpc.example.edu",
                "proxy_jump": None,
                "server_alive_interval": 30,
                "server_alive_count_max": 3,
                "consecutive_failures": 1,
                "next_attempt_in_seconds": 2,
                "connected_since": None,
                "last_error": "socket missing",
            }

        def reconnect():
            calls["reconnects"] += 1
            return status()

        server = make_server("127.0.0.1", 0, status, reconnect)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        base = f"http://127.0.0.1:{port}"
        try:
            with urllib.request.urlopen(base + "/api/status", timeout=3) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(payload["state"], "reconnecting")
            page = urllib.request.urlopen(base + "/", timeout=3).read().decode("utf-8")
            self.assertIn("hpc-login", page)
            self.assertIn("Reconnect", page)
            denied = urllib.request.Request(
                base + "/api/reconnect",
                data=b"{}",
                method="POST",
                headers={"Host": "evil.example", "Content-Type": "application/json", "X-HPC-Login": "1"},
            )
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(denied, timeout=3)
            self.assertEqual(caught.exception.code, 403)
            missing = urllib.request.Request(base + "/api/reconnect", data=b"{}", method="POST")
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(missing, timeout=3)
            self.assertEqual(caught.exception.code, 400)
            allowed = urllib.request.Request(
                base + "/api/reconnect",
                data=b"{}",
                method="POST",
                headers={"Content-Type": "application/json", "X-HPC-Login": "1"},
            )
            with urllib.request.urlopen(allowed, timeout=3) as response:
                self.assertEqual(response.status, 200)
            self.assertEqual(calls["reconnects"], 1)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
