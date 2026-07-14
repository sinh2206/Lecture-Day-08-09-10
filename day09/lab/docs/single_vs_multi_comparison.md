# So sánh Single Agent và Multi-Agent - Day 09

## Phương pháp

- Day 08 dùng các dòng `baseline_dense` trong `day08/lab/results/ab_comparison.csv`.
- Day 09 dùng JSON trace trong `day09/lab/artifacts/traces`.
- Không ước lượng metric bị thiếu. Day 08 hiện không ghi latency/confidence nên các ô đó phải là `N/A` cho đến khi có artifact cùng định nghĩa.
- `answer_match_rate` Day 09 là heuristic token coverage; grading criteria thủ công vẫn là nguồn kết luận cuối.

<!-- AUTO_COMPARISON_START -->
Chưa có số liệu runtime. Chạy Day 08 `eval.py`, sau đó chạy Day 09 `eval_trace.py --compare`.
<!-- AUTO_COMPARISON_END -->

## Khác biệt kiến trúc có thể xác minh từ code

| Tiêu chí | Day 08 | Day 09 |
|---|---|---|
| Routing visibility | Không có route | Có `supervisor_route` và `route_reason` |
| Ranh giới lỗi | Retrieval + generation trong một pipeline | `worker_io_logs` riêng cho từng worker |
| External tool | Không có | MCP envelope có input/output/error/timestamp |
| Policy exception | Phụ thuộc context/prompt | Policy worker xử lý explicit rule và temporal scope |
| HITL | Không | Ghi `hitl_triggered` khi risk cao, confidence thấp |
| Generation calls | Tối đa một | Tối đa một; policy route thêm tool call, không thêm LLM bắt buộc |

## Kết luận thiết kế

Multi-agent không mặc nhiên tăng chất lượng retrieval; lợi ích trực tiếp là khả năng quan sát, tách trách nhiệm và mở rộng tool. Đổi lại, policy route có thêm độ trễ do MCP/search và state phức tạp hơn. Với câu tra cứu một tài liệu, Day 08 thường đơn giản hơn; với câu access/refund exception hoặc cần audit đường đi, Day 09 phù hợp hơn.

Không nên dùng multi-agent khi task chỉ có một bước, không cần capability ngoài và không cần truy vết theo worker. Nên cân nhắc khi domain có policy exception, multi-hop hoặc yêu cầu kiểm soát/HITL.
