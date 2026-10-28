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


def _amounts(item):
    # ยอดของรายการโดยไม่รวม limits (limits มีเทสของตัวเองข้างล่าง)
    return {k: v for k, v in item.items() if k != "limits"}


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
    items = {key: _amounts(item) for key, item in deduction_breakdown(480000, FULL).items()}
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
        "entered": 80000.0, "used": 50000.0,
        "limits": [{"rule": "home_loan_interest_cap", "limit": 50000.0, "rate": None}]}


# ---------------------------------------------------------------------------
# limits: เพดานที่ตัดยอดจริง (PDF ใช้บอกว่าแถวไหนถูกตัดที่เพดานอะไร)
# ---------------------------------------------------------------------------
def _limits(total_income, params):
    return {key: item["limits"] for key, item in deduction_breakdown(total_income, params).items()}


def test_no_limits_when_under_caps():
    params = {'spouse': 'yes', 'num_children': 2, 'num_parents': 4, 'social_security': 10500,
              'life_insurance': 75000, 'health_insurance': 25000, 'home_loan_interest': 100000,
              'ssf_rmf': 100000, 'thai_esg': 100000}
    assert all(limits == [] for limits in _limits(1000000, params).values())


@pytest.mark.parametrize("total_income, params, key, expected", [
    (0, {'num_parents': 5}, "parents", [{"rule": "parent_max_count", "limit": 4, "rate": None}]),
    (0, {'social_security': 12000}, "social_security",
        [{"rule": "social_security_cap", "limit": 10500.0, "rate": None}]),
    (0, {'health_insurance': 30000}, "health_insurance",
        [{"rule": "health_insurance_cap", "limit": 25000.0, "rate": None}]),
    (0, {'life_insurance': 120000}, "life_insurance",
        [{"rule": "life_health_combined_cap", "limit": 100000.0, "rate": None}]),
    (0, {'home_loan_interest': 150000}, "home_loan_interest",
        [{"rule": "home_loan_interest_cap", "limit": 100000.0, "rate": None}]),
    # RMF ติด 30% × 400,000 = 120,000
    (400000, {'ssf_rmf': 150000}, "rmf",
        [{"rule": "rmf_income_rate", "limit": pytest.approx(120000.0), "rate": 0.30}]),
    # RMF ติด rmf_cap 500,000 (30% = 600,000) — ไม่ขึ้น retirement_group_cap แม้ค่าเท่ากัน เพราะเพดานกลุ่มไม่ได้ลดยอดลงอีก
    (2000000, {'ssf_rmf': 600000}, "rmf", [{"rule": "rmf_cap", "limit": 500000.0, "rate": None}]),
    # ThaiESG ติด 30% × 500,000 = 150,000
    (500000, {'thai_esg': 200000}, "thai_esg",
        [{"rule": "thai_esg_income_rate", "limit": pytest.approx(150000.0), "rate": 0.30}]),
    (2000000, {'thai_esg': 400000}, "thai_esg", [{"rule": "thai_esg_cap", "limit": 300000.0, "rate": None}]),
    # 30% × 1,000,000 = 300,000 เท่ากับ thai_esg_cap พอดี → ขึ้นทั้งสองเพดาน
    (1000000, {'thai_esg': 400000}, "thai_esg",
        [{"rule": "thai_esg_income_rate", "limit": pytest.approx(300000.0), "rate": 0.30},
         {"rule": "thai_esg_cap", "limit": 300000.0, "rate": None}]),
])
def test_limit_for_each_cap(total_income, params, key, expected):
    limits = _limits(total_income, params)
    assert limits[key] == expected
    # รายการอื่นไม่ถูกตัด
    assert all(v == [] for k, v in limits.items() if k != key)


def test_life_cut_only_by_combined_cap():
    # ประกันชีวิต 90,000 ไม่ถึงเพดานรวมถ้านับเดี่ยว ๆ แต่รวมกับสุขภาพ 20,000 = 110,000 เกินเพดานรวม 100,000
    # → ประกันชีวิตใช้ได้ 80,000 และหมายเหตุต้องอ้างเพดานรวม ส่วนประกันสุขภาพไม่ถูกตัด (ไม่เกิน health_insurance_cap)
    items = deduction_breakdown(0, {'life_insurance': 90000, 'health_insurance': 20000})
    assert items["life_insurance"]["used"] == 80000.0
    assert items["life_insurance"]["limits"] == [
        {"rule": "life_health_combined_cap", "limit": 100000.0, "rate": None}]
    assert items["health_insurance"]["limits"] == []


def test_limit_ignores_float_dust():
    # ต่างกันไม่ถึงสตางค์ไม่นับว่าถูกตัด (แบบเดียวกับหมายเหตุเงินบริจาค)
    assert _limits(0, {'health_insurance': 25000.004})["health_insurance"] == []


def test_retirement_group_cap_when_it_cuts(monkeypatch):
    # rules_store ไม่ยอมให้ rmf_cap เกิน retirement_group_cap จึงเกิดผ่านระบบ Admin ไม่ได้ — ตั้งตรง ๆ เพื่อตรวจโค้ดสาขานี้
    rules = {**tax_rules.TAX_RULES, "deduction": {**tax_rules.TAX_RULES["deduction"], "retirement_group_cap": 400000.0}}
    monkeypatch.setattr(tax_rules, "TAX_RULES", rules)
    assert _limits(3000000, {'ssf_rmf': 450000})["rmf"] == [
        {"rule": "retirement_group_cap", "limit": 400000.0, "rate": None}]
