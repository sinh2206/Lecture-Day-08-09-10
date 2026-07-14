# Báo cáo nhóm - Day 10

> Thông tin hành chính cần nhóm tự xác nhận: tên nhóm, thành viên, vai trò, email, ngày nộp và repo. Không tự động điền để tránh khai sai đóng góp cá nhân.

## 1. Pipeline

Pipeline đọc CSV raw của năm nguồn nghiệp vụ, chuẩn hóa và phân loại record thành cleaned/quarantine, chạy expectation rồi mới publish snapshot ChromaDB. Mỗi run tạo log, cleaned CSV, quarantine CSV và manifest dùng chung `run_id`. Vector được upsert bằng stable chunk ID; ID không còn trong cleaned run bị prune để corruption hoặc policy cũ không tiếp tục xuất hiện trong retrieval.

Luồng chuẩn:

```powershell
python etl_pipeline.py run --run-id clean-fix
```

## 2. Cleaning và expectation

Các thay đổi chính gồm đăng ký `access_control_sop`, cutoff version cấu hình theo source, validate ngày lịch thật, normalize `exported_at`, quarantine marker mơ hồ, semantic rule HR 2025, sửa refund 14 thành 7 ngày, chuẩn hóa cụm từ lặp và dedupe sau transform.

Expectation halt kiểm đủ năm nguồn, schema bắt buộc, doc ID đã đăng ký, unique ID/content, refund/HR stale, current effective date và ISO exported timestamp. Hai expectation warn kiểm marker mơ hồ và chunk ngắn.

### Metric impact

| Rule / expectation | Metric bị tác động | Bằng chứng runtime |
|---|---|---|
| Access source registration | cleaned count theo doc + top-1 Level 4 | log, grading |
| Source cutoff | `stale_effective_date` quarantine | quarantine reason counts |
| Semantic HR conflict | stale HR quarantine + forbidden sạch | quarantine, HR eval |
| Refund fix | expectation refund + `hits_forbidden` | inject/clean CSV |
| Dedupe sau normalize | duplicate quarantine + stable vector count | log, manifest |
| Required doc IDs | halt fail khi thiếu source | expectation list |

<!-- AUTO_GROUP_EVIDENCE_START -->
Chưa có artifact runtime. Vùng này được cập nhật cùng Quality Report khi chạy `eval_retrieval.py`.
<!-- AUTO_GROUP_EVIDENCE_END -->

## 3. Before/after

Kịch bản before giữ dòng refund 14 ngày bằng `--no-refund-fix` và cố ý vượt halt bằng `--skip-validate`; đây không phải chế độ serving. Kịch bản after chạy pipeline chuẩn để fix, validate, upsert và prune. Hai eval phải dùng cùng questions/top-k/model; kết luận dựa trên `contains_expected`, `hits_forbidden`, `top1_doc_matches` và `run_id`, không dựa vào quan sát cảm tính.

## 4. Freshness

SLA là 24 giờ. Monitor dùng `latest_exported_at` làm source boundary và `published_at` làm publish boundary. PASS nghĩa source còn trong SLA; WARN nghĩa timestamp thiếu/sai/future; FAIL nghĩa manifest lỗi/thiếu hoặc source stale. Snapshot mẫu có thể FAIL theo ngày chạy hiện tại, phản ánh đúng data freshness thay vì lỗi pipeline.

## 5. Liên hệ Day 09

Day 10 tách collection `day10_kb` khỏi `day09_docs` để corruption experiment không làm hỏng demo orchestration. Production nên để Day 09 đọc alias trỏ tới snapshot Day 10 mới nhất đã pass expectation, sau đó đổi alias atomically.

## 6. Rủi ro và cải tiến

- MCP/agent chưa tự đọc manifest freshness trước mỗi câu trả lời.
- Cutoff chưa lấy từ version registry trung tâm.
- Chroma local chưa có transaction publish/rollback hoàn chỉnh.
- Hybrid domain rerank hiện rule-based; cần benchmark multilingual reranker khi thêm domain.
- Báo cáo cá nhân và phân công phải do từng thành viên tự viết từ commit/run evidence thật.
