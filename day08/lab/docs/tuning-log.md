# Nhật ký tuning RAG - Day 08

## Thí nghiệm A/B

Mục tiêu là đo tác động của hybrid retrieval trên corpus có cả ngôn ngữ tự nhiên và exact term. Hai nhánh giữ nguyên chunking, embedding, top-k, prompt, LLM và rerank; biến duy nhất thay đổi là `retrieval_mode`.

| Tham số | Baseline | Variant |
|---|---|---|
| Chunk size / overlap | 400 / 80 token ước lượng | 400 / 80 token ước lượng |
| Retrieval | Dense | Hybrid: dense + BM25 + RRF + query-domain coverage |
| Top-k search / select | 10 / 3 | 10 / 3 |
| Rerank | Tắt | Tắt |
| Embedding | `paraphrase-multilingual-MiniLM-L12-v2` | Không đổi |
| Generation | Provider cấu hình hoặc extractive fallback | Không đổi |

Giả thuyết: hybrid tăng context recall cho alias `Approval Matrix`, nhãn `P1`, `Level 3`, email và mã định danh. Dense có thể tốt tương đương ở câu paraphrase tự nhiên; BM25 có thể thêm noise nên RRF chỉ dành trọng số 0,4 cho nhánh sparse.

## Kết quả runtime

Phần giữa hai marker được `eval.py` cập nhật sau khi chạy. Trước khi chạy, không có số liệu thực và không được suy diễn điểm.

<!-- AUTO_RESULTS_START -->
Chưa có kết quả runtime. Chạy `python eval.py` sau khi đã chạy `python index.py`.
<!-- AUTO_RESULTS_END -->

## Cách đọc kết quả

- Context recall tăng nhưng faithfulness giảm: hybrid tìm đủ nguồn hơn nhưng top-k có noise; xem từng chunk trước khi đổi prompt.
- Relevance và completeness cùng tăng: variant cải thiện cả retrieval lẫn câu trả lời cuối.
- Alias `q07` tăng riêng: BM25 đã bổ sung đúng exact phrase trong preamble của Access Control SOP.
- Câu abstain `q09` phải tiếp tục không dùng nguồn và không bịa; nếu variant trả lời mã lỗi, đó là regression nghiêm trọng.
- Delta bằng 0 vẫn là kết quả hợp lệ với corpus nhỏ; không bật thêm rerank trong cùng lần đo để “làm đẹp” số.

## Quyết định tiếp theo

Chỉ chọn hybrid làm cấu hình phục vụ nếu context recall không giảm và không tạo regression ở câu abstain. Nếu hybrid tăng recall nhưng thêm noise, thí nghiệm kế tiếp nên giữ hybrid và chỉ bật rerank; đó phải là một A/B mới, không gộp vào kết quả hiện tại.
