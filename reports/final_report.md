# Bao cao Day 25 - Reliability cho LLM Agent Gateway

Sinh vien: Tran Thi Thanh Tam

File nay do `scripts/generate_report.py` sinh ra tu cac file so lieu that
trong thu muc `reports/`. Muon dung lai thi chay `make all-evidence`.

---

## 1. Kien truc

Y tuong chinh: mot request khong bao gio duoc phep chet han. No di qua 3 lop,
lop truoc hong thi lop sau do. Moi loi ra deu duoc dan nhan (`route`) de sau
nay doc metrics biet request da di duong nao.

```
                        User request
                             |
                             v
                    +--------------------+
                    | ReliabilityGateway |
                    +--------------------+
                             |
            (1) Cache check   |  ResponseCache / SharedRedisCache
                             v
                 +-----------------------+
                 | similarity >= 0.92 ?  |
                 | privacy guard ?       |--- HIT --> tra loi luon
                 | false-hit guard ?     |            route=cache_hit:0.98
                 +-----------------------+            0 ms, 0 dong
                             |
                           MISS
                             v
            (2) +-------------------------------+
                | CircuitBreaker['primary']     |
                | CLOSED -> goi that            |--- OK ---> route=primary
                | OPEN   -> fail fast, khong goi|
                +-------------------------------+
                             |
                    loi / circuit dang OPEN
                             v
                +-------------------------------+
                | CircuitBreaker['backup']      |--- OK ---> route=fallback
                +-------------------------------+
                             |
                    ca 2 provider deu hong
                             v
            (3) +-------------------------------+
                | Static fallback message       |--- route=static_fallback
                +-------------------------------+
```

Circuit breaker co 3 trang thai:

```
            >= failure_threshold loi lien tiep
   CLOSED ----------------------------------> OPEN
     ^                                          |
     |                                          | het reset_timeout_seconds
     | probe_success                            v
     +---------------------------------- HALF_OPEN
                                                |
                                                | probe that bai
                                                +--> quay lai OPEN
```

Cho quan trong nhat trong `record_failure()`: hai duong mo circuit phai tach
rieng bang `if/elif` chu khong gop bang `or`, vi ly do khac nhau:

- Dang HALF_OPEN ma probe that bai -> mo lai ngay, ly do `probe_failure`.
  Neu khong tach thi phai doi du `failure_threshold` loi nua moi mo lai,
  tuc la ban them mot dong request vao provider dang om - dung retry storm.
- Dang CLOSED ma du nguong -> mo, ly do `failure_threshold_reached`.

## 2. Cau hinh va ly do chon

| Tham so | Gia tri | Ly do |
|---|---:|---|
| `failure_threshold` | 3 | De 1 thi qua nhay, provider chi trot 1 loi mang la cat luon. 3 loi lien tiep thi gan nhu chac chan provider hong that. |
| `reset_timeout_seconds` | 0.2 | Trong lab moi request chi ~200-300 ms nen de ngan cho kip thay chu ky phuc hoi. He that thi phai de 10-30 s. |
| `success_threshold` | 1 | 1 probe thanh cong la dong lai. Danh doi: phuc hoi nhanh nhung de dong nham neu provider dang chap chon. |
| `cache.ttl_seconds` | 300 | 5 phut. Cau hoi FAQ/chinh sach it doi trong 5 phut, ma van du ngan de khong phuc vu noi dung qua cu. |
| `cache.similarity_threshold` | 0.92 | Nguong cao. Thu 0.85 thi cau hoi hoc phi 2024 va 2025 cham diem ~0.96 nen van dinh nhau, phai co them false-hit guard. De 0.92 thi cau viet lai nhe van hit (~0.95) con cau khac han thi truot. |
| `load_test.requests` | 100 / kich ban | 4 kich ban = 400 request, du de P95 va P99 co y nghia. |
| `load_test.concurrency` | 10 | Chay 10 thread song song cho giong tai that, khong phai goi tuan tu tung cai. |

Bang provider (trong `configs/default.yaml`):

| Provider | fail_rate | base_latency_ms | cost / 1k token |
|---|---:|---:|---:|
| primary | 0.25 | 180 | 0.01 |
| backup | 0.05 | 260 | 0.006 |

Primary nhanh hon nhung hong nhieu hon va dat hon; backup cham hon nhung on
dinh va re hon. Vay nen thu tu goi la primary truoc, backup do.

## 3. SLO

So o cot 'Do duoc' la so gop cua ca 4 kich ban chaos, tuc la da tinh ca kich
ban primary chet 100%. Do la truong hop xau nhat chu khong phai ngay thuong.

| SLI | Muc tieu | Do duoc | Dat? |
|---|---|---:|---|
| Availability | >= 99% | 99.25% | DAT |
| Latency P95 | < 2500 ms | 514.19 ms | DAT |
| Fallback success rate | >= 95% | 96.77% | DAT |
| Cache hit rate | >= 10% | 51.50% | DAT |
| Recovery time | < 5000 ms | khong do duoc o lan chay nay | xem muc 7 |

## 4. So lieu (reports/metrics.json)

| Metric | Gia tri |
|---|---:|
| total_requests | 400 |
| availability | 0.9925 |
| error_rate | 0.0075 |
| latency_p50_ms | 1.44 |
| latency_p95_ms | 514.19 |
| latency_p99_ms | 539.29 |
| fallback_success_rate | 0.9677 |
| cache_hit_rate | 0.515 |
| circuit_open_count | 7 |
| recovery_time_ms | None |
| false_hits_blocked | 10 |
| privacy_bypassed | 98 |
| estimated_cost | 0.091202 |
| estimated_cost_saved | 0.09958 |

Cach do latency: em do bang `time.perf_counter()` bao quanh ca loi goi
`gateway.complete()`, tuc la latency ma nguoi dung thuc su cam nhan. Neu lay
`latency_ms` cua provider thi cache hit se bi bo qua (no bang 0) va luc do
bang so sanh cache o muc 5 se khong the hien duoc gi.

Cach tinh `estimated_cost_saved`: lay chi phi trung binh that cua nhung
request da phai goi provider trong chinh lan chay do, roi nhan voi so cache
hit. Luc dau em de mot hang so 0.001/hit thi ra so tien tiet kiem con lon hon
ca tong tien da tieu - vo ly, nen phai sua.

## 5. So sanh co cache va khong cache

Hai lan chay giong het nhau, chi khac `cache.enabled`:

```bash
python scripts/run_chaos.py --config configs/no_cache.yaml --out reports/metrics_nocache.json
python scripts/run_chaos.py --config configs/default.yaml  --out reports/metrics.json
```

| Metric | Khong cache | Co cache | Chenh lech |
|---|---:|---:|---|
| latency_p50_ms | 237.76 | 1.44 | giam 99.4% (-236.32 ms) - tot |
| latency_p95_ms | 531.19 | 514.19 | giam 3.2% (-17.00 ms) - tot |
| latency_p99_ms | 545.26 | 539.29 | giam 1.1% (-5.97 ms) - tot |
| estimated_cost | 0.182690 | 0.091202 | giam 50.1% (-0.09) - tot |
| cache_hit_rate | 0.00% | 51.50% | tang 51.50% |
| availability | 98.50% | 99.25% | - |

Nhan xet:

- P50 giam rat manh (khoang 165 lan) vi hon mot nua so request
  duoc tra tu bo nho, khong phai cho provider ngu 180-260 ms.
- P95 gan nhu khong doi. Dieu nay dung: P95 la nhom request bi miss cache
  roi con phai fallback sang backup, cache khong cuu duoc nhom nay.
- Tien giam khoang 50%, dung xap xi voi ty le cache hit
  51.50%.

## 6. Redis shared cache

**Vi sao cache trong bo nho khong du:** cache `ResponseCache` nam trong RAM cua
dung mot tien trinh. Chay 3 replica sau load balancer thi thanh 3 cai cache
roi rac, cung mot cau hoi vao 3 may la 3 lan tra tien. TTL va guardrail cung
moi may mot kieu, restart la mat sach.

**SharedRedisCache giai quyet the nao:** day entry ra Redis, moi entry la mot
Redis Hash `{prefix}{md5(query)[:12]}` gom 2 field `query` va `response`, het
han bang `EXPIRE`. Moi replica doc/ghi chung mot cho nen hit cua may nay dung
duoc cho may kia. Tim gan dung thi `SCAN` theo prefix roi cham diem
`ResponseCache.similarity()` o phia client.

Gateway con bat exception quanh cache, nen Redis chet thi request van chay
tiep xuong provider - mat hit rate chu khong mat availability.

### Bang chung state dung chung

Hai object `SharedRedisCache` doc lap, dong vai 2 replica (`scripts/redis_evidence.py`):

```
=== Redis shared cache evidence ===
redis_url=redis://localhost:6379/0  prefix=rl:cache:
redis_version=7.4.10

Instance A and instance B are two separate Python objects,
standing in for two gateway replicas behind a load balancer.

Step 1 - instance A writes, instance B reads
  A.set('Explain circuit breaker states in one paragraph.')
  B.get(...) -> hit=True  score=1.00
  value='[primary] circuit breakers have three states ...'

Step 2 - instance B gets a semantic hit on a reworded question
  B.get('Explain the circuit breaker states in one paragraph')
  -> hit=True  score=0.95

Step 3 - privacy guard: a sensitive query never reaches Redis
  keys before=1  keys after=1  (unchanged means it was blocked)

Step 4 - false-hit guard: different year must not reuse the cached answer
  B.get('... 2025 academic year?') -> hit=False  score=0.96
  false_hit_log=[{'query': 'What is the tuition fee for the 2025 academic year?', 'cached_key': 'What is the tuition fee for the 2024 academic year?', 'reason': 'date_or_number_mismatch'}]

Step 5 - raw Redis contents (equivalent of: redis-cli KEYS "rl:cache:*")
  rl:cache:095946136fea  ttl=300s  query='Explain circuit breaker states in one paragraph.'
  rl:cache:0bc3b1acf73d  ttl=300s  query='What is the tuition fee for the 2024 academic year?'

TTL is handled by Redis EXPIRE, so entries are evicted by the server
and every replica sees the same expiry.
```

### Redis CLI

```
$ docker compose exec redis redis-cli INFO server | grep redis_version
redis_version:7.4.10

$ docker compose exec redis redis-cli DBSIZE
14

$ docker compose exec redis redis-cli KEYS "rl:cache:*"
rl:cache:4fc3c69b9376
rl:cache:3dab98c0e49e
rl:cache:98332d0d1c9c
rl:cache:dacb2b833659
rl:cache:fff10da1c72c
rl:cache:844ef0143a5c
rl:cache:0bc3b1acf73d
rl:cache:9e413fd814eb
rl:cache:da61fb49b4f6
rl:cache:d354658dc020
rl:cache:734852f3cf4a
rl:cache:095946136fea
rl:cache:3936614ac4c2
rl:cache:b2a52f7dc795

$ docker compose exec redis redis-cli TYPE rl:cache:4fc3c69b9376
hash

$ docker compose exec redis redis-cli HGETALL rl:cache:4fc3c69b9376
response
[primary] reliable answer for: Summarize the refund policy for a student who missed the 202
query
Summarize the refund policy for a student who missed the 2024 deadline.

$ docker compose exec redis redis-cli TTL rl:cache:4fc3c69b9376
288
```

Cho dang chu y: `DBSIZE` luon nho hon 20. Bo `data/sample_queries.jsonl` co 20
cau, nhung 5 cau nhay cam (so du tai khoan, reset mat khau, the tin dung, SSN,
...) bi privacy guard chan nen khong bao gio vao Redis. Toi da chi con 15 key,
va thuc te con it hon vi khong phai cau nao cung duoc random goi toi.

### Cache trong bo nho vs cache Redis

| Metric | In-memory | Redis | Ghi chu |
|---|---:|---:|---|
| latency_p50_ms | 1.44 | 26.44 | Redis cham hon vi phai di qua TCP va SCAN |
| latency_p95_ms | 514.19 | 529.32 | gan bang nhau, P95 bi provider chi phoi |
| cache_hit_rate | 51.50% | 53.75% | tuong duong |
| availability | 99.25% | 98.75% | tuong duong |

Doi lai vai ms moi request thi duoc cache dung chung cho nhieu instance.
Voi he thong nhieu replica thi danh doi nay xung dang.

## 7. Kich ban chaos

4 kich ban, moi kich ban 100 request, dung tieu chi pass/fail rieng chu khong
dung chung mot cau 'co request nao thanh cong khong'.

| Kich ban | Mong doi | Quan sat duoc | Ket qua |
|---|---|---|---|
| `primary_timeout_100` | primary chet 100%, traffic phai chay sang backup, circuit phai mo | availability 100.00%, circuit mo 5 lan, cache hit 48.00%, P95 525.32 ms | **pass** |
| `primary_flaky_50` | primary hong 50%, circuit dong/mo qua lai, nguoi dung van duoc phuc vu | availability 98.00%, circuit mo 2 lan, cache hit 55.00%, P95 504.47 ms | **pass** |
| `all_healthy` | khong hong gi, khong circuit nao duoc mo | availability 100.00%, circuit mo 0 lan, cache hit 54.00%, P95 231.66 ms | **pass** |
| `cache_stress_repeat` | cau hoi lap lai, phai co cache hit that su | availability 99.00%, circuit mo 0 lan, cache hit 49.00%, P95 511.91 ms | **pass** |

### Bang chung phuc hoi circuit

Trong `reports/metrics.json` lan nay `recovery_time_ms` la `null`. Em de
nguyen chu khong sua cho dep, vi no noi len mot van de that:

Khi cache hit rate cao (~50%), sau khi circuit cua primary mo ra thi phan
lon request tiep theo duoc tra tu cache, khong con ai goi provider nua.
Khong co request nao goi thi khong co probe HALF_OPEN, ma khong co probe
thi circuit khong bao gio dong lai. Tuc la primary co the da khoe tro lai
tu lau ma he thong van bam vao backup.

De chung minh may trang thai van chay dung, em viet
`scripts/recovery_evidence.py` ep chay tron mot chu ky:

```
=== Circuit breaker recovery evidence ===
failure_threshold=3  reset_timeout_seconds=0.2

Step 1 - primary is down (fail_rate=1.0), send 4 requests
  req 0: route=fallback   provider=backup primary_circuit=closed
  req 1: route=fallback   provider=backup primary_circuit=closed
  req 2: route=fallback   provider=backup primary_circuit=open
  req 3: route=fallback   provider=backup primary_circuit=open
  -> primary circuit is now OPEN; users are still served by the backup

Step 2 - primary is healed (fail_rate=0.0), wait out the reset timeout
  slept 0.25s

Step 3 - next request is allowed through as a HALF_OPEN probe
  route=primary  provider=primary  primary_circuit=closed
  -> primary circuit is back to CLOSED

Full transition log for the primary circuit:
     closed -> open      reason=failure_threshold_reached  ts=1787851422.738
       open -> half_open reason=reset_timeout_elapsed  ts=1787851423.070
  half_open -> closed    reason=probe_success  ts=1787851423.120

Measured recovery time: 381.3 ms
(reset_timeout is 200 ms, so anything a little above that is the expected result)
```

Doc transition log thay du 3 buoc: `closed -> open` (ly do
`failure_threshold_reached`), `open -> half_open` (`reset_timeout_elapsed`),
roi `half_open -> closed` (`probe_success`).

### Guardrail cua cache

- `false_hits_blocked` = 10: so lan cache tim
  duoc entry qua nguong nhung bi tu choi vi so 4 chu so khac nhau.
- `privacy_bypassed` = 98/400:
  so request nhay cam khong duoc luu va khong duoc phuc vu tu cache.

Vi du that bi chan (lay tu `reports/metrics_scenarios.json`):

- Hoi: `What is the tuition fee for the 2024 academic year?`
  - Cache co: `What is the tuition fee for the 2025 academic year?`
  - Diem similarity rat cao nhung nam khac nhau -> tu choi, ghi log
    `date_or_number_mismatch`.
- Hoi: `Summarize the refund policy for a student who missed the 2024 deadline.`
  - Cache co: `Summarize the refund policy for a student who missed the 2026 deadline.`
  - Diem similarity rat cao nhung nam khac nhau -> tu choi, ghi log
    `date_or_number_mismatch`.
- Hoi: `Summarize the refund policy for a student who missed the 2026 deadline.`
  - Cache co: `Summarize the refund policy for a student who missed the 2024 deadline.`
  - Diem similarity rat cao nhung nam khac nhau -> tu choi, ghi log
    `date_or_number_mismatch`.

Neu khong co guardrail nay thi he thong se tra chinh sach 2024 cho nguoi hoi
ve 2026. Sai kieu do nguy hiem hon la miss cache, vi nguoi dung khong biet
la minh dang doc nham.

## 8. Phan tich diem yeu con lai

**Diem yeu lon nhat: circuit chi doi trang thai khi co request di qua no.**

`allow_request()` la ham duy nhat chuyen OPEN -> HALF_OPEN, ma ham nay chi chay
khi co request that su goi xuong provider. Cache hit rate cua he thong dang la
51.50%, tuc la hon mot nua so request khong he cham toi provider.
Neu traffic thua va cache dang an gan het request thi sau khi circuit mo ra co
the rat lau moi co mot request tao probe, va he thong bam vao backup lau hon
can thiet - backup cham hon (260 ms so voi 180 ms) va neu backup cung hong thi
khong con gi do.

Dung tinh huong nay xay ra o chinh lan chay nay: `circuit_open_count` = 7 nhung `recovery_time_ms` = `null`, tuc la
circuit mo ra roi khong dong lai duoc lan nao truoc khi kich ban ket thuc.
So nay dao dong giua cac lan chay, nen bang chung chac chan cho may trang thai
la `reports/recovery_evidence.txt` chu khong phai o day.

**Cach sua:** them mot health-check chay nen, dinh ky (vi du 10 s/lan) tu goi
`breaker.allow_request()` va ban mot request nho toi provider dang OPEN. Nhu
vay probe khong con phu thuoc vao traffic that nua.

**Diem yeu thu hai: trang thai circuit chi nam trong RAM.** Cache da dung chung
qua Redis nhung circuit thi chua. 3 replica thi moi cai tu hoc lai rang provider
dang hong, tuc la provider om phai an gap 3 lan so request loi. Sua bang cach
day counter cua breaker vao Redis (`INCR` + `EXPIRE`).

**Diem yeu thu ba: false-hit guard chi bat so 4 chu so.** No bat duoc 2024 vs
2026 nhung khong bat duoc 'chinh sach nam nay' vs 'chinh sach nam ngoai', hay
'ky 1' vs 'ky 2'. Day la heuristic chu khong phai giai phap dung nghia.

## 9. Viec se lam tiep

1. Health-check chay nen de probe provider dang OPEN, khong phu thuoc traffic.
2. Day trang thai circuit vao Redis de nhieu instance dung chung.
3. Thay false-hit guard bang cach gan nhan thoi gian cho cau hoi (query co
   nhac toi thoi diem cu the thi TTL ngan hon hoac khong cache).

---

## Phu luc - cach chay lai

```bash
pip install -e ".[dev]"
docker compose up -d          # Redis 7 cho shared cache
make test                     # 35 passed, 7 xpassed
make run-chaos                # sinh reports/metrics.json
make all-evidence             # chay het cac lan do + sinh bao cao nay
```

Log test day du: `reports/test_output.txt`.
