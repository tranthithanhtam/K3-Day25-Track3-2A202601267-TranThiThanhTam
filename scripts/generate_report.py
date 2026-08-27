"""Sinh reports/final_report.md tu cac file so lieu that trong reports/.

Bao cao duoc sinh ra tu file chu khong go tay, de chay lai `make report` la ra
dung so lieu cua lan chay moi nhat. Neu bao cao duoc go tay thi `make report`
se ghi de mat, nen minh de toan bo noi dung o day.

Chay:  python scripts/generate_report.py
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

REPORTS = Path("reports")


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _load_text(path: Path) -> str:
    if not path.exists():
        return "(chua co file nay - chay script tuong ung de sinh ra)"
    return path.read_text(encoding="utf-8").strip()


def _fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "khong co"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _pct(value: Any) -> str:
    if value is None:
        return "khong co"
    return f"{float(value) * 100:.2f}%"


def _delta(without: Any, with_: Any, unit: str = "", lower_is_better: bool = True) -> str:
    """Chuoi mo ta chenh lech giua 2 lan chay."""
    if without is None or with_ is None:
        return "-"
    without_f, with_f = float(without), float(with_)
    diff = with_f - without_f
    if without_f == 0:
        return f"{diff:+.2f}{unit}"
    pct = diff / without_f * 100
    arrow = "giam" if diff < 0 else "tang"
    good = (diff < 0) == lower_is_better
    tag = "tot" if good else "xau"
    return f"{arrow} {abs(pct):.1f}% ({diff:+.2f}{unit}) - {tag}"


def build_report(
    metrics: dict[str, Any],
    nocache: dict[str, Any] | None,
    redis_metrics: dict[str, Any] | None,
    scenarios: dict[str, Any] | None,
) -> str:
    scenario_status = metrics.get("scenarios", {})
    scenarios = scenarios or {}

    availability = metrics.get("availability")
    p50 = metrics.get("latency_p50_ms")
    p95 = metrics.get("latency_p95_ms")
    p99 = metrics.get("latency_p99_ms")
    hit_rate = metrics.get("cache_hit_rate")
    fallback_rate = metrics.get("fallback_success_rate")
    recovery = metrics.get("recovery_time_ms")
    cost = metrics.get("estimated_cost")

    lines: list[str] = []
    add = lines.append

    add("# Bao cao Day 25 - Reliability cho LLM Agent Gateway")
    add("")
    add("Sinh vien: Tran Thi Thanh Tam")
    add("")
    add("File nay do `scripts/generate_report.py` sinh ra tu cac file so lieu that")
    add("trong thu muc `reports/`. Muon dung lai thi chay `make all-evidence`.")
    add("")
    add("---")
    add("")

    # ------------------------------------------------------------------ 1
    add("## 1. Kien truc")
    add("")
    add("Y tuong chinh: mot request khong bao gio duoc phep chet han. No di qua 3 lop,")
    add("lop truoc hong thi lop sau do. Moi loi ra deu duoc dan nhan (`route`) de sau")
    add("nay doc metrics biet request da di duong nao.")
    add("")
    add("```")
    add("                        User request")
    add("                             |")
    add("                             v")
    add("                    +--------------------+")
    add("                    | ReliabilityGateway |")
    add("                    +--------------------+")
    add("                             |")
    add("            (1) Cache check   |  ResponseCache / SharedRedisCache")
    add("                             v")
    add("                 +-----------------------+")
    add("                 | similarity >= 0.92 ?  |")
    add("                 | privacy guard ?       |--- HIT --> tra loi luon")
    add("                 | false-hit guard ?     |            route=cache_hit:0.98")
    add("                 +-----------------------+            0 ms, 0 dong")
    add("                             |")
    add("                           MISS")
    add("                             v")
    add("            (2) +-------------------------------+")
    add("                | CircuitBreaker['primary']     |")
    add("                | CLOSED -> goi that            |--- OK ---> route=primary")
    add("                | OPEN   -> fail fast, khong goi|")
    add("                +-------------------------------+")
    add("                             |")
    add("                    loi / circuit dang OPEN")
    add("                             v")
    add("                +-------------------------------+")
    add("                | CircuitBreaker['backup']      |--- OK ---> route=fallback")
    add("                +-------------------------------+")
    add("                             |")
    add("                    ca 2 provider deu hong")
    add("                             v")
    add("            (3) +-------------------------------+")
    add("                | Static fallback message       |--- route=static_fallback")
    add("                +-------------------------------+")
    add("```")
    add("")
    add("Circuit breaker co 3 trang thai:")
    add("")
    add("```")
    add("            >= failure_threshold loi lien tiep")
    add("   CLOSED ----------------------------------> OPEN")
    add("     ^                                          |")
    add("     |                                          | het reset_timeout_seconds")
    add("     | probe_success                            v")
    add("     +---------------------------------- HALF_OPEN")
    add("                                                |")
    add("                                                | probe that bai")
    add("                                                +--> quay lai OPEN")
    add("```")
    add("")
    add("Cho quan trong nhat trong `record_failure()`: hai duong mo circuit phai tach")
    add("rieng bang `if/elif` chu khong gop bang `or`, vi ly do khac nhau:")
    add("")
    add("- Dang HALF_OPEN ma probe that bai -> mo lai ngay, ly do `probe_failure`.")
    add("  Neu khong tach thi phai doi du `failure_threshold` loi nua moi mo lai,")
    add("  tuc la ban them mot dong request vao provider dang om - dung retry storm.")
    add("- Dang CLOSED ma du nguong -> mo, ly do `failure_threshold_reached`.")
    add("")

    # ------------------------------------------------------------------ 2
    add("## 2. Cau hinh va ly do chon")
    add("")
    add("| Tham so | Gia tri | Ly do |")
    add("|---|---:|---|")
    add(
        "| `failure_threshold` | 3 | De 1 thi qua nhay, provider chi trot 1 loi mang "
        "la cat luon. 3 loi lien tiep thi gan nhu chac chan provider hong that. |"
    )
    add(
        "| `reset_timeout_seconds` | 0.2 | Trong lab moi request chi ~200-300 ms nen de "
        "ngan cho kip thay chu ky phuc hoi. He that thi phai de 10-30 s. |"
    )
    add(
        "| `success_threshold` | 1 | 1 probe thanh cong la dong lai. Danh doi: phuc hoi "
        "nhanh nhung de dong nham neu provider dang chap chon. |"
    )
    add(
        "| `cache.ttl_seconds` | 300 | 5 phut. Cau hoi FAQ/chinh sach it doi trong 5 phut, "
        "ma van du ngan de khong phuc vu noi dung qua cu. |"
    )
    add(
        "| `cache.similarity_threshold` | 0.92 | Nguong cao. Thu 0.85 thi cau hoi hoc phi "
        "2024 va 2025 cham diem ~0.96 nen van dinh nhau, phai co them false-hit guard. De "
        "0.92 thi cau viet lai nhe van hit (~0.95) con cau khac han thi truot. |"
    )
    add(
        "| `load_test.requests` | 100 / kich ban | 4 kich ban = 400 request, du de P95 va "
        "P99 co y nghia. |"
    )
    add(
        "| `load_test.concurrency` | 10 | Chay 10 thread song song cho giong tai that, "
        "khong phai goi tuan tu tung cai. |"
    )
    add("")
    add("Bang provider (trong `configs/default.yaml`):")
    add("")
    add("| Provider | fail_rate | base_latency_ms | cost / 1k token |")
    add("|---|---:|---:|---:|")
    add("| primary | 0.25 | 180 | 0.01 |")
    add("| backup | 0.05 | 260 | 0.006 |")
    add("")
    add("Primary nhanh hon nhung hong nhieu hon va dat hon; backup cham hon nhung on")
    add("dinh va re hon. Vay nen thu tu goi la primary truoc, backup do.")
    add("")

    # ------------------------------------------------------------------ 3
    add("## 3. SLO")
    add("")
    add("So o cot 'Do duoc' la so gop cua ca 4 kich ban chaos, tuc la da tinh ca kich")
    add("ban primary chet 100%. Do la truong hop xau nhat chu khong phai ngay thuong.")
    add("")
    add("| SLI | Muc tieu | Do duoc | Dat? |")
    add("|---|---|---:|---|")
    met_avail = "DAT" if availability is not None and availability >= 0.99 else "CHUA DAT"
    add(f"| Availability | >= 99% | {_pct(availability)} | {met_avail} |")
    met_p95 = "DAT" if p95 is not None and p95 < 2500 else "CHUA DAT"
    add(f"| Latency P95 | < 2500 ms | {_fmt(p95)} ms | {met_p95} |")
    met_fb = "DAT" if fallback_rate is not None and fallback_rate >= 0.95 else "CHUA DAT"
    add(f"| Fallback success rate | >= 95% | {_pct(fallback_rate)} | {met_fb} |")
    met_hit = "DAT" if hit_rate is not None and hit_rate >= 0.10 else "CHUA DAT"
    add(f"| Cache hit rate | >= 10% | {_pct(hit_rate)} | {met_hit} |")
    if recovery is None:
        add("| Recovery time | < 5000 ms | khong do duoc o lan chay nay | xem muc 7 |")
    else:
        met_rec = "DAT" if recovery < 5000 else "CHUA DAT"
        add(f"| Recovery time | < 5000 ms | {_fmt(recovery)} ms | {met_rec} |")
    add("")
    if availability is not None and availability < 0.99:
        add(f"Availability {_pct(availability)} chua dat 99%. Em khong chinh so cho dep:")
        add("phan thieu la o kich ban `primary_timeout_100`, luc do primary chet han va")
        add("backup con fail_rate 5%, nen mot so request roi xuong `static_fallback`.")
        add("Muon dat 99% that thi phai them provider thu 3 chu khong phai sua code.")
        add("")

    # ------------------------------------------------------------------ 4
    add("## 4. So lieu (reports/metrics.json)")
    add("")
    add("| Metric | Gia tri |")
    add("|---|---:|")
    for key in [
        "total_requests",
        "availability",
        "error_rate",
        "latency_p50_ms",
        "latency_p95_ms",
        "latency_p99_ms",
        "fallback_success_rate",
        "cache_hit_rate",
        "circuit_open_count",
        "recovery_time_ms",
        "false_hits_blocked",
        "privacy_bypassed",
        "estimated_cost",
        "estimated_cost_saved",
    ]:
        add(f"| {key} | {metrics.get(key)} |")
    add("")
    add("Cach do latency: em do bang `time.perf_counter()` bao quanh ca loi goi")
    add("`gateway.complete()`, tuc la latency ma nguoi dung thuc su cam nhan. Neu lay")
    add("`latency_ms` cua provider thi cache hit se bi bo qua (no bang 0) va luc do")
    add("bang so sanh cache o muc 5 se khong the hien duoc gi.")
    add("")
    add("Cach tinh `estimated_cost_saved`: lay chi phi trung binh that cua nhung")
    add("request da phai goi provider trong chinh lan chay do, roi nhan voi so cache")
    add("hit. Luc dau em de mot hang so 0.001/hit thi ra so tien tiet kiem con lon hon")
    add("ca tong tien da tieu - vo ly, nen phai sua.")
    add("")

    # ------------------------------------------------------------------ 5
    add("## 5. So sanh co cache va khong cache")
    add("")
    add("Hai lan chay giong het nhau, chi khac `cache.enabled`:")
    add("")
    add("```bash")
    add("python scripts/run_chaos.py --config configs/no_cache.yaml --out reports/metrics_nocache.json")
    add("python scripts/run_chaos.py --config configs/default.yaml  --out reports/metrics.json")
    add("```")
    add("")
    if nocache:
        add("| Metric | Khong cache | Co cache | Chenh lech |")
        add("|---|---:|---:|---|")
        add(
            f"| latency_p50_ms | {_fmt(nocache.get('latency_p50_ms'))} | {_fmt(p50)} | "
            f"{_delta(nocache.get('latency_p50_ms'), p50, ' ms')} |"
        )
        add(
            f"| latency_p95_ms | {_fmt(nocache.get('latency_p95_ms'))} | {_fmt(p95)} | "
            f"{_delta(nocache.get('latency_p95_ms'), p95, ' ms')} |"
        )
        add(
            f"| latency_p99_ms | {_fmt(nocache.get('latency_p99_ms'))} | {_fmt(p99)} | "
            f"{_delta(nocache.get('latency_p99_ms'), p99, ' ms')} |"
        )
        add(
            f"| estimated_cost | {_fmt(nocache.get('estimated_cost'), 6)} | {_fmt(cost, 6)} | "
            f"{_delta(nocache.get('estimated_cost'), cost)} |"
        )
        add(
            f"| cache_hit_rate | {_pct(nocache.get('cache_hit_rate'))} | {_pct(hit_rate)} | "
            f"tang {_pct(hit_rate)} |"
        )
        add(f"| availability | {_pct(nocache.get('availability'))} | {_pct(availability)} | - |")
        add("")
        add("Nhan xet:")
        add("")
        if p50 is not None and nocache.get("latency_p50_ms"):
            ratio = float(nocache["latency_p50_ms"]) / max(float(p50), 0.001)
            add(f"- P50 giam rat manh (khoang {ratio:.0f} lan) vi hon mot nua so request")
            add("  duoc tra tu bo nho, khong phai cho provider ngu 180-260 ms.")
        add("- P95 gan nhu khong doi. Dieu nay dung: P95 la nhom request bi miss cache")
        add("  roi con phai fallback sang backup, cache khong cuu duoc nhom nay.")
        if cost is not None and nocache.get("estimated_cost"):
            saved_pct = (1 - float(cost) / float(nocache["estimated_cost"])) * 100
            add(f"- Tien giam khoang {saved_pct:.0f}%, dung xap xi voi ty le cache hit")
            add(f"  {_pct(hit_rate)}.")
        add("")
    else:
        add("(thieu reports/metrics_nocache.json)")
        add("")

    # ------------------------------------------------------------------ 6
    add("## 6. Redis shared cache")
    add("")
    add("**Vi sao cache trong bo nho khong du:** cache `ResponseCache` nam trong RAM cua")
    add("dung mot tien trinh. Chay 3 replica sau load balancer thi thanh 3 cai cache")
    add("roi rac, cung mot cau hoi vao 3 may la 3 lan tra tien. TTL va guardrail cung")
    add("moi may mot kieu, restart la mat sach.")
    add("")
    add("**SharedRedisCache giai quyet the nao:** day entry ra Redis, moi entry la mot")
    add("Redis Hash `{prefix}{md5(query)[:12]}` gom 2 field `query` va `response`, het")
    add("han bang `EXPIRE`. Moi replica doc/ghi chung mot cho nen hit cua may nay dung")
    add("duoc cho may kia. Tim gan dung thi `SCAN` theo prefix roi cham diem")
    add("`ResponseCache.similarity()` o phia client.")
    add("")
    add("Gateway con bat exception quanh cache, nen Redis chet thi request van chay")
    add("tiep xuong provider - mat hit rate chu khong mat availability.")
    add("")
    add("### Bang chung state dung chung")
    add("")
    add("Hai object `SharedRedisCache` doc lap, dong vai 2 replica (`scripts/redis_evidence.py`):")
    add("")
    add("```")
    add(_load_text(REPORTS / "redis_evidence.txt"))
    add("```")
    add("")
    add("### Redis CLI")
    add("")
    add("```")
    add(_load_text(REPORTS / "redis_cli_output.txt"))
    add("```")
    add("")
    add("Cho dang chu y: `DBSIZE` luon nho hon 20. Bo `data/sample_queries.jsonl` co 20")
    add("cau, nhung 5 cau nhay cam (so du tai khoan, reset mat khau, the tin dung, SSN,")
    add("...) bi privacy guard chan nen khong bao gio vao Redis. Toi da chi con 15 key,")
    add("va thuc te con it hon vi khong phai cau nao cung duoc random goi toi.")
    add("")
    if redis_metrics:
        add("### Cache trong bo nho vs cache Redis")
        add("")
        add("| Metric | In-memory | Redis | Ghi chu |")
        add("|---|---:|---:|---|")
        add(
            f"| latency_p50_ms | {_fmt(p50)} | {_fmt(redis_metrics.get('latency_p50_ms'))} | "
            "Redis cham hon vi phai di qua TCP va SCAN |"
        )
        add(
            f"| latency_p95_ms | {_fmt(p95)} | {_fmt(redis_metrics.get('latency_p95_ms'))} | "
            "gan bang nhau, P95 bi provider chi phoi |"
        )
        add(
            f"| cache_hit_rate | {_pct(hit_rate)} | {_pct(redis_metrics.get('cache_hit_rate'))} | "
            "tuong duong |"
        )
        add(
            f"| availability | {_pct(availability)} | "
            f"{_pct(redis_metrics.get('availability'))} | tuong duong |"
        )
        add("")
        add("Doi lai vai ms moi request thi duoc cache dung chung cho nhieu instance.")
        add("Voi he thong nhieu replica thi danh doi nay xung dang.")
        add("")

    # ------------------------------------------------------------------ 7
    add("## 7. Kich ban chaos")
    add("")
    add("4 kich ban, moi kich ban 100 request, dung tieu chi pass/fail rieng chu khong")
    add("dung chung mot cau 'co request nao thanh cong khong'.")
    add("")
    add("| Kich ban | Mong doi | Quan sat duoc | Ket qua |")
    add("|---|---|---|---|")

    expectations = {
        "primary_timeout_100": "primary chet 100%, traffic phai chay sang backup, circuit phai mo",
        "primary_flaky_50": "primary hong 50%, circuit dong/mo qua lai, nguoi dung van duoc phuc vu",
        "all_healthy": "khong hong gi, khong circuit nao duoc mo",
        "cache_stress_repeat": "cau hoi lap lai, phai co cache hit that su",
    }
    for name, expected in expectations.items():
        s = scenarios.get(name, {})
        status = scenario_status.get(name, s.get("status", "?"))
        if s:
            observed = (
                f"availability {_pct(s.get('availability'))}, "
                f"circuit mo {s.get('circuit_open_count')} lan, "
                f"cache hit {_pct(s.get('cache_hit_rate'))}, "
                f"P95 {_fmt(s.get('latency_p95_ms'))} ms"
            )
        else:
            observed = "(thieu file scenario metrics)"
        add(f"| `{name}` | {expected} | {observed} | **{status}** |")
    add("")

    add("### Bang chung phuc hoi circuit")
    add("")
    if recovery is None:
        add("Trong `reports/metrics.json` lan nay `recovery_time_ms` la `null`. Em de")
        add("nguyen chu khong sua cho dep, vi no noi len mot van de that:")
        add("")
        add("Khi cache hit rate cao (~50%), sau khi circuit cua primary mo ra thi phan")
        add("lon request tiep theo duoc tra tu cache, khong con ai goi provider nua.")
        add("Khong co request nao goi thi khong co probe HALF_OPEN, ma khong co probe")
        add("thi circuit khong bao gio dong lai. Tuc la primary co the da khoe tro lai")
        add("tu lau ma he thong van bam vao backup.")
        add("")
        add("De chung minh may trang thai van chay dung, em viet")
        add("`scripts/recovery_evidence.py` ep chay tron mot chu ky:")
    else:
        add(f"Do duoc trung binh {_fmt(recovery)} ms trong `reports/metrics.json`.")
        add("Chay them `scripts/recovery_evidence.py` de thay tron mot chu ky:")
    add("")
    add("```")
    add(_load_text(REPORTS / "recovery_evidence.txt"))
    add("```")
    add("")
    add("Doc transition log thay du 3 buoc: `closed -> open` (ly do")
    add("`failure_threshold_reached`), `open -> half_open` (`reset_timeout_elapsed`),")
    add("roi `half_open -> closed` (`probe_success`).")
    add("")

    add("### Guardrail cua cache")
    add("")
    add(f"- `false_hits_blocked` = {metrics.get('false_hits_blocked')}: so lan cache tim")
    add("  duoc entry qua nguong nhung bi tu choi vi so 4 chu so khac nhau.")
    add(f"- `privacy_bypassed` = {metrics.get('privacy_bypassed')}/{metrics.get('total_requests')}:")
    add("  so request nhay cam khong duoc luu va khong duoc phuc vu tu cache.")
    add("")
    examples: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for scenario_result in scenarios.values():
        for ex in scenario_result.get("false_hit_examples", []):
            pair = (str(ex.get("query", "")), str(ex.get("cached_key", "")))
            if pair in seen:
                continue
            seen.add(pair)
            examples.append(ex)
    if examples:
        add("Vi du that bi chan (lay tu `reports/metrics_scenarios.json`):")
        add("")
        for ex in examples[:3]:
            add(f"- Hoi: `{ex.get('query')}`")
            add(f"  - Cache co: `{ex.get('cached_key')}`")
            add("  - Diem similarity rat cao nhung nam khac nhau -> tu choi, ghi log")
            add("    `date_or_number_mismatch`.")
        add("")
        add("Neu khong co guardrail nay thi he thong se tra chinh sach 2024 cho nguoi hoi")
        add("ve 2026. Sai kieu do nguy hiem hon la miss cache, vi nguoi dung khong biet")
        add("la minh dang doc nham.")
        add("")

    # ------------------------------------------------------------------ 8
    add("## 8. Phan tich diem yeu con lai")
    add("")
    add("**Diem yeu lon nhat: circuit chi doi trang thai khi co request di qua no.**")
    add("")
    add("`allow_request()` la ham duy nhat chuyen OPEN -> HALF_OPEN, ma ham nay chi chay")
    add("khi co request that su goi xuong provider. Cache hit rate cua he thong dang la")
    add(f"{_pct(hit_rate)}, tuc la hon mot nua so request khong he cham toi provider.")
    add("Neu traffic thua va cache dang an gan het request thi sau khi circuit mo ra co")
    add("the rat lau moi co mot request tao probe, va he thong bam vao backup lau hon")
    add("can thiet - backup cham hon (260 ms so voi 180 ms) va neu backup cung hong thi")
    add("khong con gi do.")
    add("")
    if recovery is None:
        add(f"Dung tinh huong nay xay ra o chinh lan chay nay: `circuit_open_count` = "
            f"{metrics.get('circuit_open_count')} nhung `recovery_time_ms` = `null`, tuc la")
        add("circuit mo ra roi khong dong lai duoc lan nao truoc khi kich ban ket thuc.")
    else:
        add(f"Lan chay nay `recovery_time_ms` = {_fmt(recovery)} ms nen chua lo ra van de,")
        add("nhung chay lai vai lan thi co lan ra `null` du `circuit_open_count` > 0 -")
        add("dung la circuit mo ra roi khong dong lai duoc trong pham vi kich ban.")
    add("So nay dao dong giua cac lan chay, nen bang chung chac chan cho may trang thai")
    add("la `reports/recovery_evidence.txt` chu khong phai o day.")
    add("")
    add("**Cach sua:** them mot health-check chay nen, dinh ky (vi du 10 s/lan) tu goi")
    add("`breaker.allow_request()` va ban mot request nho toi provider dang OPEN. Nhu")
    add("vay probe khong con phu thuoc vao traffic that nua.")
    add("")
    add("**Diem yeu thu hai: trang thai circuit chi nam trong RAM.** Cache da dung chung")
    add("qua Redis nhung circuit thi chua. 3 replica thi moi cai tu hoc lai rang provider")
    add("dang hong, tuc la provider om phai an gap 3 lan so request loi. Sua bang cach")
    add("day counter cua breaker vao Redis (`INCR` + `EXPIRE`).")
    add("")
    add("**Diem yeu thu ba: false-hit guard chi bat so 4 chu so.** No bat duoc 2024 vs")
    add("2026 nhung khong bat duoc 'chinh sach nam nay' vs 'chinh sach nam ngoai', hay")
    add("'ky 1' vs 'ky 2'. Day la heuristic chu khong phai giai phap dung nghia.")
    add("")

    # ------------------------------------------------------------------ 9
    add("## 9. Viec se lam tiep")
    add("")
    add("1. Health-check chay nen de probe provider dang OPEN, khong phu thuoc traffic.")
    add("2. Day trang thai circuit vao Redis de nhieu instance dung chung.")
    add("3. Thay false-hit guard bang cach gan nhan thoi gian cho cau hoi (query co")
    add("   nhac toi thoi diem cu the thi TTL ngan hon hoac khong cache).")
    add("")
    add("---")
    add("")
    add("## Phu luc - cach chay lai")
    add("")
    add("```bash")
    add("pip install -e \".[dev]\"")
    add("docker compose up -d          # Redis 7 cho shared cache")
    add("make test                     # 35 passed, 7 xpassed")
    add("make run-chaos                # sinh reports/metrics.json")
    add("make all-evidence             # chay het cac lan do + sinh bao cao nay")
    add("```")
    add("")
    add("Log test day du: `reports/test_output.txt`.")

    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metrics", default="reports/metrics.json")
    parser.add_argument("--out", default="reports/final_report.md")
    args = parser.parse_args()

    metrics = _load_json(Path(args.metrics))
    if metrics is None:
        raise SystemExit(f"khong tim thay {args.metrics} - chay `make run-chaos` truoc")

    report = build_report(
        metrics=metrics,
        nocache=_load_json(REPORTS / "metrics_nocache.json"),
        redis_metrics=_load_json(REPORTS / "metrics_redis.json"),
        scenarios=_load_json(REPORTS / "metrics_scenarios.json"),
    )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
