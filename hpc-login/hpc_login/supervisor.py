"""Keep one OpenSSH ControlMaster running and publish its status locally."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from hpc_login.config import Config
from hpc_login.reconnect import backoff_seconds, decide_reconnect
from hpc_login.ssh import exit_argv, master_argv, master_is_running


class SupervisorAlreadyRunning(RuntimeError):
    """Another hpc-login serve process holds the state-directory lock."""


class OpenSSHOps:
    """Real OpenSSH control-master operations used by the supervisor."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._stderr = bytearray()
        self._lock = threading.Lock()

    def is_running(self) -> tuple[bool, str]:
        return master_is_running(self.cfg)

    def last_error(self) -> str:
        thread = getattr(self, "_thread", None)
        if thread is not None:
            thread.join(timeout=0.5)
        with self._lock:
            text = self._stderr.decode("utf-8", "replace").strip()
        return text[-2000:]

    def start(self) -> subprocess.Popen:
        self._prepare_socket_dir()
        with self._lock:
            self._stderr = bytearray()
        proc = subprocess.Popen(
            master_argv(self.cfg),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        self._thread = threading.Thread(target=self._read_stderr, args=(proc,), daemon=True)
        self._thread.start()
        return proc

    def stop(self, proc: subprocess.Popen | None) -> None:
        try:
            subprocess.run(
                exit_argv(self.cfg),
                capture_output=True,
                timeout=5,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        if proc is None or proc.poll() is not None:
            self._unlink_socket()
            return
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
            proc.wait(timeout=3)
        self._unlink_socket()

    def _read_stderr(self, proc: subprocess.Popen) -> None:
        stream = proc.stderr
        if stream is None:
            return
        while True:
            chunk = stream.read(1024)
            if not chunk:
                break
            with self._lock:
                self._stderr.extend(chunk)
                if len(self._stderr) > 8192:
                    del self._stderr[:-8192]

    def _prepare_socket_dir(self) -> None:
        path = Path(self.cfg.control_path)
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        alive, _ = self.is_running()
        if not alive and path.exists():
            path.unlink()

    def _unlink_socket(self) -> None:
        path = Path(self.cfg.control_path)
        alive, _ = master_is_running(self.cfg, timeout=2)
        if not alive and path.exists():
            try:
                path.unlink()
            except OSError:
                pass


class Supervisor:
    """Watch the control master and restart it with capped exponential backoff."""

    def __init__(self, cfg: Config, ops=None, clock=None):
        self.cfg = cfg
        self.ops = ops if ops is not None else OpenSSHOps(cfg)
        self._now = clock or time.monotonic
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._force = False
        self._proc: subprocess.Popen | None = None
        self._state = "stopped"
        self._failures = 0
        self._last_error: str | None = None
        self._last_attempt: float | None = None
        self._connected_since: str | None = None
        self._next_wait = 0.0
        self._lock_file = None

    def acquire_process_lock(self) -> None:
        import fcntl

        state = Path(self.cfg.state_dir)
        state.mkdir(parents=True, mode=0o700, exist_ok=True)
        lock_path = state / "supervisor.lock"
        handle = open(lock_path, "a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise SupervisorAlreadyRunning(
                f"hpc-login serve is already running (lock {lock_path})"
            ) from exc
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        self._lock_file = handle

    def request_stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def request_reconnect(self) -> dict:
        self._force = True
        self._wake.set()
        return self.status()

    def status(self) -> dict:
        with self._lock:
            pid = None
            if self._proc is not None and self._proc.poll() is None:
                pid = self._proc.pid
            return _status_payload(
                cfg=self.cfg,
                state=self._state,
                master_pid=pid,
                consecutive_failures=self._failures,
                last_error=self._last_error,
                connected_since=self._connected_since,
                next_attempt_in_seconds=self._next_wait if self._state != "connected" else 0,
            )

    def run(self) -> None:
        self._set(state="reconnecting", error=self._last_error, wait=0)
        while not self._stop.is_set():
            self.tick()
            if self._stop.is_set():
                break
            delay = 2.0 if self._state == "connected" else min(1.0, max(0.2, self._next_wait or 0.2))
            self._wake.wait(delay)
            self._wake.clear()
        self.ops.stop(self._proc)
        self._proc = None
        self._set(state="stopped", error=self._last_error, wait=0, connected_since=None)

    def tick(self) -> None:
        force = self._force
        self._force = False
        if force:
            self.ops.stop(self._proc)
            self._proc = None
        running, check_error = self.ops.is_running()
        in_progress = self._proc is not None and self._proc.poll() is None
        since = None if self._last_attempt is None else self._now() - self._last_attempt
        decision = decide_reconnect(
            master_running=running,
            connect_in_progress=in_progress and not force,
            consecutive_failures=self._failures,
            seconds_since_last_attempt=since,
            force=force,
        )
        if running:
            since_stamp = self._connected_since or _iso_now()
            self._proc_pid_if_alive()
            self._set(
                state="connected",
                error=None,
                wait=0,
                failures=0,
                connected_since=since_stamp,
            )
            return
        if not decision.should_start:
            self._set(
                state="connecting" if in_progress else "reconnecting",
                error=self._last_error or check_error or None,
                wait=decision.wait_seconds,
                connected_since=None,
            )
            return
        self._set(state="connecting", error=self._last_error, wait=0, connected_since=None)
        self._last_attempt = self._now()
        try:
            self._proc = self.ops.start()
        except Exception as exc:
            failures = self._failures + 1
            self._set(
                state="reconnecting",
                error=str(exc),
                wait=backoff_seconds(failures),
                failures=failures,
                connected_since=None,
            )
            return
        if self._wait_until_up():
            self._set(
                state="connected",
                error=None,
                wait=0,
                failures=0,
                connected_since=_iso_now(),
            )
            return
        error = self.ops.last_error() or check_error or "connection failed"
        self.ops.stop(self._proc)
        self._proc = None
        failures = self._failures + 1
        wait = backoff_seconds(failures)
        self._set(
            state="reconnecting",
            error=error,
            wait=wait,
            failures=failures,
            connected_since=None,
        )

    def _wait_until_up(self) -> bool:
        deadline = self._now() + self.cfg.connect_timeout + 2
        while self._now() < deadline:
            if self._stop.is_set():
                return False
            running, _ = self.ops.is_running()
            if running:
                return True
            if self._proc is not None and self._proc.poll() is not None:
                return False
            if self._wake.wait(0.2):
                self._wake.clear()
        return False

    def _proc_pid_if_alive(self) -> None:
        if self._proc is not None and self._proc.poll() is not None:
            self._proc = None

    def _set(
        self,
        *,
        state: str,
        error: str | None,
        wait: float,
        failures: int | None = None,
        connected_since: str | None = None,
    ) -> None:
        with self._lock:
            self._state = state
            self._last_error = error or None
            self._next_wait = wait
            if failures is not None:
                self._failures = failures
            if state == "connected":
                self._connected_since = connected_since
            elif connected_since is None:
                self._connected_since = None
            payload = _status_payload(
                cfg=self.cfg,
                state=self._state,
                master_pid=self._proc.pid if self._proc and self._proc.poll() is None else None,
                consecutive_failures=self._failures,
                last_error=self._last_error,
                connected_since=self._connected_since,
                next_attempt_in_seconds=0 if state == "connected" else self._next_wait,
            )
        _write_state(self.cfg, payload)


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _status_payload(
    *,
    cfg: Config,
    state: str,
    master_pid: int | None,
    consecutive_failures: int,
    last_error: str | None,
    connected_since: str | None,
    next_attempt_in_seconds: float,
) -> dict:
    return {
        "service": "hpc-login",
        "state": state,
        "host": cfg.host,
        "user": cfg.user,
        "port": cfg.port,
        "proxy_jump": cfg.proxy_jump,
        "destination": cfg.destination,
        "control_path": cfg.control_path,
        "master_pid": master_pid,
        "consecutive_failures": consecutive_failures,
        "last_error": last_error,
        "connected_since": connected_since,
        "next_attempt_in_seconds": round(next_attempt_in_seconds, 1),
        "server_alive_interval": cfg.server_alive_interval,
        "server_alive_count_max": cfg.server_alive_count_max,
        "http": cfg.http_url,
    }


def _write_state(cfg: Config, payload: dict) -> None:
    state_dir = Path(cfg.state_dir)
    state_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    target = state_dir / "state.json"
    temporary = state_dir / "state.json.tmp"
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)


def read_state(cfg: Config) -> dict | None:
    path = Path(cfg.state_dir) / "state.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def supervisor_is_running(cfg: Config) -> bool:
    import fcntl

    lock_path = Path(cfg.state_dir) / "supervisor.lock"
    if not lock_path.exists():
        return False
    handle = open(lock_path, "a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    else:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return False
    finally:
        handle.close()


def spawn_supervisor() -> None:
    """Start ``hpc-login serve`` in its own session so MCP clients share one master."""
    import sys

    log_dir = None
    try:
        from hpc_login.config import load_config

        cfg = load_config()
        log_dir = Path(cfg.state_dir)
        log_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    except Exception:
        cfg = None
    if cfg is not None and supervisor_is_running(cfg):
        return
    log_handle = None
    stdout = subprocess.DEVNULL
    if log_dir is not None:
        log_handle = open(log_dir / "serve.log", "ab")
        stdout = log_handle
    try:
        subprocess.Popen(
            [sys.executable, "-m", "hpc_login", "serve"],
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            cwd=str(Path(__file__).resolve().parent.parent),
            env=os.environ.copy(),
        )
    finally:
        if log_handle is not None:
            log_handle.close()

