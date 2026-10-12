"""ดึงยอดเงินบริจาคจากข้อความที่ OCR อ่านได้ และตรวจว่าข้อความเป็นหลักฐานการบริจาคหรือไม่ (has_donation_keyword)

แยกจากการอ่านรูป (utils/ocr.py) เพื่อให้ test ได้ด้วยข้อความล้วนโดยไม่ต้องมีรูปหรือ Tesseract
import แค่ re, decimal และ utils.thai_text — รักษาคุณสมบัตินี้ไว้

หลักการ: ไม่เดา ถ้าไม่เจอยอดหรือเจอหลายยอดที่ต่างกัน คืน amount เป็น None ให้ผู้ใช้พิมพ์เอง
ไม่ใช้ clean_number ใน tax_calculator เพราะมันเลือกตัวเลขที่ยาวที่สุด ซึ่งคือการเดา

ขั้นตอน
1. แปลงเลขไทยเป็นอารบิก รวม ํ + า กลับเป็น ำ แล้วลบตัวเลขที่รู้แน่ว่าไม่ใช่เงิน (วันที่ เวลา ปี เลขที่เอกสาร เบอร์โทร เลข 13 หลัก)
2. ดูเฉพาะ "บรรทัดเงิน" คือบรรทัดที่มีคำสำคัญ (จำนวนเงิน, รวม, ยอด…) หรือคำสกุลเงิน (บาท, ฿, THB)
3. ในบรรทัดเงิน ตัวเลขที่ตามด้วยหน่วยนับ (รายการ, เท่า …) หรือ % ข้ามไป ไม่ใช่ยอด
4. ตัวเลขที่เหลือต้อง "จบสะอาด" ถ้ามีตัวไหนไม่สะอาด คืน ambiguous ทั้งผล
5. ตัวเลขสะอาดนับเป็นยอดเมื่อมีสกุลเงินติดอยู่หรืออยู่บรรทัดเดียวกับคำสำคัญ
6. ได้ยอดเดียวแต่ค่าเท่ากับตัวเลขที่เขียนด้วยเลขไทยในข้อความเดิม คืน thai_digits
   เหตุผล: Tesseract อ่านเลขไทยเป็นเลขไทยตัวอื่น (๙→๓, ๘→๕ ...) และผลยังจบสะอาด
   ผลวัดใบเสร็จจำลอง (scripts/measure_ocr.py) อ่านยอดเลขไทยถูก 0/42 รูป ทั้ง Tahoma และ TH Sarabun
   เทียบด้วยค่า ไม่ใช่ตำแหน่ง เพราะ normalize_thai_text รวม ํ + า เป็นตัวเดียว ตำแหน่งตัวอักษรจึงเลื่อน
   และใบที่มีเลขไทยแค่ในวันที่หรือเลขที่เอกสารยังได้ยอดอารบิกตามปกติ

กติกา "จบสะอาด"
- ด้านหลังต้องเป็นอย่างใดอย่างหนึ่ง: จบบรรทัด · สกุลเงิน (บาท, ฿, THB, baht, .-) · วงเล็บ
  · ช่องว่างแล้วตามด้วยอักษรไทย
- ด้านหน้าต้องไม่ติดอักษรละติน
- นอกจากนี้ถือว่าไม่สะอาด เช่น ติด . หรือ , ("5.000" อ่านได้แค่ 5) · ติดอักษรละติน ("5,OOO", "1O,000")
  · ช่องว่างแล้วตามด้วยตัวเลข ("5 000") · ช่องว่างแล้วตามด้วยอักษรละติน ("5 OOO")
- เหตุผล: regex ตัวเลขหยุดที่ตัวแรกที่ไม่ใช่เลข ตัวเลขที่ OCR อ่านเพี้ยนจึงถูกตัดเหลือครึ่งเดียว
  แล้วถูกนับเป็นยอดผิดโดยมี status ok ห้ามแก้ O เป็น 0 เอง เพราะนั่นคือการเดา
"""
import re
from decimal import Decimal, InvalidOperation

from utils.thai_text import normalize_thai_text

_THAI_MONTHS = (
    "มกราคม|กุมภาพันธ์|มีนาคม|เมษายน|พฤษภาคม|มิถุนายน|"
    "กรกฎาคม|สิงหาคม|กันยายน|ตุลาคม|พฤศจิกายน|ธันวาคม|"
    r"ม\.ค\.|ก\.พ\.|มี\.ค\.|เม\.ย\.|พ\.ค\.|มิ\.ย\.|"
    r"ก\.ค\.|ส\.ค\.|ก\.ย\.|ต\.ค\.|พ\.ย\.|ธ\.ค\."
)

# ตัวเลขที่ไม่ใช่ยอดเงิน ลบทิ้งก่อนหายอด ไม่งั้นถ้าอยู่บรรทัดเดียวกับ "จำนวนเงิน" จะถูกนับเป็นยอด
_NOT_AMOUNT_PATTERNS = [
    # วันที่แบบมีชื่อเดือน: "11 ตุลาคม 2569", "11 ต.ค. 69"
    re.compile(r"\d{1,2}\s*(?:" + _THAI_MONTHS + r")\s*(?:พ\.ศ\.\s*)?\d{2,4}"),
    # วันที่แบบตัวเลข: "11/10/2569", "11-10-69", "11.10.2569"
    re.compile(r"\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}"),
    # เวลา: "10:30", "10:30:15", "10:30 น.", "10.30 น." (แบบจุดต้องมี "น." ไม่งั้นชนกับทศนิยม "10.30 บาท")
    re.compile(r"\d{1,2}:\d{2}(?::\d{2})?(?:\s*น\.)?"),
    re.compile(r"\d{1,2}\.\d{2}\s*น\."),
    # ปี: "พ.ศ. 2569", "ปี 2569", "ปีภาษี 2569", "ปีภาษี พ.ศ. 2569"
    re.compile(r"(?:พ\.ศ\.|ค\.ศ\.|ปี(?:ภาษี)?)\s*(?:พ\.ศ\.\s*)?\d{2,4}"),
    # เลขที่เอกสาร: "เลขที่ 000123", "เลขที่ใบเสร็จ 000123", "No. A-123"
    # ตัวเลขเอกสารเป็น ASCII เสมอหลังแปลงเลขไทย จึงไม่ใช้ \w (ซึ่งรวมอักษรไทยด้วย)
    # \b หน้า No/Ref กันไม่ให้ตรงกับคำอย่าง "Donor"
    re.compile(r"(?:(?:เลขที่|เล่มที่)(?:ใบเสร็จ|ใบรับ)?|\bNo\.?|\bRef\.?)\s*[:#]?\s*[A-Za-z0-9/\-]+", re.IGNORECASE),
    # เบอร์โทร: "โทร 02-123-4567"
    re.compile(r"(?:โทร\.?|Tel\.?)\s*:?\s*[\d\s\-]+", re.IGNORECASE),
    # เลขประจำตัว 13 หลัก ทั้งแบบติดกันและแบบมีขีด
    re.compile(r"\d{13}"),
    re.compile(r"\d+(?:-\d+){2,}"),
]

# ตัวเลขที่มี comma คั่นหลักพัน หรือไม่มี และมีทศนิยมไม่เกิน 2 ตำแหน่งได้
_NUMBER = re.compile(r"(?<![\d.,])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?(?!\d)")

# คำที่บอกว่าบรรทัดนี้พูดถึงยอดเงิน — ไม่มี "จำนวน" เฉย ๆ เพราะใช้กับจำนวนชิ้นด้วย ("จำนวน 1 รายการ")
_MONEY_KEYWORDS = re.compile(r"จำนวนเงิน|ยอดเงิน|ยอดบริจาค|ยอดรวม|รวมเงิน|รวม|amount|total", re.IGNORECASE)

# หน่วยนับที่ไม่ใช่เงิน: ตัวเลขที่ตามด้วยคำเหล่านี้ไม่ใช่ยอด ("2 เท่า" คือสิทธิหักลดหย่อน ไม่ใช่ยอด)
_COUNT_UNIT_AFTER = re.compile(r"\s*(?:รายการ|ชิ้น|ฉบับ|ครั้ง|คน|ใบ|เท่า)")

# ร้อยละ: "10%" ไม่ใช่ยอด
_PERCENT_AFTER = re.compile(r"\s*%")

# สกุลเงิน: "5,000 บาท", "5,000.-", "฿5,000"
_CURRENCY = r"(?:บาท|฿|THB|baht|\.-)"
_CURRENCY_ANYWHERE = re.compile(_CURRENCY, re.IGNORECASE)
_CURRENCY_AFTER = re.compile(r"\s*" + _CURRENCY, re.IGNORECASE)
_CURRENCY_BEFORE = re.compile(r"฿\s*$")

# กติกา "จบสะอาด" (ดู docstring ของโมดูล)
# ช่วงอักษรไทยไม่รวมเลขไทย U+0E50–U+0E59 ซึ่งถูกแปลงเป็นอารบิกไปแล้ว
_CLEAN_AFTER = re.compile(
    r"\s*$"                         # จบบรรทัด
    r"|\s*" + _CURRENCY +           # สกุลเงิน
    r"|\s*[()]"                     # วงเล็บ
    r"|\s+[ก-๏]",                   # ช่องว่างแล้วอักษรไทย (ก-๏ คือ U+0E01–U+0E4F)
    re.IGNORECASE,
)
_LATIN_BEFORE = re.compile(r"[A-Za-z]$")

# คำที่บอกว่ารูปเป็นหลักฐานการบริจาค — ไม่มีคำเหล่านี้ ยอดที่อ่านได้อาจเป็นเงินอื่น (เช่น เบี้ยประกันชีวิต)
# ไม่นับ "สมทบ" เพราะชนกับ "เงินสมทบกองทุนประกันสังคม"
# ไม่นับ "มูลนิธิ" / "วัด" เพราะเป็นชื่อผู้รับ ใบเสร็จค่าบริการของมูลนิธิหรือวัดก็มีคำนี้
# จะเพิ่มคำต้องมีหลักฐานจากใบเสร็จจริงหรือจากผลวัดความแม่นของ OCR — คำที่หลวมทำให้เงินอื่นถูกนับเป็นเงินบริจาค
_DONATION_KEYWORDS = re.compile(r"บริจาค|อนุโมทนา|donation", re.IGNORECASE)
_WHITESPACE = re.compile(r"\s+")

# ตัวเลขในข้อความเดิม (ก่อนแปลงเลขไทย) ที่มีเลขไทยอย่างน้อยหนึ่งตัว — รวมแบบที่ OCR อ่านปนอารบิก ("๕,000")
_DIGIT_RUN = re.compile(r"[0-9๐-๙](?:[0-9๐-๙,.]*[0-9๐-๙])?")
_THAI_DIGIT = re.compile(r"[๐-๙]")


def _thai_digit_values(raw_text):
    """ค่าของตัวเลขที่เขียนด้วยเลขไทยในข้อความเดิม"""
    values = set()
    for run in _DIGIT_RUN.findall(raw_text):
        if not _THAI_DIGIT.search(run):
            continue
        try:
            values.add(Decimal(normalize_thai_text(run).replace(",", "")))
        except InvalidOperation:   # เช่น "๕.๐๐๐.๐๐" ซึ่งไม่ใช่ตัวเลขเดียว
            continue
    return values


def has_donation_keyword(text):
    """True ถ้าข้อความมีคำที่บอกว่าเป็นหลักฐานการบริจาค

    ตัดช่องว่างทั้งหมดก่อนเทียบ เพราะ OCR อาจแทรกช่องว่างกลางคำ ("บริ จาค")
    ไม่ใช้ขั้นลบตัวเลขที่ไม่ใช่ยอดของ extract_donation_amount ซึ่งลบคำที่อยู่ในเลขที่เอกสารทิ้งได้
    ("เลขที่ใบรับ DONATION-2569-001")
    """
    return bool(_DONATION_KEYWORDS.search(_WHITESPACE.sub("", normalize_thai_text(text))))


def extract_donation_amount(text):
    """คืน {"status": ..., "amount": Decimal หรือ None}

    status:
    - "ok"        เจอยอดเดียว (ยอดเดียวกันซ้ำหลายที่ เช่น 5,000 กับ 5,000.00 นับเป็นยอดเดียว)
    - "not_found" ไม่เจอตัวเลขที่ดูเป็นยอดเงิน
    - "ambiguous" เจอหลายยอดที่ต่างกัน หรือมีตัวเลขในบรรทัดเงินที่ไม่จบสะอาด ไม่เดาว่ายอดไหน
    - "thai_digits" เจอยอดเดียวแต่ยอดนั้นเขียนด้วยเลขไทย ซึ่ง OCR อ่านผิดบ่อย (ขั้นที่ 6 ใน docstring ของโมดูล)
    """
    raw_text = text or ""
    text = normalize_thai_text(raw_text)
    for pattern in _NOT_AMOUNT_PATTERNS:
        text = pattern.sub(" ", text)

    amounts = set()
    for line in text.splitlines():
        line_has_keyword = bool(_MONEY_KEYWORDS.search(line))
        if not (line_has_keyword or _CURRENCY_ANYWHERE.search(line)):
            continue
        for match in _NUMBER.finditer(line):
            after = line[match.end():]
            before = line[:match.start()]
            if _COUNT_UNIT_AFTER.match(after) or _PERCENT_AFTER.match(after):
                continue
            if not _CLEAN_AFTER.match(after) or _LATIN_BEFORE.search(before):
                return {"status": "ambiguous", "amount": None}
            has_currency = bool(_CURRENCY_AFTER.match(after) or _CURRENCY_BEFORE.search(before))
            if not (has_currency or line_has_keyword):
                continue
            amount = Decimal(match.group().replace(",", ""))
            if amount > 0:
                amounts.add(amount)

    if len(amounts) == 1:
        amount = amounts.pop()
        if amount in _thai_digit_values(raw_text):
            return {"status": "thai_digits", "amount": None}
        return {"status": "ok", "amount": amount}
    if not amounts:
        return {"status": "not_found", "amount": None}
    return {"status": "ambiguous", "amount": None}


def format_amount(amount):
    """Decimal → ข้อความที่ Dialogflow จับได้: เลขอารบิก ไม่มี comma ("5000", "5000.50")"""
    if amount == amount.to_integral_value():
        return str(int(amount))
    return f"{amount:.2f}"
