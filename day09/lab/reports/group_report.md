# Báo cáo nhóm - Day 09

> Nhóm tự xác nhận tên, thành viên, vai trò, email, ngày nộp và repo để báo cáo khớp commit/đóng góp thật.

## 1. Kiến trúc

Hệ thống dùng Supervisor-Worker với ba worker: retrieval, policy/tool và synthesis. Supervisor rule-based ghi route reason/risk/tool need nhưng không trả lời domain. Mọi task retrieve evidence trước; route policy gọi thêm policy worker và MCP; synthesis tạo answer/citation/confidence. Risk cao với confidence dưới 0,4 kích hoạt HITL trace.

Retrieval tự index năm tài liệu theo section vào ChromaDB; task nhiều domain giữ ít nhất một chunk của mỗi nguồn suy ra từ query trước khi lấp top-k. Policy xử lý refund exception, temporal scoping và access decision. MCP Standard có `search_kb`, `get_ticket_info`, `check_access_permission`, `create_ticket`; mọi call có envelope input/output/error/timestamp.

## 2. Quyết định kỹ thuật

Quyết định chính là giữ orchestration Python thuần và contract/state rõ thay vì thêm LangGraph trong phạm vi lab. Với ba worker và một conditional route, function orchestration ngắn hơn, dễ test độc lập và không thêm runtime dependency. Trade-off là chưa có persistence/checkpoint/interrupt thật; HITL hiện chỉ ghi trạng thái và cần workflow ngoài để pause/resume trong production.

Retrieval luôn chạy trước policy để synthesis nhận evidence thống nhất. Policy vẫn gọi `search_kb` qua MCP theo yêu cầu capability, sau đó dedupe khi hợp nhất. Cách này làm policy route tốn thêm search nhưng trace rõ external capability và không để policy truy cập Chroma trực tiếp.

<!-- AUTO_DAY09_RESULTS_START -->
Chưa có trace runtime. Chạy `python eval_trace.py` sau khi hoàn thành Day 08 scorecard.
<!-- AUTO_DAY09_RESULTS_END -->

## 3. Grading

`eval_trace.py --grading` ghi `artifacts/grading_run.jsonl`, gồm route, reason, workers, MCP tool names và full MCP trace, confidence, HITL, latency, sources và timestamp. Điểm raw/câu đúng sai chỉ được kết luận từ grading questions và artifact thật; không dự đoán trước.

Câu mã lỗi thiếu context đi retrieval để kiểm evidence, sau đó synthesis abstain và risk/confidence kích hoạt HITL. Câu multi-hop access + P1 đi policy route, gọi retrieval, MCP access decision, policy và synthesis; trace cho phép kiểm đủ worker/capability.

## 4. So sánh Day 08/09

Day 09 tăng routing visibility, worker-level error isolation và extensibility. Nó không tự tăng retrieval accuracy vì vẫn phụ thuộc corpus/embedding, đồng thời policy route có thêm MCP latency. `single_vs_multi_comparison.md` tự lấy scorecard Day 08 và trace Day 09, chỉ so metric có định nghĩa tương thích.

## 5. Phân công

Mỗi thành viên tự ghi chính xác file/function, một quyết định và một trace đã phân tích. Không sao chép phần kỹ thuật chung thành claim cá nhân; role phải khớp commit và khả năng giải thích khi review.

## 6. Cải tiến

Các bước tiếp theo là classifier có confidence thay keyword, MCP transport có auth/timeout, HITL persistence thật và calibration confidence bằng tập trace đã chấm tay.
