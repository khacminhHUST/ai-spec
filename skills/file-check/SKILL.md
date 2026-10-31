---
name: file-check
description: Kiểm tra file sau khi tạo (mở lại được, đủ dòng/cột, không ô lỗi, đúng font, có nội dung bắt buộc) trước khi báo xong. Dùng sau MỖI lần tạo/sửa file.
---
# Kiểm tra file

```python
import json
from mep_helpers import check
print(json.dumps(check.check_file("/workspace/output/a.xlsx", min_rows=5,
                                  expect_headers=["Khu vực", "Nguồn"]), ensure_ascii=False))
print(json.dumps(check.check_file("/workspace/output/b.docx", expect_font="Times New Roman",
                                  expect_text=["6 lần"]), ensure_ascii=False))
print(json.dumps(check.check_file("/workspace/output/b.pdf"), ensure_ascii=False))
```
- `ok: false` → đọc `problems`, sửa file, kiểm tra lại. Không báo xong khi còn lỗi.
- Ngoài kết quả tự động, in lại vài dòng dữ liệu (VD 5 dòng đầu của sheet) để đối chiếu với số liệu nguồn: mọi giá trị phải khớp kết quả tra cứu.
- Xlsx từ mẫu: so tên sheet & tiêu đề với file mẫu trong /workspace/input – phải giữ nguyên.
- Báo người dùng: tên file, số dòng/cột (xlsx) hoặc số trang/mục (docx/pdf), phần nào "Không có trong tài liệu".
