"""
ตรวจและรวมค่าที่ระบบ Admin แก้ (เก็บใน Firestore) เข้ากับค่าตั้งต้นใน utils/tax_rules.py

ไฟล์นี้เป็นฟังก์ชันล้วน ไม่มี I/O — การอ่าน/เขียน Firestore อยู่ที่อื่น แล้วส่ง dict มาให้ฟังก์ชันที่นี่
ค่าที่แก้ (override) มีรูปเดียวกับ TAX_RULES แต่มีเฉพาะ key ที่แก้ได้ และ tax_brackets เป็น list ของ
{"upper": ..., "rate": ...} เพราะ Firestore เก็บ list ซ้อน list ไม่ได้

กติกา: ผิดข้อเดียว → ปฏิเสธทั้งชุด (build_rules คืน None) ผู้เรียกต้องใช้ค่าตั้งต้นแทน
"""
import copy
import math

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
            errors.append(f"{_name(small_path)} ({small:,.2f}) ต้องไม่เกิน {_name(cap_path)} ({cap:,.2f})")
    return errors


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
            errors.append(f"ไม่มี {_name(key_path)}")
            continue
        value = node[key]
        if isinstance(kind, dict):
            _validate_node(value, kind, key_path, errors)
        elif kind == BRACKETS:
            _validate_brackets(value, errors)
        else:
            message = _check_value(value, kind)
            if message:
                errors.append(f"{_name(key_path)} {message}")


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

    before = len(errors)
    prev_upper = 0
    for i, (upper, rate) in enumerate(brackets, start=1):
        is_last = (i == len(brackets))
        if is_last:
            # ขั้นสุดท้ายไม่มีเพดาน ถ้ามี เงินได้ส่วนที่เกินเพดานจะไม่ถูกคิดภาษี
            if upper is not None:
                errors.append(f"tax_brackets ขั้นที่ {i} (ขั้นสุดท้าย) ต้องไม่มีเพดานบน")
        elif upper is None:
            errors.append(f"tax_brackets ขั้นที่ {i} ต้องมีเพดานบน (เฉพาะขั้นสุดท้ายที่ไม่มี)")
        else:
            message = _check_value(upper, MONEY)
            if message:
                errors.append(f"tax_brackets ขั้นที่ {i} เพดานบน{message}")
            elif upper <= prev_upper:
                errors.append(f"tax_brackets ขั้นที่ {i} เพดานบนต้องมากกว่าขั้นก่อนหน้า (ขั้นแรกต้องมากกว่า 0)")
            else:
                prev_upper = upper

        message = _check_value(rate, WHOLE_PERCENT)
        if message:
            errors.append(f"tax_brackets ขั้นที่ {i} อัตรา{message}")

    if len(errors) == before:
        rates = [rate for _, rate in brackets]
        if any(later < earlier for earlier, later in zip(rates, rates[1:])):
            errors.append("tax_brackets อัตราต้องไม่ลดลงเมื่อขั้นสูงขึ้น")


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


def _get(rules, path):
    for key in path:
        rules = rules[key]
    return rules


def _name(path):
    return ".".join(path)
