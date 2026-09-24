"""Run a local process with a wall-clock timeout and capped captured output."""

from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class BoundedResult:
    returncode: int | None
    stdout: bytes
    stderr: bytes
    stdout_truncated: bool
    stderr_truncated: bool
    timed_out: bool

    @property
    def truncated(self) -> bool:
        return self.stdout_truncated or self.stderr_truncated


def run_bounded(
    argv: list[str],
    *,
    timeout: float,
    stdout_limit: int,
    stderr_limit: int,
    stdin: bytes | None = None,
) -> BoundedResult:
    """Run ``argv`` and stop it when time or captured output runs out.

    Extra output beyond the limit is discarded and flagged. The process is
    killed on timeout. Callers decode the bytes.
    """
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    if stdout_limit < 0 or stderr_limit < 0:
        raise ValueError("output limits must be non-negative")

    proc = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout_box = {"data": b"", "trunc": False}
    stderr_box = {"data": b"", "trunc": False}

    def _drain(stream, box: dict, limit: int) -> None:
        chunks: list[bytes] = []
        total = 0
        try:
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    break
                if total >= limit:
                    box["trunc"] = True
                    continue
                room = limit - total
                if len(chunk) > room:
                    chunks.append(chunk[:room])
                    total = limit
                    box["trunc"] = True
                else:
                    chunks.append(chunk)
                    total += len(chunk)
        finally:
            box["data"] = b"".join(chunks)
            stream.close()

    threads = [
        threading.Thread(
            target=_drain, args=(proc.stdout, stdout_box, stdout_limit), daemon=True
        ),
        threading.Thread(
            target=_drain, args=(proc.stderr, stderr_box, stderr_limit), daemon=True
        ),
    ]
    for thread in threads:
        thread.start()

    if stdin is not None and proc.stdin is not None:
        try:
            proc.stdin.write(stdin)
        except BrokenPipeError:
            pass
        finally:
            try:
                proc.stdin.close()
            except BrokenPipeError:
                pass

    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
    for thread in threads:
        thread.join(timeout=2)

    return BoundedResult(
        returncode=proc.returncode,
        stdout=stdout_box["data"],
        stderr=stderr_box["data"],
        stdout_truncated=stdout_box["trunc"],
        stderr_truncated=stderr_box["trunc"],
        timed_out=timed_out,
    )
