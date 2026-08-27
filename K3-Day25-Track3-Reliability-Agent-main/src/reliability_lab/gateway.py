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
        """Return a reliable response or a static fallback.

        TODO(student): Implement the full request routing pipeline:

        1. CACHE CHECK — if self.cache is not None:
           - Call self.cache.get(prompt) → (cached_text, score)
           - If cached_text is not None, return GatewayResponse with:
             route=f"cache_hit:{score:.2f}", cache_hit=True, latency=0, cost=0

        2. PROVIDER FALLBACK CHAIN — iterate self.providers in order:
           - Get the circuit breaker: self.breakers[provider.name]
           - Try breaker.call(provider.complete, prompt)
           - On success:
             a. Store in cache: self.cache.set(prompt, response.text, {"provider": provider.name})
             b. Determine route: "primary" if first provider, else "fallback"
             c. Return GatewayResponse with provider info, latency, cost
           - On ProviderError or CircuitOpenError: save error, continue to next provider

        3. STATIC FALLBACK — if all providers fail:
           - Return GatewayResponse with:
             text="The service is temporarily degraded. Please try again soon."
             route="static_fallback", error=last_error

        BONUS TODO: Add cost budget tracking — if cumulative cost exceeds a threshold,
        skip expensive providers and route to cache or cheaper fallback.
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
