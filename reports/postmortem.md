# Postmortem — DR Drill Lab 23

## 1. Timeline

| ISO time | Sự kiện | Evidence |
|---|---|---|
| 2026-08-25T07:24:57Z | Region A bị netblock; RTO clock bắt đầu | `chaos/chaos-events.jsonl:3` |
| 2026-08-25T07:24:57Z | User đầu tiên bị ảnh hưởng, Edge timeout tới A | `reports/drill-2-withdr.jsonl:25` |
| 2026-08-25T07:25:12Z | Health checker chuyển Region A sang UNHEALTHY | `reports/health-events.jsonl:2` |
| 2026-08-25T07:25:20Z | Operator/runbook xác nhận DNS cutover sang B | `reports/failover-events.jsonl:13` |
| 2026-08-25T07:25:26Z | Request đầu tiên thành công từ Region B; incident resolved | `reports/drill-2-withdr.jsonl:39` |

## 2. RTO/RPO đo được vs mục tiêu — gap ở bước nào?

- RTO mục tiêu: 300s; đo được: 28.3s; gap còn 271.7s dưới mục tiêu. Evidence: `reports/measure-drill-2.json:20`.
- RPO mục tiêu: 300s; đo được: 4.0s (2 documents bị mất); gap còn 296.0s dưới mục tiêu. Evidence: `reports/failover-events.jsonl:10`.
- Bước tốn nhiều giây nhất: health-check detection floor, 15.0s (khoảng 53.0% RTO), vì cần 3 probe lỗi liên tiếp với interval 5.0s. Evidence: `reports/health-events.jsonl:2`.

## 3. Root cause (5 whys)

1. User nhận lỗi vì Edge vẫn route tới Region A đã bị netblock.
2. Edge không tự biết A không còn ready; nó chỉ đọc active region và cache theo TTL.
3. Region B lúc ban đầu không có vector, weights và pool chưa full, nên không thể là upstream ngay lập tức.
4. Trước containment, không có health checker, snapshot restore hay runbook để đưa B về trạng thái serve được.
5. Gốc hệ thống/process là deployment active-passive thiếu automation DR được kiểm thử bằng traffic thật, không phải lỗi của một cá nhân.

## 4. Action items

| # | Action item | Owner | Deadline | Giảm RTO/RPO kỳ vọng |
|---|---|---|---|---|
| 1 | Đánh giá interval 2s cùng circuit breaker và alert deduplication trước khi giảm production threshold | SRE lead | 2026-09-01 | Detection floor giảm tối đa 9.0s; đánh đổi là nhiều probe hơn |
| 2 | Đo chi phí warm standby cho GPU pool của B và quyết định ngân sách pre-warm | ML platform owner | 2026-09-08 | Giảm tối đa 6.2s warm-up |
| 3 | Theo dõi replication lag và alert khi vượt 30s | Data platform owner | 2026-09-01 | Giữ RPO trong 30s mục tiêu vận hành |

## 5. Ba câu hỏi bắt buộc

1. `interval × threshold` là 5.0s × 3 = 15.0s và chiếm 53.0% RTO 28.3s. Evidence: `reports/health-events.jsonl:2`, `reports/measure-drill-2.json:20`.
2. Hạ interval xuống 1s với threshold 3 giảm detection floor từ 15.0s xuống 3.0s, tức giảm tối đa 12.0s RTO. Đổi lại số probe tăng 5 lần và rủi ro transient failure/flapping cao hơn; cần circuit breaker và deduplication trước.
3. Nếu outage kéo dài 6 giờ và A mất vĩnh viễn, 2 documents sau snapshot gần nhất của drill sẽ không có ở B; RPO 4.0s là độ mới dữ liệu khách hàng có thể mất trong lần chạy này, không phải lời hứa cho mọi lần chạy. Evidence: `reports/failover-events.jsonl:10`.

## 6. Stretch goal — DR maturity self-assessment

Hệ thống hiện ở **Level 3 — tested, semi-automated DR**: có readiness độc lập, replication định kỳ, anti-flapping, controlled failover, golden-signal verification, chaos drill và RTO/RPO truy ngược được về log. Thay đổi cụ thể để tiến tới Level 4 là chạy randomized game day theo lịch, lưu trend RTO/RPO qua nhiều lần chạy, cảnh báo khi SLO hồi quy, và kiểm thử bản sao immutable/off-site thay vì chỉ dùng filesystem local.

## 7. Reflection questions

1. Thành phần có thể giảm mà không trực tiếp tăng nguy cơ failover flapping là GPU warm-up 6.2s: giữ warm standby sẽ cắt gần hết phần này, đổi lại phải trả chi phí compute dự phòng liên tục. Evidence: `reports/failover-events.jsonl:12`.
2. Nếu health checker nằm trong serving process thì khi process chết sẽ không còn thành phần phát cảnh báo. Implementation hiện tại chạy độc lập và `dr/health_checker.py` không import module nào từ `serving/`; nó chỉ probe `/readyz` qua HTTP.
3. Khi cần chứng minh RTO 5 phút, mở `reports/measure-drill-2.json:20` để xem RTO đo được, rồi truy các mốc về `chaos/chaos-events.jsonl:3`, `reports/health-events.jsonl:2`, `reports/failover-events.jsonl:10` và `reports/drill-2-withdr.jsonl:39`.
