"""BƯỚC 3b — SINH VIÊN VIẾT. Cutover sang region phụ.

5 bước, THỨ TỰ QUAN TRỌNG (§2 Kiến Trúc Tham Chiếu: DNS/LB, compute, state là 3 lớp riêng):
  1_verify_target    — /v1/state của region phụ: weights? vector count? pool_state?
  2_restore_snapshot — gọi state/snapshot.py get + state/snapshot.py rpo()
                       Log BẮT BUỘC: rpo_seconds, docs_lost, embed_model_version.
                       (§3: "backup index nhưng quên backup embedding model version
                        -> index không tương thích khi restore")
  3_scale_pool       — ghi "full" vào state/region-<t>/pool_state (warm -> full)
  4_wait_ready       — POLL /readyz tới khi 200. Region phụ có WARMUP_SECONDS —
                       đây là GPU pool warm-up của §4, nó nằm trong RTO của bạn.
  5_dns_cutover      — ghi region đích vào edge/active_region

BẪY: nếu bạn đổi edge/active_region TRƯỚC bước 4, user sẽ nhận 503 từ CẢ HAI region
và RTO của bạn dài hơn, không ngắn hơn. Nếu bước 4 timeout -> ABORT, KHÔNG cutover.

Mỗi bước ghi 1 dòng vào reports/failover-events.jsonl với ts + step.
Không có dòng 5_dns_cutover = tools/measure_rto.py không tìm được t_cutover = mất điểm.

Chạy:  python dr/failover.py --target b --backend fs
"""
import argparse
import json
import pathlib
import sys
import time

import httpx

sys.path.insert(0, ".")
from state import snapshot  # noqa: E402

URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}
LOG = pathlib.Path("reports/failover-events.jsonl")


def emit(**kw):
    """Append một event có timestamp vào failover log và stdout."""
    event = {
        "ts": time.time(),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        **kw,
    }
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as log:
        log.write(json.dumps(event) + "\n")
    print(json.dumps(event))
    return event


def state_of(region: str) -> dict:
    """Đọc trạng thái hiện tại để đưa vào event verify, không dùng làm readiness."""
    try:
        response = httpx.get(f"{URL[region]}/v1/state", timeout=2.0)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as exc:
        return {"region": region, "error": type(exc).__name__}


def failover(target: str, backend: str, wait: float) -> dict:
    """Khôi phục target theo đúng năm bước và chỉ cutover sau readiness."""
    if wait <= 0:
        raise ValueError("wait phai lon hon 0")

    primary = "b" if target == "a" else "a"
    target_state = state_of(target)
    emit(step="1_verify_target", target=target, state=target_state)

    try:
        restored = snapshot.get(target, backend)
        recovery_point = snapshot.rpo(
            pathlib.Path(f"state/region-{primary}/vectors.sqlite"),
            pathlib.Path(f"state/region-{target}/vectors.sqlite"),
        )
    except SystemExit as exc:
        emit(step="2_restore_snapshot", target=target, ok=False, reason=str(exc))
        return {"ok": False, "target": target, "reason": str(exc)}
    except Exception as exc:
        emit(step="2_restore_snapshot", target=target, ok=False, reason=type(exc).__name__)
        return {"ok": False, "target": target, "reason": type(exc).__name__}

    emit(
        step="2_restore_snapshot",
        target=target,
        ok=True,
        rpo_seconds=recovery_point["rpo_seconds"],
        docs_lost=recovery_point["docs_lost"],
        embed_model_version=restored.get("embed_model_version"),
        snapshot_at=restored.get("snapshot_at"),
    )

    pool_state = pathlib.Path(f"state/region-{target}/pool_state")
    pool_state.parent.mkdir(parents=True, exist_ok=True)
    pool_state.write_text("full\n")
    emit(step="3_scale_pool", target=target, pool_state="full")

    started = time.time()
    deadline = started + wait
    last_reason = "not_ready"
    while time.time() < deadline:
        try:
            response = httpx.get(f"{URL[target]}/readyz", timeout=min(2.0, wait))
            if response.status_code == 200:
                waited = round(time.time() - started, 2)
                emit(step="4_wait_ready", target=target, ok=True, waited_s=waited)
                break
            try:
                last_reason = "; ".join(response.json().get("reasons", []))
            except (ValueError, AttributeError):
                last_reason = f"http_{response.status_code}"
        except httpx.HTTPError as exc:
            last_reason = type(exc).__name__
        time.sleep(0.5)
    else:
        waited = round(time.time() - started, 2)
        emit(step="4_wait_ready", target=target, ok=False, waited_s=waited, reason=last_reason)
        return {"ok": False, "target": target, "reason": "target_not_ready", "waited_s": waited}

    active = pathlib.Path("edge/active_region")
    active.parent.mkdir(parents=True, exist_ok=True)
    active.write_text(f"{target}\n")
    emit(step="5_dns_cutover", target=target, ok=True)
    restored_state = state_of(target)
    return {
        "ok": True,
        "target": target,
        "state": target_state,
        "restored_state": restored_state,
        "restore": restored,
        "rpo": recovery_point,
        "waited_s": waited,
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--target", default="b", choices=["a", "b"])
    p.add_argument("--backend", default="fs", choices=["fs", "minio"])
    p.add_argument("--wait", type=float, default=60)
    a = p.parse_args()
    print(json.dumps(failover(a.target, a.backend, a.wait), indent=2))
