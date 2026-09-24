"""Load hpc-login settings from a gitignored TOML file and the environment.

Environment variables override file values. Nothing in this module reads or
copies private keys; an identity file is only a path passed through to OpenSSH.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

# Bind addresses other than IPv4 loopback are rejected. "localhost" is not
# accepted because it can resolve to a non-loopback address.
ALLOWED_HTTP_HOST = "127.0.0.1"

_HOST_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-")
_USER_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-")
_JUMP_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-:@,%")

_MAX_COMMAND_TIMEOUT = 3600
_MAX_OUTPUT_LIMIT = 2_000_000
_MAX_TRANSFER_LIMIT = 200_000_000
_CONTROL_PATH_MAX = 100


class ConfigError(ValueError):
    """The local config file or environment is missing or invalid."""


@dataclass(frozen=True)
class Config:
    host: str
    user: str | None
    port: int | None
    identity_file: str | None
    proxy_jump: str | None
    server_alive_interval: int
    server_alive_count_max: int
    connect_timeout: int
    http_host: str
    http_port: int
    command_timeout: int
    output_limit_bytes: int
    transfer_limit_bytes: int
    state_dir: str
    control_path: str

    @property
    def destination(self) -> str:
        if self.user:
            return f"{self.user}@{self.host}"
        return self.host

    @property
    def http_url(self) -> str:
        return f"http://{self.http_host}:{self.http_port}/"


def package_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def discover_config_path(environ: dict[str, str] | None = None) -> Path | None:
    """Return the first existing config file, or None when only env vars are used."""
    env = environ if environ is not None else os.environ
    explicit = env.get("HPC_LOGIN_CONFIG", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    home = Path(env.get("HOME") or str(Path.home()))
    xdg = env.get("XDG_CONFIG_HOME", "").strip()
    candidates = []
    if xdg:
        candidates.append(Path(xdg).expanduser() / "hpc-login" / "config.toml")
    candidates.append(home / ".config" / "hpc-login" / "config.toml")
    candidates.append(package_dir() / "config.toml")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def load_config(
    environ: dict[str, str] | None = None,
    config_path: Path | str | None = None,
) -> Config:
    env = dict(os.environ if environ is None else environ)
    path = Path(config_path).expanduser() if config_path else discover_config_path(env)
    file_values: dict = {}
    if path is not None:
        if not path.is_file():
            raise ConfigError(f"config file not found: {path}")
        try:
            file_values = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"could not parse {path}: {exc}") from exc
        if not isinstance(file_values, dict):
            raise ConfigError(f"config file must be a TOML table: {path}")
    raw = _merged(file_values, env)
    return _build(raw, env)


def _merged(file_values: dict, env: dict[str, str]) -> dict:
    def pick(file_key: str, env_key: str, default=None):
        if env_key in env and env[env_key].strip() != "":
            return env[env_key].strip()
        if file_key in file_values and file_values[file_key] is not None:
            return file_values[file_key]
        return default

    return {
        "host": pick("host", "HPC_LOGIN_HOST"),
        "user": pick("user", "HPC_LOGIN_USER"),
        "port": pick("port", "HPC_LOGIN_PORT"),
        "identity_file": pick("identity_file", "HPC_LOGIN_IDENTITY_FILE"),
        "proxy_jump": pick("proxy_jump", "HPC_LOGIN_PROXY_JUMP"),
        "server_alive_interval": pick(
            "server_alive_interval", "HPC_LOGIN_SERVER_ALIVE_INTERVAL", 30
        ),
        "server_alive_count_max": pick(
            "server_alive_count_max", "HPC_LOGIN_SERVER_ALIVE_COUNT_MAX", 3
        ),
        "connect_timeout": pick("connect_timeout", "HPC_LOGIN_CONNECT_TIMEOUT", 20),
        "http_host": pick("http_host", "HPC_LOGIN_HTTP_HOST", ALLOWED_HTTP_HOST),
        "http_port": pick("http_port", "HPC_LOGIN_HTTP_PORT", 8765),
        "command_timeout": pick("command_timeout", "HPC_LOGIN_COMMAND_TIMEOUT", 120),
        "output_limit_bytes": pick(
            "output_limit_bytes", "HPC_LOGIN_OUTPUT_LIMIT_BYTES", 200_000
        ),
        "transfer_limit_bytes": pick(
            "transfer_limit_bytes", "HPC_LOGIN_TRANSFER_LIMIT_BYTES", 50_000_000
        ),
        "state_dir": pick("state_dir", "HPC_LOGIN_STATE_DIR"),
    }


def _build(raw: dict, env: dict[str, str]) -> Config:
    host = _required_token("host", raw["host"], _HOST_CHARS)
    user = _optional_token("user", raw["user"], _USER_CHARS)
    proxy_jump = _optional_token("proxy_jump", raw["proxy_jump"], _JUMP_CHARS)
    port = _optional_port(raw["port"])
    identity = _optional_identity(raw["identity_file"])
    http_host = str(raw["http_host"]).strip()
    if http_host != ALLOWED_HTTP_HOST:
        raise ConfigError(
            f"http_host must be {ALLOWED_HTTP_HOST} so the status page stays on this machine"
        )
    state_dir = raw["state_dir"]
    if state_dir is None or str(state_dir).strip() == "":
        home = Path(env.get("HOME") or str(Path.home()))
        xdg_state = env.get("XDG_STATE_HOME", "").strip()
        base = Path(xdg_state).expanduser() if xdg_state else home / ".local" / "state"
        state_dir = str(base / "hpc-login")
    else:
        state_dir = str(Path(str(state_dir)).expanduser())
    control_path = _control_path(state_dir)
    return Config(
        host=host,
        user=user,
        port=port,
        identity_file=identity,
        proxy_jump=proxy_jump,
        server_alive_interval=_bounded_int(
            "server_alive_interval", raw["server_alive_interval"], 1, 300
        ),
        server_alive_count_max=_bounded_int(
            "server_alive_count_max", raw["server_alive_count_max"], 1, 20
        ),
        connect_timeout=_bounded_int("connect_timeout", raw["connect_timeout"], 1, 120),
        http_host=http_host,
        http_port=_bounded_int("http_port", raw["http_port"], 1, 65535),
        command_timeout=_bounded_int(
            "command_timeout", raw["command_timeout"], 1, _MAX_COMMAND_TIMEOUT
        ),
        output_limit_bytes=_bounded_int(
            "output_limit_bytes", raw["output_limit_bytes"], 1, _MAX_OUTPUT_LIMIT
        ),
        transfer_limit_bytes=_bounded_int(
            "transfer_limit_bytes", raw["transfer_limit_bytes"], 1, _MAX_TRANSFER_LIMIT
        ),
        state_dir=state_dir,
        control_path=control_path,
    )


def _required_token(name: str, value, allowed: set[str]) -> str:
    if value is None or str(value).strip() == "":
        raise ConfigError(
            f"{name} is required. Set it in the config file or HPC_LOGIN_{name.upper()}."
        )
    text = str(value).strip()
    _reject_bad_chars(name, text, allowed)
    return text


def _optional_token(name: str, value, allowed: set[str]) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text == "":
        return None
    _reject_bad_chars(name, text, allowed)
    return text


def _reject_bad_chars(name: str, text: str, allowed: set[str]) -> None:
    if any(ch not in allowed for ch in text):
        raise ConfigError(
            f"{name} contains a character OpenSSH options cannot safely carry: {text!r}"
        )


def _optional_port(value) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    return _bounded_int("port", value, 1, 65535)


def _optional_identity(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text == "":
        return None
    if any(ch in text for ch in "\n\r\x00"):
        raise ConfigError("identity_file must be a single path")
    expanded = str(Path(text).expanduser())
    return expanded


def _bounded_int(name: str, value, low: int, high: int) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if number < low or number > high:
        raise ConfigError(f"{name} must be between {low} and {high}")
    return number


def _control_path(state_dir: str) -> str:
    preferred = str(Path(state_dir) / "cm.sock")
    if len(preferred.encode("utf-8")) <= _CONTROL_PATH_MAX:
        return preferred
    short_dir = Path("/tmp") / f"hpc-login-{os.getuid()}"
    return str(short_dir / "cm.sock")
