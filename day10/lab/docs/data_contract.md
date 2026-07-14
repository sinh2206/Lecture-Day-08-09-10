# Data Contract - Day 10

Contract máy đọc nằm tại `contracts/data_contract.yaml`. Owner là **AI Platform & Knowledge Operations**; alert channel là `#data-observability`; freshness SLA 24 giờ được quyết định theo boundary source export, còn tuổi publish được ghi riêng để chẩn đoán pipeline.

## 1. Source map

| `doc_id` | Canonical source | Failure mode chính | Detection |
|---|---|---|---|
| `policy_refund_v4` | `data/docs/policy_refund_v4.txt` | stale 14 ngày, record trước hiệu lực v4, duplicate | refund expectation, cutoff, forbidden eval |
| `sla_p1_2026` | `data/docs/sla_p1_2026.txt` | SLA/version cũ, thiếu escalation/channel | cutoff, required source, retrieval eval |
| `it_helpdesk_faq` | `data/docs/it_helpdesk_faq.txt` | FAQ cũ, timestamp sai, text mơ hồ | cutoff, ISO timestamp, marker expectation |
| `hr_leave_policy` | `data/docs/hr_leave_policy.txt` | 10 ngày phép năm 2025 xung đột 12 ngày 2026 | date + semantic HR rule, forbidden eval |
| `access_control_sop` | `data/docs/access_control_sop.txt` | nguồn hợp lệ bị allowlist bỏ sót, duplicate | required doc expectation, top-1 grading |

Mọi `doc_id` khác bị quarantine với `unknown_doc_id`; raw export không tự trở thành nguồn canonical chỉ vì có nội dung hợp lý.

## 2. Schema cleaned

| Cột | Kiểu | Bắt buộc | Ràng buộc |
|---|---|---|---|
| `chunk_id` | string | Có | duy nhất; hash ổn định từ doc + normalized text |
| `doc_id` | enum string | Có | thuộc đúng năm source đã đăng ký |
| `chunk_text` | UTF-8 string | Có | tối thiểu 8 ký tự; không marker mơ hồ/stale forbidden |
| `effective_date` | date | Có | ISO `YYYY-MM-DD`; không trước cutoff source |
| `exported_at` | datetime | Có | parse được theo ISO-8601 sau normalize |

## 3. Version contract

| Source | Cutoff mặc định | Environment override |
|---|---|---|
| Refund v4 | 2026-02-01 | `REFUND_POLICY_MIN_DATE` |
| SLA P1 2026 | 2026-01-15 | `SLA_POLICY_MIN_DATE` |
| Helpdesk FAQ | 2026-01-20 | `HELPDESK_FAQ_MIN_DATE` |
| HR leave 2026 | 2026-01-01 | `HR_POLICY_MIN_DATE` |
| Access SOP | 2026-01-01 | `ACCESS_SOP_MIN_DATE` |

Ngày chỉ là điều kiện cần. HR còn có semantic rule loại câu “dưới 3 năm ... 10 ngày phép năm” ngay cả khi upstream gắn nhầm effective date mới.

## 4. Quarantine, halt và warn

- Quarantine giữ nguyên raw fields, thêm `reason` và chi tiết normalize/cutoff khi có.
- Expectation `halt` bảo vệ schema, đủ source, unique ID/content, version refund/HR và timestamp.
- Expectation `warn` dùng cho độ dài chunk và marker mơ hồ đã được cleaning loại; warn không chặn publish nhưng xuất hiện trong log/manifest.
- Không sửa tay cleaned artifact. Owner sửa source hoặc rule, chạy lại với run_id mới và đối chiếu metric impact.

## 5. Freshness và lineage

Manifest phải có `run_id`, `run_timestamp`, `published_at`, `latest_exported_at`, raw/clean/quarantine counts, paths, expectation results, embedding model và Chroma collection. Freshness:

- PASS: source age không vượt 24 giờ;
- WARN: timestamp thiếu, sai hoặc nằm trong tương lai;
- FAIL: manifest lỗi/thiếu hoặc source age vượt SLA.

`published_at` cho biết pipeline vừa publish lúc nào nhưng không che khuất source stale; trạng thái chính vẫn dựa trên `latest_exported_at`.
