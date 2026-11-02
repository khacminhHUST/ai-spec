Bạn kiểm tra xem CONTEXT (trích từ tài liệu dự án) có đủ thông tin để trả lời CÂU HỎI không.
Chỉ trả về 1 JSON object: {"verdict": "ENOUGH" | "PARTIAL" | "NONE", "missing_query": "<truy vấn tìm phần còn thiếu, rỗng nếu ENOUGH>"}
- ENOUGH: context chứa thông tin trả lời trực tiếp câu hỏi (kể cả khi tên gọi/đơn vị khác, VD "gym" = "Health club"; ô trống trong bảng không tính là thiếu).
- PARTIAL: context liên quan nhưng thiếu thông tin CỐT LÕI mà câu hỏi yêu cầu.
- NONE: context không chứa thông tin để trả lời câu hỏi.
Ưu tiên ENOUGH khi context đã có giá trị người dùng hỏi.
