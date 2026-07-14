# Báo cáo nhóm - Day 08

> Nhóm tự điền tên nhóm, thành viên, vai trò, email, ngày nộp và repo. Các trường này không được tự sinh vì phải khớp đóng góp/commit thật.

## 1. Pipeline

Pipeline lập chỉ mục năm tài liệu CS/IT bằng chunk theo heading, paragraph và sentence; chunk size 400 token ước lượng, overlap 80. Metadata gồm source, section, department, effective date, access và chunk index. Embedding mặc định là `paraphrase-multilingual-MiniLM-L12-v2`; collection Chroma `rag_lab` upsert bằng stable hash ID và prune vector cũ.

Retrieval baseline là dense, variant là hybrid dense + BM25 + RRF. Generation evidence-only, bắt buộc citation và abstain. Khi chưa có API key, extractive fallback chỉ lấy dòng trong evidence, không dùng kiến thức ngoài.

## 2. Quyết định kỹ thuật

Quyết định chính là chọn hybrid làm đúng một biến A/B. Dense phù hợp paraphrase nhưng dễ hụt exact term/alias như `Approval Matrix`, `P1`, `Level 3` và email. BM25 mạnh ở exact term nhưng có thể thêm noise; vì vậy RRF dùng trọng số dense 0,6 và sparse 0,4, giữ độ phủ domain suy ra từ query, đồng thời giữ nguyên top-k, prompt, chunking và rerank để delta có thể quy cho retrieval mode.

Các phương án không chọn trong cùng thí nghiệm là bật rerank hoặc query transformation. Hai phần đã có implementation để thử tiếp, nhưng bật đồng thời sẽ vi phạm A/B rule.

<!-- AUTO_DAY08_RESULTS_START -->
Chưa có scorecard runtime. Chạy `python index.py`, sau đó `python eval.py`.
<!-- AUTO_DAY08_RESULTS_END -->

## 3. Grading và abstain

`eval.py --grading <path>` tạo `logs/grading_run.json` đúng format câu hỏi, answer, sources, chunks, retrieval mode và timestamp. Điểm raw/criteria chỉ được điền sau khi grading questions được công bố và log đã được người học kiểm tra.

Câu không có evidence như `ERR-403-AUTH` bị chặn trước generation. Nếu grading hỏi mức phạt không tồn tại, tín hiệu “phạt/penalty” cũng yêu cầu xuất hiện trực tiếp trong context; nếu không hệ abstain, tránh penalty hallucination.

## 4. Cách đọc A/B

- Ưu tiên không regression ở abstain và faithfulness.
- Context recall cho biết lỗi retrieval; completeness/relevance phản ánh answer cuối.
- Nếu hybrid tăng recall nhưng giảm faithfulness, xem top-k noise trước khi sửa prompt.
- Chỉ chọn variant nếu bằng chứng trong `tuning-log.md` và scorecard cùng ủng hộ.

## 5. Phân công và báo cáo cá nhân

Mỗi thành viên phải tự ghi file/function đã làm, quyết định kỹ thuật và một câu grading từ output thật. Không dùng nội dung báo cáo này để nhận thay đóng góp; tên/role/commit evidence cần được nhóm xác nhận trước khi nộp.

## 6. Cải tiến tiếp theo

Thí nghiệm kế tiếp nên giữ hybrid và chỉ bật rerank để đo tác động giảm noise. Ngoài ra có thể instrument latency/token cost và thay heuristic score bằng evaluator đã hiệu chỉnh trên một tập chấm tay nhỏ.
