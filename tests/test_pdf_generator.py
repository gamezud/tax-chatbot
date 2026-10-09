"""
smoke test ของ generate_tax_pdf ตัวจริง (test_webhook.py mock ฟังก์ชันนี้ทุกเทส)
ตรวจแค่ว่าสร้างไฟล์ PDF ได้โดยไม่ error ทั้งแบบมีและไม่มี tax_year ไม่ตรวจข้อความข้างใน
ไฟล์ไปอยู่ใน tmp_path เพราะ generate_tax_pdf เขียนเฉพาะ path ที่ส่งเข้าไป
"""
from pathlib import Path

import pytest

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
