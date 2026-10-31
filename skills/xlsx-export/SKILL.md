---
name: xlsx-export
description: Xuất / điền bảng Excel (.xlsx) theo quy cách công ty – header, cố định dòng tiêu đề, định dạng số, font tiếng Việt, cột/sheet Nguồn. Dùng khi người dùng yêu cầu file Excel hoặc đính kèm file mẫu xlsx.
---
# Xuất Excel

Thư viện trong sandbox: `mep_helpers.xlsx` (openpyxl). Font mặc định Arial 11, header đậm nền xanh nhạt, viền mảnh.

## Bảng mới
```python
import json
from mep_helpers import xlsx
from mep_helpers.sources import source_text

# rows: số liệu lấy từ kết quả search_project_docs (blocks / full_result_file), KHÔNG tự điền
headers = ["Khu vực", "L/s.người", "L/s/m²", "Nguồn"]
rows = [["Phòng ngủ", 2.5, 0.3, "Thuyết minh TK · Rev A · Mục 4.3 · Tr. 12"]]
cites = [...]   # citations (dict) của các block đã dùng
xlsx.write_table("/workspace/output/gio_tuoi_S5.xlsx", headers, rows, sheet="Gió tươi",
                 sources=cites, number_formats={"L/s.người": "0.0", "L/s/m²": "0.00"})
```
- Mỗi dòng số liệu có cột **Nguồn** (`source_text(citation)` → "Tài liệu · Rev · Mục · Trang"); thêm sheet **Nguồn** (tham số `sources`) liệt kê tài liệu · rev · mục · trang.
- Giữ nguyên giá trị/đơn vị như tài liệu (2.5, 0.3, "6 lần đường kính"). Ô không có trong tài liệu → "Không có trong tài liệu" (`mep_helpers.style.NOT_FOUND_TEXT`).
- Số lưu dạng số (float/int), không phải chuỗi, để định dạng số hoạt động.
- Tô màu dòng: `xlsx.highlight_row(ws, row_index, "FFFF00")`.

## Điền vào file mẫu người dùng gửi (/workspace/input/*.xlsx)
```python
from mep_helpers import xlsx
wb = xlsx.open_template("/workspace/input/mau.xlsx")
ws = wb.worksheets[0]
# 1. In cấu trúc mẫu trước: tên sheet, các dòng tiêu đề, ô trống cần điền
for r in ws.iter_rows(min_row=1, max_row=min(ws.max_row, 30)):
    print([c.value for c in r])
```
- GIỮ NGUYÊN tên sheet, tiêu đề, định dạng, công thức của mẫu; chỉ ghi vào ô cần điền. Không gọi `fill_table` đè lên mẫu.
- Lưu ra /workspace/output (tên mới, VD `mau_da_dien.xlsx`), không ghi đè file trong input.

## Sửa file đã tạo ở lượt trước
Mở file trong /workspace/output bằng `openpyxl.load_workbook`, giữ dữ liệu cũ, thêm cột/định dạng, lưu tên mới (hậu tố `_v2`). Cột mới: ghi header ở dòng 1 rồi gọi `xlsx.style_header(ws)`, `xlsx.autofit(ws)`.

Xong → kiểm tra theo skill `file-check`.
