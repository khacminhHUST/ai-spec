"""Integration – DockerSandbox thật: vòng đời, file, tạo xlsx/docx/pdf, font tiếng Việt trong PDF."""
import io
import json
import uuid

import pytest

from app.services.agent.sandbox import SandboxManager
from app.services.agent.sandbox.docker_backend import DockerSandbox
from app.services.agent.tools.sandbox_tools import run_code_impl
from tests.agent.integration.sandbox.conftest import py

pytestmark = pytest.mark.integration

VN = "Phòng ngủ – lưu lượng gió tươi 2.5 L/s.người; Phòng tập (gym) 3.8"

OFFICE_CODE = '''
import json
from mep_helpers import xlsx, check, pdf
from mep_helpers import docx as mdocx
cites = [{"doc_title": "Thuyết minh TK", "revision": "A", "number": "4.3", "title": "Gió tươi", "page": 12}]
xlsx.write_table("/workspace/output/a.xlsx", ["Khu vực", "L/s.người"], [["Phòng ngủ", 2.5], ["Gym", 3.8]],
                 sources=cites, number_formats={"L/s.người": "0.0"})
doc = mdocx.new_document()
doc.add_heading("Yêu cầu", level=1)
doc.add_paragraph(TEXT)
mdocx.add_table(doc, ["Hạng mục", "Yêu cầu"], [["Bán kính uốn", "6 lần"]])
mdocx.add_sources(doc, cites)
doc.save("/workspace/output/b.docx")
pdf.convert("/workspace/output/b.docx")
print(json.dumps([check.check_file(p) for p in
      ["/workspace/output/a.xlsx", "/workspace/output/b.docx", "/workspace/output/b.pdf"]], ensure_ascii=False))
'''

PDF_CODE = '''
from reportlab.pdfgen import canvas
from mep_helpers import pdf
font = pdf.register_vn_fonts()
c = canvas.Canvas("/workspace/output/vn.pdf")
c.setFont(font, 12)
c.drawString(50, 800, TEXT)
c.save()
'''


def _pdf_text(data: bytes) -> str:
    import pypdfium2 as pdfium

    return pdfium.PdfDocument(data)[0].get_textpage().get_text_range()


def test_create_and_destroy(sbx_cfg):
    import docker

    client = docker.from_env()
    box = DockerSandbox(f"test-{uuid.uuid4().hex[:8]}", sbx_cfg)
    box.create()
    box.create()                                       # idempotent
    ct = client.containers.get(box.name)
    assert ct.status == "running"
    hc = ct.attrs["HostConfig"]
    assert hc["NetworkMode"] == "none" and hc["ReadonlyRootfs"] and hc["PidsLimit"] == sbx_cfg["pids_limit"]
    assert box.list("/workspace") == []
    box.destroy()
    with pytest.raises(docker.errors.NotFound):
        client.containers.get(box.name)
    with pytest.raises(docker.errors.NotFound):
        client.volumes.get(box.name)
    box.destroy()                                      # idempotent


def test_file_roundtrip_unicode_and_nested(sbx):
    p = sbx.put_file("input/thư mục/dữ liệu.json", json.dumps({"t": VN}, ensure_ascii=False).encode())
    assert json.loads(sbx.get_file(p))["t"] == VN
    assert any(f.path == p for f in sbx.list("/workspace/input"))
    res = py(sbx, f"print(open({p!r}, encoding='utf-8').read())")
    assert res.exit_code == 0 and VN in res.stdout


def test_run_code_creates_xlsx_docx_pdf(sbx):
    m = SandboxManager(lambda cid: sbx, 1800)
    out = json.loads(run_code_impl(m, sbx.cfg, "x", f"TEXT = {VN!r}\n" + OFFICE_CODE, timeout_s=120))
    assert out["ok"], out["stderr"]
    assert {f["path"] for f in out["new_files"]} >= {"output/a.xlsx", "output/b.docx", "output/b.pdf"}
    reports = json.loads(out["stdout"].strip().splitlines()[-1])
    assert all(r["ok"] for r in reports), reports
    assert reports[0]["sheets"][1]["name"] == "Nguồn"

    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(sbx.get_file("output/a.xlsx")))
    assert wb["Bảng"]["A2"].value == "Phòng ngủ" and wb["Bảng"].freeze_panes == "A2"
    assert wb["Nguồn"]["A2"].value == "Thuyết minh TK" and wb["Nguồn"]["D2"].value == "12"
    from docx import Document
    assert Document(io.BytesIO(sbx.get_file("output/b.docx"))).styles["Normal"].font.name == "Times New Roman"
    # LibreOffice: Times New Roman → font thay thế metric (Liberation Serif) đủ dấu tiếng Việt
    lo_pdf = sbx.get_file("output/b.pdf")
    assert b"LiberationSerif" in lo_pdf or b"TimesNewRoman" in lo_pdf
    assert "Phòng ngủ – lưu lượng gió tươi" in _pdf_text(lo_pdf)


def test_vietnamese_font_in_pdf(sbx):
    res = py(sbx, f"TEXT = {VN!r}\n" + PDF_CODE)
    assert res.exit_code == 0, res.stderr
    data = sbx.get_file("output/vn.pdf")
    assert b"DejaVuSans" in data                       # font TTF có dấu được nhúng
    text = _pdf_text(data)
    assert "Phòng ngủ" in text and "lưu lượng gió tươi" in text
