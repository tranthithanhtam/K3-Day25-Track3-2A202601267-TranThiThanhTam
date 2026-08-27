.PHONY: test lint typecheck run-chaos run-chaos-nocache run-chaos-redis \
        recovery-evidence redis-evidence all-evidence report clean docker-up docker-down

test:
	pytest -q

lint:
	ruff check src tests scripts

typecheck:
	mypy src

run-chaos:
	python scripts/run_chaos.py --config configs/default.yaml --out reports/metrics.json \
		--csv reports/metrics.csv --prom-out reports/metrics.prom

# Control run for the cache comparison in the report: identical config, cache off.
run-chaos-nocache:
	python scripts/run_chaos.py --config configs/no_cache.yaml \
		--out reports/metrics_nocache.json --csv reports/metrics_nocache.csv

# Same run backed by Redis instead of process memory (needs: make docker-up).
run-chaos-redis:
	python scripts/run_chaos.py --config configs/redis.yaml \
		--out reports/metrics_redis.json --csv reports/metrics_redis.csv

recovery-evidence:
	python scripts/recovery_evidence.py

redis-evidence:
	python scripts/redis_evidence.py

# Everything the report is built from, in order. Needs Redis running.
all-evidence: run-chaos run-chaos-nocache run-chaos-redis recovery-evidence redis-evidence report

report:
	python scripts/generate_report.py --metrics reports/metrics.json --out reports/final_report.md

docker-up:
	docker compose up -d

docker-down:
	docker compose down

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache reports/metrics.json reports/final_report.md
