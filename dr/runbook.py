"""BƯỚC 3c — SINH VIÊN VIẾT. Tự động hoá runbook §4 "Runbook: Region Chính Down".

7 bước trên slide, mỗi bước 1 dòng log có ts. Log này CHÍNH LÀ timeline của postmortem.
  1 xac_nhan_outage          — probe cả 2 region, đừng tin 1 lần fail (dùng nhiều lần
                              hoặc gọi health_checker.probe nếu đã viết xong 3a)
  2 thong_bao_incident       — ts của dòng này là mốc "operator biết tin", LUÔN LUÔN
                              SAU t_outage trong chaos-events (không thể trùng — operator
                              không thể biết ngay giây outage xảy ra). Ghi cả 2 ts vào
                              log để postmortem tính được "độ trễ thông báo".
  3 scale_gpu_pool           — gọi HÀM `failover.failover(...)` MỘT LẦN DUY NHẤT. Hàm
                              đó tự làm đủ 5 bước con (verify/restore/scale/wait/cutover)
                              và tự ghi log riêng vào reports/failover-events.jsonl.
  4 verify_state_replica     — KHÔNG gọi lại failover — chỉ ĐỌC kết quả (vector count +
                              weights ở region phụ) từ dict mà bước 3 trả về, để log vào
                              runbook-run.jsonl cho postmortem đọc 1 chỗ duy nhất.
  5 dns_cutover              — cũng chỉ đọc lại: kết quả cutover có ok hay không.
  6 verify_golden_signals    — 10 request thật vào region phụ: p95 latency + error rate
  7 post_incident            — elapsed_s + lệnh đo RTO

BÁN TỰ ĐỘNG, KHÔNG FULL-AUTO (§4: "failover đầu tiên nên là bán tự động — alert +
1-click confirm — tránh flapping gây failover 2 chiều liên tục"). Mặc định phải hỏi
người vận hành confirm; --auto chỉ dùng trong CI/khi chấm điểm.

Chạy:  python dr/runbook.py --primary a --target b --backend fs
"""
import argparse
import json
import pathlib
import sys
import time

import httpx

sys.path.insert(0, ".")
from dr import failover as fo  # noqa: E402
from dr import health_checker  # noqa: E402

LOG = pathlib.Path("reports/runbook-run.jsonl")
URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}
GOLDEN_P95_MS = 100.0
GOLDEN_MAX_ERROR_RATE = 0.0


def step(n, name, **kw):
    """Ghi một bước runbook có timestamp vào JSONL và stdout."""
    event = {
        "ts": time.time(),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        "step": n,
        "name": name,
        **kw,
    }
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as log:
        log.write(json.dumps(event) + "\n")
    print(json.dumps(event))
    return event


def confirm(auto: bool, msg: str) -> bool:
    """Bỏ qua prompt trong CI; mặc định yêu cầu operator xác nhận y/N."""
    if auto:
        return True
    return input(f"{msg} [y/N]: ").strip().lower() in {"y", "yes"}


def outage_ts(region: str) -> float | None:
    """Lấy outage gần nhất để incident log phân biệt thời điểm lỗi và thời điểm biết lỗi."""
    events = pathlib.Path("chaos/chaos-events.jsonl")
    if not events.exists():
        return None
    records = [json.loads(line) for line in events.read_text().splitlines() if line.strip()]
    kills = [record for record in records
             if record.get("action") == "kill" and record.get("region") == region]
    return kills[-1]["ts"] if kills else None


def golden_signals(region: str, requests: int = 10) -> dict:
    """Gửi request thật trực tiếp tới target; chỉ dùng sau khi failover báo thành công."""
    latencies = []
    successes = 0
    for _ in range(requests):
        started = time.time()
        try:
            response = httpx.get(f"{URL[region]}/v1/infer", timeout=3.0)
            successes += response.status_code == 200
        except httpx.HTTPError:
            pass
        latencies.append((time.time() - started) * 1000)
    latencies.sort()
    p95_index = max(0, int(len(latencies) * 0.95 + 0.999999) - 1)
    return {
        "requests": requests,
        "successes": successes,
        "error_rate": round((requests - successes) / requests, 3),
        "p95_latency_ms": round(latencies[p95_index], 1),
    }


def run(primary: str, target: str, backend: str, auto: bool) -> dict:
    """Thực hiện bảy bước runbook và trả về kết quả kiểm tra sau cutover."""
    if primary not in URL or target not in URL or primary == target:
        raise ValueError("primary va target phai la hai region a/b khac nhau")
    started = time.time()
    probes = []
    for attempt in range(3):
        probes.append(health_checker.probe(primary, timeout=2.0))
        if attempt < 2:
            time.sleep(5.0)
    target_ready, target_reason = health_checker.probe(target, timeout=2.0)
    primary_down = all(not ready for ready, _ in probes)
    step(1, "xac_nhan_outage", ok=primary_down, primary=primary,
         primary_probes=probes, target=target, target_ready=target_ready,
         target_reason=target_reason)
    if not primary_down:
        return {"ok": False, "reason": "primary_van_ready"}

    known_outage = outage_ts(primary)
    if not confirm(auto, f"Xac nhan failover tu region-{primary} sang region-{target}?"):
        step(2, "thong_bao_incident", ok=False, confirmed=False, outage_ts=known_outage)
        return {"ok": False, "reason": "operator_khong_xac_nhan"}
    incident = step(2, "thong_bao_incident", ok=True, confirmed=True,
                    outage_ts=known_outage, operator_ts=time.time())

    result = fo.failover(target, backend, wait=60.0)
    step(3, "scale_gpu_pool", ok=result.get("ok", False), target=target,
         failover_result=result)
    if not result.get("ok"):
        return {"ok": False, "reason": "failover_that_bai", "failover": result}

    restored = result.get("restored_state", {})
    step(4, "verify_state_replica", ok=bool(restored.get("weights")) and
         restored.get("count", 0) > 0, target=target,
         vectors=restored.get("count"), weights=restored.get("weights"),
         latest_doc_ts=restored.get("latest_doc_ts"),
         embed_model_version=result.get("restore", {}).get("embed_model_version"))
    step(5, "dns_cutover", ok=True, target=target, cutover_result=result.get("ok"))

    signals = golden_signals(target)
    signals_ok = (signals["error_rate"] <= GOLDEN_MAX_ERROR_RATE and
                  signals["p95_latency_ms"] < GOLDEN_P95_MS)
    step(6, "verify_golden_signals", ok=signals_ok, target=target,
         p95_target_ms=GOLDEN_P95_MS,
         max_error_rate=GOLDEN_MAX_ERROR_RATE, **signals)
    elapsed = round(time.time() - started, 2)
    step(7, "post_incident", ok=signals_ok, elapsed_s=elapsed,
         measure_command="python3 tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl --target-rto 300")
    return {"ok": signals_ok, "failover": result,
            "golden_signals": signals, "incident": incident, "elapsed_s": elapsed}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--primary", default="a")
    p.add_argument("--target", default="b")
    p.add_argument("--backend", default="fs", choices=["fs", "minio"])
    p.add_argument("--auto", action="store_true")
    a = p.parse_args()
    print(json.dumps(run(a.primary, a.target, a.backend, a.auto), indent=2))
