Bạn phân loại tài liệu dự án xây dựng cơ điện (MEP) từ tên file và vài trang đầu (bìa, mục lục).

Chọn đúng 1 loại tài liệu (doc_type) trong danh sách, dựa vào hình thức tài liệu:
{doc_types}

Chọn các phân hệ (disciplines) mà tài liệu thực sự nói tới, phân hệ chính đứng đầu:
{disciplines}
- Tài liệu bao trùm nhiều phân hệ hoặc không thuộc riêng phân hệ nào (VD thuyết minh tổng hợp, tiêu chuẩn chung) → thêm GENERAL.
- Không đoán phân hệ chỉ vì một từ xuất hiện thoáng qua.

Luồng tài liệu (stream) do người dùng chọn là gợi ý mạnh cho loại tài liệu:
LEGAL_STANDARD → thường LEGAL_STANDARD; OPERATOR_STANDARD → thường BRAND_STANDARD; CONTRACT → thường CONTRACT;
DESIGN_CONSULTANT → SPEC (yêu cầu kỹ thuật, có thể song ngữ EN | VN), TECH_INSTRUCTION (chỉ dẫn kỹ thuật, chia CHƯƠNG)
hoặc DESIGN_REPORT (thuyết minh thiết kế, đề mục I. II. › 1. 2.).

Chỉ trả về JSON, không giải thích:
{"doc_type": "<một giá trị trong danh sách>", "disciplines": ["<phân hệ chính>", "..."]}
