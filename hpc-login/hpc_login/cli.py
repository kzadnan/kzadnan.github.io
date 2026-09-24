"""Command line entry points: serve, mcp, status, and reconnect."""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
import urllib.error
import urllib.request

from hpc_login import __version__
from hpc_login.config import ConfigError, load_config
from hpc_login.http_status import make_server
from hpc_login.mcp_server import Context, serve_stdio
from hpc_login.supervisor import (
    Supervisor,
    SupervisorAlreadyRunning,
    spawn_supervisor,
    supervisor_is_running,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="hpc-login",
        description="Keep an OpenSSH session to an HPC login node alive and expose it over MCP.",
    )
    parser.add_argument("--version", action="version", version=f"hpc-login {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("serve", "Run the SSH supervisor and the loopback status page"),
        ("mcp", "Speak MCP on stdin/stdout for Cursor, Claude, and Hermes"),
        ("status", "Print the supervisor status JSON"),
        ("reconnect", "Ask a running supervisor to reconnect"),
    ):
        sub = commands.add_parser(name, help=help_text)
        sub.add_argument(
            "--config",
            help="Path to a TOML config file. Overrides HPC_LOGIN_CONFIG for this process.",
        )
    args = parser.parse_args(argv)
    try:
        if args.command == "mcp":
            return _cmd_mcp(args.config)
        cfg = load_config(config_path=args.config)
    except ConfigError as exc:
        print(f"hpc-login: {exc}", file=sys.stderr)
        return 2
    if args.command == "serve":
        return _cmd_serve(cfg)
    if args.command == "status":
        return _cmd_status(cfg)
    if args.command == "reconnect":
        return _cmd_reconnect(cfg)
    parser.error(f"unknown command {args.command}")
    return 2


def _cmd_serve(cfg) -> int:
    supervisor = Supervisor(cfg)
    try:
        supervisor.acquire_process_lock()
    except SupervisorAlreadyRunning as exc:
        print(f"hpc-login: {exc}", file=sys.stderr)
        return 1
    try:
        httpd = make_server(
            cfg.http_host,
            cfg.http_port,
            supervisor.status,
            supervisor.request_reconnect,
        )
    except OSError as exc:
        print(f"hpc-login: could not bind {cfg.http_url} ({exc})", file=sys.stderr)
        return 1
    worker = threading.Thread(target=httpd.serve_forever, name="hpc-login-http", daemon=True)
    worker.start()

    def _stop(signum, frame):
        supervisor.request_stop()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    print(f"hpc-login: status page {cfg.http_url}", file=sys.stderr)
    print(f"hpc-login: maintaining SSH to {cfg.destination}", file=sys.stderr)
    try:
        supervisor.run()
    finally:
        httpd.shutdown()
        httpd.server_close()
    return 0


def _cmd_mcp(config_path: str | None) -> int:
    try:
        cfg = load_config(config_path=config_path)
    except ConfigError as exc:
        serve_stdio(Context(config=None, config_error=str(exc)))
        return 0
    if os.environ.get("HPC_LOGIN_NO_AUTOSTART") != "1" and not supervisor_is_running(cfg):
        spawn_supervisor()
    serve_stdio(Context(config=cfg))
    return 0


def _cmd_status(cfg) -> int:
    url = f"http://{cfg.http_host}:{cfg.http_port}/api/status"
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as exc:
        print(f"hpc-login: supervisor is not reachable at {url} ({exc})", file=sys.stderr)
        print("Start it with: hpc-login serve", file=sys.stderr)
        return 1
    print(json.dumps(payload, indent=2))
    return 0 if payload.get("state") == "connected" else 1


def _cmd_reconnect(cfg) -> int:
    url = f"http://{cfg.http_host}:{cfg.http_port}/api/reconnect"
    request = urllib.request.Request(
        url,
        data=b"{}",
        method="POST",
        headers={"Content-Type": "application/json", "X-HPC-Login": "1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            print(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        print(f"hpc-login: supervisor is not reachable at {url} ({exc})", file=sys.stderr)
        return 1
    return 0
