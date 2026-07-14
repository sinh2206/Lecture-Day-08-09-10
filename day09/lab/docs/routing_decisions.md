# Nhật ký quyết định routing - Day 09

Tệp này chỉ chứa bằng chứng từ trace thật. `eval_trace.py` sẽ thay vùng tự động bên dưới bằng ít nhất ba quyết định routing sau khi bạn chạy bộ 15 câu hỏi; không có số liệu giả trước runtime.

<!-- AUTO_ROUTING_START -->
Chưa có trace runtime. Chạy `python eval_trace.py`.
<!-- AUTO_ROUTING_END -->

## Tiêu chí đọc trace

- `supervisor_route` là route ban đầu, không bị worker ghi đè.
- `route_reason` phải nêu signal cụ thể và việc có cần MCP hay không.
- `workers_called` cho biết control flow thực; route policy dự kiến có retrieval, policy và synthesis.
- `mcp_tools_used` chứa envelope đầy đủ để phân biệt tool lỗi với tool trả danh sách rỗng.
- `evaluation.route_matches` so route với expected route của bộ test; đây là bằng chứng cho routing accuracy.
