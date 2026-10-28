"""
smoke test ของ generate_tax_pdf ตัวจริง (test_webhook.py mock ฟังก์ชันนี้ทุกเทส)
ตรวจแค่ว่าสร้างไฟล์ PDF ได้โดยไม่ error ทั้งแบบมีและไม่มี tax_year ไม่ตรวจข้อความข้างใน
ไฟล์ไปอยู่ใน tmp_path เพราะ generate_tax_pdf เขียนเฉพาะ path ที่ส่งเข้าไป

test_pdf_shaping ตรวจว่าการวางสระ/วรรณยุกต์ไทย (shaping) เปิดอยู่จริง — จับ story ด้วย SimpleDocTemplate ปลอม ไม่เขียนไฟล์
"""
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from reportlab.pdfbase.pdfmetrics import getFont
from reportlab.platypus import Paragraph, Table

from utils import pdf_generator
from utils.pdf_generator import generate_tax_pdf

REPORTS_DIR = Path(__file__).resolve().parent.parent / "static" / "reports"

BASE_DATA = {
    "date": "09/10/2026 10:00",
    "user_id": "TEST_USER",
    "salary_total": 360000.0,
    "rental_income": 0.0,
    "online_income": 0.0,
    "total_income": 360000.0,
    "total_expense": 100000.0,
    "total_deduction": 69000.0,
    "net_income": 191000.0,
    "tax_payable": 2050.0,
    "withholding_tax": 0.0,
    "net_payable": 2050.0,
    "tax_advice": "",
}


def _report_files():
    return set(REPORTS_DIR.iterdir()) if REPORTS_DIR.exists() else set()


@pytest.mark.parametrize("extra", [{"tax_year": 2569}, {}], ids=["with_tax_year", "without_tax_year"])
def test_generate_tax_pdf_writes_only_to_given_path(tmp_path, extra):
    before = _report_files()
    out = tmp_path / "report.pdf"
    generate_tax_pdf(str(out), {**BASE_DATA, **extra})
    assert out.read_bytes().startswith(b"%PDF")
    assert _report_files() == before


def _paragraphs(flowables):
    # Paragraph ใน story ตรง ๆ และในช่องของตาราง
    for f in flowables:
        if isinstance(f, Paragraph):
            yield f
        elif isinstance(f, Table):
            for row in f._cellvalues:
                yield from _paragraphs(row)


def test_pdf_shaping(monkeypatch, tmp_path):
    # ReportLab วางวรรณยุกต์ไทยถูกเฉพาะเมื่อ style ตั้ง shaping และฟอนต์ shapable (ต้องมี uharfbuzz)
    # ถ้าขาดอย่างใดอย่างหนึ่ง PDF ยังสร้างได้โดยไม่มี error แต่วรรณยุกต์ลอยแบบเดิม — เทสนี้จึงต้องตรวจทั้งสองอย่าง
    assert getFont(pdf_generator.THAI_FONT).shapable

    captured = {}

    def fake_template(path, **kwargs):
        doc = MagicMock(name="SimpleDocTemplate")
        doc.build.side_effect = lambda story: captured.setdefault("story", story)
        return doc

    monkeypatch.setattr(pdf_generator, "SimpleDocTemplate", fake_template)
    # มี rules_note และ tax_advice ให้ style ที่สร้างตามเงื่อนไขถูกใช้ด้วย
    data = {**BASE_DATA, "tax_year": 2569, "rules_note": "หมายเหตุทดสอบ", "tax_advice": "คำแนะนำทดสอบ"}
    generate_tax_pdf(str(tmp_path / "unused.pdf"), data)
    assert not (tmp_path / "unused.pdf").exists()

    paragraphs = list(_paragraphs(captured["story"]))
    not_shaped = sorted({p.style.name for p in paragraphs if not p.style.shaping})
    assert not_shaped == []
    # ยืนยันว่าข้อมูลข้างบนพาไปถึงทุก style ที่มีอยู่ตอนเขียนเทส
    assert {p.style.name for p in paragraphs} >= {
        "DocTitle", "SubTitle", "TableCell", "TableCellBold", "AdviceHead", "RulesNote", "NoteText"}
