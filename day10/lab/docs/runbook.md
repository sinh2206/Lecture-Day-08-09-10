# Runbook Data Pipeline - Day 10

## 1. Symptom

- Agent trả “14 ngày” thay vì refund window 7 ngày.
- HR trả 10 ngày phép năm cho nhân viên dưới 3 năm thay vì 12 ngày.
- Câu Level 4 không retrieve `access_control_sop`.
- `contains_expected=false`, `hits_forbidden=true` hoặc `top1_doc_matches=false` trong eval/grading.
- Pipeline trả exit code 2 (`PIPELINE_HALT`) hoặc freshness FAIL.
- Collection vẫn trả chunk đã bị quarantine ở run mới.

## 2. Detection

1. Mở log mới nhất trong `artifacts/logs` và tìm `PIPELINE_HALT`, expectation FAIL, record counts, quarantine reason counts, `embed_prune_removed`.
2. Mở manifest cùng `run_id`; đối chiếu raw/clean/quarantine counts, expectation list, embedding model, collection và source timestamp.
3. Lọc quarantine CSV theo `reason` để biết lỗi nằm ở source, ngày, version, nội dung hay duplicate.
4. Đọc dòng câu hỏi tương ứng trong eval CSV/JSONL; kiểm top docs trước khi nghi ngờ model.

## 3. Diagnosis

| Bước | Kiểm tra | Kết luận |
|---:|---|---|
| 1 | `raw_path`, `raw_records`, doc_id trong raw | Sai ingest/path hay source chưa export |
| 2 | `quarantine_reason_counts` và row cụ thể | Rule nào loại record, có đúng contract không |
| 3 | `expectation[*]` | Snapshot vi phạm invariant nào |
| 4 | `cleaned_doc_counts` đủ năm source | Allowlist/cutoff có bỏ nhầm source không |
| 5 | `embed_upsert`, `embed_prune_removed`, metadata `run_id` | Publish có cập nhật snapshot hay còn vector cũ |
| 6 | `top_doc_ids`, preview, expected/forbidden | Retrieval sai ranking hay dữ liệu publish đã sai |
| 7 | freshness source age và publish age | Upstream stale hay pipeline publish chậm |

Thứ tự bắt buộc: freshness/version -> volume/errors -> schema/contract -> lineage/run_id -> retrieval/model.

## 4. Mitigation

- Không dùng `--skip-validate` cho run phục vụ thật; cờ này chỉ dành cho corruption demo.
- Sửa source/rule/cutoff, chạy pipeline chuẩn với run_id mới để upsert và prune snapshot.
- Nếu run mới sai nhưng collection cũ tốt, tạm dừng consumer hoặc trỏ về backup collection; không xóa raw/quarantine evidence.
- Khi source stale mà chưa thể refresh, thông báo “dữ liệu chưa cập nhật” và buộc agent abstain cho policy nhạy cảm.
- Với missing `access_control_sop`, xác nhận allowlist, cleaned doc count và top-1 grading trước khi mở lại serving.

## 5. Prevention

- Giữ `all_required_doc_ids_present`, version/semantic expectations và unique ID ở severity halt.
- Quản lý cutoff trong contract/config, review thay đổi bằng pull request thay vì hard-code rải rác.
- Alert `#data-observability` khi freshness vượt 24 giờ hoặc pipeline halt.
- Lưu manifest, cleaned, quarantine và eval theo cùng run_id; đặt retention để có thể audit/rollback.
- Chạy corruption test định kỳ cho refund 14 ngày, HR 10 ngày và source access bị thiếu.
- Theo dõi collection count và prune count qua các rerun để phát hiện mất idempotency.

## Lệnh vận hành tham chiếu

```powershell
python etl_pipeline.py run --run-id clean-fix
python eval_retrieval.py --out artifacts/eval/after_clean_fix.csv
python grading_run.py --out artifacts/eval/grading_run.jsonl
python etl_pipeline.py freshness --manifest artifacts/manifests/manifest_clean-fix.json
```

Người vận hành thực thi các lệnh; tài liệu này không coi việc có code là bằng chứng run thành công.
