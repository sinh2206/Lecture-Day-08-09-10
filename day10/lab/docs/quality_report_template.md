# Quality Report - Day 10

## Phạm vi

Report chứng minh tác động của cleaning/expectation lên snapshot retrieval. Số liệu chỉ được lấy từ manifest và CSV eval do pipeline tạo; `eval_retrieval.py` cập nhật vùng tự động sau mỗi lần chạy.

<!-- AUTO_QUALITY_START -->
Chưa có artifact runtime. Chạy kịch bản inject và clean, mỗi lần lưu eval vào một tên CSV khác nhau.
<!-- AUTO_QUALITY_END -->

## Rule và metric impact cần quan sát

| Rule / expectation | Tác động dự kiến có thể đo | Evidence |
|---|---|---|
| Đăng ký `access_control_sop` | cleaned doc count có access; câu Level 4 top-1 đúng | log, manifest, grading JSONL |
| Cutoff theo source | tăng `stale_effective_date` trong quarantine, loại version cũ | quarantine reason count |
| Semantic HR 2025 | loại 10 ngày phép năm nhưng giữ 10 ngày nghỉ ốm | quarantine + HR eval |
| Refund 14 -> 7 | run inject có forbidden, run clean không có | hai CSV eval |
| Normalize timestamp | record slash timestamp vẫn hợp lệ; invalid bị quarantine | cleaned/quarantine |
| Dedupe sau normalize | duplicate count tăng trong quarantine, vector count không phình | log + Chroma count |
| Required five docs | thiếu một source làm expectation halt | expectation result |
| Unique chunk ID/content | snapshot không có ID/content trùng | expectation result |

## Corruption scenario

Run inject dùng `--no-refund-fix --skip-validate`: dòng refund 14 ngày sau cutoff được giữ, expectation refund fail nhưng pipeline cố ý publish để đo `hits_forbidden`. Run clean không có hai cờ này phải pass expectation, thay 14 bằng 7, upsert snapshot và prune vector corruption.

## Freshness

PASS/WARN/FAIL dựa trên tuổi `latest_exported_at`; `published_at` chỉ giúp phân biệt source stale với publish stale. Dữ liệu mẫu tháng 04/2026 có thể FAIL khi chạy ở thời điểm muộn hơn, và đó là kết quả đúng của monitor chứ không phải lý do sửa timestamp giả.

## Hạn chế

- Score retrieval kiểm keyword/evidence, chưa chấm chất lượng câu trả lời LLM.
- Domain rerank rule-based cần bổ sung khi thêm nguồn mới.
- Kết luận before/after chỉ hợp lệ khi hai CSV dùng cùng bộ câu hỏi, top-k và embedding model.
