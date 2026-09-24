"""Build OpenSSH commands and run them through an existing ControlMaster.

OpenSSH itself performs the protocol, authentication, and multiplexing. This
module only assembles arguments from the local config. Private keys are never
read here; ``identity_file`` is passed to ``ssh -i`` when the user set one.
``~/.ssh/config`` is used automatically because no ``-F`` override is passed.
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from hpc_login.config import Config
from hpc_login.limits import BoundedResult, run_bounded

_MAX_COMMAND_CHARS = 16_000


class SshError(RuntimeError):
    """The control master is down, or a local path cannot be transferred."""


@dataclass(frozen=True)
class CommandResult:
    exit_code: int | None
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    timed_out: bool
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and not self.timed_out and self.exit_code == 0


def common_options(cfg: Config, *, master: bool) -> list[str]:
    """Options shared by the master and by sessions that join it.

    Clients use ``ControlMaster=no`` with the same ``ControlPath``. OpenSSH
    then attaches to the listening master, and falls back to a direct
    connection when the socket is absent. Callers that must not open a second
    session check the master first and stop when it is down.
    """
    options = [
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={cfg.connect_timeout}",
        "-o",
        f"ControlMaster={'yes' if master else 'no'}",
        "-o",
        "ControlPersist=yes",
        "-o",
        f"ControlPath={cfg.control_path}",
        "-o",
        f"ServerAliveInterval={cfg.server_alive_interval}",
        "-o",
        f"ServerAliveCountMax={cfg.server_alive_count_max}",
        "-o",
        "ClearAllForwardings=yes",
    ]
    if cfg.identity_file:
        options.extend(["-i", cfg.identity_file, "-o", "IdentitiesOnly=yes"])
    if cfg.proxy_jump:
        options.extend(["-o", f"ProxyJump={cfg.proxy_jump}"])
    if cfg.port is not None:
        options.extend(["-p", str(cfg.port)])
    return options


def master_argv(cfg: Config) -> list[str]:
    """Foreground ``ssh -N`` that owns the multiplexed connection."""
    return ["ssh", *common_options(cfg, master=True), "-N", "-T", cfg.destination]


def check_argv(cfg: Config) -> list[str]:
    """Ask an existing master whether it is alive. Does not open a new session."""
    return [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ControlPath={cfg.control_path}",
        "-O",
        "check",
        cfg.destination,
    ]


def exit_argv(cfg: Config) -> list[str]:
    return [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ControlPath={cfg.control_path}",
        "-O",
        "exit",
        cfg.destination,
    ]


def exec_argv(cfg: Config, remote_command: str) -> list[str]:
    return [
        "ssh",
        *common_options(cfg, master=False),
        "-T",
        cfg.destination,
        "--",
        remote_command,
    ]


def master_is_running(cfg: Config, *, timeout: float = 5) -> tuple[bool, str]:
    """Return whether the control socket answers, plus a short error string."""
    try:
        result = subprocess.run(
            check_argv(cfg),
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, "ssh -O check timed out"
    except FileNotFoundError:
        return False, "ssh was not found on PATH"
    if result.returncode == 0:
        return True, ""
    detail = (result.stderr or result.stdout or b"").decode("utf-8", "replace").strip()
    return False, detail[:500]


def clamp_timeout(requested: float | None, cfg: Config) -> float:
    if requested is None:
        return float(cfg.command_timeout)
    value = float(requested)
    if value <= 0:
        raise SshError("timeout_seconds must be positive")
    return min(value, float(cfg.command_timeout))


def compose_remote_command(command: str, working_directory: str | None = None) -> str:
    text = command.strip()
    if not text:
        raise SshError("command is empty")
    if len(text) > _MAX_COMMAND_CHARS:
        raise SshError("command is too long")
    if "\n" in text or "\r" in text or "\x00" in text:
        raise SshError("command must be a single line")
    if working_directory:
        directory = _remote_path(working_directory)
        return f"cd -- {shlex.quote(directory)} && {text}"
    return text


def _remote_path(path: str) -> str:
    text = path.strip()
    if not text:
        raise SshError("remote path is empty")
    if any(ch in text for ch in "\n\r\x00"):
        raise SshError("remote path must be a single line")
    return text


def _decode(data: bytes) -> str:
    return data.decode("utf-8", "replace")


def _from_bounded(result: BoundedResult, *, error: str | None = None) -> CommandResult:
    return CommandResult(
        exit_code=result.returncode,
        stdout=_decode(result.stdout),
        stderr=_decode(result.stderr),
        stdout_truncated=result.stdout_truncated,
        stderr_truncated=result.stderr_truncated,
        timed_out=result.timed_out,
        error=error,
    )


def run_remote(
    cfg: Config,
    command: str,
    *,
    timeout: float | None = None,
    working_directory: str | None = None,
    checker=master_is_running,
    runner=run_bounded,
) -> CommandResult:
    alive, detail = checker(cfg)
    if not alive:
        message = "SSH master is not connected. Start `hpc-login serve` or call reconnect."
        if detail:
            message = f"{message} {detail}"
        return CommandResult(
            exit_code=None,
            stdout="",
            stderr="",
            stdout_truncated=False,
            stderr_truncated=False,
            timed_out=False,
            error=message,
        )
    remote = compose_remote_command(command, working_directory)
    bounded = runner(
        exec_argv(cfg, remote),
        timeout=clamp_timeout(timeout, cfg),
        stdout_limit=cfg.output_limit_bytes,
        stderr_limit=cfg.output_limit_bytes,
    )
    return _from_bounded(bounded)


def list_directory(
    cfg: Config,
    path: str = ".",
    *,
    timeout: float | None = None,
    checker=master_is_running,
    runner=run_bounded,
) -> CommandResult:
    remote = f"ls -la -- {shlex.quote(_remote_path(path))}"
    return run_remote(cfg, remote, timeout=timeout, checker=checker, runner=runner)


def upload_file(
    cfg: Config,
    local_path: str,
    remote_path: str,
    *,
    timeout: float | None = None,
    checker=master_is_running,
    runner=run_bounded,
) -> CommandResult:
    source = _local_file(local_path, cfg.transfer_limit_bytes)
    destination = _remote_path(remote_path)
    payload = source.read_bytes()
    remote = f"cat > {shlex.quote(destination)}"
    alive, detail = checker(cfg)
    if not alive:
        message = "SSH master is not connected. Start `hpc-login serve` or call reconnect."
        if detail:
            message = f"{message} {detail}"
        return CommandResult(
            exit_code=None,
            stdout="",
            stderr="",
            stdout_truncated=False,
            stderr_truncated=False,
            timed_out=False,
            error=message,
        )
    bounded = runner(
        exec_argv(cfg, remote),
        timeout=clamp_timeout(timeout, cfg),
        stdout_limit=cfg.output_limit_bytes,
        stderr_limit=cfg.output_limit_bytes,
        stdin=payload,
    )
    result = _from_bounded(bounded)
    if result.ok:
        return CommandResult(
            exit_code=0,
            stdout=f"uploaded {len(payload)} bytes to {destination}",
            stderr=result.stderr,
            stdout_truncated=False,
            stderr_truncated=result.stderr_truncated,
            timed_out=False,
        )
    return result


def download_file(
    cfg: Config,
    remote_path: str,
    local_path: str,
    *,
    timeout: float | None = None,
    checker=master_is_running,
    runner=run_bounded,
) -> CommandResult:
    destination = _local_destination(local_path)
    source = _remote_path(remote_path)
    remote = f"cat -- {shlex.quote(source)}"
    alive, detail = checker(cfg)
    if not alive:
        message = "SSH master is not connected. Start `hpc-login serve` or call reconnect."
        if detail:
            message = f"{message} {detail}"
        return CommandResult(
            exit_code=None,
            stdout="",
            stderr="",
            stdout_truncated=False,
            stderr_truncated=False,
            timed_out=False,
            error=message,
        )
    bounded = runner(
        exec_argv(cfg, remote),
        timeout=clamp_timeout(timeout, cfg),
        stdout_limit=cfg.transfer_limit_bytes,
        stderr_limit=cfg.output_limit_bytes,
    )
    if bounded.timed_out:
        return _from_bounded(bounded, error="download timed out")
    if bounded.stdout_truncated:
        return _from_bounded(
            bounded,
            error=(
                f"remote file is larger than transfer_limit_bytes "
                f"({cfg.transfer_limit_bytes})"
            ),
        )
    if bounded.returncode != 0:
        return _from_bounded(bounded)
    destination.write_bytes(bounded.stdout)
    return CommandResult(
        exit_code=0,
        stdout=f"downloaded {len(bounded.stdout)} bytes to {destination}",
        stderr=_decode(bounded.stderr),
        stdout_truncated=False,
        stderr_truncated=bounded.stderr_truncated,
        timed_out=False,
    )


def _local_file(path: str, limit: int) -> Path:
    if any(ch in path for ch in "\n\r\x00"):
        raise SshError("local path must be a single line")
    source = Path(path).expanduser()
    if not source.is_file():
        raise SshError(f"local file not found: {source}")
    size = source.stat().st_size
    if size > limit:
        raise SshError(f"local file is larger than transfer_limit_bytes ({limit})")
    return source


def _local_destination(path: str) -> Path:
    if any(ch in path for ch in "\n\r\x00"):
        raise SshError("local path must be a single line")
    destination = Path(path).expanduser()
    if destination.exists() and not destination.is_file():
        raise SshError(f"local path is not a file: {destination}")
    parent = destination.parent
    if parent and not parent.is_dir():
        raise SshError(f"local directory does not exist: {parent}")
    return destination


def format_command_result(result: CommandResult) -> str:
    lines = [
        f"exit_code: {result.exit_code if result.exit_code is not None else ''}",
        f"timed_out: {str(result.timed_out).lower()}",
        f"stdout_truncated: {str(result.stdout_truncated).lower()}",
        f"stderr_truncated: {str(result.stderr_truncated).lower()}",
    ]
    if result.error:
        lines.append(f"error: {result.error}")
    lines.append("")
    lines.append("--- stdout ---")
    lines.append(result.stdout)
    lines.append("--- stderr ---")
    lines.append(result.stderr)
    return "\n".join(lines)
