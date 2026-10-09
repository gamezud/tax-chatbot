"""
เทส utils/firebase_db.py ตัวจริง (test_webhook.py ใช้โมดูลปลอมแทน จึงไม่ครอบไฟล์นี้)

โหลดไฟล์ด้วยชื่อโมดูลอื่น ("real_firebase_db") เพื่อไม่ชนกับโมดูลปลอม utils.firebase_db ใน sys.modules
ก่อนโหลด patch credentials.Certificate ให้โยน error ซึ่งเป็นจุดเดียวที่อ่าน firebase_key.json
→ FirebaseManager() ตอน import ตกไป except ได้ db = None ไม่อ่านไฟล์ key และไม่ต่อ Firestore
"""
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import firebase_admin.credentials
import pytest

MODULE_PATH = Path(__file__).resolve().parent.parent / "utils" / "firebase_db.py"


@pytest.fixture
def real_firebase_db(monkeypatch):
    certificate = MagicMock(name="Certificate", side_effect=RuntimeError("เทสห้ามอ่าน firebase_key.json"))
    monkeypatch.setattr(firebase_admin.credentials, "Certificate", certificate)
    spec = importlib.util.spec_from_file_location("real_firebase_db", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.certificate_mock = certificate
    return module


def test_init_without_key_sets_db_none(real_firebase_db):
    real_firebase_db.certificate_mock.assert_called_once_with("firebase_key.json")
    assert real_firebase_db.firebase_client.db is None
    assert real_firebase_db.firebase_client.save_tax_report("U", {"tax_year": 2569}) is False


def test_save_tax_report_writes_tax_year_in_single_add(real_firebase_db):
    client = real_firebase_db.firebase_client
    client.db = MagicMock(name="firestore_db")
    assert client.save_tax_report("TEST_USER", {"tax_year": 2569, "total_income": 360000.0}) is True
    # เขียนครั้งเดียวเหมือนเดิม ไม่เพิ่มการเรียก Firestore (CLAUDE.md กฎเหล็กข้อ 1)
    client.db.collection.assert_called_once_with("tax_history")
    client.db.collection.return_value.add.assert_called_once()
    doc = client.db.collection.return_value.add.call_args.args[0]
    assert doc["tax_year"] == 2569
    assert doc["user_id"] == "TEST_USER"
    assert doc["total_income"] == 360000.0
