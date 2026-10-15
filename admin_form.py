"""
แปลงระหว่างฟอร์มหน้า /admin/edit กับ override ของ utils/rules_store

ไฟล์นี้ไม่ตรวจกฎภาษีเอง — ทำแค่แปลงข้อความเป็นตัวเลข แล้วส่งให้ rules_store.build_rules
(ตัวเดียวกับที่ตัวโหลดใช้ตอนเปิดเซิร์ฟเวอร์) ตรวจ กฎจึงอยู่ที่เดียว ฟอร์มกับตัวโหลดตัดสินเหมือนกันเสมอ

ชื่อช่องในฟอร์มคือชื่อ key แบบมีจุด เช่น "deduction.personal" และขั้นบันไดเป็น "tax_brackets.3.upper"
ซึ่งตรงกับ RuleError.field ที่ build_rules คืนมา จึงแสดงข้อผิดพลาดข้างช่องได้ตรง ๆ
"""
import re
from decimal import Decimal, InvalidOperation

from utils import rules_store
from utils.rules_store import BRACKETS, DEFAULT_TAX_RULES, EDITABLE_SCHEMA, MONEY

# ชื่อไทยของทุก key ใน EDITABLE_SCHEMA (เทสตรวจว่าครบ) — ขั้นบันไดแต่ละขั้นสร้างชื่อจาก step_label
LABELS = {
    "expense.salary_rate": "เงินเดือน: อัตราหักค่าใช้จ่าย",
    "expense.salary_cap": "เงินเดือน: เพดานค่าใช้จ่าย",
    "expense.rental_rate.building": "ค่าเช่า: บ้าน โรงเรือน สิ่งปลูกสร้าง แพ ยานพาหนะ",
    "expense.rental_rate.agricultural_land": "ค่าเช่า: ที่ดินที่ใช้ในการเกษตรกรรม",
    "expense.rental_rate.land": "ค่าเช่า: ที่ดินที่ไม่ได้ใช้ในการเกษตรกรรม",
    "expense.online_sale_rate": "ขายของออนไลน์: อัตราหักค่าใช้จ่าย",
    "deduction.personal": "ค่าลดหย่อนส่วนตัว",
    "deduction.spouse": "คู่สมรสที่ไม่มีเงินได้",
    "deduction.child_first": "บุตรคนแรก",
    "deduction.child_second_onward": "บุตรคนที่ 2 ขึ้นไป (ต่อคน)",
    "deduction.parent_per_person": "บิดามารดา (ต่อคน)",
    "deduction.social_security_cap": "ประกันสังคม: เพดาน",
    "deduction.health_insurance_cap": "ประกันสุขภาพ: เพดาน",
    "deduction.life_health_combined_cap": "ประกันชีวิตรวมประกันสุขภาพ: เพดานรวม",
    "deduction.home_loan_interest_cap": "ดอกเบี้ยเงินกู้ซื้อบ้าน: เพดาน",
    "deduction.rmf_income_rate": "RMF: ไม่เกินร้อยละของเงินได้",
    "deduction.rmf_cap": "RMF: เพดาน",
    "deduction.retirement_group_cap": "กลุ่มเงินออมเพื่อการเกษียณ: เพดานรวม",
    "deduction.thai_esg_income_rate": "ThaiESG: ไม่เกินร้อยละของเงินได้",
    "deduction.thai_esg_cap": "ThaiESG: เพดาน",
    "deduction.donation_rate": "เงินบริจาค: ไม่เกินร้อยละของเงินได้หลังหักค่าใช้จ่ายและค่าลดหย่อนอื่น",
    "tax_brackets": "ขั้นบันไดภาษี",
    "method_2.rate": "ภาษีวิธีที่ 2: อัตรา",
    "method_2.income_threshold": "ภาษีวิธีที่ 2: คิดเมื่อเงินได้ที่ไม่ใช่เงินเดือนตั้งแต่",
    "method_2.exempt_tax_up_to": "ภาษีวิธีที่ 2: ไม่ต้องเสียถ้าภาษีไม่เกิน",
}

# กลุ่มในฟอร์มตามลำดับที่แสดง (key ชั้นบนสุดของ EDITABLE_SCHEMA)
GROUPS = [
    ("tax_brackets", "ขั้นบันไดภาษี (คิดจากเงินได้สุทธิ)"),
    ("expense", "ค่าใช้จ่าย"),
    ("deduction", "ค่าลดหย่อน"),
    ("method_2", "ภาษีวิธีที่ 2"),
]

REFERENCE_MIN_LENGTH = 10
REFERENCE_MAX_LENGTH = 300
NOTE_MAX_LENGTH = 300
# ค่าใหม่ห่างจากค่าเดิมเกินกี่เท่าจึงไฮไลต์ในหน้ายืนยัน (จับเลข 0 เกินหรือขาด)
CHANGE_WARNING_RATIO = 3

MONEY_FIELD = "money"      # กรอกเป็นบาท รับ , ได้
PERCENT_FIELD = "percent"  # กรอกเป็น % เก็บเป็นสัดส่วน 0–1


def bracket_count():
    return len(DEFAULT_TAX_RULES["tax_brackets"])


def step_label(i, part):
    return f"ขั้นที่ {i}: {'เพดานเงินได้สุทธิ' if part == 'upper' else 'อัตราภาษี'}"


def label(name):
    """ชื่อไทยของช่อง — ช่องขั้นบันได "tax_brackets.3.rate" สร้างจาก step_label"""
    if name.startswith("tax_brackets."):
        _, i, part = name.split(".")
        return step_label(int(i), part)
    return LABELS[name]


def form_fields():
    """[(ชื่อกลุ่ม, [(ชื่อช่อง, ชื่อไทย, ชนิดช่อง)])] ตามลำดับที่แสดง
    ขั้นสุดท้ายของขั้นบันไดไม่มีช่องเพดาน (ไม่มีเพดาน แก้ไม่ได้)"""
    groups = []
    for key, title in GROUPS:
        if EDITABLE_SCHEMA[key] == BRACKETS:
            fields = []
            for i in range(1, bracket_count() + 1):
                if i < bracket_count():
                    fields.append((f"tax_brackets.{i}.upper", step_label(i, "upper"), MONEY_FIELD))
                fields.append((f"tax_brackets.{i}.rate", step_label(i, "rate"), PERCENT_FIELD))
        else:
            fields = [(name, LABELS[name], MONEY_FIELD if kind == MONEY else PERCENT_FIELD)
                      for name, kind in _leaves(EDITABLE_SCHEMA[key], key)]
        groups.append((title, fields))
    return groups


def all_fields():
    return [field for _, fields in form_fields() for field in fields]


# ---------------------------------------------------------------------------
# override → ข้อความในช่อง
# ---------------------------------------------------------------------------
def values_from_override(override):
    """{ชื่อช่อง: ข้อความที่เติมในช่อง} — เงินมี , อัตราเป็น %"""
    return {name: format_value(field_value(override, name), kind) for name, _, kind in all_fields()}


def field_value(override, name):
    if name.startswith("tax_brackets."):
        _, i, part = name.split(".")
        return override["tax_brackets"][int(i) - 1][part]
    node = override
    for key in name.split("."):
        node = node[key]
    return node


def format_value(value, kind):
    # ผ่าน str(float) ก่อน (ทศนิยมที่สั้นที่สุดที่ได้ float เดิม) เช่น 0.005 → "0.005" → "0.5" (%)
    # normalize ตัด 0 ท้าย: 60000.0 → "60,000" · รูปแบบ "f" ไม่ใช้เลขยกกำลัง ("6E+4" → "60000")
    number = Decimal(str(value))
    if kind == PERCENT_FIELD:
        number = number * 100
    return format(number.normalize(), ",f" if kind == MONEY_FIELD else "f")


# ---------------------------------------------------------------------------
# ข้อความในช่อง → override
# ---------------------------------------------------------------------------
def parse_number(text, kind):
    """คืน (float, None) หรือ (None, ข้อความผิด) — แปลงด้วย Decimal ไม่ใช้ float(ข้อความ)
    % หารด้วย 100 ด้วย Decimal แล้วค่อยแปลงเป็น float ครั้งเดียว
    (float("0.7") / 100 ได้ 0.006999999999999999 ส่วน Decimal ได้ 0.007 เท่ากับที่พิมพ์)
    ค่าติดลบผ่านตรงนี้ได้ แล้วให้ build_rules ปฏิเสธพร้อมเหตุผล (กฎอยู่ที่เดียว)"""
    text = (text or "").strip().replace(",", "")
    if not text:
        return None, "กรุณากรอกตัวเลข"
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None, "ต้องเป็นตัวเลข"
    if not number.is_finite():
        return None, "ต้องเป็นตัวเลข"
    if kind == PERCENT_FIELD:
        number = number / Decimal(100)
    return float(number), None


def parse_form(form):
    """แปลงฟอร์มทั้งหมด คืน dict:
    raw: ข้อความที่ผู้ใช้กรอก (ใช้เติมฟอร์มคืนเมื่อผิด) · override / rules: None ถ้าผิด
    field_errors: {ชื่อช่อง: ข้อความ} · general_errors: ข้อผิดพลาดที่ไม่ได้อยู่ที่ช่องเดียว
    reference / note: ตัดช่องว่างหัวท้ายแล้ว · base_token: ลายนิ้วมือของ doc ที่ฟอร์มเห็น หรือ None ถ้าผิดรูป"""
    raw = {name: form.get(name, "") for name, _, _ in all_fields()}
    field_errors, general_errors = {}, []
    numbers = {}
    for name, _, kind in all_fields():
        number, error = parse_number(raw[name], kind)
        if error:
            field_errors[name] = error
        else:
            numbers[name] = number

    reference = form.get("source_reference", "").strip()
    note = form.get("note", "").strip()
    if not REFERENCE_MIN_LENGTH <= len(reference) <= REFERENCE_MAX_LENGTH:
        field_errors["source_reference"] = (f"กรุณากรอกแหล่งอ้างอิง {REFERENCE_MIN_LENGTH}–{REFERENCE_MAX_LENGTH} ตัวอักษร "
                                            "(เช่น ชื่อประกาศหรือกฎกระทรวงพร้อมเลขที่)")
    if len(note) > NOTE_MAX_LENGTH:
        field_errors["note"] = f"หมายเหตุต้องไม่เกิน {NOTE_MAX_LENGTH} ตัวอักษร"

    # คำอ้างว่าย้อนจากเวอร์ชันไหน — รูปผิดถือว่าไม่ได้อ้าง (ไม่ใช่ข้อผิดพลาด) การตัดสินจริงอยู่ใน transaction ตอนบันทึก
    restored_from = parse_restored_from(form.get("restored_from", ""))

    base_token = _parse_base_token(form.get("base_token", ""))
    if base_token is None:
        general_errors.append("ฟอร์มไม่สมบูรณ์ กรุณาเปิดหน้าแก้ค่าใหม่")

    override = rules = None
    if len(numbers) == len(raw):
        # ทุกช่องเป็นตัวเลขแล้ว ส่งให้ build_rules ตรวจกฎทั้งหมด (ช่วง, ขั้นบันไดเรียง, ข้าม key ฯลฯ)
        candidate = _assemble(numbers)
        rules, rule_errors = rules_store.build_rules(candidate)
        for error in rule_errors:
            field = getattr(error, "field", None)
            if field in raw:
                field_errors.setdefault(field, rule_error_text(error))
            else:
                general_errors.append(str(error))
        if not rule_errors:
            override = candidate

    if field_errors or general_errors:
        override = rules = None
    return {"raw": raw, "override": override, "rules": rules, "field_errors": field_errors,
            "general_errors": general_errors, "reference": reference, "note": note,
            "base_token": base_token, "restored_from": restored_from}


def rule_error_text(error):
    """ข้อความข้างช่อง: ไม่มีชื่อ key ภาษาอังกฤษ ข้อผิดพลาดข้าม key บอกชื่อไทยของอีกช่อง"""
    if getattr(error, "related", None):
        return f"{error.detail} {label(error.related)}"
    return error.detail


def _parse_base_token(text):
    """รูปที่ rules_store.base_token สร้างเท่านั้น: "none" หรือ sha256 แบบ hex 64 ตัว"""
    text = str(text).strip()
    if text == rules_store.NO_DOC_TOKEN or re.fullmatch(r"[0-9a-f]{64}", text):
        return text
    return None


def parse_restored_from(text):
    """"default" → rules_store.RESTORE_DEFAULT · "3" → 3 · อย่างอื่น (ว่าง, ไม่ใช่ตัวเลข, < 1) → None"""
    text = str(text).strip()
    if text == rules_store.RESTORE_DEFAULT:
        return rules_store.RESTORE_DEFAULT
    # [0-9] ไม่ใช่ isdigit(): isdigit รับเลขไทยและตัวยก ("²" แล้ว int() โยน error)
    if re.fullmatch(r"[0-9]+", text) and int(text) >= 1:
        return int(text)
    return None


def restore_source_name(source):
    return "ค่าตั้งต้นในโค้ด" if source == rules_store.RESTORE_DEFAULT else f"เวอร์ชัน {source}"


def restore_reference(source, old_reference=""):
    """แหล่งอ้างอิงที่เติมไว้ในฟอร์มย้อนเวอร์ชัน — ไม่เกิน REFERENCE_MAX_LENGTH ไม่อย่างนั้นฟอร์มจะปฏิเสธข้อความที่ระบบเติมเอง
    แหล่งอ้างอิงเดิมที่ยาวเกินถูกตัดแล้วลงท้าย "…" """
    if source == rules_store.RESTORE_DEFAULT:
        return "ย้อนไปใช้ค่าตั้งต้นในโค้ด (utils/tax_rules.py)"
    head = f"ย้อนไปใช้ค่าของเวอร์ชัน {source} (แหล่งอ้างอิงเดิม: "
    old = (old_reference if isinstance(old_reference, str) else "").strip() or "—"
    room = REFERENCE_MAX_LENGTH - len(head) - len(")")
    if len(old) > room:
        old = old[:room - 1] + "…"
    return f"{head}{old})"


def _assemble(numbers):
    """{ชื่อช่อง: ตัวเลข} → override รูปเดียวกับ to_override() (ขั้นสุดท้ายเพดานเป็น None)"""
    override = {}
    for name, number in numbers.items():
        if name.startswith("tax_brackets."):
            continue
        node = override
        *parents, leaf = name.split(".")
        for key in parents:
            node = node.setdefault(key, {})
        node[leaf] = number
    override["tax_brackets"] = [
        {"upper": numbers.get(f"tax_brackets.{i}.upper") if i < bracket_count() else None,
         "rate": numbers[f"tax_brackets.{i}.rate"]}
        for i in range(1, bracket_count() + 1)
    ]
    return override


# ---------------------------------------------------------------------------
# หน้ายืนยัน
# ---------------------------------------------------------------------------
def changes(old_override, new_override):
    """แถวของหน้ายืนยัน เฉพาะช่องที่เปลี่ยน: {name, label, old, new, warn}"""
    rows = []
    for name, field_label, kind in all_fields():
        old, new = field_value(old_override, name), field_value(new_override, name)
        if old == new:
            continue
        rows.append({"name": name, "label": field_label,
                     "old": format_value(old, kind), "new": format_value(new, kind),
                     "unit": "บาท" if kind == MONEY_FIELD else "%",
                     "warn": changed_a_lot(old, new)})
    return rows


def changed_a_lot(old, new):
    """ค่าใหม่ห่างจากค่าเดิมเกิน CHANGE_WARNING_RATIO เท่า (มากขึ้นหรือน้อยลง) — จากหรือไปที่ 0 นับว่าเกิน"""
    if old == new:
        return False
    if old == 0 or new == 0:
        return True
    big, small = max(abs(old), abs(new)), min(abs(old), abs(new))
    return big / small > CHANGE_WARNING_RATIO


def _leaves(schema, prefix):
    for key, kind in schema.items():
        name = f"{prefix}.{key}"
        if isinstance(kind, dict):
            yield from _leaves(kind, name)
        else:
            yield name, kind
