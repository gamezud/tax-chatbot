"""
เทส utils/rules_store.py — ตรวจและรวมค่าที่ระบบ Admin แก้เข้ากับค่าตั้งต้น

ไม่ต่อ Firestore: override ในเทสคือ dict รูปเดียวกับที่จะอ่านจาก Firestore
เทสที่แทนที่ tax_rules.TAX_RULES ใช้ monkeypatch เท่านั้น ไม่งั้นไฟล์ที่รันต่อจะได้ค่าที่แก้ (เช่น test_line_verified_cases.py)
"""
import copy

import pytest

from utils import rules_store, tax_rules
from utils.rules_store import (
    DEFAULT_TAX_RULES,
    EDITABLE_SCHEMA,
    NOT_EDITABLE,
    brackets_to_firestore,
    build_rules,
    merge_rules,
    to_override,
    validate_rules,
)
from utils.tax_calculator import calculate_detailed_deductions, compute_tax_from_net


def _leaf_paths(node, path=()):
    """path ของค่าทุกตัว — tax_brackets นับเป็นค่าเดียว"""
    if isinstance(node, dict):
        paths = set()
        for key, value in node.items():
            paths |= _leaf_paths(value, path + (key,))
        return paths
    return {path}


def _brackets_with(change):
    """ขั้นบันไดตั้งต้นในรูป Firestore แล้วให้ change แก้ list นั้น"""
    items = brackets_to_firestore(DEFAULT_TAX_RULES["tax_brackets"])
    change(items)
    return {"tax_brackets": items}


# ---------------------------------------------------------------------------
# A. ค่าตั้งต้นและรายการ key
# ---------------------------------------------------------------------------
def test_every_tax_rules_key_is_editable_or_not_editable():
    # key ใหม่ใน tax_rules.py ต้องตัดสินใจว่าให้ admin แก้ได้หรือไม่ (เพิ่มใน EDITABLE_SCHEMA หรือ NOT_EDITABLE)
    editable = _leaf_paths(EDITABLE_SCHEMA)
    assert editable.isdisjoint(NOT_EDITABLE)
    assert editable | NOT_EDITABLE == _leaf_paths(tax_rules.TAX_RULES)


def test_defaults_are_a_copy_of_tax_rules():
    assert DEFAULT_TAX_RULES == tax_rules.TAX_RULES
    assert DEFAULT_TAX_RULES is not tax_rules.TAX_RULES


def test_defaults_pass_validation():
    assert validate_rules(DEFAULT_TAX_RULES) == []


def test_empty_override_gives_defaults_as_new_dict():
    rules, errors = build_rules({})
    assert errors == []
    assert rules == DEFAULT_TAX_RULES
    assert rules is not DEFAULT_TAX_RULES
    assert rules["deduction"] is not DEFAULT_TAX_RULES["deduction"]


def test_round_trip_through_firestore_shape():
    override = to_override(DEFAULT_TAX_RULES)
    # Firestore เก็บ list ซ้อน list ไม่ได้ ขั้นบันไดต้องเป็น list ของ map
    assert all(isinstance(item, dict) for item in override["tax_brackets"])
    rules, errors = build_rules(override)
    assert errors == []
    assert rules == DEFAULT_TAX_RULES


def test_to_override_has_only_editable_keys():
    assert _leaf_paths(to_override(DEFAULT_TAX_RULES)) == _leaf_paths(EDITABLE_SCHEMA)


# ---------------------------------------------------------------------------
# B. การรวมค่า
# ---------------------------------------------------------------------------
def test_partial_nested_override_keeps_other_keys():
    rules, errors = build_rules({"deduction": {"personal": 70000.0}})
    assert errors == []
    assert rules["deduction"]["personal"] == 70000.0
    # key อื่นใน deduction ยังอยู่ — merge ชั้นบนสุดอย่างเดียวจะทำให้ทั้งก้อนหาย
    expected = copy.deepcopy(DEFAULT_TAX_RULES)
    expected["deduction"]["personal"] = 70000.0
    assert rules == expected


def test_nested_rental_rate_override_keeps_unmatched():
    rules, errors = build_rules({"expense": {"rental_rate": {"land": 0.10}}})
    assert errors == []
    assert rules["expense"]["rental_rate"]["land"] == 0.10
    assert rules["expense"]["rental_rate"]["unmatched"] == DEFAULT_TAX_RULES["expense"]["rental_rate"]["unmatched"]


def test_brackets_override_replaces_whole_list():
    override = _brackets_with(lambda items: items[1].update(rate=0.06))
    rules, errors = build_rules(override)
    assert errors == []
    expected = list(DEFAULT_TAX_RULES["tax_brackets"])
    expected[1] = (expected[1][0], 0.06)
    assert rules["tax_brackets"] == expected


def test_merge_does_not_change_inputs():
    base = copy.deepcopy(DEFAULT_TAX_RULES)
    override = {"deduction": {"personal": 1.0}, "method_2": {"rate": 0.01}}
    override_before = copy.deepcopy(override)
    merge_rules(base, override)
    assert base == DEFAULT_TAX_RULES
    assert override == override_before


def test_build_rules_does_not_change_defaults():
    build_rules({"deduction": {"personal": 1.0}})
    build_rules(_brackets_with(lambda items: items[0].update(upper=100000)))
    assert DEFAULT_TAX_RULES == tax_rules.TAX_RULES


# ---------------------------------------------------------------------------
# C. ค่าผิด → ปฏิเสธทั้งชุด และบอกชื่อ key ที่ผิด
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("override, key_in_message", [
    # ค่าเดี่ยว
    ({"deduction": {"personal": -1.0}}, "deduction.personal"),
    ({"deduction": {"personal": float("nan")}}, "deduction.personal"),
    ({"deduction": {"personal": float("inf")}}, "deduction.personal"),
    ({"deduction": {"personal": True}}, "deduction.personal"),
    ({"deduction": {"personal": "60000"}}, "deduction.personal"),
    ({"deduction": {"personal": None}}, "deduction.personal"),
    ({"deduction": {"personal": {"amount": 1}}}, "deduction.personal"),
    ({"deduction": {"donation_rate": 1.5}}, "deduction.donation_rate"),
    ({"deduction": {"donation_rate": -0.1}}, "deduction.donation_rate"),
    ({"method_2": {"rate": 2}}, "method_2.rate"),
    # อัตราที่ข้อความถึงผู้ใช้แสดงเป็นจำนวนเต็ม %
    ({"expense": {"online_sale_rate": 0.605}}, "expense.online_sale_rate"),
    ({"expense": {"rental_rate": {"land": 0.125}}}, "expense.rental_rate.land"),
    # ข้าม key
    ({"deduction": {"health_insurance_cap": 150000.0}}, "deduction.life_health_combined_cap"),
    ({"deduction": {"rmf_cap": 600000.0}}, "deduction.retirement_group_cap"),
    # key ที่ไม่รู้จัก / ห้ามแก้ / ผิดรูป
    ({"salary_capp": 1}, "salary_capp"),
    ({"deduction": {"personall": 1.0}}, "deduction.personall"),
    ({"tax_year": 2570}, "tax_year"),
    ({"deduction": {"parent_max_count": 5}}, "deduction.parent_max_count"),
    ({"deduction": {"ssf_last_tax_year": 2568}}, "deduction.ssf_last_tax_year"),
    ({"expense": {"rental_rate": {"unmatched": 0.10}}}, "expense.rental_rate.unmatched"),
    ({"deduction": 5}, "deduction"),
    # ขั้นบันได
    ({"tax_brackets": "ไม่ใช่ list"}, "tax_brackets"),
    (_brackets_with(lambda items: items.pop()), "tax_brackets"),
    (_brackets_with(lambda items: items[2].update(upper=200000)), "tax_brackets"),   # ไม่เรียง
    (_brackets_with(lambda items: items[0].update(upper=0)), "tax_brackets"),
    (_brackets_with(lambda items: items[-1].update(upper=9000000)), "tax_brackets"),  # ขั้นสุดท้ายมีเพดาน
    (_brackets_with(lambda items: items[3].update(upper=None)), "tax_brackets"),      # None กลางทาง
    (_brackets_with(lambda items: items[4].update(rate=0.01)), "tax_brackets"),       # อัตราลดลง
    (_brackets_with(lambda items: items[1].update(rate=0.055)), "tax_brackets"),      # ไม่เป็นจำนวนเต็ม %
    (_brackets_with(lambda items: items[1].update(rate=None)), "tax_brackets"),
    (_brackets_with(lambda items: items[1].pop("rate")), "tax_brackets"),
    (_brackets_with(lambda items: items[1].update(extra=1)), "tax_brackets"),
])
def test_invalid_override_is_rejected(override, key_in_message):
    rules, errors = build_rules(override)
    assert rules is None
    assert any(key_in_message in e for e in errors), errors


def test_override_that_is_not_a_dict_is_rejected():
    rules, errors = build_rules(["deduction"])
    assert rules is None
    assert errors


def test_one_bad_key_rejects_valid_changes_too():
    # ผิดข้อเดียวปฏิเสธทั้งชุด — ค่า personal ที่ถูกก็ไม่ถูกใช้
    rules, errors = build_rules({"deduction": {"personal": 70000.0}, "salary_capp": 1})
    assert rules is None
    assert len(errors) == 1


def test_all_errors_are_reported():
    rules, errors = build_rules({"deduction": {"personal": -1.0, "donation_rate": 1.5}})
    assert rules is None
    assert any("deduction.personal" in e for e in errors)
    assert any("deduction.donation_rate" in e for e in errors)


def test_whole_percent_accepts_float_noise():
    # 0.15 * 100 = 15.000000000000002 ใน float ต้องนับเป็นจำนวนเต็ม %
    rules, errors = build_rules({"expense": {"rental_rate": {"land": 0.15, "building": 0.07}}})
    assert errors == []


def test_cross_key_equal_is_allowed():
    rules, errors = build_rules({"deduction": {"health_insurance_cap": 100000.0, "rmf_cap": 500000.0}})
    assert errors == []


def test_rates_and_caps_may_be_zero():
    # เช่น ปีที่ไม่มีสิทธิ ThaiESG แล้ว
    rules, errors = build_rules({"deduction": {"thai_esg_cap": 0.0, "thai_esg_income_rate": 0.0}})
    assert errors == []


# ---------------------------------------------------------------------------
# D. ค่าที่รวมแล้วใช้กับตัวคำนวณได้จริง
# ---------------------------------------------------------------------------
def test_round_tripped_brackets_give_same_tax(monkeypatch):
    rules, _ = build_rules(to_override(DEFAULT_TAX_RULES))
    monkeypatch.setattr(tax_rules, "TAX_RULES", rules)
    assert compute_tax_from_net(1000000) == pytest.approx(115000.0)


def test_valid_override_reaches_calculator(monkeypatch):
    # กันระบบ Admin ที่บันทึกได้แต่ไม่มีผล: personal ที่แก้ต้องไปถึง calculate_detailed_deductions
    before, *_ = calculate_detailed_deductions(500000.0, {})
    rules, _ = build_rules({"deduction": {"personal": DEFAULT_TAX_RULES["deduction"]["personal"] + 10000.0}})
    monkeypatch.setattr(tax_rules, "TAX_RULES", rules)
    after, *_ = calculate_detailed_deductions(500000.0, {})
    assert after == before + 10000.0


def test_module_reads_defaults_not_live_tax_rules(monkeypatch):
    # ค่าตั้งต้นต้องไม่เปลี่ยนตาม tax_rules.TAX_RULES ที่ถูกแทนที่ตอนรัน (ไม่งั้นค่าที่แก้จะซ้อนทับกันทุกครั้งที่โหลด)
    replaced, _ = build_rules({"deduction": {"personal": 1.0}})
    monkeypatch.setattr(tax_rules, "TAX_RULES", replaced)
    rules, _ = build_rules({})
    assert rules["deduction"]["personal"] == rules_store.DEFAULT_TAX_RULES["deduction"]["personal"]
    assert rules["deduction"]["personal"] != 1.0
