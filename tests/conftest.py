"""ตั้งค่าที่ต้องเกิดก่อนไฟล์ test ใด ๆ ถูกโหลด

ใส่โมดูลปลอมแทน utils.firebase_db ลง sys.modules ก่อนใครจะ import app
  ไฟล์จริงสร้าง firebase_client = FirebaseManager() ทันทีที่ถูก import ซึ่งอ่าน firebase_key.json
  และสร้าง client ที่เขียน Firestore จริงได้ โมดูลปลอมทำให้ FirebaseManager() ไม่ถูกสร้างเลย
  pytest โหลด conftest.py ก่อนไฟล์ test ทุกไฟล์ ไฟล์ไหน import app ก่อนก็ได้โมดูลปลอมเหมือนกัน
  (เดิมอยู่ใน test_webhook.py ย้ายมาเมื่อ test_liff_ocr.py ต้อง import app ด้วย)
  ** โมดูลปลอมนี้อยู่ใน sys.modules ตลอดการรัน pytest ทั้งชุด **
  test_firebase_db.py ทดสอบไฟล์จริงโดยโหลดด้วยชื่อโมดูลอื่น จึงไม่ชนกับโมดูลปลอมนี้
"""
import sys
import types
from unittest.mock import MagicMock

# ถ้ามีใคร import ของจริงไปก่อน FirebaseManager() ได้ทำงานไปแล้ว โมดูลปลอมจะสายเกินไป
assert "app" not in sys.modules, "app ถูก import ไปก่อน conftest.py"
assert "utils.firebase_db" not in sys.modules, "utils.firebase_db ตัวจริงถูก import ไปก่อนแล้ว"
_fake_firebase_db = types.ModuleType("utils.firebase_db")
_fake_firebase_db.firebase_client = MagicMock(name="fake_module_firebase_client")
sys.modules["utils.firebase_db"] = _fake_firebase_db
