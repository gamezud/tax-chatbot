"""
ตรวจและรวมค่าที่ระบบ Admin แก้ (เก็บใน Firestore) เข้ากับค่าตั้งต้นใน utils/tax_rules.py

ไม่ import firebase — ฟังก์ชันที่อ่าน Firestore (load_effective_rules) รับ db เป็น argument เทสจึงส่ง db ปลอมได้
ค่าที่แก้ (override) มีรูปเดียวกับ TAX_RULES แต่มีเฉพาะ key ที่แก้ได้ และ tax_brackets เป็น list ของ
{"upper": ..., "rate": ...} เพราะ Firestore เก็บ list ซ้อน list ไม่ได้

กติกา: ผิดข้อเดียว → ปฏิเสธทั้งชุด (build_rules คืน None) ผู้เรียกต้องใช้ค่าตั้งต้นแทน
"""
import copy
import hashlib
import json
import math
import threading
import time

from utils import tax_rules

# ค่าตั้งต้นจาก tax_rules.py เก็บสำเนาไว้ตอน import ก่อนที่ใครจะแทนที่ tax_rules.TAX_RULES
# (การแทนที่ทำผ่านโมดูลนี้ตอนเริ่มเซิร์ฟเวอร์ โมดูลนี้จึงถูก import ก่อนเสมอ)
# ห้ามแก้ dict นี้ในที่ — ทุกฟังก์ชันคืนสำเนาใหม่
DEFAULT_TAX_RULES = copy.deepcopy(tax_rules.TAX_RULES)

# ชนิดของค่าที่แก้ได้
MONEY = "money"                  # จำนวนเงิน ≥ 0
RATE = "rate"                    # อัตรา 0–1
WHOLE_PERCENT = "whole_percent"  # อัตรา 0–1 ที่เป็นจำนวนเต็ม % เพราะข้อความ LINE/PDF แสดงด้วย :.0f
BRACKETS = "brackets"            # ขั้นบันไดภาษี

# key ที่ admin แก้ได้ (รูปเดียวกับ TAX_RULES) — key ที่ไม่อยู่ที่นี่แก้ไม่ได้
# WHOLE_PERCENT: rental_rate แสดงใน handle_rental, online_sale_rate ใน handle_online,
#                อัตราขั้นบันไดใน generate_tax_planning_advice (ไปถึงทั้ง LINE และ PDF)
EDITABLE_SCHEMA = {
    "expense": {
        "salary_rate": RATE,
        "salary_cap": MONEY,
        "rental_rate": {
            "building": WHOLE_PERCENT,
            "agricultural_land": WHOLE_PERCENT,
            "land": WHOLE_PERCENT,
        },
        "online_sale_rate": WHOLE_PERCENT,
    },
    "deduction": {
        "personal": MONEY,
        "spouse": MONEY,
        "child_first": MONEY,
        "child_second_onward": MONEY,
        "parent_per_person": MONEY,
        "social_security_cap": MONEY,
        "health_insurance_cap": MONEY,
        "life_health_combined_cap": MONEY,
        "home_loan_interest_cap": MONEY,
        "rmf_income_rate": RATE,
        "rmf_cap": MONEY,
        "retirement_group_cap": MONEY,
        "thai_esg_income_rate": RATE,
        "thai_esg_cap": MONEY,
        "donation_rate": RATE,
    },
    "tax_brackets": BRACKETS,
    "method_2": {
        "rate": RATE,
        "income_threshold": MONEY,
        "exempt_tax_up_to": MONEY,
    },
}

# key ที่มีใน TAX_RULES แต่ห้ามแก้ผ่านระบบ Admin (เทสตรวจว่าทุก key ใน TAX_RULES อยู่ในที่ใดที่หนึ่ง)
# - tax_year: ชื่อของชุดกติกา เปลี่ยนปี = แก้ docs + โค้ด
# - rental_rate.unmatched: ค่าชั่วคราวของปัญหา #8 ไม่ใช่ตัวเลขกฎหมาย
# - parent_max_count: จำนวนคน เป็นโครงสร้างของสิทธิ
# - ssf_last_tax_year: ผูกกับข้อความที่อ้างกฎกระทรวงใน handle_deductions
NOT_EDITABLE = {
    ("tax_year",),
    ("expense", "rental_rate", "unmatched"),
    ("deduction", "parent_max_count"),
    ("deduction", "ssf_last_tax_year"),
}

# ความสัมพันธ์ข้าม key: (key ที่ต้องไม่เกิน, key เพดาน)
CROSS_KEY_LIMITS = [
    (("deduction", "health_insurance_cap"), ("deduction", "life_health_combined_cap")),
    (("deduction", "rmf_cap"), ("deduction", "retirement_group_cap")),
]


class RuleError(str):
    """ข้อความข้อผิดพลาด — ยังเป็น str ข้อความเดิมทุกตัวอักษร (ใช้ใน log และเทสเดิมได้)
    แต่บอกเพิ่มว่าผิดที่ช่องไหน ให้หน้าฟอร์มแสดงข้อผิดพลาดข้างช่องนั้นได้โดยไม่ต้องแยกข้อความเอง
    field:   ชื่อช่อง เช่น "deduction.personal", "tax_brackets.3.upper" — None ถ้าไม่ได้ผิดที่ช่องเดียว
    detail:  ข้อความส่วนที่ไม่มีชื่อ key
    related: key อีกตัวของข้อผิดพลาดข้าม key (เช่น เพดานรวม)"""

    def __new__(cls, text, field=None, detail=None, related=None):
        error = super().__new__(cls, text)
        error.field = field
        error.detail = detail if detail is not None else text
        error.related = related
        return error


def build_rules(override):
    """
    รวม override เข้ากับค่าตั้งต้นแล้วตรวจ
    คืน (rules, errors) — ผ่าน: (dict ใหม่, []) / ไม่ผ่าน: (None, รายการข้อผิดพลาดทุกข้อ)
    """
    if not isinstance(override, dict):
        return None, ["ค่าที่แก้ต้องเป็นกลุ่มของค่า (dict)"]

    errors = []
    _check_override_keys(override, EDITABLE_SCHEMA, DEFAULT_TAX_RULES, (), errors)
    if errors:
        return None, errors

    override = dict(override)
    if "tax_brackets" in override:
        override["tax_brackets"] = brackets_from_firestore(override["tax_brackets"], errors)
        if errors:
            return None, errors

    rules = merge_rules(DEFAULT_TAX_RULES, override)
    errors = validate_rules(rules)
    if errors:
        return None, errors
    return rules, []


def to_override(rules):
    """ดึงเฉพาะ key ที่แก้ได้จาก TAX_RULES ทั้งก้อน เป็นรูปที่เก็บลง Firestore ได้"""
    return _extract(rules, EDITABLE_SCHEMA)


def merge_rules(base, override):
    """
    รวมลงไปถึง dict ชั้นใน ได้ dict ใหม่ ไม่แก้ base หรือ override
    list (tax_brackets) แทนทั้งก้อน ไม่รวมทีละขั้น
    """
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_rules(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def brackets_to_firestore(brackets):
    return [{"upper": upper, "rate": rate} for upper, rate in brackets]


def brackets_from_firestore(items, errors):
    """แปลงกลับเป็น list ของ (เพดานบน, อัตรา) แบบเดียวกับ tax_rules.py — รูปผิดเพิ่มข้อความลง errors แล้วคืน None"""
    if not isinstance(items, list):
        errors.append("tax_brackets ต้องเป็นรายการของขั้น")
        return None
    brackets = []
    for i, item in enumerate(items, start=1):
        if not isinstance(item, dict) or set(item) != {"upper", "rate"}:
            errors.append(f"tax_brackets ขั้นที่ {i} ต้องมี upper และ rate เท่านั้น")
            continue
        brackets.append((item["upper"], item["rate"]))
    return None if errors else brackets


def validate_rules(rules):
    """ตรวจ TAX_RULES ทั้งก้อน คืนรายการข้อผิดพลาดทุกข้อ (ว่าง = ผ่าน)"""
    errors = []
    _validate_node(rules, EDITABLE_SCHEMA, (), errors)
    if errors:
        # ค่าบางตัวผิดหรือหายไป การเทียบข้าม key จะได้ข้อความซ้ำซ้อน
        return errors

    for (small_path, cap_path) in CROSS_KEY_LIMITS:
        small = _get(rules, small_path)
        cap = _get(rules, cap_path)
        if small > cap:
            # ไม่ใส่ตัวเลขในข้อความ — ข้อความนี้ลง log ตอนเปิดเซิร์ฟเวอร์ด้วย (log บอกได้แค่ชื่อ key)
            errors.append(RuleError(f"{_name(small_path)} ต้องไม่เกิน {_name(cap_path)}",
                                    field=_name(small_path), detail="ต้องไม่เกิน", related=_name(cap_path)))
    return errors


def changed_keys(rules):
    """ชื่อ key ที่แก้ได้ซึ่งค่าใน rules ต่างจากค่าตั้งต้น (tax_brackets นับเป็น key เดียว)"""
    return [_name(path) for path in _schema_paths(EDITABLE_SCHEMA, ())
            if _get(rules, path) != _get(DEFAULT_TAX_RULES, path)]


# ---------------------------------------------------------------------------
# โหลดค่าที่แก้จาก Firestore — เรียกครั้งเดียวตอนเปิดเซิร์ฟเวอร์ ห้ามเรียกใน webhook (กฎเหล็กข้อ 1)
# ---------------------------------------------------------------------------
# doc เดียว: tax_rules_config/active (ระบบ Admin เขียนตามรูปนี้)
#   based_on_tax_year: int  ต้องตรงกับ tax_year ของค่าตั้งต้น ไม่งั้นค่าที่แก้ไว้ของปีเก่าจะติดไปกับโค้ดปีใหม่
#   version:           int ≥ 1  เพิ่มทีละ 1 ทุกครั้งที่บันทึก ใช้ผูกผลคำนวณกับชุดกติกา
#   override:          dict รูปเดียวกับ to_override() (มีเฉพาะ key ที่แก้ได้)
# field อื่นในระดับบนสุด (เช่น เวลา ผู้แก้ แหล่งอ้างอิง) ตัวโหลดไม่อ่าน จึงไม่ปฏิเสธ
# — ชื่อสะกดผิดของ 3 field ข้างบนถูกจับอยู่แล้ว เพราะ field ที่ต้องมีจะหายไป
CONFIG_COLLECTION = "tax_rules_config"
CONFIG_DOCUMENT = "active"

# เวลารออ่าน doc สูงสุด เกินแล้วเปิดเซิร์ฟเวอร์ด้วยค่าตั้งต้น
# การอ่านปกติใช้หลายร้อยมิลลิวินาที แต่นี่เป็นการเรียก Firestore ครั้งแรกหลังเปิดโปรแกรม
# ต้องต่อ TLS และขอ token ของ service account ก่อน จึงเผื่อไว้มาก ค่านี้ไม่อยู่ในงบ 5 วินาทีเพราะไม่ได้อยู่ใน webhook
# log บอกเวลาที่ใช้จริงทุกครั้ง ใช้ตรวจว่าค่านี้เหมาะหรือไม่
READ_TIMEOUT_SECONDS = 10.0


def load_effective_rules(db):
    """
    คืน (rules, info) — ไม่โยน exception ไม่ว่ากรณีใด พิมพ์ log [TAX-RULES] หนึ่งบรรทัด
    info = {"source": "default" หรือ "firestore", "version": int หรือ None, "changed_keys": [...]}
    ผลมีได้ 4 แบบ: ไม่มี doc / doc ถูก / doc ผิด / อ่านไม่ได้ — ทุกแบบยกเว้น doc ถูกใช้ค่าตั้งต้น
    log บอกแค่ชื่อ key ไม่พิมพ์ค่าใน doc และไม่พิมพ์ข้อความของ exception (อาจมีเนื้อหาของ doc)
    """
    started = time.monotonic()
    if db is None:
        return _use_defaults(f"❌ [TAX-RULES] ไม่ได้เชื่อม Firestore — ใช้ค่าตั้งต้นในโค้ด{_elapsed(started)}")
    try:
        doc = read_config_doc(db)
    except Exception as e:
        return _use_defaults(f"❌ [TAX-RULES] อ่านค่าที่แก้จาก Firestore ไม่ได้ ({type(e).__name__}) "
                             f"— ใช้ค่าตั้งต้นในโค้ด{_elapsed(started)}")

    if doc is None:
        return _use_defaults(f"ℹ️ [TAX-RULES] ไม่มีค่าที่แก้ใน Firestore — ใช้ค่าตั้งต้นในโค้ด "
                             f"(ปีภาษี {DEFAULT_TAX_RULES['tax_year']}){_elapsed(started)}")

    rules, errors = rules_from_doc(doc)
    if errors:
        return _use_defaults(f"❌ [TAX-RULES] ค่าที่แก้ใน Firestore ผิด — ใช้ค่าตั้งต้นในโค้ดแทน: "
                             f"{' / '.join(errors)}{_elapsed(started)}")

    changed = changed_keys(rules)
    changed_text = f"แก้ {len(changed)} รายการ: {', '.join(changed)}" if changed else "ไม่ต่างจากค่าตั้งต้น"
    print(f"✅ [TAX-RULES] ใช้ค่าที่แก้จาก Firestore version {doc['version']} — {changed_text}{_elapsed(started)}")
    return rules, {"source": "firestore", "version": doc["version"], "changed_keys": changed}


def read_config_doc(db):
    """คืน dict ของ doc หรือ None ถ้าไม่มี doc — อ่านไม่ได้หรือเกินเวลาโยน exception
    ใช้ทั้งตัวโหลดตอนเปิดเซิร์ฟเวอร์และหน้าแก้ค่าของ admin (timeout เดียวกัน) — ห้ามเรียกใน webhook"""
    timeout = READ_TIMEOUT_SECONDS

    def read():
        # retry=None: ลองครั้งเดียว ถ้าใช้ retry ตั้งต้น การลองซ้ำจะทำให้รอนานเกิน timeout ได้
        snapshot = db.collection(CONFIG_COLLECTION).document(CONFIG_DOCUMENT).get(timeout=timeout, retry=None)
        return snapshot.to_dict() if snapshot.exists else None

    # timeout ของ get คุมแค่ตัว RPC ครอบด้วย thread อีกชั้นให้แน่ใจว่าการเปิดเซิร์ฟเวอร์ไม่ค้างเกินเวลานี้
    return _call_with_timeout(read, timeout)


def _call_with_timeout(fn, timeout):
    """เรียก fn() ใน thread แยก รอไม่เกิน timeout วินาที แล้วคืนค่าที่ fn คืน
    เกินเวลาโยน TimeoutError (thread ยังทำงานต่อจนจบเอง) · exception ที่ fn โยนถูกโยนต่อให้ผู้เรียก
    daemon=True: ถ้า fn ค้าง thread นั้นไม่กันโปรแกรมปิด"""
    result = {}

    def run():
        try:
            result["value"] = fn()
        except Exception as e:
            result["error"] = e

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise TimeoutError()
    if "error" in result:
        raise result["error"]
    return result["value"]


def rules_from_doc(doc):
    """คืน (rules, errors) ตรวจทุก field แล้วรายงานข้อผิดพลาดทุกข้อพร้อมกัน"""
    if not isinstance(doc, dict):
        return None, ["doc ต้องเป็น map"]
    errors = []
    if not _is_int(doc.get("based_on_tax_year")) or doc["based_on_tax_year"] != DEFAULT_TAX_RULES["tax_year"]:
        errors.append("based_on_tax_year ไม่ตรงกับ tax_year ของค่าตั้งต้น")
    if not _is_int(doc.get("version")) or doc["version"] < 1:
        errors.append("version ต้องเป็นจำนวนเต็มตั้งแต่ 1")
    if "override" not in doc:
        errors.append("ไม่มี override")
        return None, errors

    rules, override_errors = build_rules(doc["override"])
    errors += override_errors
    if errors:
        return None, errors
    return rules, []


# ---------------------------------------------------------------------------
# บันทึกค่าที่แก้จากหน้า admin — เขียน active และประวัติใน transaction เดียว (สำเร็จพร้อมกันหรือไม่สำเร็จทั้งคู่)
# ---------------------------------------------------------------------------
# ประวัติ: tax_rules_history/{version} เนื้อหาเดียวกับ active + previous_version เขียนด้วย create (ห้ามทับของเดิม)
HISTORY_COLLECTION = "tax_rules_history"

# เวลารอบันทึกสูงสุด — begin + อ่าน active + อ่านประวัติ + commit ในโปรเซสที่ต่อ Firestore แล้ว
# (อ่านครั้งแรกหลังเปิดโปรแกรมวัดได้ราว 0.5 วินาที) หน้า admin ไม่อยู่ในงบ 5 วินาทีของ webhook
# begin/commit ของไลบรารีไม่รับ timeout จึงครอบทั้งก้อนด้วย thread (_call_with_timeout ตัวเดียวกับ read_config_doc)
SAVE_TIMEOUT_SECONDS = 15.0

# ลายนิ้วมือของ "ยังไม่มี doc"
NO_DOC_TOKEN = "none"


class VersionConflict(Exception):
    """doc ใน Firestore ไม่ใช่ตัวเดียวกับที่ฟอร์มเห็นตอนเปิด (มีการบันทึกหรือแก้จากที่อื่นในระหว่างนั้น)"""


class NoChanges(Exception):
    """ค่าที่จะบันทึกเท่ากับ doc ล่าสุด"""


def valid_version(doc):
    """version ของ doc ถ้าถูกรูป (int ≥ 1) ไม่อย่างนั้น None"""
    version = doc.get("version") if isinstance(doc, dict) else None
    return version if _is_int(version) and version >= 1 else None


def base_token(doc):
    """ลายนิ้วมือของ doc ที่ฟอร์มเห็น — ส่งไปกับฟอร์มแล้วเทียบใหม่ใน transaction ตอนบันทึก
    hash ทั้ง doc ไม่ใช่แค่ version: จับได้ทั้งการบันทึกจากแท็บอื่น และการแก้ใน Firebase Console ที่ไม่เปลี่ยนเลข version
    และใช้ได้กับ doc ที่ version ผิดรูป (ถ้าเทียบแค่ version doc แบบนั้นจะเทียบไม่ได้ แก้ผ่านเว็บไม่ได้เลย)
    default=repr: ค่าที่ json ไม่รู้จัก (เช่น เวลาจาก Firestore) แปลงเป็นข้อความที่คงที่สำหรับค่าเดียวกัน"""
    if doc is None:
        return NO_DOC_TOKEN
    text = json.dumps(doc, sort_keys=True, ensure_ascii=False, default=repr)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def save_config(db, rules, base, reference, note, restored_from=None):
    """บันทึก rules (ผ่าน build_rules มาแล้ว) เป็น active + ประวัติ
    คืน {"version", "previous_override", "restored_from"}
    restored_from: คำอ้างจากฟอร์มว่าย้อนจากเวอร์ชันไหน (int หรือ RESTORE_DEFAULT) — บันทึกเฉพาะเมื่อตรวจใน transaction แล้วตรง
    โยน VersionConflict / NoChanges / TimeoutError (ไม่รู้ผล) / exception อื่นจาก Firestore
    ห้ามเรียกใน webhook (กฎเหล็กข้อ 1) — ใช้จากหน้า admin เท่านั้น"""
    # import ตอนเรียก: ไฟล์นี้ไม่ผูกกับ Firestore ตอน import (ตัวโหลดรับ db เป็น argument)
    from google.cloud import firestore

    timeout = SAVE_TIMEOUT_SECONDS

    def run():
        # transactional ของไลบรารี: ถ้า commit ชนกับการเขียนอื่น (Aborted) จะเรียก _save_in_transaction ใหม่
        # สูงสุด 5 รอบ อ่านค่าใหม่ทุกรอบ — error อื่นยกเลิก transaction แล้วโยนต่อ ไม่มี doc ไหนถูกเขียน
        return firestore.transactional(_save_in_transaction)(
            db.transaction(), db, rules, base, reference, note, firestore.SERVER_TIMESTAMP, restored_from)

    # เกินเวลา (TimeoutError) = ไม่รู้ผล: commit อาจสำเร็จทีหลัง (ถ้าสำเร็จก็ได้ครบทั้งสอง doc) ผู้ใช้ต้องเปิดหน้า admin ตรวจ
    return _call_with_timeout(run, timeout)


def read_history_docs(db):
    """คืน [(doc id, dict)] ของทุก doc ใน tax_rules_history ไม่เรียง — อ่านไม่ได้หรือเกินเวลาโยน exception
    อ่านทั้งหมดไม่จำกัดจำนวน (หนึ่ง doc ต่อการกดบันทึกหนึ่งครั้ง) และไม่ใช้ order_by("version")
    เพราะ Firestore ตัด doc ที่ไม่มี field นั้นทิ้งเงียบ ๆ หน้าประวัติต้องเห็น doc ผิดรูปด้วย
    ไม่มี limit: ถ้าไม่มี order_by จะเรียงตาม doc id แบบข้อความ ("10" มาก่อน "2") ตัดผิดตัว"""
    timeout = READ_TIMEOUT_SECONDS

    def read():
        # อ่านจนครบใน thread — stream คืน generator ที่ต่อ Firestore ทีละชุดระหว่างวน
        return [(snapshot.id, snapshot.to_dict())
                for snapshot in db.collection(HISTORY_COLLECTION).stream(timeout=timeout, retry=None)]

    return _call_with_timeout(read, timeout)


# ---------------------------------------------------------------------------
# ย้อนเวอร์ชัน — ค่าของเวอร์ชันเก่าต้องผ่านการตรวจปัจจุบันใหม่ (rules_from_doc) ก่อนใช้
# ---------------------------------------------------------------------------
# restored_from ใน doc: int (เวอร์ชันที่ย้อนไป) หรือค่านี้ (ค่าตั้งต้นในโค้ด) · ไม่มี field = แก้ปกติ
RESTORE_DEFAULT = "default"


def read_history_doc(db, version):
    """dict ของ tax_rules_history/{version} หรือ None ถ้าไม่มี — อ่านไม่ได้หรือเกินเวลาโยน exception"""
    timeout = READ_TIMEOUT_SECONDS

    def read():
        snapshot = db.collection(HISTORY_COLLECTION).document(str(version)).get(timeout=timeout, retry=None)
        return snapshot.to_dict() if snapshot.exists else None

    return _call_with_timeout(read, timeout)


def restore_target(source, doc=None):
    """override ที่การย้อนไป source จะได้ หรือ None ถ้าใช้ไม่ได้
    source = RESTORE_DEFAULT → ค่าตั้งต้น · source = int → doc ต้องเป็นประวัติเวอร์ชันนั้น (version ตรง) และผ่าน rules_from_doc
    ใช้ที่เดียวทั้งตอนเปิดฟอร์มย้อน หน้ายืนยัน และใน transaction ตอนบันทึก — กติกาจึงตรงกันเสมอ"""
    if source == RESTORE_DEFAULT:
        return to_override(DEFAULT_TAX_RULES)
    if not _is_int(source) or source < 1 or valid_version(doc) != source:
        return None
    rules, errors = rules_from_doc(doc)
    return None if errors else to_override(rules)


def _save_in_transaction(transaction, db, rules, base, reference, note, server_timestamp, restored_from=None):
    """ถูกเรียกซ้ำได้เมื่อชนกัน — ห้าม print และห้ามแก้ argument
    Firestore บังคับให้อ่านทั้งหมดก่อนเขียน: อ่าน active และประวัติก่อน แล้วค่อย set/create"""
    active_ref = db.collection(CONFIG_COLLECTION).document(CONFIG_DOCUMENT)
    snapshot = next(iter(transaction.get(active_ref, timeout=READ_TIMEOUT_SECONDS, retry=None)))
    doc = snapshot.to_dict() if snapshot.exists else None
    if base_token(doc) != base:
        raise VersionConflict()

    new_override = to_override(rules)
    if doc is None:
        previous_override = to_override(DEFAULT_TAX_RULES)
        doc_rejected = False
    else:
        previous_rules, errors = rules_from_doc(doc)
        doc_rejected = bool(errors)
        # doc ที่ตัวโหลดปฏิเสธ: ฟอร์มเติมค่าตั้งต้นให้ จึงเทียบกับค่าตั้งต้น
        previous_override = to_override(DEFAULT_TAX_RULES if doc_rejected else previous_rules)
    # doc ที่ถูกปฏิเสธบันทึกทับได้แม้ค่าเท่าค่าตั้งต้น ไม่อย่างนั้นแก้ doc ที่ผิดให้กลับเป็นค่าตั้งต้นผ่านเว็บไม่ได้
    if new_override == previous_override and not doc_rejected:
        raise NoChanges()

    # เลขใหม่ต้องมากกว่าทั้ง active และทุกเลขในประวัติ — active อาจถูกแก้มือให้เลขต่ำลง หรือ version ผิดรูป
    # rules_version ใน tax_history จึงชี้ชุดกติกาเดียวเสมอ (order_by คืนเฉพาะ doc ที่มี field version)
    previous_version = valid_version(doc)
    history_query = db.collection(HISTORY_COLLECTION).order_by("version")
    history_versions = [valid_version(s.to_dict()) for s in
                        transaction.get(history_query, timeout=READ_TIMEOUT_SECONDS, retry=None)]
    new_version = max([previous_version or 0] + [v for v in history_versions if v is not None]) + 1

    # restored_from จากฟอร์มเป็นแค่คำอ้าง — อ่านเวอร์ชันนั้นใน transaction (ก่อนเขียน) แล้วบันทึกเฉพาะเมื่อค่าตรงทุกช่อง
    # ไม่ตรง (แก้ค่าหลังเติม, เลขปลอม, doc หายหรือถูกแก้) → บันทึกเป็นการแก้ปกติ ไม่ปฏิเสธ เพราะค่าผ่านการตรวจครบแล้ว
    restored = None
    source_doc = None
    if _is_int(restored_from) and restored_from >= 1:
        source_ref = db.collection(HISTORY_COLLECTION).document(str(restored_from))
        source_snapshot = next(iter(transaction.get(source_ref, timeout=READ_TIMEOUT_SECONDS, retry=None)))
        source_doc = source_snapshot.to_dict() if source_snapshot.exists else None
    if restored_from is not None and restore_target(restored_from, source_doc) == new_override:
        restored = restored_from

    record = {
        "based_on_tax_year": DEFAULT_TAX_RULES["tax_year"],
        "version": new_version,
        "override": new_override,
        "source_reference": reference,
        "note": note,
        "updated_at": server_timestamp,
    }
    if restored is not None:
        record["restored_from"] = restored
    transaction.set(active_ref, record)
    # create: ถ้ามี doc เลขนี้อยู่แล้ว (เช่น ถูกสร้างมือโดยไม่มี field version) commit ล้มทั้งก้อน ประวัติเดิมไม่ถูกทับ
    transaction.create(db.collection(HISTORY_COLLECTION).document(str(new_version)),
                       {**record, "previous_version": previous_version})
    return {"version": new_version, "previous_override": previous_override, "restored_from": restored}


def _use_defaults(message):
    print(message)
    # สำเนาใหม่ทุกครั้ง: ถ้าคืน DEFAULT_TAX_RULES ตรง ๆ แล้วมีใครแก้ TAX_RULES ในที่ ค่าตั้งต้นจะเปลี่ยนตาม
    return copy.deepcopy(DEFAULT_TAX_RULES), {"source": "default", "version": None, "changed_keys": []}


def _elapsed(started):
    return f" ({time.monotonic() - started:.2f}s)"


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _check_override_keys(override, schema, defaults, path, errors):
    for key, value in override.items():
        key_path = path + (key,)
        if isinstance(schema, dict) and key in schema:
            if isinstance(schema[key], dict):
                if not isinstance(value, dict):
                    errors.append(f"{_name(key_path)} ต้องเป็นกลุ่มของค่า (dict)")
                else:
                    _check_override_keys(value, schema[key], defaults.get(key, {}), key_path, errors)
        elif isinstance(defaults, dict) and key in defaults:
            errors.append(f"{_name(key_path)} ห้ามแก้ผ่านระบบ Admin")
        else:
            errors.append(f"{_name(key_path)} ไม่ใช่ key ที่รู้จัก")


def _validate_node(node, schema, path, errors):
    for key, kind in schema.items():
        key_path = path + (key,)
        if not isinstance(node, dict) or key not in node:
            errors.append(RuleError(f"ไม่มี {_name(key_path)}", field=_name(key_path), detail="ไม่มีค่า"))
            continue
        value = node[key]
        if isinstance(kind, dict):
            _validate_node(value, kind, key_path, errors)
        elif kind == BRACKETS:
            _validate_brackets(value, errors)
        else:
            message = _check_value(value, kind)
            if message:
                errors.append(RuleError(f"{_name(key_path)} {message}", field=_name(key_path), detail=message))


def _check_value(value, kind):
    """คืนข้อความข้อผิดพลาด หรือ None ถ้าผ่าน"""
    # bool เป็น int ใน Python ต้องกันแยก · NaN/inf ผ่านการเทียบ ≥ 0 บางแบบได้ จึงเช็ก isfinite ก่อน
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return "ต้องเป็นตัวเลข"
    if kind == MONEY:
        return "ต้องไม่ติดลบ" if value < 0 else None
    if not 0 <= value <= 1:
        return "ต้องอยู่ระหว่าง 0% ถึง 100%"
    if kind == WHOLE_PERCENT and not _is_whole_percent(value):
        return "ต้องเป็นจำนวนเต็ม % เพราะข้อความถึงผู้ใช้แสดงเป็นจำนวนเต็ม"
    return None


def _is_whole_percent(rate):
    # อัตราเก็บเป็น float เช่น 0.15 * 100 = 15.000000000000002 จึงเทียบแบบมีค่าคลาดเคลื่อน
    return abs(rate * 100 - round(rate * 100)) < 1e-9


def _validate_brackets(brackets, errors):
    expected_count = len(DEFAULT_TAX_RULES["tax_brackets"])
    if not isinstance(brackets, (list, tuple)) or len(brackets) != expected_count:
        errors.append(f"tax_brackets ต้องมี {expected_count} ขั้น")
        return
    if not all(isinstance(b, (list, tuple)) and len(b) == 2 for b in brackets):
        errors.append("tax_brackets แต่ละขั้นต้องมีเพดานบนและอัตรา")
        return

    def step_error(i, part, detail):
        # ข้อความเดิม "tax_brackets ขั้นที่ i <detail>" + ช่องของขั้นนั้น เช่น "tax_brackets.3.upper"
        return RuleError(f"tax_brackets ขั้นที่ {i} {detail}", field=f"tax_brackets.{i}.{part}", detail=detail)

    before = len(errors)
    prev_upper = 0
    for i, (upper, rate) in enumerate(brackets, start=1):
        is_last = (i == len(brackets))
        if is_last:
            # ขั้นสุดท้ายไม่มีเพดาน ถ้ามี เงินได้ส่วนที่เกินเพดานจะไม่ถูกคิดภาษี
            if upper is not None:
                errors.append(step_error(i, "upper", "(ขั้นสุดท้าย) ต้องไม่มีเพดานบน"))
        elif upper is None:
            errors.append(step_error(i, "upper", "ต้องมีเพดานบน (เฉพาะขั้นสุดท้ายที่ไม่มี)"))
        else:
            message = _check_value(upper, MONEY)
            if message:
                errors.append(step_error(i, "upper", f"เพดานบน{message}"))
            elif upper <= prev_upper:
                errors.append(step_error(i, "upper", "เพดานบนต้องมากกว่าขั้นก่อนหน้า (ขั้นแรกต้องมากกว่า 0)"))
            else:
                prev_upper = upper

        message = _check_value(rate, WHOLE_PERCENT)
        if message:
            errors.append(step_error(i, "rate", f"อัตรา{message}"))

    if len(errors) == before:
        rates = [rate for _, rate in brackets]
        for i, (earlier, later) in enumerate(zip(rates, rates[1:]), start=2):
            if later < earlier:
                # ชี้ขั้นแรกที่อัตราลดลง ข้อความยังเป็นข้อความเดิม
                errors.append(RuleError("tax_brackets อัตราต้องไม่ลดลงเมื่อขั้นสูงขึ้น", field=f"tax_brackets.{i}.rate",
                                        detail="อัตราต้องไม่ต่ำกว่าขั้นก่อนหน้า"))
                break


def _extract(node, schema):
    result = {}
    for key, kind in schema.items():
        if isinstance(kind, dict):
            result[key] = _extract(node[key], kind)
        elif kind == BRACKETS:
            result[key] = brackets_to_firestore(node[key])
        else:
            result[key] = node[key]
    return result


def _schema_paths(schema, path):
    for key, kind in schema.items():
        if isinstance(kind, dict):
            yield from _schema_paths(kind, path + (key,))
        else:
            yield path + (key,)


def _get(rules, path):
    for key in path:
        rules = rules[key]
    return rules


def _name(path):
    return ".".join(path)
