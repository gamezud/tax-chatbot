"""
เทส deduction_breakdown ใน utils/tax_calculator.py — ค่าลดหย่อนทีละรายการ (ยอดที่กรอก / ยอดที่ใช้สิทธิได้)

calculate_detailed_deductions เรียกฟังก์ชันนี้ เทสเดิมใน test_tax_calculator.py จึงยังล็อก tuple 4 ค่าอยู่
ไฟล์นี้ตรวจส่วนที่เพิ่ม: ลำดับรายการ ยอดแต่ละแถว และผลรวมแต่ละแถวต้องเท่ากับ subtotal (ยอดที่แสดงตรงกับที่หักจริง)
ค่าคาดหวังตามปีภาษี 2569 (docs/tax-rules-2569.md)
"""
import pytest

from utils import tax_rules
from utils.tax_calculator import calculate_detailed_deductions, deduction_breakdown

# ลำดับตามใบแนบรายละเอียดการลดหย่อนของ ภ.ง.ด.90/91 ปีภาษี 2568 (แบบล่าสุดที่มีตอนเขียน)
FORM_ORDER = [
    "personal", "spouse", "child_first", "child_second_onward", "parents",
    "life_insurance", "health_insurance", "rmf", "home_loan_interest", "social_security", "thai_esg",
]

FULL = {
    'spouse': 'yes', 'num_children': 3, 'num_parents': 5, 'social_security': 12000,
    'life_insurance': 90000, 'health_insurance': 30000, 'home_loan_interest': 150000,
    'ssf_rmf': 200000, 'thai_esg': 400000,
}


def _used_total(items):
    # ประกันชีวิตกับสุขภาพนับสองแถวแยกกัน (ไม่ใช้ group_used) — ต้องได้เท่ากับ subtotal
    return sum(item["used"] for item in items.values())


def test_items_follow_form_order():
    assert list(deduction_breakdown(0, {})) == FORM_ORDER


@pytest.mark.parametrize("total_income, params", [
    (0, {}),
    (0, {'num_children': 1}),
    (0, {'num_children': 3, 'num_parents': 5}),
    (0, {'life_insurance': 80000, 'health_insurance': 25000}),     # ชนเพดานรวม
    (0, {'life_insurance': 120000, 'health_insurance': 30000}),    # ชนทั้งเพดานสุขภาพและเพดานรวม
    (400000, {'ssf_rmf': 150000, 'thai_esg': 150000}),             # ชน 30% ของเงินได้
    (480000, FULL),
    (3000000, {'ssf_rmf': 500000, 'thai_esg': 300000}),
])
def test_used_sums_to_subtotal(total_income, params):
    items = deduction_breakdown(total_income, params)
    subtotal, life_health, retirement, thai_esg = calculate_detailed_deductions(total_income, params)
    assert _used_total(items) == pytest.approx(subtotal)
    assert items["life_insurance"]["used"] + items["health_insurance"]["used"] == pytest.approx(life_health)
    assert items["life_insurance"]["group_used"] == life_health
    assert items["rmf"]["used"] == retirement
    assert items["thai_esg"]["used"] == thai_esg


def test_each_item_entered_and_used():
    items = deduction_breakdown(480000, FULL)
    assert items["personal"] == {"entered": None, "used": 60000.0}
    assert items["spouse"] == {"entered": True, "used": 60000.0}
    # บุตร 3 คน: คนแรก 1 คน 30,000 · คนที่ 2 ขึ้นไป 2 คน 2 × 60,000
    assert items["child_first"] == {"entered": 1, "used": 30000.0}
    assert items["child_second_onward"] == {"entered": 2, "used": 120000.0}
    # กรอก 5 คน ใช้ได้ parent_max_count (4) × 30,000
    assert items["parents"] == {"entered": 5, "used": 120000.0}
    # สุขภาพ 30,000 ตัดที่ 25,000 · ชีวิต 90,000 + 25,000 = 115,000 ตัดที่เพดานรวม 100,000 → ชีวิตใช้ได้ 75,000
    assert items["health_insurance"] == {"entered": 30000.0, "used": 25000.0}
    assert items["life_insurance"] == {"entered": 90000.0, "used": 75000.0, "group_used": 100000.0}
    # RMF 200,000 ไม่เกิน 30% × 480,000 = 144,000 → 144,000
    assert items["rmf"] == {"entered": 200000.0, "used": pytest.approx(144000.0)}
    assert items["home_loan_interest"] == {"entered": 150000.0, "used": 100000.0}
    assert items["social_security"] == {"entered": 12000.0, "used": 10500.0}
    # ThaiESG 400,000 ไม่เกิน 30% × 480,000 = 144,000
    assert items["thai_esg"] == {"entered": 400000.0, "used": pytest.approx(144000.0)}


@pytest.mark.parametrize("num_children, first, later", [
    (0, (0, 0.0), (0, 0.0)),
    (1, (1, 30000.0), (0, 0.0)),
    (2, (1, 30000.0), (1, 60000.0)),
])
def test_children_split_into_two_rows(num_children, first, later):
    items = deduction_breakdown(0, {'num_children': num_children})
    assert (items["child_first"]["entered"], items["child_first"]["used"]) == first
    assert (items["child_second_onward"]["entered"], items["child_second_onward"]["used"]) == later


def test_reads_rules_at_call_time(monkeypatch):
    # ค่าที่ระบบ Admin แทน tax_rules.TAX_RULES ต้องมีผลกับยอดแต่ละแถว (อ่านตอนถูกเรียก — กฎเหล็กข้อ 2)
    rules = {**tax_rules.TAX_RULES, "deduction": {**tax_rules.TAX_RULES["deduction"], "home_loan_interest_cap": 50000.0}}
    monkeypatch.setattr(tax_rules, "TAX_RULES", rules)
    assert deduction_breakdown(0, {'home_loan_interest': 80000})["home_loan_interest"] == {
        "entered": 80000.0, "used": 50000.0}
