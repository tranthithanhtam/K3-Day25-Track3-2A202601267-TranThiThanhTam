"""Prove that SharedRedisCache really shares state between gateway instances.

Two independent SharedRedisCache objects are created, standing in for two gateway
replicas. One writes, the other reads. The raw Redis keys and their TTLs are printed
so the state can be checked outside Python as well.

Run:  docker compose up -d && python scripts/redis_evidence.py
"""
from __future__ import annotations

import sys

from reliability_lab.cache import SharedRedisCache

REDIS_URL = "redis://localhost:6379/0"
PREFIX = "rl:cache:"


def main() -> None:
    instance_a = SharedRedisCache(REDIS_URL, ttl_seconds=300, similarity_threshold=0.92, prefix=PREFIX)
    if not instance_a.ping():
        print("Redis is not reachable. Start it with: docker compose up -d")
        sys.exit(1)

    instance_b = SharedRedisCache(REDIS_URL, ttl_seconds=300, similarity_threshold=0.92, prefix=PREFIX)

    lines: list[str] = []

    def log(msg: str) -> None:
        print(msg)
        lines.append(msg)

    instance_a.flush()

    log("=== Redis shared cache evidence ===")
    log(f"redis_url={REDIS_URL}  prefix={PREFIX}")
    server_version = instance_a._redis.info("server")["redis_version"]
    log(f"redis_version={server_version}")
    log("")

    log("Instance A and instance B are two separate Python objects,")
    log("standing in for two gateway replicas behind a load balancer.")
    log("")

    log("Step 1 - instance A writes, instance B reads")
    query = "Explain circuit breaker states in one paragraph."
    instance_a.set(query, "[primary] circuit breakers have three states ...")
    value, score = instance_b.get(query)
    log(f"  A.set({query!r})")
    log(f"  B.get(...) -> hit={value is not None}  score={score:.2f}")
    log(f"  value={value!r}")
    log("")

    log("Step 2 - instance B gets a semantic hit on a reworded question")
    value2, score2 = instance_b.get("Explain the circuit breaker states in one paragraph")
    log("  B.get('Explain the circuit breaker states in one paragraph')")
    log(f"  -> hit={value2 is not None}  score={score2:.2f}")
    log("")

    log("Step 3 - privacy guard: a sensitive query never reaches Redis")
    before = len(list(instance_a._redis.scan_iter(f"{PREFIX}*")))
    instance_a.set("Give me the current account balance for user 123.", "Balance: $500")
    after = len(list(instance_a._redis.scan_iter(f"{PREFIX}*")))
    log(f"  keys before={before}  keys after={after}  (unchanged means it was blocked)")
    log("")

    log("Step 4 - false-hit guard: different year must not reuse the cached answer")
    instance_a.set("What is the tuition fee for the 2024 academic year?", "2024 tuition is X")
    value3, score3 = instance_b.get("What is the tuition fee for the 2025 academic year?")
    log(f"  B.get('... 2025 academic year?') -> hit={value3 is not None}  score={score3:.2f}")
    log(f"  false_hit_log={instance_b.false_hit_log}")
    log("")

    log('Step 5 - raw Redis contents (equivalent of: redis-cli KEYS "rl:cache:*")')
    for key in sorted(instance_a._redis.scan_iter(f"{PREFIX}*")):
        ttl = instance_a._redis.ttl(key)
        stored_query = instance_a._redis.hget(key, "query")
        log(f"  {key}  ttl={ttl}s  query={stored_query!r}")
    log("")
    log("TTL is handled by Redis EXPIRE, so entries are evicted by the server")
    log("and every replica sees the same expiry.")

    instance_a.flush()
    instance_a.close()
    instance_b.close()

    out = "reports/redis_evidence.txt"
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
