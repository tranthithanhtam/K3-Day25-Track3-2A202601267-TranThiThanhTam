"""Show one full circuit-breaker recovery cycle, step by step.

The averaged ``recovery_time_ms`` in metrics.json can come out as ``null`` when a
run happens to serve most of its traffic from cache, because a circuit that never
gets probed never closes again. This script forces the whole cycle deterministically
so the CLOSED -> OPEN -> HALF_OPEN -> CLOSED transition is visible as evidence.

Run:  python scripts/recovery_evidence.py
"""
from __future__ import annotations

import time

from reliability_lab.chaos import calculate_recovery_time_ms
from reliability_lab.circuit_breaker import CircuitBreaker
from reliability_lab.gateway import ReliabilityGateway
from reliability_lab.providers import FakeLLMProvider

RESET_TIMEOUT_S = 0.2
FAILURE_THRESHOLD = 3


def main() -> None:
    primary = FakeLLMProvider("primary", fail_rate=1.0, base_latency_ms=20, cost_per_1k_tokens=0.01)
    backup = FakeLLMProvider("backup", fail_rate=0.0, base_latency_ms=20, cost_per_1k_tokens=0.006)
    breakers = {
        "primary": CircuitBreaker("primary", FAILURE_THRESHOLD, RESET_TIMEOUT_S, success_threshold=1),
        "backup": CircuitBreaker("backup", FAILURE_THRESHOLD, RESET_TIMEOUT_S, success_threshold=1),
    }
    gateway = ReliabilityGateway([primary, backup], breakers, cache=None)
    cb = breakers["primary"]

    lines: list[str] = []

    def log(msg: str) -> None:
        print(msg)
        lines.append(msg)

    log("=== Circuit breaker recovery evidence ===")
    log(f"failure_threshold={FAILURE_THRESHOLD}  reset_timeout_seconds={RESET_TIMEOUT_S}")
    log("")

    log("Step 1 - primary is down (fail_rate=1.0), send 4 requests")
    for i in range(4):
        response = gateway.complete(f"recovery probe request {i}")
        log(f"  req {i}: route={response.route:<10} provider={response.provider} "
            f"primary_circuit={cb.state.value}")
    log(f"  -> primary circuit is now {cb.state.value.upper()}; "
        f"users are still served by the backup")
    log("")

    log("Step 2 - primary is healed (fail_rate=0.0), wait out the reset timeout")
    primary.fail_rate = 0.0
    time.sleep(RESET_TIMEOUT_S + 0.05)
    log(f"  slept {RESET_TIMEOUT_S + 0.05:.2f}s")
    log("")

    log("Step 3 - next request is allowed through as a HALF_OPEN probe")
    response = gateway.complete("recovery probe after heal")
    log(f"  route={response.route}  provider={response.provider}  "
        f"primary_circuit={cb.state.value}")
    log(f"  -> primary circuit is back to {cb.state.value.upper()}")
    log("")

    log("Full transition log for the primary circuit:")
    for entry in cb.transition_log:
        log(f"  {entry['from']:>9} -> {entry['to']:<9} reason={entry['reason']}  ts={entry['ts']:.3f}")
    log("")

    recovery_ms = calculate_recovery_time_ms(gateway)
    log(f"Measured recovery time: {recovery_ms:.1f} ms" if recovery_ms is not None
        else "Measured recovery time: none")
    log(f"(reset_timeout is {RESET_TIMEOUT_S * 1000:.0f} ms, so anything a little above "
        f"that is the expected result)")

    out = "reports/recovery_evidence.txt"
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
