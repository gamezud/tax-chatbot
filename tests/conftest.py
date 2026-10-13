"""ตั้งค่าที่ต้องเกิดก่อนไฟล์ test ใด ๆ ถูกโหลด

ใส่โมดูลปลอมแทน utils.firebase_db ลง sys.modules ก่อนใครจะ import app
  ไฟล์จริงสร้าง firebase_client = FirebaseManager() ทันทีที่ถูก import ซึ่งอ่าน firebase_key.json
  และสร้าง client ที่เขียน Firestore จริงได้ โมดูลปลอมทำให้ FirebaseManager() ไม่ถูกสร้างเลย
  pytest โหลด conftest.py ก่อนไฟล์ test ทุกไฟล์ ไฟล์ไหน import app ก่อนก็ได้โมดูลปลอมเหมือนกัน
  (เดิมอยู่ใน test_webhook.py ย้ายมาเมื่อ test_liff_ocr.py ต้อง import app ด้วย)
  ** โมดูลปลอมนี้อยู่ใน sys.modules ตลอดการรัน pytest ทั้งชุด **
  test_firebase_db.py ทดสอบไฟล์จริงโดยโหลดด้วยชื่อโมดูลอื่น จึงไม่ชนกับโมดูลปลอมนี้

fixture tax_rules_not_leaked (autouse) ตรวจหลังทุกเทสว่า tax_rules.TAX_RULES และ RULES_INFO ยังเป็นค่าตั้งต้น
"""
import copy
import sys
import types
from unittest.mock import MagicMock

import pytest

# ถ้ามีใคร import ของจริงไปก่อน FirebaseManager() ได้ทำงานไปแล้ว โมดูลปลอมจะสายเกินไป
assert "app" not in sys.modules, "app ถูก import ไปก่อน conftest.py"
assert "utils.firebase_db" not in sys.modules, "utils.firebase_db ตัวจริงถูก import ไปก่อนแล้ว"
_fake_firebase_db = types.ModuleType("utils.firebase_db")
_fake_firebase_db.firebase_client = MagicMock(name="fake_module_firebase_client")
sys.modules["utils.firebase_db"] = _fake_firebase_db

from utils import rules_store, tax_rules  # noqa: E402

# สำเนาค่าตั้งต้นของ conftest เอง จับกรณีที่มีเทสไปแก้ rules_store.DEFAULT_TAX_RULES ในที่
_PRISTINE_TAX_RULES = copy.deepcopy(rules_store.DEFAULT_TAX_RULES)
_PRISTINE_RULES_INFO = copy.deepcopy(tax_rules.RULES_INFO)


def restore_if_leaked():
    """คืน True ถ้า TAX_RULES, RULES_INFO หรือ DEFAULT_TAX_RULES ไม่ตรงค่าตั้งต้น และคืนค่าตั้งต้นให้แล้ว
    แยกจาก fixture เพื่อให้เทสเรียกตรวจตัวกันนี้เองได้"""
    leaked = (tax_rules.TAX_RULES != _PRISTINE_TAX_RULES
              or tax_rules.RULES_INFO != _PRISTINE_RULES_INFO
              or rules_store.DEFAULT_TAX_RULES != _PRISTINE_TAX_RULES)
    if leaked:
        tax_rules.TAX_RULES = copy.deepcopy(_PRISTINE_TAX_RULES)
        tax_rules.RULES_INFO = copy.deepcopy(_PRISTINE_RULES_INFO)
        rules_store.DEFAULT_TAX_RULES = copy.deepcopy(_PRISTINE_TAX_RULES)
    return leaked


@pytest.fixture(autouse=True)
def tax_rules_not_leaked(request):
    """หลังทุกเทส tax_rules.TAX_RULES และ RULES_INFO ต้องเท่ากับค่าตั้งต้น
    ถ้าเทสหนึ่งแทนที่ค่าแล้วไม่คืน (ไม่ใช้ monkeypatch) เทสที่รันต่อจะได้ภาษีผิดตามลำดับการรัน
    เช่น test_line_verified_cases.py ล้มโดยที่ไม่ได้ทำอะไรผิด — fixture นี้ชี้ตัวเทสที่ทำรั่วแทน
    autouse ของ conftest ถูกตั้งก่อน monkeypatch ของเทส จึงตรวจหลังจาก monkeypatch คืนค่าแล้ว"""
    yield
    # คืนค่าก่อน fail เพื่อไม่ให้เทสถัดไปพังตามเป็นทอด ๆ
    if restore_if_leaked():
        pytest.fail(f"{request.node.nodeid} ทิ้ง TAX_RULES หรือ RULES_INFO ที่ถูกแก้ไว้หลังจบเทส — "
                    f"ใช้ monkeypatch.setattr/setitem แทนการกำหนดค่าตรง ๆ", pytrace=False)
