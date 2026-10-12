"""ตัวกันค่าลดหย่อนที่ Dialogflow ส่งมา ก่อนนำไปคำนวณในสาขา 04 (#25)

Dialogflow จับ intent 04 ได้ แต่บางครั้งใส่ตัวเลขผิดช่อง เช่น "ดอกเบี้ยเงินกู้รถ 20000" ได้ donation 20000
ตัวกันไม่แก้ค่าให้เอง ทำได้แค่ปฏิเสธทั้งข้อความ — เดาผิดคือตัวเลขภาษีผิด (กฎเหล็กข้อ 2)

หลักฐานผูกกับตัวเลข: ค่าของ parameter ต้องเท่ากับตัวเลขในข้อความ และคำสุดท้ายในช่วง
ตั้งแต่ตัวเลขก่อนหน้าถึงตัวเลขนั้นต้องเป็นของ parameter นั้น (กติกาเดียวกับเทส training phrase)
และตรวจย้อนกลับ: ตัวเลขทุกตัวในข้อความต้องมี parameter รองรับ ยกเว้นตัวเลขหลังคำที่ห้ามนับ

import แค่ re, decimal และ utils.deduction_terms — ไม่มี I/O
"""
import re
from decimal import Decimal, InvalidOperation

from utils.deduction_terms import find_terms, normalize

COUNT_PARAMETERS = ("num_children", "num_parents")
MONEY_PARAMETERS = ("life_insurance", "health_insurance", "home_loan_interest", "ssf_rmf", "thai_esg", "donation")

# spouse = "yes" ต้องมีคำใดคำหนึ่งนี้ในข้อความ — ทุกคำฝั่ง yes ของ @marital_status มีคำเหล่านี้อยู่ (เทสเทียบกับ export)
SPOUSE_WORDS = ("คู่สมรส", "สมรส", "แต่งงาน", "ภรรยา", "สามี")

_NUMBER = re.compile(r"\d+(?:\.\d+)?")

# คำ หนึ่ง–สิบ นับเป็นตัวเลขเฉพาะเมื่อมี "คน" ติดท้าย และใช้ได้กับจำนวนคนเท่านั้น
# ถ้าไม่บังคับ "คน" คำว่า "สาม" ใน "สามี" จะกลายเป็นเลข 3
_COUNT_WORDS = {"หนึ่ง": 1, "สอง": 2, "สาม": 3, "สี่": 4, "ห้า": 5, "หก": 6, "เจ็ด": 7, "แปด": 8, "เก้า": 9, "สิบ": 10}
_COUNT_WORD = re.compile("(?:" + "|".join(_COUNT_WORDS) + ")(?=คน)")

# กรณี ง: บอทยังไม่รองรับเบี้ยประกันสุขภาพบิดามารดา ไม่มีตัวเลขคั่นระหว่างสองคำ ("ประกันสุขภาพ 8000 แม่ 1 คน" ไม่ใช่)
_PARENT_HEALTH = re.compile(r"ประกันสุขภาพ\D*?(?:พ่อ|แม่|บิดา|มารดา)")


def has_number(text):
    """True ถ้าข้อความมีตัวเลข (เลขไทยก็นับ)"""
    return bool(_NUMBER.search(normalize(text)))


def _mentions_parent_health(text):
    # text ผ่าน normalize แล้ว — แยกเป็นฟังก์ชันให้เทสปิดชั่วคราวได้ เพื่อพิสูจน์ว่าการตรวจ "คน" กันได้อีกชั้น
    return bool(_PARENT_HEALTH.search(text))


def _number_tokens(text):
    # (เริ่ม, จบ, ค่า, เป็นตัวเลขหรือไม่) เรียงตามตำแหน่ง — คำ หนึ่ง–สิบ ได้ False
    tokens = [(m.start(), m.end(), Decimal(m.group()), True) for m in _NUMBER.finditer(text)]
    tokens += [(m.start(), m.end(), Decimal(_COUNT_WORDS[m.group()]), False) for m in _COUNT_WORD.finditer(text)]
    return sorted(tokens)


def _segments(text, tokens):
    # (ข้อความตั้งแต่ตัวเลขก่อนหน้าถึงตัวเลขนี้, ตัวเลข) — คำที่อยู่หน้าตัวเลขก่อนหน้าเป็นของตัวเลขนั้น
    previous_end = 0
    for token in tokens:
        yield text[previous_end:token[0]], token
        previous_end = token[1]


def _fits(text, segment, token, name, value):
    # ตัวเลขนี้รองรับ parameter นี้ได้ไหม: ค่าเท่ากัน ชนิดถูก และคำสุดท้ายในช่วงเป็นของ parameter นี้
    start, end, number, is_digit = token
    if number != value:
        return False
    counts_people = text.startswith("คน", end)
    if name in COUNT_PARAMETERS and not counts_people:
        return False
    # เงินต้องเป็นตัวเลข และตัวเลขที่ตามด้วย "คน" เป็นจำนวนคน ไม่ใช่เงิน
    if name in MONEY_PARAMETERS and (not is_digit or counts_people):
        return False
    terms = find_terms(segment)
    return bool(terms) and terms[-1][1] == name


def check_deduction_parameters(text, parameters):
    """ตรวจว่า parameters ของ 04 รอบนี้มีหลักฐานในข้อความหรือไม่

    คืน None (ผ่าน), "parent_health" (กรณี ง ตรวจก่อน ปฏิเสธทั้งข้อความ) หรือ "unclear"
    """
    text = normalize(text)
    if _mentions_parent_health(text):
        return "parent_health"
    segments = list(_segments(text, _number_tokens(text)))
    values = {}
    for name, value in parameters.items():
        if value in ("", None):
            continue
        if name == "spouse":
            # "no" ไม่ได้เพิ่มค่าลดหย่อน จึงตรวจเฉพาะ "yes"
            if value == "yes" and not any(word in text for word in SPOUSE_WORDS):
                return "unclear"
            continue
        # parameter ที่ไม่รู้จัก (เช่น เพิ่มบน Console แล้วลืมเพิ่มที่นี่) ปฏิเสธ ดีกว่าปล่อยผ่านโดยไม่ตรวจ
        if name not in COUNT_PARAMETERS + MONEY_PARAMETERS:
            return "unclear"
        try:
            value = Decimal(str(value))
        except InvalidOperation:
            return "unclear"
        if not any(_fits(text, segment, token, name, value) for segment, token in segments):
            return "unclear"
        values[name] = value

    # ตรวจย้อนกลับ: ตัวเลขทุกตัวในข้อความต้องมี parameter รองรับ
    # ไม่งั้น "บริจาค 3500 ประกันชีวิต 20000" ที่ได้แค่ donation จะผ่าน แล้วยอดประกันชีวิตหายเงียบ (แบบ #23)
    # ยกเว้นตัวเลขหลังคำที่ห้ามนับ (SSF, ประกันสังคม ฯลฯ) ซึ่งตั้งใจไม่ให้มี parameter
    for segment, token in segments:
        terms = find_terms(segment)
        if terms and terms[-1][1] is None:
            continue
        if not any(_fits(text, segment, token, name, value) for name, value in values.items()):
            return "unclear"
    return None
