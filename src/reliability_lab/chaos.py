from __future__ import annotations

import json
import random
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from reliability_lab.cache import ResponseCache, SharedRedisCache, _is_uncacheable
from reliability_lab.circuit_breaker import CircuitBreaker
from reliability_lab.config import LabConfig, ScenarioConfig
from reliability_lab.gateway import GatewayResponse, ReliabilityGateway
from reliability_lab.metrics import RunMetrics
from reliability_lab.providers import FakeLLMProvider


def load_queries(path: str | Path = "data/sample_queries.jsonl") -> list[str]:
    queries: list[str] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        queries.append(json.loads(line)["query"])
    return queries


def build_gateway(
    config: LabConfig, provider_overrides: dict[str, float] | None = None
) -> ReliabilityGateway:
    providers = []
    for p in config.providers:
        fail_rate = (
            provider_overrides.get(p.name, p.fail_rate) if provider_overrides else p.fail_rate
        )
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
            cache.flush()  # start each scenario from a cold shared cache
        else:
            cache = ResponseCache(config.cache.ttl_seconds, config.cache.similarity_threshold)
    return ReliabilityGateway(providers, breakers, cache)


def calculate_recovery_time_ms(gateway: ReliabilityGateway) -> float | None:
    """Average time a circuit stayed OPEN before closing again, in milliseconds.

    Each breaker transition_log is walked in order. An entry with to="open" starts
    a stopwatch and the next to="closed" stops it, which is one full outage and
    recovery cycle. Intermediate to="half_open" entries belong to the same cycle
    and are skipped. Returns None when no circuit ever recovered, because that is
    a meaningful result and not the same as zero.
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


def _timed_complete(gateway: ReliabilityGateway, prompt: str) -> tuple[GatewayResponse, float]:
    """Call the gateway and measure the latency a real user would feel.

    The provider latency_ms field is not used here: it ignores the cache lookup and
    reports 0 ms for cache hits and static fallbacks. Timing the whole call is what
    makes the with-cache and without-cache percentiles comparable.
    """
    start = time.perf_counter()
    response = gateway.complete(prompt)
    return response, (time.perf_counter() - start) * 1000.0


def run_scenario(
    config: LabConfig,
    queries: list[str],
    scenario: ScenarioConfig,
    concurrency: int | None = None,
) -> RunMetrics:
    """Run one named chaos scenario and collect its metrics."""
    gateway = build_gateway(config, scenario.provider_overrides or None)
    metrics = RunMetrics()
    effective_concurrency = (
        concurrency if concurrency is not None else getattr(config.load_test, "concurrency", 1)
    )

    prompts = [random.choice(queries) for _ in range(config.load_test.requests)]

    if effective_concurrency > 1:
        with ThreadPoolExecutor(max_workers=effective_concurrency) as executor:
            results = list(executor.map(lambda p: _timed_complete(gateway, p), prompts))
    else:
        results = [_timed_complete(gateway, p) for p in prompts]

    for result, latency_ms in results:
        metrics.total_requests += 1
        metrics.estimated_cost += result.estimated_cost
        metrics.latencies_ms.append(latency_ms)

        if result.cache_hit:
            metrics.cache_hits += 1

        if result.route == "fallback":
            metrics.fallback_successes += 1
            metrics.successful_requests += 1
        elif result.route == "static_fallback":
            metrics.static_fallbacks += 1
            metrics.failed_requests += 1
        else:
            metrics.successful_requests += 1

    # Cost saved = what the cache hits would have cost, priced from the calls this
    # same run actually paid for. A fixed per-hit constant would just be a guess.
    paid = [r.estimated_cost for r, _ in results if not r.cache_hit and r.estimated_cost > 0]
    avg_paid_cost = sum(paid) / len(paid) if paid else 0.0
    metrics.estimated_cost_saved = metrics.cache_hits * avg_paid_cost

    # Guardrail evidence: how often the cache refused to answer, and why.
    if gateway.cache is not None:
        metrics.false_hits_blocked = len(gateway.cache.false_hit_log)
        metrics.false_hit_examples = [
            {"query": str(e.get("query", "")), "cached_key": str(e.get("cached_key", ""))}
            for e in gateway.cache.false_hit_log[:3]
        ]
    metrics.privacy_bypassed = sum(1 for p in prompts if _is_uncacheable(p))

    metrics.circuit_open_count = sum(
        len([entry for entry in breaker.transition_log if entry.get("to") == "open"])
        for breaker in gateway.breakers.values()
    )
    metrics.recovery_time_ms = calculate_recovery_time_ms(gateway)
    return metrics


def run_all_scenarios(
    config: LabConfig,
    queries: list[str],
    concurrency: int | None = None,
) -> tuple[RunMetrics, dict[str, RunMetrics]]:
    """Run every named scenario and return the merged metrics plus each scenario's own.

    Each scenario is judged against its own criterion instead of one generic
    "did anything succeed" check, so a pass really means the scenario proved what
    it was designed to prove. The per-scenario metrics are kept so the report can
    show observed numbers next to expected behaviour, not only pass/fail.
    """
    per_scenario: dict[str, RunMetrics] = {}

    if not config.scenarios:
        default_scenario = ScenarioConfig(name="default", description="baseline run")
        metrics = run_scenario(config, queries, default_scenario, concurrency=concurrency)
        metrics.scenarios = {"default": "pass" if metrics.successful_requests > 0 else "fail"}
        return metrics, {"default": metrics}

    combined = RunMetrics()
    recovery_times: list[float] = []

    for scenario in config.scenarios:
        result = run_scenario(config, queries, scenario, concurrency=concurrency)
        per_scenario[scenario.name] = result

        if scenario.name == "primary_timeout_100":
            # Primary is dead: traffic must reach the backup, not the static message.
            passed = (
                result.fallback_successes > 0
                and result.static_fallbacks < result.total_requests
            )
        elif scenario.name == "primary_flaky_50":
            # Half the primary calls fail, so the circuit must react while users
            # keep being served.
            passed = result.circuit_open_count > 0 and result.availability >= 0.95
        elif scenario.name == "all_healthy":
            # Nothing is broken, so no circuit should ever open.
            passed = (
                result.successful_requests == result.total_requests
                and result.circuit_open_count == 0
            )
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
        combined.false_hits_blocked += result.false_hits_blocked
        combined.privacy_bypassed += result.privacy_bypassed
        combined.false_hit_examples.extend(result.false_hit_examples)
        combined.estimated_cost += result.estimated_cost
        combined.estimated_cost_saved += result.estimated_cost_saved
        combined.latencies_ms.extend(result.latencies_ms)
        if result.recovery_time_ms is not None:
            recovery_times.append(result.recovery_time_ms)

    combined.false_hit_examples = combined.false_hit_examples[:3]
    if recovery_times:
        combined.recovery_time_ms = sum(recovery_times) / len(recovery_times)

    return combined, per_scenario


def run_simulation(
    config: LabConfig,
    queries: list[str],
    concurrency: int | None = None,
) -> RunMetrics:
    """Merged metrics for every scenario. Thin wrapper over run_all_scenarios()."""
    combined, _ = run_all_scenarios(config, queries, concurrency=concurrency)
    return combined
