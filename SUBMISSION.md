# Bai nop - Day 25 Track 3: Reliability Agent

Sinh vien: Tran Thi Thanh Tam - 2A202601267

## Bao cao chinh

**[reports/final_report.md](reports/final_report.md)** - day du 9 muc theo template,
so lieu lay tu cac lan chay that.

## Ket qua chay

| Thu | Ket qua |
|---|---|
| `make test` (co Redis) | **35 passed, 7 xpassed**, 0 failed, 0 skipped |
| `make lint` (ruff) | sach |
| `make typecheck` (mypy strict) | sach |
| 4 kich ban chaos | pass het |

7 test `xfail` trong `test_todo_requirements.py` da chuyen thanh **xpass**, tuc la
lam xong het TODO. Log day du o [reports/test_output.txt](reports/test_output.txt).

## Cac TODO da lam

| File | Ham | Test |
|---|---|---|
| `circuit_breaker.py` | `allow_request()`, `call()`, `record_success()`, `record_failure()` | 12 pass |
| `cache.py` | `ResponseCache.similarity()`, `get()`, `set()` | 9 pass |
| `cache.py` | `SharedRedisCache.get()`, `set()` | 6 pass |
| `gateway.py` | `ReliabilityGateway.complete()` | 4 pass |
| `chaos.py` | `run_scenario()`, `calculate_recovery_time_ms()` | qua `make run-chaos` |
| `metrics.py` | `write_csv()` | 1 xpass |

## File bang chung

| File | Noi dung |
|---|---|
| `reports/metrics.json` / `.csv` | so lieu chinh (cache trong bo nho) |
| `reports/metrics_scenarios.json` | so lieu rieng cua tung kich ban chaos |
| `reports/metrics_nocache.json` | lan chay doi chung, tat cache |
| `reports/metrics_redis.json` | lan chay dung Redis lam cache |
| `reports/metrics.prom` | export dang Prometheus |
| `reports/recovery_evidence.txt` | tron mot chu ky CLOSED -> OPEN -> HALF_OPEN -> CLOSED |
| `reports/redis_evidence.txt` | 2 instance dung chung state + guardrail |
| `reports/redis_cli_output.txt` | output `redis-cli` KEYS / HGETALL / TTL |
| `reports/test_output.txt` | log `pytest -v` |

## Cach chay lai

```bash
python -m venv .venv && source .venv/Scripts/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
docker compose up -d      # Redis 7, can cho phan shared cache
make test                 # 35 passed, 7 xpassed
make run-chaos            # sinh reports/metrics.json
make report               # sinh reports/final_report.md
make all-evidence         # chay het moi thu o tren mot luot
```

Luu y: neu may da co san Redis chay o cong 6379 thi phai tat di, khong container
Docker se bi che mat. Em bi dinh dung loi nay: may co service Redis 3.0.504 cua
Windows dang chay, redis-py 7 goi lenh `HELLO` thi bao `unknown command`, va
`hset(mapping=...)` cung khong chay tren Redis 3.

## Nhung cho em phai sua them

Ngoai phan TODO, em sua them may cho vi chay ra so lieu vo ly:

1. **`estimated_cost_saved` dung hang so 0.001/hit** - ra ket qua tiet kiem
   `0.205` trong khi tong tien tieu chi co `0.097`. Sua thanh: lay chi phi trung
   binh that cua cac request da phai goi provider trong chinh lan chay do.
2. **Latency bo qua cache hit** - code cu chi ghi nhan `latency_ms > 0`, ma cache
   hit thi bang 0 nen bi loai. Nhu vay bang so sanh co cache / khong cache khong
   cho thay gi. Sua thanh do `time.perf_counter()` quanh ca `gateway.complete()`.
3. **`make report` ghi de mat bao cao** - `generate_report.py` cu sinh ra mot file
   rat so sai co dong "Analysis TODO(student)", chay `make report` la mat bai. Sua
   thanh: script sinh ra ca bao cao day du tu file so lieu, nen chay lai bao nhieu
   lan cung duoc.
4. **`mypy` bao loi thieu stub cua PyYAML** - them `types-PyYAML` vao `[dev]`.
5. **Docstring con nguyen chu "TODO(student): Implement..."** o tren nhung ham da
   viet xong - sua lai thanh mo ta that.
