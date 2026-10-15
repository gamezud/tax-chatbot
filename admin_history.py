"""
แปลง doc ใน tax_rules_history เป็นแถวของหน้า /admin/history (อ่านอย่างเดียว)

ไฟล์นี้ไม่อ่าน Firestore และไม่ import Flask — รับ doc ที่อ่านมาแล้ว คืนข้อมูลสำหรับ template
doc ผิดทุกแบบต้องกลายเป็นแถวที่บอกว่าผิด ไม่ใช่ exception (หน้าประวัติต้องไม่พังเพราะ doc เดียว)
"""
import datetime

import admin_form
from utils import rules_store

# เวลาไทย: ไทยไม่มีเวลาออมแสง จึงใช้ +7 คงที่ ไม่ใช้ zoneinfo("Asia/Bangkok") ซึ่งบน Windows ต้องลง tzdata เพิ่ม
THAI_TZ = datetime.timezone(datetime.timedelta(hours=7))
THAI_MONTHS = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
               "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]
BUDDHIST_ERA_OFFSET = 543

MALFORMED = "ข้อมูลผิดรูป"
NOT_COMPARABLE = "เทียบไม่ได้"


def thai_time(value):
    """เวลาจาก Firestore (UTC) เป็นเวลาไทย เช่น "15 ต.ค. 2569 14:05 น." — ไม่ใช่ datetime คืน "—" """
    if not isinstance(value, datetime.datetime):
        return "—"
    if value.tzinfo is None:
        # Firestore คืนเวลาที่มี tzinfo เสมอ ถ้าไม่มีถือว่าเป็น UTC
        value = value.replace(tzinfo=datetime.timezone.utc)
    local = value.astimezone(THAI_TZ)
    return f"{local.day} {THAI_MONTHS[local.month - 1]} {local.year + BUDDHIST_ERA_OFFSET} {local:%H:%M} น."


def is_well_formed(doc_id, doc):
    """รูปที่หน้าประวัติอ่านได้: map ที่มี version (int ≥ 1 ตรงกับ doc id) และ override เป็น map
    (ค่าข้างใน override ยังอาจผิดกฎ — ตรวจแยกด้วย rules_from_doc)"""
    version = rules_store.valid_version(doc)
    return version is not None and str(version) == doc_id and isinstance(doc.get("override"), dict)


def unusable_reason(doc):
    """เหตุผลภาษาไทยที่ค่าใน doc ใช้กับระบบตอนนี้ไม่ได้ หรือ None ถ้าใช้ได้ — ไม่มีชื่อ key ภาษาอังกฤษ"""
    tax_year = rules_store.DEFAULT_TAX_RULES["tax_year"]
    saved_year = doc.get("based_on_tax_year")
    if not rules_store._is_int(saved_year) or saved_year != tax_year:
        if rules_store._is_int(saved_year):
            return f"บันทึกไว้สำหรับปีภาษี {saved_year} ใช้กับปีภาษี {tax_year} ไม่ได้"
        return "ไม่มีปีภาษีที่ถูกต้อง"
    _, errors = rules_store.rules_from_doc(doc)
    if not errors:
        return None
    labelled = [f"{admin_form.label(e.field)}: {admin_form.rule_error_text(e)}" for e in errors
                if getattr(e, "field", None) and _has_label(e.field)]
    # ข้อผิดพลาดที่ไม่ได้ผูกกับช่อง (เช่น key ที่ไม่รู้จัก) มีชื่อ key ภาษาอังกฤษ — บอกรวม ๆ แทน
    if len(labelled) < len(errors):
        labelled.append("มีค่าที่ระบบไม่รู้จักหรือห้ามแก้")
    return "ค่าไม่ผ่านการตรวจปัจจุบัน — " + " · ".join(labelled)


def history_rows(docs, active_version, rules_info):
    """docs: [(doc id, dict)] จาก rules_store.read_history_docs
    active_version: version ของ tax_rules_config/active (0 = ไม่มีหรือผิดรูป) · rules_info: tax_rules.RULES_INFO
    คืนแถวเรียงจากใหม่ไปเก่า แถวผิดรูปอยู่ท้ายเรียงตาม doc id"""
    in_use = rules_info.get("version") if rules_info.get("source") == "firestore" else None

    # ตรวจรูปและค่าของแต่ละ doc ครั้งเดียว — exception ที่ไม่คาดคิดจาก doc ใดก็ตามนับเป็นผิดรูป (หน้าต้องไม่พัง)
    checked = []
    for doc_id, doc in docs:
        try:
            reason = unusable_reason(doc) if is_well_formed(doc_id, doc) else MALFORMED
        except Exception as e:
            # ข้อมูลผิดที่ตรวจเจอตามปกติไม่ log (ไม่ใช่บั๊ก) แต่ exception แปลว่าโค้ดตรงนี้อาจผิด
            # ถ้าไม่ log ทุกแถวจะขึ้น "ข้อมูลผิดรูป" โดยไม่มีร่องรอย — บอกแค่ doc id กับชนิด ไม่พิมพ์ข้อความหรือเนื้อหา doc
            print(f"⚠️ [ADMIN] แถวประวัติ {doc_id} แสดงเป็นข้อมูลผิดรูป เพราะ {type(e).__name__}")
            reason = MALFORMED
        checked.append((doc_id, doc, reason))

    # ค่าเต็มของทุกเวอร์ชันที่ใช้ได้ ใช้เทียบ "เปลี่ยนจากเวอร์ชันก่อน" (doc ที่เก็บค่าไม่ครบถูกเติมค่าตั้งต้น)
    overrides = {}
    for doc_id, doc, reason in checked:
        if reason is None:
            rules, _ = rules_store.rules_from_doc(doc)
            overrides[doc["version"]] = rules_store.to_override(rules)

    rows, malformed = [], []
    for doc_id, doc, reason in checked:
        if reason == MALFORMED:
            malformed.append({"doc_id": doc_id, "malformed": True, "status": MALFORMED})
            continue
        version = doc["version"]
        compared_with, changes = _compare(doc, overrides)
        rows.append({
            "doc_id": doc_id,
            "malformed": False,
            "version": version,
            "saved_at": thai_time(doc.get("updated_at")),
            "reference": _text(doc.get("source_reference")),
            "note": _text(doc.get("note")),
            "compared_with": compared_with,
            "changes": changes,
            "unusable": reason,
            "is_active": version == active_version,
            "in_use": version == in_use,
            "restored_from": _restored_from(doc.get("restored_from")),
        })
    rows.sort(key=lambda row: row["version"], reverse=True)
    malformed.sort(key=lambda row: row["doc_id"])
    return rows + malformed


def _compare(doc, overrides):
    """(ข้อความว่าเทียบกับอะไร, แถวของ admin_form.changes) หรือ (NOT_COMPARABLE, None)"""
    own = overrides.get(doc["version"])
    if own is None:
        return NOT_COMPARABLE, None
    previous = doc.get("previous_version")
    if previous is None:
        # เวอร์ชันแรก หรือ active ตอนบันทึกผิดรูป — save_config เทียบกับค่าตั้งต้นในกรณีนี้เช่นกัน
        return "เทียบกับค่าตั้งต้น", admin_form.changes(rules_store.to_override(rules_store.DEFAULT_TAX_RULES), own)
    # เช็กชนิดก่อน: ค่าที่ hash ไม่ได้ (list, map) ใช้กับ "in dict" ไม่ได้
    if rules_store._is_int(previous) and previous in overrides:
        return f"เทียบกับเวอร์ชัน {previous}", admin_form.changes(overrides[previous], own)
    return NOT_COMPARABLE, None


def _restored_from(value):
    """ชื่อที่ย้อนมาจาก (เช่น "เวอร์ชัน 1") หรือ None — ค่ารูปอื่นที่ save_config ไม่เขียน ไม่แสดง"""
    if value == rules_store.RESTORE_DEFAULT or (rules_store._is_int(value) and value >= 1):
        return admin_form.restore_source_name(value)
    return None


def _text(value):
    return value if isinstance(value, str) else ""


def _has_label(field):
    try:
        admin_form.label(field)
    except (KeyError, ValueError):
        return False
    return True
