# Runbook 1 trang — Region chính down

Phạm vi: Region A là primary, Region B là target; dùng bare mode và filesystem snapshot.
On-call không tự sửa `edge/active_region`; mọi cutover đi qua `dr/runbook.py`/`dr/failover.py`.
Kích hoạt môi trường trước: `source .venv/bin/activate`. Chỉ chạy lệnh runbook ở bước 2
một lần; các bước 3–7 là lệnh kiểm chứng độc lập sau khi automation hoàn tất.

| # | Bước | Lệnh copy-paste | Biết là xong khi | Ai làm |
|---|---|---|---|---|
| 1 | Xác nhận outage | `python3 chaos/kill_region.py status` (lặp 3 lần, cách nhau 5 giây) | A không `/readyz` trong cả 3 lần; B vẫn alive | on-call SRE |
| 2 | Mở incident, bấm giờ và chạy controlled failover | `python3 dr/runbook.py --primary a --target b --backend fs` | Xác nhận `y`; dòng 2 trong `reports/runbook-run.jsonl` có `confirmed:true`, `outage_ts` và `operator_ts` | incident commander |
| 3 | Xác nhận restore state và scale pool | `tail -n 5 reports/failover-events.jsonl` | Năm event cuối đi đúng thứ tự `1_verify_target` → `2_restore_snapshot` → `3_scale_pool` → `4_wait_ready` → `5_dns_cutover`; bước 4 có `ok:true` | on-call SRE |
| 4 | Verify replica | `curl -s http://127.0.0.1:8002/v1/state` | `weights:true`, `count>0`, và dòng 4 `verify_state_replica` trong runbook log có `ok:true` | data/platform owner |
| 5 | DNS/LB cutover | `curl -s http://127.0.0.1:8080/edge/state` | `active_region:"b"` và event `5_dns_cutover` đã có trong `reports/failover-events.jsonl` | on-call SRE |
| 6 | Verify golden signals | `for i in {1..10}; do curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8002/v1/infer; done` | Cả 10 request là 200; runbook log ghi error rate = 0 và p95 < 100ms | application owner |
| 7 | Đo RTO/RPO và postmortem | `python3 tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl --target-rto 300` | `valid:true`, `rto_verdict:"PASS"`; điền evidence/postmortem bằng line log thật | incident commander |

**Rollback (failover ngược):** chỉ cân nhắc trả traffic về A khi B `/readyz` lỗi liên tục, golden-signal error rate > 5%, hoặc phát hiện sai lệch state có ảnh hưởng khách hàng. Incident commander có quyền duy nhất phê duyệt. On-call khôi phục process A bằng `python3 chaos/kill_region.py restore --region a --backend bare`, xác nhận A alive, snapshot state mới nhất từ B bằng `python3 state/snapshot.py put --region b --backend fs`, rồi chạy `python3 dr/failover.py --target a --backend fs`. Chỉ tuyên bố rollback hoàn tất khi A `/readyz` trả 200, Edge báo `active_region:"a"`, và golden signals đạt error rate = 0, p95 < 100ms. Không rollback tự động để tránh flapping hai chiều.
