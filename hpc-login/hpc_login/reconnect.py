"""Pure reconnect decisions for the OpenSSH control-master supervisor.

The supervisor calls these helpers. They do not open sockets or sleep, so the
backoff rules can be tested without a network.
"""

from __future__ import annotations

from dataclasses import dataclass


def backoff_seconds(
    consecutive_failures: int,
    *,
    base: float = 1.0,
    cap: float = 60.0,
) -> float:
    """Exponential delay after failed attempts. Zero failures means try now."""
    if consecutive_failures <= 0:
        return 0.0
    if base <= 0:
        raise ValueError("base must be positive")
    if cap <= 0:
        raise ValueError("cap must be positive")
    delay = base * (2 ** (consecutive_failures - 1))
    return min(cap, delay)


@dataclass(frozen=True)
class ReconnectDecision:
    should_start: bool
    wait_seconds: float
    reason: str


def decide_reconnect(
    *,
    master_running: bool,
    connect_in_progress: bool,
    consecutive_failures: int,
    seconds_since_last_attempt: float | None,
    force: bool = False,
    base: float = 1.0,
    cap: float = 60.0,
) -> ReconnectDecision:
    """Decide whether to spawn a new ControlMaster.

    A live master is left alone. A requested reconnect still waits until the
    caller has stopped the old master, so this function will not start a second
    one while ``master_running`` is true. Failed attempts wait for exponential
    backoff, capped, and then try again. The supervisor does not give up.
    """
    if master_running:
        return ReconnectDecision(False, 0.0, "master is running")
    if force:
        return ReconnectDecision(True, 0.0, "reconnect requested")
    if connect_in_progress:
        return ReconnectDecision(False, 0.0, "connection attempt in progress")
    delay = backoff_seconds(consecutive_failures, base=base, cap=cap)
    if seconds_since_last_attempt is None or seconds_since_last_attempt >= delay:
        reason = "backoff elapsed" if consecutive_failures else "master is down"
        return ReconnectDecision(True, 0.0, reason)
    remaining = delay - seconds_since_last_attempt
    return ReconnectDecision(False, remaining, "waiting for backoff")
