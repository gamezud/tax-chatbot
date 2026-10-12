"""
ภาษีที่ทดสอบผ่าน LINE จริงแล้ว ล็อกไว้เป็นเทสกันถอยหลัง (regression)

ทุกกรณี: เงินเดือน 40,000 บาท/เดือน ไม่มีโบนัส ประกันสังคม 10,500 หัก ณ ที่จ่าย 0
แล้วตอบคำถามค่าลดหย่อนด้วยข้อความในตาราง ยิงผ่าน /webhook (fixture 021 แทนข้อความ ค่าลดหย่อน และ tax_session)

ค่าที่คาดหวังมาจากผลใน LINE จริง ไม่ได้คำนวณใหม่ในเทส — เทสนี้จับว่าตัวเลขเปลี่ยนไปจากที่เคยตรวจแล้ว
**ไม่ใช่หลักฐานว่าสูตรถูกตามกฎหมาย** หลักฐานนั้นคือ test_tax_calculator.py และ docs/tax-rules-2569.md
ถ้าเทสนี้ล้ม ให้หาว่าอะไรเปลี่ยน ห้ามแก้ค่าที่คาดให้ผ่าน

parameters มาจาก log [RAW PARAMS] ของการทดสอบใน LINE (บางประโยคบันทึกไว้ใน test_deduction_guard.py ด้วย)
ยกเว้นสองกรณีที่ระบุไว้ในตาราง ช่องที่ไม่ได้กรอกเป็น "" ตามค่าใน fixture 021
"""
import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# โมดูลปลอมแทน utils.firebase_db จาก conftest.py ต้องอยู่ก่อน import app (เหมือน test_webhook.py)
assert isinstance(sys.modules["utils.firebase_db"].firebase_client, MagicMock), \
    "utils.firebase_db ไม่ใช่โมดูลปลอม — conftest.py ไม่ได้ทำงานหรือมีคน import ของจริงไปก่อน"

import app as app_module  # noqa: E402
import utils.knowledge_search as knowledge_search  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

SALARY_PER_MONTH = 40000.0
SOCIAL_SECURITY = 10500.0

EIGHT_ITEMS = ("ประกันชีวิต 20000, ประกันสุขภาพ 10000, ดอกเบี้ยบ้าน 50000, RMF 30000, ThaiESG 30000, "
               "บริจาค 5000, ลูก 1 คน, พ่อแม่ 2 คน")

# (ข้อความที่พิมพ์ใน LINE, parameters ของรอบนี้, ภาษีที่คำนวณได้ทั้งสิ้น)
LINE_CASES = [
    ("ไม่มี", {}, 8450.00),  # parameters ของ fixture 021 (ทุกช่องว่าง)
    ("ประกันชีวิต 20000", {"life_insurance": 20000.0}, 6975.00),
    ("ประกันสุขภาพ 10000", {"health_insurance": 10000.0}, 7475.00),
    ("ดอกเบี้ยบ้าน 50000", {"home_loan_interest": 50000.0}, 5475.00),
    ("RMF 30000", {"ssf_rmf": 30000.0}, 6475.00),
    ("ThaiESG 30000", {"thai_esg": 30000.0}, 6475.00),
    ("บริจาค 5000", {"donation": 5000.0}, 7950.00),
    ("พ่อแม่ 2 คน", {"num_parents": 2.0}, 4975.00),
    ("บริจาค 3500 ประกันชีวิต 20000", {"life_insurance": 20000.0, "donation": 3500.0}, 6800.00),
    ("ลูกสองคน", {"num_children": 2.0}, 3475.00),
    ("ลูก 1 คน", {"num_children": 1.0}, 6475.00),
    (EIGHT_ITEMS,
     {"life_insurance": 20000.0, "health_insurance": 10000.0, "home_loan_interest": 50000.0,
      "ssf_rmf": 30000.0, "thai_esg": 30000.0, "donation": 5000.0,
      "num_children": 1.0, "num_parents": 2.0},
     0.00),
    ("บุตร 1 คน", {"num_children": 1.0}, 6475.00),
    ("กองทุนรวมเพื่อการเลี้ยงชีพ 70000", {"ssf_rmf": 70000.0}, 4475.00),
    ("อาร์เอ็มเอฟ 25000", {"ssf_rmf": 25000.0}, 6725.00),
    ("ดอกเบี้ยกู้บ้าน 33000", {"home_loan_interest": 33000.0}, 6325.00),
    ("ประกันสุขภาพ 8000", {"health_insurance": 8000.0}, 7650.00),
    ("เบี้ยประกันสุขภาพ 12000", {"health_insurance": 12000.0}, 7375.00),
    ("ดอกเบี้ยบ้าน 40000 ประกันสุขภาพ 8000", {"health_insurance": 8000.0, "home_loan_interest": 40000.0}, 5575.00),
    ("ดอกเบี้ยเงินกู้บ้าน 35000", {"home_loan_interest": 35000.0}, 6225.00),
    # ไม่มี log: ส่งจากหน้า LIFF มีแค่ผลภาษีจากภาพหน้าจอ parameters จึงเขียนตามข้อความ
    ("บริจาค 102773", {"donation": 102773.0}, 6427.50),
]


def _forbidden(name):
    def _fail(*args, **kwargs):
        pytest.fail(f"{name} ถูกเรียกระหว่างเทส — ต้องไม่โหลดโมเดลหรือเรียก Ollama จริง")
    return _fail


@pytest.fixture(autouse=True)
def mocks(monkeypatch, tmp_path):
    # แทนของที่ออกนอกระบบ (Firestore, PDF, RAG) แบบเดียวกับ test_webhook.py
    m = types.SimpleNamespace(
        firebase=MagicMock(name="firebase_client"),
        pdf=MagicMock(name="generate_tax_pdf"),
        rag=MagicMock(name="query_tax_knowledge"),
    )
    monkeypatch.setattr(app_module, "firebase_client", m.firebase)
    monkeypatch.setattr(app_module, "generate_tax_pdf", m.pdf)
    monkeypatch.setattr(app_module, "query_tax_knowledge", m.rag)
    monkeypatch.setattr(knowledge_search, "get_db", _forbidden("get_db"))
    monkeypatch.setattr(knowledge_search, "ask_llm", _forbidden("ask_llm"))
    monkeypatch.chdir(tmp_path)  # static/reports ที่หลุดออกมาไม่ลง repo
    return m


def line_payload(query_text, params):
    """021 (ตอบ "ไม่มี") เปลี่ยนเป็นเงินเดือน 40,000 ประกันสังคม 10,500 แล้วแทนข้อความและค่าลดหย่อนของรอบนี้
    04 มี tax_session เป็น affected context ค่าของรอบนี้จึงอยู่ใน tax_session ของ request ด้วย (fixture 005)"""
    payload = json.loads((FIXTURES_DIR / "021_04_Tax_Interview_Deductions.json").read_text(encoding="utf-8"))
    payload["queryResult"]["queryText"] = query_text
    payload["queryResult"]["parameters"].update(params)
    for ctx in payload["queryResult"]["outputContexts"]:
        if ctx["name"].endswith("/contexts/tax_session"):
            ctx["parameters"]["salary_per_month"] = SALARY_PER_MONTH
            ctx["parameters"]["salary_per_month.original"] = "40000"
            ctx["parameters"]["social_security"] = {"value": SOCIAL_SECURITY}
            ctx["parameters"]["social_security.original"] = "10500"
            ctx["parameters"].update(params)
    return payload


@pytest.mark.parametrize("query_text, params, tax", LINE_CASES, ids=[c[0][:40] for c in LINE_CASES])
def test_tax_matches_line(query_text, params, tax, mocks):
    resp = app_module.app.test_client().post("/webhook", json=line_payload(query_text, params))
    assert resp.status_code == 200
    text = resp.get_json()["fulfillmentText"]
    assert f"5. ภาษีที่คำนวณได้ทั้งสิ้น: {tax:,.2f} บาท" in text, text
    # ต้องถึงสาขาสรุปผลจริง (ไม่ถูกตัวกันปฏิเสธ) — ค่าเดียวกับที่ส่งเข้า PDF และ Firestore
    _, data = mocks.firebase.save_tax_report.call_args.args
    assert data["tax_payable"] == pytest.approx(tax)


def test_case_count():
    # รายการจาก LINE มี 21 กรณี — ลบกรณีออกต้องเป็นการตัดสินใจ ไม่ใช่หลุดหายระหว่างแก้ไฟล์
    assert len(LINE_CASES) == 21
