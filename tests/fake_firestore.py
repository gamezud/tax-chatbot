"""
Firestore ปลอมที่เก็บข้อมูลในหน่วยความจำจริง — ใช้เทสการบันทึกของหน้า admin (ก้อน 5B)

ทำไมไม่ใช้ MagicMock (แบบ fake_db ใน test_rules_loading.py)
  MagicMock ยอมรับทุกการเรียกและไม่เก็บอะไร จึงพิสูจน์ไม่ได้ว่า "เขียนสอง doc แล้วสำเร็จหรือล้มพร้อมกัน"
  หรือว่าสิ่งที่ฟอร์มเขียน ตัวโหลด (load_effective_rules) อ่านกลับได้จริง ตัวปลอมนี้เก็บ doc จริงและบังคับกฎของ transaction

ใช้ decorator จริงของไลบรารี (google.cloud.firestore.transactional) ไม่ได้เขียนการลองใหม่เลียนแบบเอง
  decorator นั้นเรียก method private ของ Transaction: _clean_up, _begin, _id, _commit, _rollback,
  _max_attempts, _read_only — FakeTransaction ทำ method เหล่านี้ให้ตรงกับ google-cloud-firestore 2.29.0
  (ล็อกใน requirements.txt) ถ้าอัปเกรดไลบรารี test_fake_firestore.py จะล้มและบอกให้ตรวจไฟล์นี้ใหม่

กฎที่ตัวปลอมบังคับ (เหมือนของจริง)
  - เขียนใน transaction (set/create) แค่เก็บไว้ ยังไม่เปลี่ยนข้อมูล — commit ใช้กับสำเนาทั้งชุดแล้วค่อยสลับแทน
    write ไหนล้ม (fail_on_commit_write) สำเนาถูกทิ้ง ไม่มี doc ไหนเปลี่ยน
  - อ่านหลังเขียนใน transaction เดียวกัน → ReadAfterWriteError (คลาสเดียวกับของจริง)
  - create ทับ doc ที่มีอยู่ → AlreadyExists ตอน commit ไม่มีอะไรถูกเขียน
  - doc ที่ transaction อ่านไว้ถูกเขียนจากที่อื่นก่อน commit → Aborted แล้ว decorator จริงเรียกฟังก์ชันใหม่
  - เขียนตรงที่ DocumentReference (นอก transaction) → บันทึกลง violations แล้วโยน AssertionError
    (บันทึกไว้ด้วยเพราะ except Exception ในโค้ดอาจกลืน error ไป) — อ่านนอก transaction ทำได้ตามปกติ

ต่างจากของจริง
  - ของจริง (server SDK อย่าง Python) ล็อก doc ที่อ่าน (pessimistic) การเขียนอื่นต้องรอ หรือ transaction ถูก ABORTED
    ตัวปลอมตรวจตอน commit (optimistic — คนอื่นชนะเสมอ) ผลต่อโค้ดเราเหมือนกัน: ถูกเรียกใหม่ อ่านใหม่ แล้วเทียบลายนิ้วมือ
  - ไม่มีเครือข่าย ล็อก หรือ timeout จริง · SERVER_TIMESTAMP แทนด้วยเวลาในเครื่องตอน commit
  - query รองรับแค่ collection().order_by(field) (คืนเฉพาะ doc ที่มี field นั้น เรียงแบบคร่าว ๆ)
  - ความเป็น atomic เป็นการรับประกันของ Firestore เทสพิสูจน์ได้แค่ว่าโค้ดเราเขียนทั้งสอง doc ใน transaction เดียว
    และตรวจลายนิ้วมือข้างใน — ยังต้องทดสอบกับ Firestore จริงแบบ end-to-end
"""
import copy
import datetime
import itertools

from google.api_core import exceptions
from google.cloud import firestore
from google.cloud.firestore_v1._helpers import ReadAfterWriteError
from google.cloud.firestore_v1.base_transaction import MAX_ATTEMPTS


class FakeFirestore:
    def __init__(self, docs=None, fail_reads=None):
        """docs: {(collection, id): dict} ข้อมูลตั้งต้น · fail_reads: exception ที่การอ่านทุกครั้งโยน (จำลองอ่านไม่ได้)"""
        self.docs = {}
        self.revisions = {}           # เพิ่มทุกครั้งที่ doc ถูกเขียน ใช้ตรวจการชนตอน commit
        self.violations = []          # การเขียนนอก transaction
        self.fail_reads = fail_reads
        self.fail_on_commit_write = None   # (collection, id) ที่ commit จะล้มเมื่อถึง write นั้น
        self.after_transaction_read = None  # callback(fake) เรียกครั้งเดียวหลังการอ่านครั้งแรกใน transaction
        self._hook_fired = False
        self.begins = 0               # จำนวนรอบที่ decorator เริ่ม transaction (ลองใหม่ = มากกว่า 1)
        self.commits = 0
        self.rollbacks = 0
        self._ids = itertools.count(1)
        for (collection, doc_id), data in (docs or {}).items():
            self.put(collection, doc_id, data)

    # ---- ใช้ในเทส ----
    def put(self, collection, doc_id, data):
        """เขียนจาก "ที่อื่น" (Firebase Console หรือแท็บ/เซิร์ฟเวอร์อื่น) — ไม่นับเป็นการเขียนของโค้ดที่ทดสอบ"""
        path = (collection, str(doc_id))
        self.docs[path] = copy.deepcopy(data)
        self.revisions[path] = self.revisions.get(path, 0) + 1

    def doc(self, collection, doc_id):
        data = self.docs.get((collection, str(doc_id)))
        return copy.deepcopy(data)

    def collection_docs(self, collection):
        return {doc_id: copy.deepcopy(data) for (name, doc_id), data in self.docs.items() if name == collection}

    # ---- API แบบ Firestore ----
    def collection(self, name):
        return FakeCollection(self, name)

    def transaction(self, max_attempts=MAX_ATTEMPTS, read_only=False):
        return FakeTransaction(self, max_attempts, read_only)

    def _snapshot(self, path):
        if self.fail_reads is not None:
            raise self.fail_reads
        return FakeSnapshot(path[1], self.docs.get(path))


class FakeSnapshot:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self.exists = data is not None
        self._data = copy.deepcopy(data)

    def to_dict(self):
        return copy.deepcopy(self._data)


class FakeCollection:
    def __init__(self, db, name):
        self._db = db
        self.name = name

    def document(self, doc_id):
        return FakeDocumentReference(self._db, (self.name, str(doc_id)))

    def order_by(self, field, direction="ASCENDING"):
        return FakeQuery(self._db, self.name, field, direction)


class FakeDocumentReference:
    def __init__(self, db, path):
        self._db = db
        self.path = path

    def get(self, timeout=None, retry=None):
        # อ่านนอก transaction (ตัวโหลด, saved_state) ทำได้
        return self._db._snapshot(self.path)

    def _write_outside_transaction(self, method):
        self._db.violations.append((method, self.path))
        raise AssertionError(f"{method} {self.path} นอก transaction")

    def set(self, *args, **kwargs):
        self._write_outside_transaction("set")

    def create(self, *args, **kwargs):
        self._write_outside_transaction("create")

    def update(self, *args, **kwargs):
        self._write_outside_transaction("update")

    def delete(self, *args, **kwargs):
        self._write_outside_transaction("delete")


class FakeQuery:
    def __init__(self, db, collection, field, direction):
        self._db = db
        self.collection = collection
        self.field = field
        self.direction = direction

    def _snapshots(self):
        if self._db.fail_reads is not None:
            raise self._db.fail_reads
        found = [(doc_id, data) for (name, doc_id), data in self._db.docs.items()
                 if name == self.collection and isinstance(data, dict) and self.field in data]

        def key(item):
            value = item[1][self.field]
            is_number = isinstance(value, (int, float)) and not isinstance(value, bool)
            return (0, value, "") if is_number else (1, 0, repr(value))

        found.sort(key=key, reverse=(self.direction == "DESCENDING"))
        return [FakeSnapshot(doc_id, data) for doc_id, data in found]


class FakeTransaction:
    """Transaction ปลอมที่ decorator จริง (google.cloud.firestore.transactional) ใช้ได้"""

    def __init__(self, db, max_attempts, read_only):
        self._db = db
        self._max_attempts = max_attempts
        self._read_only = read_only
        self._id = None
        self._write_pbs = []        # (ชนิด, path, ข้อมูล) — ชื่อเดียวกับของจริง
        self._read_revisions = {}

    # ---- private protocol ที่ _Transactional.__call__ / _pre_commit เรียก ----
    @property
    def in_progress(self):
        return self._id is not None

    def _clean_up(self):
        self._write_pbs = []
        self._read_revisions = {}
        self._id = None

    def _begin(self, retry_id=None):
        if self.in_progress:
            raise ValueError("transaction เริ่มไปแล้ว")
        self._db.begins += 1
        self._id = f"fake-transaction-{next(self._db._ids)}".encode()

    def _rollback(self):
        if not self.in_progress:
            raise ValueError("ไม่มี transaction ที่ทำงานอยู่")
        self._db.rollbacks += 1
        self._clean_up()

    def _commit(self):
        if not self.in_progress:
            raise ValueError("ไม่มี transaction ที่ทำงานอยู่")
        db = self._db
        # ล้มแล้วไม่ล้าง (เหมือนของจริง) — decorator ล้างเองตอนเริ่มรอบใหม่ หรือเรียก _rollback
        for path, revision in self._read_revisions.items():
            if db.revisions.get(path, 0) != revision:
                raise exceptions.Aborted(f"{path} ถูกเขียนจากที่อื่นระหว่าง transaction")

        staged_docs = copy.deepcopy(db.docs)
        staged_revisions = dict(db.revisions)
        now = datetime.datetime.now(datetime.timezone.utc)
        for kind, path, data in self._write_pbs:
            if db.fail_on_commit_write == path:
                raise exceptions.ServiceUnavailable(f"จำลองการเขียน {path} ล้ม")
            if kind == "create" and path in staged_docs:
                raise exceptions.AlreadyExists(f"{path} มีอยู่แล้ว")
            staged_docs[path] = _replace_server_timestamp(data, now)
            staged_revisions[path] = staged_revisions.get(path, 0) + 1
        db.docs, db.revisions = staged_docs, staged_revisions
        db.commits += 1
        self._clean_up()
        return []

    # ---- API สาธารณะ ----
    def get(self, ref_or_query, retry=None, timeout=None):
        if not self.in_progress:
            raise ValueError("อ่านใน transaction ที่ยังไม่เริ่ม")
        if self._write_pbs:
            raise ReadAfterWriteError("Attempted read after write in a transaction.")
        if isinstance(ref_or_query, FakeDocumentReference):
            snapshots = [self._db._snapshot(ref_or_query.path)]
            paths = [ref_or_query.path]
        elif isinstance(ref_or_query, FakeQuery):
            snapshots = ref_or_query._snapshots()
            paths = [(ref_or_query.collection, s.id) for s in snapshots]
        else:
            raise ValueError("อ่านได้เฉพาะ DocumentReference หรือ Query")
        for path in paths:
            self._read_revisions.setdefault(path, self._db.revisions.get(path, 0))
        if self._db.after_transaction_read is not None and not self._db._hook_fired:
            self._db._hook_fired = True
            self._db.after_transaction_read(self._db)
        return iter(snapshots)

    def set(self, reference, document_data, merge=False):
        self._add_write("set", reference, document_data)

    def create(self, reference, document_data):
        self._add_write("create", reference, document_data)

    def _add_write(self, kind, reference, data):
        if not self.in_progress:
            raise ValueError("เขียนใน transaction ที่ยังไม่เริ่ม")
        # สำเนาตอนสั่งเขียน (ของจริงแปลงเป็น protobuf ตอนนี้) — โค้ดแก้ dict ทีหลังต้องไม่มีผล
        self._write_pbs.append((kind, reference.path, _copy_data(data)))


def _copy_data(data, now=None):
    """สำเนา dict/list ทุกชั้น — ไม่ใช้ deepcopy เพราะจะได้ SERVER_TIMESTAMP ตัวใหม่ที่เทียบ is ไม่ได้
    now: ถ้าส่งมา แทน SERVER_TIMESTAMP ด้วยเวลานั้น (ตอน commit)"""
    if data is firestore.SERVER_TIMESTAMP and now is not None:
        return now
    if isinstance(data, dict):
        return {key: _copy_data(value, now) for key, value in data.items()}
    if isinstance(data, list):
        return [_copy_data(value, now) for value in data]
    return data


def _replace_server_timestamp(data, now):
    return _copy_data(data, now)
