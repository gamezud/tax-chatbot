"""
เทส rules_version ในประวัติ (Firestore) และหมายเหตุ "ใช้ตัวเลขที่ผู้ดูแลระบบปรับ" ใน LINE/PDF

ตัดสินไว้ (2026-10-13):
- rules_version บันทึกทุกครั้งที่ source == "firestore" แม้ค่าจะเท่าค่าตั้งต้น — ค่าตั้งต้นบันทึกเป็น None
- หมายเหตุแสดงเฉพาะตอน changed_keys ไม่ว่าง
ไม่เขียน Firestore จริง ไม่สร้าง PDF จริง — จับ argument จากตัวปลอม
"""
import ast
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import app as app_module
import conftest
from utils import pdf_generator, rules_store, tax_rules
from utils.rules_store import DEFAULT_TAX_RULES
# helper และ fixture จากเทสการโหลด (mocks, apply_doc ถูกใช้เป็น fixture ในไฟล์นี้ด้วย)
from test_rules_loading import apply_doc, config_doc, fake_db, mocks  # noqa: F401
from test_line_verified_cases import line_payload

ROOT = Path(__file__).resolve().parents[1]
NOTE_START = "หมายเหตุ: ระบบใช้อัตราและเกณฑ์ภาษีที่ผู้ดูแลระบบปรับปรุง"
# ค่าที่ไม่กระทบกรณี "ไม่มี" (ไม่มี ThaiESG) — ตัวเลขในสรุปผลจึงต้องเท่าเดิมทุกตัว
UNRELATED_CHANGE = {"deduction": {"thai_esg_cap": DEFAULT_TAX_RULES["deduction"]["thai_esg_cap"] + 1000.0}}


def summary(mocks, query_text="ไม่มี", params=None):
    """ยิงกรณี "ไม่มี" ผ่าน /webhook คืน (ข้อความ LINE, dict ที่ส่ง Firestore, dict ที่ส่ง PDF)"""
    mocks.firebase.save_tax_report.reset_mock()
    mocks.pdf.reset_mock()
    resp = app_module.app.test_client().post("/webhook", json=line_payload(query_text, params or {}))
    assert resp.status_code == 200
    _, firestore_data = mocks.firebase.save_tax_report.call_args.args
    _, pdf_data = mocks.pdf.call_args.args
    return resp.get_json()["fulfillmentText"], firestore_data, pdf_data


# ---------------------------------------------------------------------------
# A. rules_version และหมายเหตุ 3 กรณี
# ---------------------------------------------------------------------------
def test_defaults_save_none_and_show_no_note(mocks):
    text, firestore_data, pdf_data = summary(mocks)
    assert firestore_data["rules_version"] is None
    assert not pdf_data["rules_note"]
    assert "ผู้ดูแลระบบ" not in text
    assert text.splitlines()[1] == "--------------------------------"  # ใต้หัวข้อเป็นเส้นคั่นทันที


def test_doc_equal_to_defaults_saves_version_without_note(apply_doc, mocks):
    default_text, _, _ = summary(mocks)
    apply_doc(fake_db(config_doc(rules_store.to_override(DEFAULT_TAX_RULES), version=4)))
    text, firestore_data, pdf_data = summary(mocks)
    assert firestore_data["rules_version"] == 4  # ประวัติเก็บสิ่งที่ระบบใช้จริง
    assert not pdf_data["rules_note"]            # แต่ผู้ใช้ไม่ต้องรู้ เพราะตัวเลขเท่าค่าตั้งต้น
    assert text == default_text


def test_changed_doc_saves_version_and_shows_note_in_line_and_pdf(apply_doc, mocks):
    default_text, _, _ = summary(mocks)
    apply_doc(fake_db(config_doc(UNRELATED_CHANGE, version=7)))
    text, firestore_data, pdf_data = summary(mocks)
    assert firestore_data["rules_version"] == 7

    lines = text.splitlines()
    note = lines[1]  # บรรทัดถัดจากหัวข้อ "📊 สรุปผลการประเมินภาษี..."
    # บอกสถานะของระบบ ไม่ได้บอกว่าผลของผู้ใช้เปลี่ยน (ค่าที่แก้อาจไม่เกี่ยวกับเงินได้ของเขา)
    # ไม่มี "ครับ" เพราะข้อความเดียวกันไปถึง PDF ซึ่งเป็นเอกสารทางการ (#34)
    assert note == f"{NOTE_START} (เวอร์ชัน 7)"
    # PDF ได้ข้อความเดียวกับ LINE จาก dict เดียวกัน
    assert pdf_data["rules_note"] == note
    assert "ครับ" not in pdf_data["rules_note"]
    # ข้อความ LINE ทั้งข้อความยังลงท้าย "ครับ" (บรรทัดปิดท้ายของสรุปผล)
    assert text.endswith("ครับ")
    # ข้อความอื่นในสรุปผลไม่เปลี่ยนเลย — ตัดบรรทัดหมายเหตุออกแล้วต้องเท่าข้อความเดิม
    assert "\n".join(lines[:1] + lines[2:]) == "\n".join(default_text.splitlines())


def test_rules_note_and_version_come_from_rules_info_read_at_call_time(mocks, monkeypatch):
    # อ่าน tax_rules.RULES_INFO ตอนถูกเรียก (กับดักข้อ 9) — แทนที่ทั้งก้อนแล้วต้องเห็นค่าใหม่
    monkeypatch.setattr(tax_rules, "RULES_INFO",
                        {"source": "firestore", "version": 9, "changed_keys": ["deduction.thai_esg_cap"]})
    text, firestore_data, pdf_data = summary(mocks)
    assert firestore_data["rules_version"] == 9
    assert "(เวอร์ชัน 9)" in pdf_data["rules_note"]
    assert "(เวอร์ชัน 9)" in text


def test_apply_sets_rules_info_with_tax_rules(apply_doc):
    info = apply_doc(fake_db(config_doc(UNRELATED_CHANGE, version=7)))
    assert tax_rules.RULES_INFO == info
    assert info["version"] == 7 and info["changed_keys"] == ["deduction.thai_esg_cap"]


def test_default_rules_info_matches_loader_default():
    # ค่าเริ่มต้นใน tax_rules.py ต้องตรงกับ info ที่ตัวโหลดคืนตอนใช้ค่าตั้งต้น
    _, info = rules_store.load_effective_rules(None)
    assert tax_rules.RULES_INFO == info


# ---------------------------------------------------------------------------
# B. PDF — จับ story ด้วย SimpleDocTemplate ปลอม ไม่เขียนไฟล์
# ---------------------------------------------------------------------------
@pytest.fixture
def pdf_story(monkeypatch, tmp_path):
    captured = {}

    def fake_template(path, **kwargs):
        doc = MagicMock(name="SimpleDocTemplate")
        doc.build.side_effect = lambda story: captured.setdefault("story", story)
        return doc

    monkeypatch.setattr(pdf_generator, "SimpleDocTemplate", fake_template)

    def _texts(tax_data):
        captured.clear()
        pdf_generator.generate_tax_pdf(str(tmp_path / "unused.pdf"), tax_data)
        assert not (tmp_path / "unused.pdf").exists()
        return [p.text for p in captured["story"] if hasattr(p, "text")]
    return _texts


BASE_PDF_DATA = {"date": "13/10/2026 10:00", "tax_year": 2569, "user_id": "TEST_USER"}


def test_pdf_shows_rules_note_under_header(pdf_story):
    note = f"{NOTE_START} (เวอร์ชัน 7)"
    texts = pdf_story({**BASE_PDF_DATA, "rules_note": note})
    date_index = next(i for i, t in enumerate(texts) if t.startswith("วันที่ประเมิน"))
    assert texts[date_index + 1] == note  # ใต้บรรทัดวันที่ ก่อนตารางเงินได้


@pytest.mark.parametrize("extra", [{}, {"rules_note": ""}], ids=["no_key", "empty"])
def test_pdf_without_rules_note_shows_nothing(pdf_story, extra):
    texts = pdf_story({**BASE_PDF_DATA, **extra})
    assert not any("ผู้ดูแลระบบ" in t for t in texts)


def test_pdf_generator_does_not_import_tax_rules():
    # pdf_generator รับค่าแล้วสร้าง PDF อย่างเดียว ปีและหมายเหตุมาจาก tax_data
    tree = ast.parse((ROOT / "utils" / "pdf_generator.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported |= {node.module or ""} | {alias.name for alias in node.names}
    assert not any("tax_rules" in name or "rules_store" in name for name in imported)


# ---------------------------------------------------------------------------
# C. ประวัติเก่าที่ไม่มี rules_version
# ---------------------------------------------------------------------------
def test_history_reads_records_with_and_without_rules_version(mocks):
    record = {"created_at": "01/10/2026 10:00:00", "tax_year": 2569,
              "total_income": 360000.0, "tax_payable": 2050.0, "pdf_file_url": ""}
    mocks.firebase.get_user_history.return_value = [record]
    old_text = app_module.handle_history("TEST_USER")["fulfillmentText"]
    mocks.firebase.get_user_history.return_value = [{**record, "rules_version": 7}]
    new_text = app_module.handle_history("TEST_USER")["fulfillmentText"]
    # ยังไม่แสดง version ในหน้าดูประวัติ ข้อความจึงเหมือนกัน
    assert old_text == new_text
    assert "เวอร์ชัน" not in new_text


# ---------------------------------------------------------------------------
# D. ตัวกันค่ารั่วใน conftest ครอบ RULES_INFO ด้วย
# ---------------------------------------------------------------------------
def test_leak_guard_restores_rules_info():
    tax_rules.RULES_INFO = {"source": "firestore", "version": 7, "changed_keys": ["x"]}  # ทำรั่วตั้งใจ
    assert conftest.restore_if_leaked() is True
    assert tax_rules.RULES_INFO == {"source": "default", "version": None, "changed_keys": []}


def test_leak_guard_restores_tax_rules():
    tax_rules.TAX_RULES = {**tax_rules.TAX_RULES, "tax_year": 2570}  # ทำรั่วตั้งใจ
    assert conftest.restore_if_leaked() is True
    assert tax_rules.TAX_RULES == DEFAULT_TAX_RULES


def test_leak_guard_reports_nothing_when_clean():
    assert conftest.restore_if_leaked() is False
