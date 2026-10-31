---
name: docx-export
description: Tạo tài liệu Word (.docx) – heading, bảng, font mặc định, danh sách trích dẫn nguồn cuối tài liệu; chuyển PDF nếu cần. Dùng khi người dùng yêu cầu file Word/báo cáo/tóm tắt hoặc PDF.
---
# Xuất Word

Thư viện: `mep_helpers.docx` (python-docx). Mặc định Times New Roman 13 (đặt cho cả ký tự Đông Á để tiếng Việt hiển thị đúng font).

```python
from mep_helpers import docx as mdocx

doc = mdocx.new_document(font="Times New Roman", size=13)   # font/cỡ theo yêu cầu người dùng nếu có
doc.add_heading("Yêu cầu cáp điện – Spec Điện mục 5.7", level=1)
doc.add_paragraph("Bán kính uốn cong tối thiểu: 6 lần đường kính ngoài của cáp [1].")
mdocx.add_table(doc, ["Hạng mục", "Yêu cầu", "Nguồn"], [["Tiết diện tối thiểu", "1.5mm²", "[2]"]])
mdocx.add_sources(doc, cites)          # "Nguồn tham khảo": [n] Tài liệu · Rev · Mục · Trang
mdocx.apply_font(doc, "Times New Roman", 13)   # gọi CUỐI CÙNG để mọi run/bảng dùng đúng font
doc.save("/workspace/output/yeu_cau_cap.docx")
```
- Nội dung chỉ từ kết quả `search_project_docs` / file người dùng; giữ nguyên giá trị, đơn vị (VD "6 lần", "1.5mm²", màu pha vàng/xanh lá/đỏ).
- Mỗi ý có [n] khớp với danh sách nguồn cuối tài liệu (n của block).
- Người dùng gửi file mẫu .docx → `mdocx.new_document(template="/workspace/input/mau.docx")`, giữ style của mẫu.
- Cần PDF: `from mep_helpers import pdf; pdf.convert("/workspace/output/x.docx")` (LibreOffice). PDF vẽ trực tiếp bằng reportlab → `pdf.register_vn_fonts()` để có dấu tiếng Việt.

Xong → kiểm tra theo skill `file-check`.
