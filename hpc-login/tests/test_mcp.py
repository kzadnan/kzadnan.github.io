import io
import json
import os
import selectors
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from hpc_login.mcp_server import (
    TOOL_NAMES,
    Context,
    handle_message,
    read_message,
    serve_stdio,
    write_message,
)
from tests.test_ssh import sample_config

ROOT = Path(__file__).resolve().parents[1]


class McpTests(unittest.TestCase):
    def test_initialize_and_tool_list(self):
        with tempfile.TemporaryDirectory() as raw:
            ctx = Context(config=sample_config(Path(raw)))
        init = handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"},
                },
            },
            ctx,
        )
        self.assertEqual(init["result"]["protocolVersion"], "2025-11-25")
        self.assertEqual(init["result"]["serverInfo"]["name"], "hpc-login")
        listed = handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, ctx)
        names = [tool["name"] for tool in listed["result"]["tools"]]
        self.assertEqual(tuple(names), TOOL_NAMES)
        self.assertIsNone(
            handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}, ctx)
        )
        unknown = handle_message({"jsonrpc": "2.0", "id": 9, "method": "nope"}, ctx)
        self.assertEqual(unknown["error"]["code"], -32601)

    def test_status_and_command_report_a_down_master_without_connecting(self):
        with tempfile.TemporaryDirectory() as raw:
            ctx = Context(config=sample_config(Path(raw)))
            status = handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "connection_status", "arguments": {}},
                },
                ctx,
            )
            body = json.loads(status["result"]["content"][0]["text"])
            self.assertEqual(body["master"], "down")
            self.assertFalse(status["result"]["isError"])
            command = handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {"name": "run_command", "arguments": {"command": "hostname"}},
                },
                ctx,
            )
        self.assertTrue(command["result"]["isError"])
        self.assertIn("not connected", command["result"]["content"][0]["text"])

    def test_missing_config_is_a_tool_error(self):
        ctx = Context(config=None, config_error="host is required")
        response = handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "connection_status", "arguments": {}},
            },
            ctx,
        )
        self.assertTrue(response["result"]["isError"])
        self.assertIn("host is required", response["result"]["content"][0]["text"])

    def test_both_framings_round_trip(self):
        for framing in ("line", "lsp"):
            buffer = io.BytesIO()
            write_message(buffer, {"jsonrpc": "2.0", "id": 1, "method": "ping"}, framing)
            buffer.seek(0)
            message, used = read_message(buffer)
            self.assertEqual(used, framing)
            self.assertEqual(message["method"], "ping")

    def test_stdio_ping(self):
        ctx = Context(config=None, config_error="unconfigured")
        incoming = io.BytesIO(b'{"jsonrpc":"2.0","id":7,"method":"ping"}\n')
        outgoing = io.BytesIO()
        serve_stdio(ctx, stdin=incoming, stdout=outgoing)
        outgoing.seek(0)
        reply, framing = read_message(outgoing)
        self.assertEqual(framing, "line")
        self.assertEqual(reply["id"], 7)
        self.assertEqual(reply["result"], {})

    def test_subprocess_stdio_handshake(self):
        with tempfile.TemporaryDirectory() as raw:
            env = {
                "PATH": os.environ.get("PATH", ""),
                "HOME": raw,
                "HPC_LOGIN_HOST": "hpc.example.invalid",
                "HPC_LOGIN_USER": "demo",
                "HPC_LOGIN_STATE_DIR": str(Path(raw) / "state"),
                "HPC_LOGIN_NO_AUTOSTART": "1",
            }
            proc = subprocess.Popen(
                [sys.executable, "-m", "hpc_login", "mcp"],
                cwd=ROOT,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                payload = json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {"protocolVersion": "2024-11-05", "capabilities": {}},
                    }
                ).encode() + b"\n"
                assert proc.stdin is not None and proc.stdout is not None
                proc.stdin.write(payload)
                proc.stdin.flush()
                watcher = selectors.DefaultSelector()
                watcher.register(proc.stdout, selectors.EVENT_READ)
                ready = watcher.select(5)
                if not ready:
                    proc.kill()
                    err = b""
                    if proc.stderr is not None:
                        err = proc.stderr.read()
                    self.fail(f"MCP server did not reply: {err.decode('utf-8', 'replace')}")
                reply = json.loads(proc.stdout.readline().decode("utf-8"))
                self.assertEqual(reply["result"]["protocolVersion"], "2024-11-05")
            finally:
                if proc.poll() is None:
                    proc.kill()
                proc.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
