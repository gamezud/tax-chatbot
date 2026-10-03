"""
เทสสูตรภาษีใน utils/tax_calculator.py

หลักการแบ่งเคส
- เทสธรรมดา      : ค่าคาดหวังคำนวณจากกฎหมาย/สูตร และโค้ดให้ผลตรงแล้ว
- xfail(strict)   : บั๊กชัดเจน ค่าคาดหวังคือ "ค่าที่ถูก" — วันที่แก้บั๊ก เทสจะ XPASS แล้วฟ้องให้ถอด xfail
- test_current_behavior_ : เรื่องที่ยังไม่ได้ตัดสินใจ ล็อกค่าที่ได้จากการรันจริงไว้
                           เพื่อให้รู้ทันทีถ้าพฤติกรรมเปลี่ยนระหว่าง refactor
"""
import pytest

from utils.tax_calculator import (
    clean_number,
    compute_tax_from_net,
    compute_method_2_tax,
    get_rental_expense_rate,
    get_tax_bracket_rate,
    calculate_detailed_deductions,
)


# ---------------------------------------------------------------------------
# A. ขั้นบันไดภาษี compute_tax_from_net
# ภาษีสะสม ณ ปลายขั้น: 150k→0, 300k→7,500, 500k→27,500, 750k→65,000,
#                    1M→115,000, 2M→365,000, 5M→1,265,000
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("net_income, expected", [
    (-50000, 0.0),        # ติดลบถูกปัดเป็น 0 ด้วย max(0, net)
    (0, 0.0),
    (150000, 0.0),        # ขั้นยกเว้น
    (150001, 0.05),       # 1 × 5%
    (200000, 2500.0),     # 50,000 × 5%
    (300000, 7500.0),     # 150,000 × 5%
    (400000, 17500.0),    # 7,500 + 100,000 × 10%
    (500000, 27500.0),    # 7,500 + 200,000 × 10%
    (600000, 42500.0),    # 27,500 + 100,000 × 15%
    (750000, 65000.0),    # 27,500 + 250,000 × 15%
    (900000, 95000.0),    # 65,000 + 150,000 × 20%
    (1000000, 115000.0),  # 65,000 + 250,000 × 20%
    (1500000, 240000.0),  # 115,000 + 500,000 × 25%
    (2000000, 365000.0),  # 115,000 + 1,000,000 × 25%
    (3000000, 665000.0),  # 365,000 + 1,000,000 × 30%
    (5000000, 1265000.0), # 365,000 + 3,000,000 × 30%
    (6000000, 1615000.0), # 1,265,000 + 1,000,000 × 35%
])
def test_compute_tax_from_net(net_income, expected):
    assert compute_tax_from_net(net_income) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# B. get_tax_bracket_rate — อัตราขั้นสูงสุด ทดสอบที่ขอบทุกขั้น
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("net_income, expected", [
    (150000, 0.00), (150001, 0.05),
    (300000, 0.05), (300001, 0.10),
    (500000, 0.10), (500001, 0.15),
    (750000, 0.15), (750001, 0.20),
    (1000000, 0.20), (1000001, 0.25),
    (2000000, 0.25), (2000001, 0.30),
    (5000000, 0.30), (5000001, 0.35),
])
def test_get_tax_bracket_rate(net_income, expected):
    assert get_tax_bracket_rate(net_income) == expected


# ตารางขั้นบันไดถูกเขียนไว้ 2 ที่ (compute_tax_from_net กับ get_tax_bracket_rate)
# เทสนี้กันไม่ให้สองที่เพี้ยนจากกัน: เงินบาทที่เพิ่มขึ้น 1 บาท ต้องเสียภาษีเท่าอัตราขั้นนั้นพอดี
@pytest.mark.parametrize("boundary", [150000, 300000, 500000, 750000, 1000000, 2000000, 5000000])
def test_bracket_tables_agree(boundary):
    for x in (boundary - 1, boundary):
        marginal = compute_tax_from_net(x + 1) - compute_tax_from_net(x)
        assert marginal == pytest.approx(get_tax_bracket_rate(x + 1))


# ---------------------------------------------------------------------------
# C. วิธีที่ 2 (ม.48(2)): 0.5% ของเงินได้ที่ไม่ใช่ 40(1)
#    ใช้เมื่อเงินได้ ≥ 120,000 และถ้าภาษีไม่เกิน 5,000 ได้รับยกเว้น
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("non_salary_income, expected", [
    (0, 0.0),
    (119999, 0.0),        # ต่ำกว่าเกณฑ์ 120,000
    (120000, 0.0),        # 600 ≤ 5,000 → ยกเว้น
    (999999, 0.0),        # 4,999.995 ≤ 5,000 → ยกเว้น
    (1000000, 0.0),       # 5,000 พอดี — "ไม่เกิน 5,000" ยังยกเว้น
    (1000001, 5000.005),  # เกิน 5,000 → ต้องเสีย
    (2000000, 10000.0),
])
def test_compute_method_2_tax(non_salary_income, expected):
    assert compute_method_2_tax(non_salary_income) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# D. อัตราค่าใช้จ่ายค่าเช่า (พ.ร.ฎ. ฉบับที่ 11 มาตรา 5)
#    บ้าน/โรงเรือน/สิ่งปลูกสร้าง 30% · ที่ดินเกษตร 20% · ที่ดินอื่น 15%
#    ยานพาหนะ 30% · ทรัพย์สินอื่น 10%
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("property_type, expected", [
    ("บ้าน", 0.30),
    ("คอนโด", 0.30),
    ("อาคาร", 0.30),
    ("หอพัก", 0.30),
    ("ที่ดินเกษตร", 0.20),
    ("ที่ดิน", 0.15),
])
def test_rental_expense_rate(property_type, expected):
    assert get_rental_expense_rate(property_type) == expected


@pytest.mark.parametrize("property_type", ["รถยนต์", "เรือ", "ยานพาหนะ"])
def test_rental_expense_rate_vehicle_is_30_percent(property_type):
    assert get_rental_expense_rate(property_type) == 0.30


def test_rental_expense_rate_rong_ruean_is_30_percent():
    assert get_rental_expense_rate("โรงเรือน") == 0.30


@pytest.mark.parametrize("property_type, current", [
    ("ที่ดินพร้อมบ้าน", 0.30),  # ปัญหาที่รู้แล้ว #2: เจอ 'บ้าน' ก่อน 'ที่ดิน'
    ("เครื่องจักร", 0.30),      # กฎหมายให้ทรัพย์สินอื่น 10% แต่ else คืน 30% — ยังไม่ตัดสินใจ
    ("", 0.30),
    (None, 0.30),
])
def test_current_behavior_rental_expense_rate(property_type, current):
    assert get_rental_expense_rate(property_type) == current


# ---------------------------------------------------------------------------
# E. ค่าลดหย่อน calculate_detailed_deductions → (subtotal, life_health, ssf)
#    ส่วนตัว 60,000 ได้เสมอ ทุกเคสใส่เฉพาะคีย์ที่กำลังทดสอบ
# ---------------------------------------------------------------------------
PERSONAL = 60000.0


@pytest.mark.parametrize("total_income, params, expected", [
    # ไม่มีอะไรเลย → ส่วนตัวอย่างเดียว
    (0, {}, (PERSONAL, 0.0, 0.0)),

    # คู่สมรส 60,000 (อ่าน 'yes' หลัง strip + lower)
    (0, {'spouse': 'yes'}, (PERSONAL + 60000, 0.0, 0.0)),
    (0, {'spouse': ' YES '}, (PERSONAL + 60000, 0.0, 0.0)),
    (0, {'spouse': 'no'}, (PERSONAL, 0.0, 0.0)),
    (0, {'spouse': ''}, (PERSONAL, 0.0, 0.0)),

    # บุตร: คนแรก 30,000 คนที่ 2 ขึ้นไป 60,000 (สมมติเกิดตั้งแต่ 2561)
    (0, {'num_children': 0}, (PERSONAL, 0.0, 0.0)),
    (0, {'num_children': 1}, (PERSONAL + 30000, 0.0, 0.0)),
    (0, {'num_children': 2}, (PERSONAL + 90000, 0.0, 0.0)),   # 30,000 + 60,000
    (0, {'num_children': 3}, (PERSONAL + 150000, 0.0, 0.0)),  # 30,000 + 2 × 60,000

    # บิดามารดา คนละ 30,000 ไม่เกิน 4 คน
    (0, {'num_parents': 1}, (PERSONAL + 30000, 0.0, 0.0)),
    (0, {'num_parents': 2}, (PERSONAL + 60000, 0.0, 0.0)),
    (0, {'num_parents': 4}, (PERSONAL + 120000, 0.0, 0.0)),
    (0, {'num_parents': 5}, (PERSONAL + 120000, 0.0, 0.0)),   # ตัดที่ 4 คน

    # ประกันสังคม เพดาน 9,000
    (0, {'social_security': 5000}, (PERSONAL + 5000, 0.0, 0.0)),
    (0, {'social_security': 9000}, (PERSONAL + 9000, 0.0, 0.0)),
    (0, {'social_security': 12000}, (PERSONAL + 9000, 0.0, 0.0)),

    # ประกันชีวิต+สุขภาพ รวมไม่เกิน 100,000 / สุขภาพไม่เกิน 25,000
    (0, {'life_insurance': 50000}, (PERSONAL + 50000, 50000.0, 0.0)),
    (0, {'life_insurance': 120000}, (PERSONAL + 100000, 100000.0, 0.0)),
    (0, {'health_insurance': 30000}, (PERSONAL + 25000, 25000.0, 0.0)),
    (0, {'life_insurance': 80000, 'health_insurance': 25000},
        (PERSONAL + 100000, 100000.0, 0.0)),                  # 105,000 → ตัดที่ 100,000

    # ดอกเบี้ยบ้าน เพดาน 100,000
    (0, {'home_loan_interest': 80000}, (PERSONAL + 80000, 0.0, 0.0)),
    (0, {'home_loan_interest': 150000}, (PERSONAL + 100000, 0.0, 0.0)),

    # SSF/RMF: min(ที่ซื้อ, 30% ของเงินได้, 200,000)
    (1000000, {'ssf_rmf': 250000}, (PERSONAL + 200000, 0.0, 200000.0)),  # ติด 200,000
    (400000, {'ssf_rmf': 150000}, (PERSONAL + 120000, 0.0, 120000.0)),   # ติด 30% × 400,000
    (500000, {'ssf_rmf': 100000}, (PERSONAL + 100000, 0.0, 100000.0)),   # ไม่ติดเพดาน
])
def test_deductions_each_item(total_income, params, expected):
    assert calculate_detailed_deductions(total_income, params) == pytest.approx(expected)


def test_deductions_all_items_full():
    params = {
        'spouse': 'yes',
        'num_children': 2,
        'num_parents': 2,
        'social_security': 9000,
        'life_insurance': 100000,
        'health_insurance': 25000,
        'home_loan_interest': 100000,
        'ssf_rmf': 200000,
    }
    # 60,000 + 60,000 + 90,000 + 60,000 + 9,000 + 100,000 + 100,000 + 200,000 = 679,000
    assert calculate_detailed_deductions(1000000, params) == pytest.approx(
        (679000.0, 100000.0, 200000.0))


def test_current_behavior_spouse_as_list_not_counted():
    """ถ้า spouse เป็น list, str(['yes']) ≠ 'yes' จึงไม่ได้ลดหย่อนคู่สมรส

    #12 ปิดแล้ว: บน Console spouse ตั้ง isList=false จึงไม่มาเป็น list ในทางปฏิบัติ
    กรณีนี้ถูกกันไว้ด้วย test_spouse_is_not_list ใน tests/test_dialogflow_export.py
    เทสนี้เก็บไว้เพื่อบันทึกว่าโค้ดเองไม่รองรับ list ถ้าวันหนึ่งการตั้งค่าเปลี่ยน
    """
    assert calculate_detailed_deductions(0, {'spouse': ['yes']}) == pytest.approx(
        (PERSONAL, 0.0, 0.0))


def test_current_behavior_negative_social_security_becomes_positive():
    # clean_number('-5000') ได้ 5000 จึงลดหย่อนประกันสังคมได้ 5,000
    assert calculate_detailed_deductions(0, {'social_security': '-5000'}) == pytest.approx(
        (PERSONAL + 5000, 0.0, 0.0))


# ---------------------------------------------------------------------------
# F. clean_number
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("val, expected", [
    (None, 0.0),
    ("", 0.0),
    ("   ", 0.0),
    ([], 0.0),
    (50000, 50000.0),
    (30000.0, 30000.0),              # Dialogflow ส่งตัวเลขมาเป็น float
    ("50,000", 50000.0),
    (["50000"], 50000.0),
    ("50000.5", 50000.5),
    ("1.5 ล้าน", 1500000.0),
    ("2ล้าน", 2000000.0),
    ("25000", 25000.0),              # regression บั๊ก 1: lookaround ต้องไม่กิน 25000
    ("250000", 250000.0),
    ("2567", 0.0),                   # ปี พ.ศ. โดด ๆ ถูกตัด
    ("ปี 2567", 0.0),
    ("ปี 2567 เงินเดือน 30000", 30000.0),
    ({'amount': 5000, 'currency': 'THB'}, 5000.0),  # รูปแบบ sys.unit-currency
])
def test_clean_number(val, expected):
    assert clean_number(val) == pytest.approx(expected)


@pytest.mark.xfail(strict=True, reason="จำนวนเงิน 2,500–2,599 ถูกมองเป็นปี พ.ศ. แล้วถูกตัดทิ้งเหลือ 0")
@pytest.mark.parametrize("val, expected", [
    ("2500", 2500.0),
    (2500.0, 2500.0),
    ("บริจาค 2550", 2550.0),
    ({'amount': 2550, 'currency': 'THB'}, 2550.0),
])
def test_clean_number_amount_looks_like_buddhist_year(val, expected):
    assert clean_number(val) == pytest.approx(expected)


@pytest.mark.xfail(strict=True, reason="สาขา 'ล้าน' ไม่ตัดปี พ.ศ. ก่อน เลยเอา 2567 มาคูณล้าน")
def test_clean_number_million_with_year():
    assert clean_number("ปี 2567 รายได้ 1 ล้าน") == pytest.approx(1000000.0)


# ค่าด้านล่างทั้งหมดได้จากการรันจริง ไม่ใช่จากการอ่านโค้ด
@pytest.mark.parametrize("val, current", [
    ("เงินเดือน 30000 โบนัส 50000", 30000.0),  # เลือกสตริงยาวสุด เสมอกันเอาตัวแรก
    ("30000.50 หรือ 100000", 30000.5),          # "30000.50" ยาวกว่า "100000"
    ("5 หมื่น", 5.0),                           # ไม่รองรับหมื่น/แสน
    ("ครึ่งล้าน", 0.0),                         # มีคำว่าล้านแต่ไม่มีตัวเลข
    ("-5000", 5000.0),                          # เครื่องหมายลบติดมาเฉพาะรูปแบบที่มีจุดทศนิยม
    ("-1.5", -1.5),                             # ...จึงต่างจากเคสบนนี้
])
def test_current_behavior_clean_number(val, current):
    assert clean_number(val) == pytest.approx(current)
