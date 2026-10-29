"""Quy cách mặc định (chỉ áp dụng khi người dùng không yêu cầu khác / không có file mẫu)."""
import os

DOCX_FONT = "Times New Roman"
DOCX_FONT_SIZE = 13
XLSX_FONT = "Arial"
XLSX_FONT_SIZE = 11
HEADER_FILL = "D9E1F2"      # xanh nhạt
HIGHLIGHT_FILL = "FFFF00"   # vàng
BORDER_COLOR = "808080"
NOT_FOUND_TEXT = "Không có trong tài liệu"

TEMPLATES_DIR = os.environ.get("MEP_TEMPLATES", "/opt/mep/templates")

# Font TTF có dấu tiếng Việt cho reportlab (PDF vẽ trực tiếp)
PDF_FONTS = {
    "DejaVuSans": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "DejaVuSans-Bold": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
}
