# Day 10 Reliability Final Report

## Metrics Summary

| Metric | Value |
|---|---:|
| total_requests | 400 |
| availability | 0.9975 |
| error_rate | 0.0025 |
| latency_p50_ms | 232.57 |
| latency_p95_ms | 313.56 |
| latency_p99_ms | 320.27 |
| fallback_success_rate | 0.9877 |
| cache_hit_rate | 0.5125 |
| circuit_open_count | 8 |
| recovery_time_ms | None |
| estimated_cost | 0.097434 |
| estimated_cost_saved | 0.205 |

## Chaos Scenarios

| Scenario | Status |
|---|---|
| primary_timeout_100 | pass |
| primary_flaky_50 | pass |
| all_healthy | pass |
| cache_stress_repeat | pass |

## Analysis TODO(student)

Explain what failed, why the fallback path worked or did not work, and what you would change before production.