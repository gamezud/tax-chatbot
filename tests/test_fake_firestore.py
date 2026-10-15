"""
เทสของ Firestore ปลอม (fake_firestore.py) — ตัวปลอมต้องบังคับกฎของ transaction จริง ไม่ใช่ยอมรับทุกอย่าง
ถ้าเทสในไฟล์นี้ล้ม เทสการบันทึกของหน้า admin (test_admin_save.py) จะเชื่อไม่ได้

ทุกเทสใช้ decorator จริง google.cloud.firestore.transactional ตัวเดียวกับที่ rules_store.save_config ใช้
"""
import datetime

import pytest
from google.api_core import exceptions
from google.cloud import firestore
from google.cloud.firestore_v1._helpers import ReadAfterWriteError

from fake_firestore import FakeFirestore

A = ("things", "a")
B = ("things", "b")


def test_installed_firestore_is_the_version_the_fake_was_written_for():
    # ตัวปลอมทำ method private ของ Transaction ที่ decorator ของ 2.29.0 เรียก
    assert firestore.__version__ == "2.29.0", "อัปเกรด google-cloud-firestore แล้ว ต้องตรวจ fake_firestore.py ใหม่"


def write_both(transaction, db, value):
    transaction.set(db.collection("things").document("a"), {"value": value})
    transaction.create(db.collection("things").document("b"), {"value": value})
    return value


def test_commit_writes_both_docs():
    db = FakeFirestore()
    assert firestore.transactional(write_both)(db.transaction(), db, 1) == 1
    assert db.doc(*A) == {"value": 1} and db.doc(*B) == {"value": 1}
    assert db.commits == 1 and db.violations == []


def test_failure_in_second_write_leaves_nothing_changed():
    db = FakeFirestore({A: {"value": 0}})
    db.fail_on_commit_write = B
    with pytest.raises(exceptions.ServiceUnavailable):
        firestore.transactional(write_both)(db.transaction(), db, 1)
    assert db.doc(*A) == {"value": 0}  # write แรกถูกทิ้งไปด้วย
    assert db.doc(*B) is None
    assert db.commits == 0 and db.rollbacks == 1


def test_writes_are_held_until_commit():
    db = FakeFirestore({A: {"value": 0}})

    def check_inside(transaction, db):
        transaction.set(db.collection("things").document("a"), {"value": 1})
        assert db.doc(*A) == {"value": 0}  # ยังไม่ commit คนอื่นยังเห็นค่าเดิม

    firestore.transactional(check_inside)(db.transaction(), db)
    assert db.doc(*A) == {"value": 1}


def test_read_after_write_is_rejected():
    db = FakeFirestore({A: {"value": 0}})

    def write_then_read(transaction, db):
        ref = db.collection("things").document("a")
        transaction.set(ref, {"value": 1})
        transaction.get(ref)

    with pytest.raises(ReadAfterWriteError):
        firestore.transactional(write_then_read)(db.transaction(), db)
    assert db.doc(*A) == {"value": 0} and db.rollbacks == 1


def test_write_outside_transaction_is_caught_even_if_swallowed():
    db = FakeFirestore()
    try:
        db.collection("things").document("a").set({"value": 1})
    except Exception:
        pass  # แบบที่ except กว้างในโค้ดจะทำ
    assert db.violations == [("set", A)]
    assert db.doc(*A) is None


def test_create_over_existing_doc_fails_and_writes_nothing():
    db = FakeFirestore({B: {"value": "old"}})
    with pytest.raises(exceptions.AlreadyExists):
        firestore.transactional(write_both)(db.transaction(), db, 1)
    assert db.doc(*A) is None and db.doc(*B) == {"value": "old"}


def test_concurrent_write_aborts_and_real_decorator_retries():
    db = FakeFirestore({A: {"value": 0}})
    db.after_transaction_read = lambda fake: fake.put(*A, {"value": 10})  # อีกแท็บเขียนระหว่างอ่านกับ commit
    seen = []

    def increment(transaction, db):
        ref = db.collection("things").document("a")
        value = next(iter(transaction.get(ref))).to_dict()["value"]
        seen.append(value)
        transaction.set(ref, {"value": value + 1})

    firestore.transactional(increment)(db.transaction(), db)
    assert seen == [0, 10]          # รอบแรกชน decorator จริงเรียกใหม่ รอบสองอ่านค่าใหม่
    assert db.begins == 2
    assert db.doc(*A) == {"value": 11}  # ไม่ทับค่าของอีกแท็บด้วยค่าที่คำนวณจากของเก่า


def test_server_timestamp_replaced_at_commit():
    db = FakeFirestore()

    def stamp(transaction, db):
        transaction.set(db.collection("things").document("a"), {"at": firestore.SERVER_TIMESTAMP})

    firestore.transactional(stamp)(db.transaction(), db)
    assert isinstance(db.doc(*A)["at"], datetime.datetime)


def test_query_returns_docs_with_field_only():
    db = FakeFirestore({("h", "1"): {"version": 1}, ("h", "2"): {"version": 2}, ("h", "x"): {"other": 1}})

    def read(transaction, db):
        return [s.id for s in transaction.get(db.collection("h").order_by("version"))]

    assert firestore.transactional(read)(db.transaction(), db) == ["1", "2"]


def test_plain_reads_work_and_failing_reads_raise():
    db = FakeFirestore({A: {"value": 0}})
    snapshot = db.collection("things").document("a").get(timeout=1, retry=None)
    assert snapshot.exists and snapshot.to_dict() == {"value": 0}
    assert not db.collection("things").document("zzz").get().exists
    with pytest.raises(RuntimeError):
        FakeFirestore(fail_reads=RuntimeError()).collection("things").document("a").get()


def test_unknown_api_is_not_silently_accepted():
    # ไม่ใช่ MagicMock — method ที่ไม่ได้ทำไว้ต้องล้ม
    with pytest.raises(AttributeError):
        FakeFirestore().batch()
