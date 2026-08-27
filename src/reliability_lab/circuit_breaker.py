from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import TypeVar

T = TypeVar("T")


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(RuntimeError):
    """Raised when a circuit is open and calls should fail fast."""


@dataclass(slots=True)
class CircuitBreaker:
    """Three-state circuit breaker.

    States:
    - CLOSED: calls pass through; consecutive failures are counted.
    - OPEN: calls fail fast until ``reset_timeout_seconds`` has elapsed.
    - HALF_OPEN: a limited number of probe calls are allowed; the circuit closes
      after ``success_threshold`` probes succeed, and re-opens on the first probe
      failure (this is what prevents a retry storm against a sick provider).

    Every state change is appended to ``transition_log`` so chaos runs can derive
    circuit-open counts and recovery time from evidence instead of guesswork.
    """

    name: str
    failure_threshold: int
    reset_timeout_seconds: float
    success_threshold: int = 1
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    success_count: int = 0
    opened_at: float | None = None
    transition_log: list[dict[str, str | float]] = field(default_factory=list)

    def allow_request(self) -> bool:
        """Return whether a request should be attempted right now.

        CLOSED and HALF_OPEN always allow the call (HALF_OPEN allows a probe).
        OPEN denies until ``reset_timeout_seconds`` has passed since ``opened_at``,
        at which point the circuit moves to HALF_OPEN and lets one probe through.
        Uses ``time.monotonic()`` so a wall-clock change cannot skew the timer.
        """
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.HALF_OPEN:
            return True
        if self.state == CircuitState.OPEN:
            if (
                self.opened_at is not None
                and (time.monotonic() - self.opened_at) >= self.reset_timeout_seconds
            ):
                self._transition(CircuitState.HALF_OPEN, reason="reset_timeout_elapsed")
                return True
            return False
        return False

    def call(self, fn: Callable[..., T], *args: object, **kwargs: object) -> T:
        """Run ``fn`` through the breaker.

        Raises ``CircuitOpenError`` without touching the provider when the circuit
        is open. Otherwise the result is recorded as a success, or the exception is
        recorded as a failure and re-raised so the caller can fall back.
        """
        if not self.allow_request():
            raise CircuitOpenError(f"Circuit '{self.name}' is OPEN and calls are denied.")
        try:
            result = fn(*args, **kwargs)
            self.record_success()
            return result
        except Exception:
            self.record_failure()
            raise

    def record_success(self) -> None:
        """Record a successful call.

        Clears the consecutive-failure counter. When the circuit is HALF_OPEN and
        enough probes have succeeded, the circuit closes with reason
        ``"probe_success"`` and the probe counter is reset for the next cycle.
        """
        self.failure_count = 0
        self.success_count += 1
        if self.state == CircuitState.HALF_OPEN and self.success_count >= self.success_threshold:
            self._transition(CircuitState.CLOSED, reason="probe_success")
            self.success_count = 0

    def record_failure(self) -> None:
        """Record a failed call.

        The two ways a circuit opens are kept separate on purpose (``if``/``elif``,
        never combined with ``or``) because they mean different things and are
        logged with different reasons:

        - HALF_OPEN failure -> re-open immediately as ``"probe_failure"``: the
          provider is still sick, so do not spend the remaining probe budget.
        - CLOSED and ``failure_count >= failure_threshold`` -> open as
          ``"failure_threshold_reached"``.
        """
        self.failure_count += 1
        self.success_count = 0
        if self.state == CircuitState.HALF_OPEN:
            self.opened_at = time.monotonic()
            self._transition(CircuitState.OPEN, reason="probe_failure")
        elif self.failure_count >= self.failure_threshold:
            self.opened_at = time.monotonic()
            self._transition(CircuitState.OPEN, reason="failure_threshold_reached")

    def _transition(self, new_state: CircuitState, reason: str) -> None:
        """Move to ``new_state`` and log it. A no-op when already in that state.

        The no-op guard is what keeps an already-open circuit from logging a new
        "open" entry on every subsequent failure.
        """
        if self.state == new_state:
            return
        self.transition_log.append(
            {"from": self.state.value, "to": new_state.value, "reason": reason, "ts": time.time()}
        )
        self.state = new_state
