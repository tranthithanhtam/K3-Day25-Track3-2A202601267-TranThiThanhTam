from __future__ import annotations

import json
import random
from pathlib import Path

from reliability_lab.cache import ResponseCache, SharedRedisCache
from reliability_lab.circuit_breaker import CircuitBreaker
from reliability_lab.config import LabConfig, ScenarioConfig
from reliability_lab.gateway import ReliabilityGateway
from reliability_lab.metrics import RunMetrics
from reliability_lab.providers import FakeLLMProvider


def load_queries(path: str | Path = "data/sample_queries.jsonl") -> list[str]:
    queries: list[str] = []
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        queries.append(json.loads(line)["query"])
    return queries


def build_gateway(config: LabConfig, provider_overrides: dict[str, float] | None = None) -> ReliabilityGateway:
    providers = []
    for p in config.providers:
        fail_rate = provider_overrides.get(p.name, p.fail_rate) if provider_overrides else p.fail_rate
        providers.append(FakeLLMProvider(p.name, fail_rate, p.base_latency_ms, p.cost_per_1k_tokens))
    breakers = {
        p.name: CircuitBreaker(
            name=p.name,
            failure_threshold=config.circuit_breaker.failure_threshold,
            reset_timeout_seconds=config.circuit_breaker.reset_timeout_seconds,
            success_threshold=config.circuit_breaker.success_threshold,
        )
        for p in config.providers
    }
    cache: ResponseCache | SharedRedisCache | None = None
    if config.cache.enabled:
        if config.cache.backend == "redis":
            cache = SharedRedisCache(
                config.cache.redis_url,
                config.cache.ttl_seconds,
                config.cache.similarity_threshold,
            )
        else:
            cache = ResponseCache(config.cache.ttl_seconds, config.cache.similarity_threshold)
    return ReliabilityGateway(providers, breakers, cache)


def calculate_recovery_time_ms(gateway: ReliabilityGateway) -> float | None:
    """Derive recovery time from circuit breaker transition logs.

    TODO(student): Implement recovery time calculation:
    1. For each breaker in gateway.breakers.values():
       - Walk breaker.transition_log entries
       - Track when circuit goes to "open" (save ts)
       - Track when circuit goes to "closed" (compute delta from open ts)
       - Recovery time = (close_ts - open_ts) * 1000 (convert to ms)
    2. Return average of all recovery times, or None if no recovery occurred.

    Each transition_log entry is a dict with keys: "from", "to", "reason", "ts"
    where "ts" is time.time() (epoch seconds).
    """
    recovery_times: list[float] = []
    for breaker in gateway.breakers.values():
        open_ts: float | None = None
        for entry in breaker.transition_log:
            if entry.get("to") == "open":
                open_ts = float(entry["ts"])
            elif entry.get("to") == "closed" and open_ts is not None:
                close_ts = float(entry["ts"])
                recovery_times.append((close_ts - open_ts) * 1000.0)
                open_ts = None

    if not recovery_times:
        return None
    return sum(recovery_times) / len(recovery_times)


from concurrent.futures import ThreadPoolExecutor


def run_scenario(
    config: LabConfig,
    queries: list[str],
    scenario: ScenarioConfig,
    concurrency: int | None = None,
) -> RunMetrics:
    """Run a single named chaos scenario with optional concurrent load."""
    gateway = build_gateway(config, scenario.provider_overrides or None)
    metrics = RunMetrics()
    effective_concurrency = concurrency if concurrency is not None else getattr(config.load_test, "concurrency", 1)

    prompts = [random.choice(queries) for _ in range(config.load_test.requests)]

    if effective_concurrency > 1:
        with ThreadPoolExecutor(max_workers=effective_concurrency) as executor:
            results = list(executor.map(gateway.complete, prompts))
    else:
        results = [gateway.complete(p) for p in prompts]

    for result in results:
        metrics.total_requests += 1
        metrics.estimated_cost += result.estimated_cost

        if result.cache_hit:
            metrics.cache_hits += 1
            metrics.estimated_cost_saved += 0.001

        if result.route == "fallback":
            metrics.fallback_successes += 1
            metrics.successful_requests += 1
        elif result.route == "static_fallback":
            metrics.static_fallbacks += 1
            metrics.failed_requests += 1
        else:
            metrics.successful_requests += 1

        if result.latency_ms > 0:
            metrics.latencies_ms.append(result.latency_ms)

    metrics.circuit_open_count = sum(
        len([entry for entry in breaker.transition_log if entry.get("to") == "open"])
        for breaker in gateway.breakers.values()
    )
    metrics.recovery_time_ms = calculate_recovery_time_ms(gateway)
    return metrics


def run_simulation(
    config: LabConfig,
    queries: list[str],
    concurrency: int | None = None,
) -> RunMetrics:
    """Run all named scenarios from config, or a default run if none defined."""
    if not config.scenarios:
        default_scenario = ScenarioConfig(name="default", description="baseline run")
        metrics = run_scenario(config, queries, default_scenario, concurrency=concurrency)
        metrics.scenarios = {"default": "pass" if metrics.successful_requests > 0 else "fail"}
        return metrics

    combined = RunMetrics()
    recovery_times: list[float] = []

    for scenario in config.scenarios:
        result = run_scenario(config, queries, scenario, concurrency=concurrency)

        # Precise pass/fail criteria per scenario
        if scenario.name == "primary_timeout_100":
            passed = result.fallback_successes > 0 and result.static_fallbacks < result.total_requests
        elif scenario.name == "all_healthy":
            passed = result.successful_requests == result.total_requests and result.circuit_open_count == 0
        elif scenario.name == "cache_stress_repeat":
            passed = result.cache_hits > 0
        else:
            passed = result.successful_requests > 0

        combined.scenarios[scenario.name] = "pass" if passed else "fail"

        combined.total_requests += result.total_requests
        combined.successful_requests += result.successful_requests
        combined.failed_requests += result.failed_requests
        combined.fallback_successes += result.fallback_successes
        combined.static_fallbacks += result.static_fallbacks
        combined.cache_hits += result.cache_hits
        combined.circuit_open_count += result.circuit_open_count
        combined.estimated_cost += result.estimated_cost
        combined.estimated_cost_saved += result.estimated_cost_saved
        combined.latencies_ms.extend(result.latencies_ms)
        if result.recovery_time_ms is not None:
            recovery_times.append(result.recovery_time_ms)

    if recovery_times:
        combined.recovery_time_ms = sum(recovery_times) / len(recovery_times)

    return combined
