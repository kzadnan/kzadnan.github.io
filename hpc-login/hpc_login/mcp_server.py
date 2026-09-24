"""Stdio MCP server for Cursor, Claude, and Hermes.

The server speaks newline-delimited JSON-RPC and also accepts the older
Content-Length framing. stdout is reserved for protocol messages.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass

from hpc_login import __version__
from hpc_login.config import Config
from hpc_login.ssh import (
    SshError,
    download_file,
    format_command_result,
    list_directory,
    run_remote,
    upload_file,
    master_is_running,
)
from hpc_login.supervisor import read_state, supervisor_is_running

LEGACY_VERSIONS = (
    "2024-11-05",
    "2025-03-26",
    "2025-06-18",
    "2025-11-25",
)
FALLBACK_VERSION = "2025-11-25"

TOOL_NAMES = (
    "connection_status",
    "reconnect",
    "run_command",
    "upload_file",
    "download_file",
    "list_directory",
)

_INSTRUCTIONS = (
    "hpc-login keeps one OpenSSH ControlMaster session to an HPC login node. "
    "Call connection_status first. If the master is down, call reconnect and "
    "wait until connection_status reports the master is up. "
    "run_command, upload_file, download_file, and list_directory all use that "
    "shared session. Never send passwords or private-key material."
)


@dataclass
class Context:
    config: Config | None
    config_error: str | None = None


def tool_definitions() -> list[dict]:
    return [
        _tool(
            "connection_status",
            "Report whether the local SSH control master and the hpc-login supervisor are up.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            read_only=True,
        ),
        _tool(
            "reconnect",
            "Ask the local supervisor to drop the SSH control master and connect again.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            read_only=False,
            idempotent=True,
        ),
        _tool(
            "run_command",
            "Run one shell command on the HPC login node through the shared SSH session.",
            {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Single-line command for the remote shell.",
                    },
                    "timeout_seconds": {
                        "type": "number",
                        "description": "Wall-clock limit. Cannot exceed the configured command_timeout.",
                    },
                    "working_directory": {
                        "type": "string",
                        "description": "Remote directory to enter before running the command.",
                    },
                },
                "required": ["command"],
                "additionalProperties": False,
            },
            read_only=False,
            destructive=True,
        ),
        _tool(
            "upload_file",
            "Copy a local file to the HPC login node through the shared SSH session.",
            {
                "type": "object",
                "properties": {
                    "local_path": {"type": "string"},
                    "remote_path": {"type": "string"},
                    "timeout_seconds": {"type": "number"},
                },
                "required": ["local_path", "remote_path"],
                "additionalProperties": False,
            },
            read_only=False,
            destructive=True,
        ),
        _tool(
            "download_file",
            "Copy a file from the HPC login node to a local path through the shared SSH session.",
            {
                "type": "object",
                "properties": {
                    "remote_path": {"type": "string"},
                    "local_path": {"type": "string"},
                    "timeout_seconds": {"type": "number"},
                },
                "required": ["remote_path", "local_path"],
                "additionalProperties": False,
            },
            read_only=False,
            destructive=True,
        ),
        _tool(
            "list_directory",
            "List one directory on the HPC login node.",
            {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Remote directory. Defaults to the login home directory when omitted.",
                    },
                    "timeout_seconds": {"type": "number"},
                },
                "additionalProperties": False,
            },
            read_only=True,
        ),
    ]


def _tool(
    name: str,
    description: str,
    schema: dict,
    *,
    read_only: bool,
    destructive: bool = False,
    idempotent: bool = False,
) -> dict:
    return {
        "name": name,
        "description": description,
        "inputSchema": schema,
        "annotations": {
            "title": name.replace("_", " "),
            "readOnlyHint": read_only,
            "destructiveHint": destructive,
            "idempotentHint": idempotent or read_only,
            "openWorldHint": True,
        },
    }


def handle_message(message: dict, ctx: Context) -> dict | None:
    if not isinstance(message, dict):
        return _error(None, -32600, "invalid request")
    method = message.get("method")
    msg_id = message.get("id")
    has_id = "id" in message and msg_id is not None
    params = message.get("params") or {}
    if not isinstance(params, dict):
        if has_id:
            return _error(msg_id, -32602, "params must be an object")
        return None
    if method in {"notifications/initialized", "notifications/cancelled"}:
        return None
    if method == "initialize":
        requested = str(params.get("protocolVersion") or "")
        version = requested if requested in LEGACY_VERSIONS else FALLBACK_VERSION
        return _result(
            msg_id,
            {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "hpc-login", "version": __version__},
                "instructions": _INSTRUCTIONS,
            },
        )
    if method == "ping":
        return _result(msg_id, {})
    if method == "logging/setLevel":
        return _result(msg_id, {})
    if method == "tools/list":
        return _result(msg_id, {"tools": tool_definitions()})
    if method == "prompts/list":
        return _result(msg_id, {"prompts": []})
    if method == "resources/list":
        return _result(msg_id, {"resources": []})
    if method == "tools/call":
        if not has_id:
            return None
        return _result(msg_id, _call_tool(params, ctx))
    if not has_id:
        return None
    return _error(msg_id, -32601, f"method not found: {method}")


def _call_tool(params: dict, ctx: Context) -> dict:
    name = params.get("name")
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        return _tool_error("arguments must be an object")
    if ctx.config_error or ctx.config is None:
        return _tool_error(ctx.config_error or "hpc-login is not configured")
    try:
        if name == "connection_status":
            return _tool_text(_connection_status(ctx.config))
        if name == "reconnect":
            return _tool_text(_reconnect(ctx.config))
        if name == "run_command":
            return _from_command(
                run_remote(
                    ctx.config,
                    _require_str(arguments, "command"),
                    timeout=_optional_number(arguments, "timeout_seconds"),
                    working_directory=_optional_str(arguments, "working_directory"),
                )
            )
        if name == "upload_file":
            return _from_command(
                upload_file(
                    ctx.config,
                    _require_str(arguments, "local_path"),
                    _require_str(arguments, "remote_path"),
                    timeout=_optional_number(arguments, "timeout_seconds"),
                )
            )
        if name == "download_file":
            return _from_command(
                download_file(
                    ctx.config,
                    _require_str(arguments, "remote_path"),
                    _require_str(arguments, "local_path"),
                    timeout=_optional_number(arguments, "timeout_seconds"),
                )
            )
        if name == "list_directory":
            return _from_command(
                list_directory(
                    ctx.config,
                    _optional_str(arguments, "path") or ".",
                    timeout=_optional_number(arguments, "timeout_seconds"),
                )
            )
    except SshError as exc:
        return _tool_error(str(exc))
    return _tool_error(f"unknown tool: {name}")


def _connection_status(cfg: Config) -> str:
    alive, detail = master_is_running(cfg)
    published = read_state(cfg)
    payload = {
        "supervisor": "running" if supervisor_is_running(cfg) else "stopped",
        "master": "up" if alive else "down",
        "master_check": detail or None,
        "destination": cfg.destination,
        "proxy_jump": cfg.proxy_jump,
        "http": cfg.http_url,
        "published": published,
    }
    return json.dumps(payload, indent=2)


def _reconnect(cfg: Config) -> str:
    url = f"http://{cfg.http_host}:{cfg.http_port}/api/reconnect"
    request = urllib.request.Request(
        url,
        data=b"{}",
        method="POST",
        headers={"Content-Type": "application/json", "X-HPC-Login": "1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            body = response.read(16_384).decode("utf-8", "replace")
    except urllib.error.URLError as exc:
        return (
            "Could not reach the local supervisor at "
            f"{url}. Start it with `hpc-login serve` and try again. {exc}"
        )
    return body


def _from_command(result) -> dict:
    failed = bool(result.error) or result.timed_out or result.exit_code not in (0, None)
    return _tool_text(format_command_result(result), error=failed)


def _require_str(arguments: dict, key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SshError(f"{key} is required")
    return value


def _optional_str(arguments: dict, key: str) -> str | None:
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise SshError(f"{key} must be a string")
    return value


def _optional_number(arguments: dict, key: str) -> float | None:
    value = arguments.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SshError(f"{key} must be a number")
    return float(value)


def _tool_text(text: str, *, error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": error}


def _tool_error(text: str) -> dict:
    return _tool_text(text, error=True)


def _result(msg_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id, code: int, message: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": code, "message": message},
    }


def read_message(buffer) -> tuple[dict | None, str | None]:
    """Read one JSON-RPC message. Returns (None, None) on EOF."""
    while True:
        first = buffer.read(1)
        if first == b"":
            return None, None
        if first in b" \t\r\n":
            continue
        if first == b"{":
            rest = buffer.readline()
            return json.loads((first + rest).decode("utf-8")), "line"
        header = first + buffer.readline()
        headers = [header]
        while True:
            line = buffer.readline()
            if line in (b"\n", b"\r\n", b""):
                break
            headers.append(line)
        length = None
        for item in headers:
            text = item.decode("ascii", "replace").strip()
            if text.lower().startswith("content-length:"):
                length = int(text.split(":", 1)[1].strip())
        if length is None:
            raise ValueError("expected a JSON line or a Content-Length header")
        body = buffer.read(length)
        return json.loads(body.decode("utf-8")), "lsp"


def write_message(buffer, payload: dict, framing: str) -> None:
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    if framing == "lsp":
        buffer.write(f"Content-Length: {len(data)}\r\n\r\n".encode("ascii") + data)
    else:
        buffer.write(data + b"\n")
    buffer.flush()


def serve_stdio(ctx: Context, stdin=None, stdout=None) -> None:
    incoming = stdin if stdin is not None else sys.stdin.buffer
    outgoing = stdout if stdout is not None else sys.stdout.buffer
    framing = "line"
    while True:
        try:
            message, used = read_message(incoming)
        except (json.JSONDecodeError, ValueError, UnicodeError) as exc:
            write_message(outgoing, _error(None, -32700, f"parse error: {exc}"), framing)
            return
        if message is None:
            return
        if used:
            framing = used
        try:
            response = handle_message(message, ctx)
        except Exception as exc:
            print(f"hpc-login: {exc}", file=sys.stderr)
            msg_id = message.get("id") if isinstance(message, dict) else None
            response = _error(msg_id, -32603, "internal error")
        if response is not None:
            write_message(outgoing, response, framing)
