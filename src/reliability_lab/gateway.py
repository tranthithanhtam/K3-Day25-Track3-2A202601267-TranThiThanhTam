from __future__ import annotations

from dataclasses import dataclass

from reliability_lab.cache import ResponseCache, SharedRedisCache
from reliability_lab.circuit_breaker import CircuitBreaker, CircuitOpenError
from reliability_lab.providers import FakeLLMProvider, ProviderError, ProviderResponse


@dataclass(slots=True)
class GatewayResponse:
    text: str
    route: str
    provider: str | None
    cache_hit: bool
    latency_ms: float
    estimated_cost: float
    error: str | None = None


class ReliabilityGateway:
    """Routes requests through cache, circuit breakers, and fallback providers."""

    def __init__(
        self,
        providers: list[FakeLLMProvider],
        breakers: dict[str, CircuitBreaker],
        cache: ResponseCache | SharedRedisCache | None = None,
        cost_budget: float | None = None,
    ):
        self.providers = providers
        self.breakers = breakers
        self.cache = cache
        self.cost_budget = cost_budget
        self.cumulative_cost: float = 0.0

    def complete(self, prompt: str) -> GatewayResponse:
        """Return a reliable response, degrading gracefully instead of failing.

        The request walks one pipeline, and every exit is labelled so the route
        reason is visible in the metrics:

        1. Cache — a hit returns immediately with ``route="cache_hit:<score>"``,
           zero latency and zero cost. If the cache backend itself is down the
           error is swallowed and the request continues to the providers, so a
           dead Redis degrades the hit rate instead of the availability.
        2. Provider chain — each provider is called through its own circuit
           breaker. The first provider is ``route="primary"``, any later one is
           ``route="fallback"``. A provider failure or an open circuit records the
           error and moves to the next provider; an open circuit costs no network
           call at all, which is what stops a retry storm.
        3. Static fallback — every provider is unavailable, so a degraded message
           is returned with ``route="static_fallback"`` and the last error attached.

        Cost-aware routing: once ``cost_budget`` is spent, the expensive primary is
        skipped and traffic goes straight to the cheaper backup.
        """
        # 1. CACHE CHECK (with graceful degradation)
        if self.cache is not None:
            try:
                cached_text, score = self.cache.get(prompt)
                if cached_text is not None:
                    return GatewayResponse(
                        text=cached_text,
                        route=f"cache_hit:{score:.2f}",
                        provider=None,
                        cache_hit=True,
                        latency_ms=0.0,
                        estimated_cost=0.0,
                    )
            except Exception:  # noqa: S110, BLE001
                pass  # Graceful fallback to provider chain if cache is unavailable

        # 2. PROVIDER FALLBACK CHAIN
        last_error: str | None = None
        for i, provider in enumerate(self.providers):
            # Cost-aware routing: skip expensive provider if cost budget is exceeded
            if (
                self.cost_budget is not None
                and self.cumulative_cost >= self.cost_budget
                and i == 0
                and len(self.providers) > 1
            ):
                continue

            breaker = self.breakers.get(provider.name)
            if breaker is None:
                continue
            try:
                resp: ProviderResponse = breaker.call(provider.complete, prompt)
                self.cumulative_cost += resp.estimated_cost
                if self.cache is not None:
                    try:
                        self.cache.set(prompt, resp.text, {"provider": provider.name})
                    except Exception:  # noqa: S110, BLE001
                        pass
                route = "primary" if i == 0 else "fallback"
                return GatewayResponse(
                    text=resp.text,
                    route=route,
                    provider=provider.name,
                    cache_hit=False,
                    latency_ms=resp.latency_ms,
                    estimated_cost=resp.estimated_cost,
                )
            except (ProviderError, CircuitOpenError) as e:
                last_error = str(e)
                continue

        # 3. STATIC FALLBACK
        return GatewayResponse(
            text="The service is temporarily degraded. Please try again soon.",
            route="static_fallback",
            provider=None,
            cache_hit=False,
            latency_ms=0.0,
            estimated_cost=0.0,
            error=last_error,
        )
