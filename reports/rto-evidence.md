# RTO/RPO Evidence — Lab 23

Mọi giá trị dưới đây được đo từ JSONL của chính drill này, không lấy từ reference run.

## 1. Drill 1 — không có DR (baseline)

| Chỉ số | Giá trị | Cách đo | Evidence |
|---|---:|---|---|
| t_outage | 2026-08-25T07:08:22Z | chaos kill Region A | `chaos/chaos-events.jsonl:1` |
| Request fail đầu tiên | +0.0s | request `ok:false` đầu tiên sau outage | `reports/drill-1-nodr.jsonl:17` |
| Request thành công sau đó | Không có | kết quả đo không tìm thấy recovery | `reports/measure-drill-1.json:9` |
| RTO | NO_RECOVERY | `tools/measure_rto.py` | `reports/measure-drill-1.json:25` |

## 2. Drill 2 — có DR

| Mốc | +giây từ t_outage | Cách đo | Evidence |
|---|---:|---|---|
| t_outage (mốc 0) | 0.0s | `action:kill` Region A | `chaos/chaos-events.jsonl:3` |
| User thấy lỗi đầu tiên | +0.1s | request `ok:false` đầu tiên | `reports/drill-2-withdr.jsonl:25` |
| Health check phát hiện | +15.0s | `to:UNHEALTHY`, region A | `reports/health-events.jsonl:2` |
| Snapshot restore xong | +16.1s | `2_restore_snapshot` | `reports/failover-events.jsonl:10` |
| Region phụ ready | +22.4s | `4_wait_ready` | `reports/failover-events.jsonl:12` |
| DNS cutover | +22.4s | `5_dns_cutover` | `reports/failover-events.jsonl:13` |
| **RTO đo được** | **+28.3s** | request OK đầu tiên từ B | `reports/drill-2-withdr.jsonl:39` |

| Chỉ số | Đo được | Mục tiêu | Verdict |
|---|---:|---:|---|
| RTO — Inference API | 28.3s | 300s (5 phút) | PASS |
| RPO — Vector DB | 4.0s / 2 docs | 300s (5 phút) | PASS |

`reports/measure-drill-2.json:20` là kết quả RTO, còn `reports/measure-drill-2.json:23` ghi RPO và `reports/measure-drill-2.json:24` ghi số document mất. File cũng xác nhận drill hợp lệ, không warnings, và recovery do Region B phục vụ.

## 3. RTO của tôi gồm những gì

| Thành phần | Giây | Nó đến từ đâu | Giảm được bằng cách nào | Evidence |
|---|---:|---|---|---|
| Health-check detection floor | 15.0s | `interval_s:5.0` × `threshold:3` trong event phát hiện A | Hạ interval có kiểm soát, kèm chống flapping | `reports/health-events.jsonl:2` |
| Snapshot restore và handoff sau detect | 1.1s | từ detect +15.0s đến restore +16.1s | Snapshot nhỏ hơn, orchestration ít độ trễ hơn | `reports/health-events.jsonl:2`, `reports/failover-events.jsonl:10` |
| GPU pool warm-up | 6.2s | `waited_s` tại readiness của B | Giữ warm standby hoặc pre-warm GPU pool | `reports/failover-events.jsonl:12` |
| DNS/LB TTL cache | 6.0s | 28.3s recovery trừ 22.4s cutover, làm tròn 0.1s | TTL ngắn hơn hoặc client failover tốt hơn | `reports/failover-events.jsonl:13`, `reports/drill-2-withdr.jsonl:39` |
| **Tổng** | **28.3s** | 15.0 + 1.1 + 6.2 + 6.0 | Đúng RTO đo được | `reports/measure-drill-2.json:20` |
