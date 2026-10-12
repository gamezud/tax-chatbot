from decimal import Decimal

import pytest

from utils.receipt_parser import extract_donation_amount, format_amount, has_donation_keyword


def ok(amount):
    return {"status": "ok", "amount": Decimal(amount)}


NOT_FOUND = {"status": "not_found", "amount": None}
AMBIGUOUS = {"status": "ambiguous", "amount": None}
THAI_DIGITS = {"status": "thai_digits", "amount": None}


# ---------------------------------------------------------------------------
# รูปแบบยอดเงิน
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text, expected", [
    ("จำนวนเงิน 5,000 บาท", ok("5000")),
    ("5,000.00 บาท", ok("5000")),                   # ทศนิยม ไม่มีคำสำคัญแต่มี "บาท"
    ("1,250.50 บาท", ok("1250.50")),
    ("฿1,500", ok("1500")),
    ("จำนวน 2,500.-", ok("2500")),                   # ".-" ท้ายยอด
    ("จำนวนเงิน 3000", ok("3000")),                  # คำสำคัญอย่างเดียว ไม่มี "บาท"
    ("ยอดรวม 12,345 บาท", ok("12345")),
    ("Amount 800 THB", ok("800")),
])
def test_amount_formats(text, expected):
    assert extract_donation_amount(text) == expected


# ---------------------------------------------------------------------------
# ไม่เดา
# ---------------------------------------------------------------------------
def test_same_amount_twice_is_one_amount():
    text = "จำนวนเงิน 5,000.00 บาท\nรวม 5,000 บาท"
    assert extract_donation_amount(text) == ok("5000")


def test_different_amounts_are_ambiguous():
    text = "จำนวนเงิน 1,500 บาท\nจำนวนเงิน 2,000 บาท"
    assert extract_donation_amount(text) == AMBIGUOUS


@pytest.mark.parametrize("text", [
    "",
    None,
    "ใบอนุโมทนาบัตร",
    "ใบเสร็จ 5000",          # ตัวเลขไม่มีคำสำคัญและไม่มี "บาท"
    "จำนวนเงิน 0.00 บาท",     # ยอดศูนย์ไม่ใช่เงินบริจาค
])
def test_not_found(text):
    assert extract_donation_amount(text) == NOT_FOUND


# ---------------------------------------------------------------------------
# ตัวเลขที่ไม่ใช่ยอดเงิน แม้อยู่บรรทัดเดียวกับคำสำคัญ
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "วันที่ 11 ตุลาคม 2569 จำนวนเงิน 5,000 บาท",
    "11 ต.ค. 69 รวม 5,000 บาท",
    "วันที่ 11/10/2569 จำนวนเงิน 5,000 บาท",
    "พ.ศ. 2569 จำนวนเงิน 5,000 บาท",
    "เลขที่ 000123 จำนวนเงิน 5,000 บาท",
    "เลขที่ใบเสร็จ 000123 จำนวนเงิน 5,000 บาท",
    "No. 000123 Amount 5,000 THB",
    "Donor: Test Amount 5,000 THB",           # "no" ใน Donor ไม่ใช่ "No."
    "เลขประจำตัวผู้เสียภาษี 0994000123456 รวม 5,000 บาท",
    "เลขประจำตัว 0-9940-00123-45-6 รวม 5,000 บาท",
    "โทร 02-123-4567 รวม 5,000 บาท",
])
def test_ignores_non_amount_numbers(text):
    assert extract_donation_amount(text) == ok("5000")


@pytest.mark.parametrize("text", [
    "ยอดบริจาค 5,000 บาท หักลดหย่อนได้ 2 เท่า",           # "2 เท่า" เป็นหน่วยนับ
    "รวม 5,000 บาท (คิดเป็น 10% ของเงินได้)",              # ตัวเลขตามด้วย % ไม่ใช่ยอด
    "วันที่ 11/10/2569 เวลา 10:30 น. รวม 5,000 บาท",       # เวลา
    "ปีภาษี 2569 ยอดบริจาค 5,000 บาท",                     # ปีภาษี
])
def test_ignores_count_percent_time_and_tax_year(text):
    assert extract_donation_amount(text) == ok("5000")


# ---------------------------------------------------------------------------
# ตัวเลขที่ OCR อ่านเพี้ยน: ตัวเลขไม่ "จบสะอาด" ต้องได้ ambiguous ไม่ใช่ยอดครึ่งเดียว
# ห้ามแก้ O เป็น 0 เอง เพราะนั่นคือการเดา
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "จำนวนเงิน 5.000 บาท",         # จุดแทน comma → เคยได้ 5
    "รวม 5 000 บาท",               # ช่องว่างแทน comma → เคยได้ 5
    "ยอดรวม 5,OOO บาท",            # O แทน 0 → เคยได้ 5
    "จำนวนเงิน 1O,000 บาท",        # O แทน 0 → เคยได้ 1
    "จำนวนเงิน 5,000.0O บาท",      # O แทน 0 ในทศนิยม → เคยได้ 5000.0
    "รวม 5 OOO บาท",               # ช่องว่างแล้วตามด้วยอักษรละติน
    "5,OOO บาท",                   # ไม่มีคำสำคัญ แต่บรรทัดมี "บาท"
])
def test_garbled_number_is_ambiguous(text):
    assert extract_donation_amount(text) == AMBIGUOUS


# ---------------------------------------------------------------------------
# ยอดรูปแบบเงินนอกบรรทัดเงิน: OCR ทำคำเงินและสกุลเงินของบรรทัดหนึ่งหาย บรรทัดนั้นจึงไม่ถูกนับ
# ถ้าเหลือยอดเดียวจากบรรทัดอื่น จะได้ ok ด้วยยอดที่อาจผิด (ผลวัดใบเสร็จจำลอง A1: ได้ยอดรวมแทน)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "ใบอนุโมทนาบัตร\nจานวนเงน 27,100 บาห\nรวม 27,200 บาท",   # คำเงินและ "บาท" ถูกอ่านเพี้ยน
    "27,100\nรวม 27,200 บาท",
    "1,250.50\nจำนวนเงิน 1,300 บาท",                          # ทศนิยม 2 ตำแหน่งนับเป็นรูปแบบเงิน
])
def test_money_shaped_number_outside_money_line_is_ambiguous(text):
    assert extract_donation_amount(text) == AMBIGUOUS


@pytest.mark.parametrize("text", [
    "99 ถนนสมมติ แขวงทดสอบ 10400\nจำนวนเงิน 5,000 บาท",     # บ้านเลขที่และรหัสไปรษณีย์ไม่มีจุลภาคหรือทศนิยม
    "5,000\nจำนวนเงิน 5,000 บาท",                             # ค่าเท่ากับยอด ไม่ขัดกัน
    "5,000.00\nรวม 5,000 บาท",
    "ผู้ร่วมบริจาค 1,200 คน\nจำนวนเงิน 5,000 บาท",            # ตามด้วยหน่วยนับ ไม่ใช่เงิน
    "วันที่ 11/10/2569 เวลา 10.30 น.\nจำนวนเงิน 5,000 บาท",   # วันที่และเวลาถูกลบก่อน
])
def test_other_numbers_outside_money_line_keep_ok(text):
    assert extract_donation_amount(text) == ok("5000")


def test_sara_am_split_by_ocr():
    # Tesseract เขียน ำ (U+0E33) เป็น ํ + า (U+0E4D U+0E32) — "จำนวนเงิน" ต้องยังเป็นคำสำคัญ
    # ไม่มี "บาท" ในข้อความ ยอดจึงได้มาจากคำสำคัญอย่างเดียว
    split_am = "\N{THAI CHARACTER NIKHAHIT}\N{THAI CHARACTER SARA AA}"
    assert extract_donation_amount(f"จ{split_am}นวนเงิน 3,000") == ok("3000")


def test_thai_date_alone_is_not_amount():
    assert extract_donation_amount("วันที่ 11 ตุลาคม 2569\n11 ต.ค. 69") == NOT_FOUND


def test_receipt_number_alone_is_not_amount():
    assert extract_donation_amount("เลขที่ 000123") == NOT_FOUND


def test_item_count_is_not_amount():
    # "จำนวน" เฉย ๆ ไม่ใช่คำสำคัญ และ "1 รายการ" เป็นหน่วยนับ
    assert extract_donation_amount("จำนวน 1 รายการ") == NOT_FOUND
    assert extract_donation_amount("จำนวน 1 รายการ\nรวม 1 รายการ 5,000 บาท") == ok("5000")


def test_amount_in_words_is_ignored():
    # ยอดเป็นตัวอักษรไม่มีตัวเลข ไม่ทำให้ผลเปลี่ยน และไม่ถูกนับเอง
    assert extract_donation_amount("5,000 บาท (ห้าพันบาทถ้วน)") == ok("5000")
    assert extract_donation_amount("(ห้าพันบาทถ้วน)") == NOT_FOUND


def test_receipt_like_text():
    # ยอดเป็นเลขไทย → thai_digits (ดูหัวข้อเลขไทยข้างล่าง) ตัวเลขอื่นในใบยังถูกลบตามเดิม
    text = (
        "มูลนิธิตัวอย่างเพื่อการทดสอบ\n"
        "ใบอนุโมทนาบัตร เลขที่ ๐๐๐๑๒๓\n"
        "วันที่ ๑๑ ตุลาคม ๒๕๖๙\n"
        "ได้รับเงินจาก นายทดสอบ ระบบ\n"
        "จำนวน 1 รายการ\n"
        "จำนวนเงิน ๕,๐๐๐.๐๐ บาท (ห้าพันบาทถ้วน)\n"
        "โทร ๐๒-๑๒๓-๔๕๖๗\n"
    )
    assert extract_donation_amount(text) == THAI_DIGITS


# ---------------------------------------------------------------------------
# เลขไทย: Tesseract อ่านเลขไทยเป็นเลขไทยตัวอื่น (๙→๓, ๘→๕ ...) แล้วตัวเลขยังจบสะอาด
# ผลวัดใบเสร็จจำลอง (scripts/measure_ocr.py) อ่านยอดเลขไทยถูก 0/42 รูป ทั้ง Tahoma และ TH Sarabun
# ยอดที่เลือกได้ตรงกับตัวเลขที่เขียนด้วยเลขไทย → ไม่เชื่อ ให้ผู้ใช้พิมพ์เอง
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "จำนวนเงิน ๕,๐๐๐ บาท",
    "จำนวนเงิน ๙,๓๖๙.๐๐ บาท",
    "จำนวนเงิน 5,000 บาท\nรวม ๕,๐๐๐ บาท",       # ยอดเดียวกันมีทั้งอารบิกและเลขไทย — ไม่รู้ว่า OCR อ่านตัวไหนถูก
])
def test_thai_digit_amount_is_not_trusted(text):
    assert extract_donation_amount(text) == THAI_DIGITS


@pytest.mark.parametrize("text", [
    "วันที่ ๑๑ ตุลาคม ๒๕๖๙\nจำนวนเงิน 5,000 บาท",   # เลขไทยแค่ในวันที่ ยอดเป็นอารบิก
    "เลขที่ ๐๐๐๑๒๓\nจำนวนเงิน 5,000 บาท",
    "รวม 1 รายการ ๒ ฉบับ 5,000 บาท",                  # เลขไทยที่ค่าไม่เท่ากับยอด (เทียบด้วยค่า ไม่ใช่ดูแค่ว่ามีเลขไทย)
])
def test_thai_digits_elsewhere_do_not_block_arabic_amount(text):
    assert extract_donation_amount(text) == ok("5000")


# ---------------------------------------------------------------------------
# หลักฐานการบริจาค
# ---------------------------------------------------------------------------
INSURANCE_RECEIPT = (
    "บริษัท ประกันชีวิตตัวอย่าง จำกัด\n"
    "ใบเสร็จรับเงิน\n"
    "เบี้ยประกันชีวิต\n"
    "จำนวนเงินที่ได้รับ 35,000.00 บาท"
)


def test_insurance_receipt_is_not_donation():
    # parser หายอดเจอ คำบริจาคจึงเป็นตัวกันเพียงอย่างเดียวไม่ให้เบี้ยประกันกลายเป็นเงินบริจาค (กฎเหล็กข้อ 2)
    assert extract_donation_amount(INSURANCE_RECEIPT) == ok("35000")
    assert has_donation_keyword(INSURANCE_RECEIPT) is False


@pytest.mark.parametrize("text", [
    "ใบอนุโมทนาบัตร",
    "ยอดบริจาค 5,000 บาท",
    "e-Donation",
    "DONATION RECEIPT",
    # OCR แทรกช่องว่างกลางคำ
    "บริ จาค",
    "อนุ โมท นา",
    "e- Donation",
    "e-Dona tion",
    # คำบริจาคอยู่ใกล้เลขที่เอกสาร — ต้องไม่ใช้ขั้นลบเลขที่เอกสารของ extract_donation_amount
    "ใบรับเงิน e-Donation เลขที่ ED-2569/001 จำนวน 5,000 บาท",
    "เลขที่ใบรับ DONATION-2569-001 จำนวน 5,000 บาท",  # ขั้นลบเลขที่เอกสารลบ "DONATION-2569-001" ทั้งก้อน
])
def test_donation_keywords(text):
    assert has_donation_keyword(text) is True


@pytest.mark.parametrize("text", [
    "",
    None,
    INSURANCE_RECEIPT,
    "เงินสมทบกองทุนประกันสังคม 750 บาท",    # "สมทบ" ไม่นับ
    "มูลนิธิตัวอย่าง จำนวนเงิน 5,000 บาท",   # ชื่อผู้รับไม่ได้บอกว่าเป็นการบริจาค
])
def test_not_donation_keywords(text):
    assert has_donation_keyword(text) is False


# ---------------------------------------------------------------------------
# ข้อความที่จะเติมในช่องบนหน้า LIFF
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("amount, expected", [
    (Decimal("5000"), "5000"),
    (Decimal("5000.00"), "5000"),
    (Decimal("1250.5"), "1250.50"),
    (Decimal("12345.67"), "12345.67"),
])
def test_format_amount(amount, expected):
    assert format_amount(amount) == expected
