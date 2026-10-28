"""
เทสตาราง "รายละเอียดค่าลดหย่อนและเงินบริจาค" ใน PDF (utils/pdf_generator.py)

จับ story ด้วย SimpleDocTemplate ปลอม ไม่เขียนไฟล์ (แบบ pdf_story ใน test_rules_version.py)
ยกเว้น smoke test ท้ายไฟล์ที่เขียน PDF จริงลง tmp_path
deduction_detail สร้างจาก deduction_breakdown ตัวจริง รูปเดียวกับที่ handle_deductions ส่งมา
"""
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from reportlab.platypus import Paragraph, Table

from utils import pdf_generator, tax_rules
from utils.pdf_generator import (
    ASSUMPTION_CHILDREN, ASSUMPTION_DONATION, ASSUMPTION_PARENTS, ASSUMPTION_SOCIAL_SECURITY, ASSUMPTION_SPOUSE,
    CHILD_BORN_FROM_LABEL, DEDUCTION_LABELS, LIMIT_NOTE_FALLBACK, LIMIT_NOTES, generate_tax_pdf,
)
from utils.tax_calculator import calculate_detailed_deductions, deduction_breakdown

REPORTS_DIR = Path(__file__).resolve().parent.parent / "static" / "reports"
BASE = {"date": "28/10/2026 10:00", "tax_year": 2569, "user_id": "TEST_USER",
        "salary_total": 480000.0, "total_income": 480000.0, "total_expense": 100000.0, "tax_advice": ""}
SECTION_HEAD = "รายละเอียดค่าลดหย่อนและเงินบริจาค"


def make_data(total_income, params, donation=(0.0, 0.0, []), born_from=2561):
    """tax_data ที่มี deduction_detail รูปเดียวกับ handle_deductions — ยอดรวมมาจาก calculate_detailed_deductions"""
    subtotal = calculate_detailed_deductions(total_income, params)[0]
    entered, used, limits = donation
    detail = {"items": deduction_breakdown(total_income, params), "subtotal": subtotal,
              "donation": {"entered": entered, "used": used, "limits": limits},
              "child_second_onward_born_from": born_from}
    return {**BASE, "total_income": total_income, "total_deduction": subtotal + used, "deduction_detail": detail}


@pytest.fixture
def story(monkeypatch, tmp_path):
    captured = {}

    def fake_template(path, **kwargs):
        doc = MagicMock(name="SimpleDocTemplate")
        doc.build.side_effect = lambda s: captured.setdefault("story", s)
        return doc

    monkeypatch.setattr(pdf_generator, "SimpleDocTemplate", fake_template)

    def _build(tax_data):
        captured.clear()
        generate_tax_pdf(str(tmp_path / "unused.pdf"), tax_data)
        assert not (tmp_path / "unused.pdf").exists()
        return captured["story"]
    return _build


def _texts(flowables):
    # ข้อความทุก Paragraph รวมที่อยู่ในช่องตาราง (ช่องหนึ่งอาจเป็น list)
    for f in flowables:
        if isinstance(f, Paragraph):
            yield f.text
        elif isinstance(f, Table):
            for row in f._cellvalues:
                yield from _texts(row)
        elif isinstance(f, (list, tuple)):
            yield from _texts(f)


def _deduction_table(story):
    head = next(i for i, f in enumerate(story) if isinstance(f, Paragraph) and SECTION_HEAD in f.text)
    return next(f for f in story[head:] if isinstance(f, Table))


def _rows(table):
    # แต่ละแถว: (ข้อความช่องรายการ — รวมหมายเหตุ, ช่องยอดที่กรอก, ช่องใช้สิทธิได้)
    return [tuple(" | ".join(_texts([cell])) if not isinstance(cell, str) else cell for cell in row)
            for row in table._cellvalues[1:]]


FULL = {'spouse': 'yes', 'num_children': 3, 'num_parents': 5, 'social_security': 12000,
        'life_insurance': 90000, 'health_insurance': 30000, 'home_loan_interest': 150000,
        'ssf_rmf': 200000, 'thai_esg': 400000}


# ---------------------------------------------------------------------------
# A. มี/ไม่มีตาราง และลำดับแถว
# ---------------------------------------------------------------------------
def test_old_dict_has_no_section(story):
    # PDF เก่าสร้างไว้แล้วไม่ถูกสร้างใหม่ แต่ dict ที่ไม่มี key นี้ต้องยังสร้างได้และไม่มีตาราง
    texts = list(_texts(story({**BASE, "total_deduction": 60000.0})))
    assert not any(SECTION_HEAD in t for t in texts)
    assert not any("ข้อสมมติ" in t for t in texts)


def test_rows_follow_form_order_and_skip_unentered(story):
    rows = _rows(_deduction_table(story(make_data(480000.0, FULL))))
    labels = [r[0].split(" | ")[0] for r in rows]
    assert labels == [
        "ผู้มีเงินได้", "คู่สมรสที่ไม่มีเงินได้", "บุตรคนแรก",
        "บุตรตั้งแต่คนที่ 2" + CHILD_BORN_FROM_LABEL.format(year=2561),
        "อุปการะเลี้ยงดูบิดามารดา", "เบี้ยประกันชีวิต", "เบี้ยประกันสุขภาพ",
        DEDUCTION_LABELS["rmf"], DEDUCTION_LABELS["home_loan_interest"], "เงินสมทบกองทุนประกันสังคม",
        DEDUCTION_LABELS["thai_esg"], "<b>รวมค่าลดหย่อน</b>",
        "<b>รวมค่าลดหย่อนและเงินบริจาค (เท่ากับข้อ 3 ด้านบน)</b>"]


def test_personal_only(story):
    rows = _rows(_deduction_table(story(make_data(480000.0, {}))))
    assert rows[0] == ("ผู้มีเงินได้", "-", "60,000.00")
    assert [r[0] for r in rows[1:]] == ["<b>รวมค่าลดหย่อน</b>", "<b>รวมค่าลดหย่อนและเงินบริจาค (เท่ากับข้อ 3 ด้านบน)</b>"]


def test_every_breakdown_item_has_label():
    assert set(deduction_breakdown(0, {})) <= set(DEDUCTION_LABELS)


# ---------------------------------------------------------------------------
# B. ช่องยอดที่กรอก / ใช้สิทธิได้
# ---------------------------------------------------------------------------
def test_counts_show_as_people(story):
    rows = {r[0].split(" | ")[0]: r for r in _rows(_deduction_table(story(make_data(480000.0, FULL))))}
    assert rows["บุตรคนแรก"][1] == "1 คน"
    assert rows["บุตรตั้งแต่คนที่ 2" + CHILD_BORN_FROM_LABEL.format(year=2561)][1] == "2 คน"
    assert rows["อุปการะเลี้ยงดูบิดามารดา"][1] == "5 คน"
    assert rows["คู่สมรสที่ไม่มีเงินได้"][1] == "มี"


def test_group_used_not_shown(story):
    # ชีวิต 50,000 + สุขภาพ 20,000 → group_used 70,000 ซึ่งไม่ใช่ยอดของแถวไหน จึงต้องไม่ขึ้นใน PDF
    data = make_data(480000.0, {'life_insurance': 50000, 'health_insurance': 20000})
    assert data["deduction_detail"]["items"]["life_insurance"]["group_used"] == 70000.0
    texts = list(_texts(_deduction_table(story(data))._cellvalues))
    assert not any("70,000.00" in t for t in texts)


def test_totals_come_from_data_not_summed(story):
    # PDF ไม่บวกเลขเอง — ใส่ยอดรวมที่ไม่ตรงกับแถวโดยตั้งใจ ต้องแสดงตามที่ส่งมา
    data = make_data(480000.0, {})
    data["deduction_detail"]["subtotal"] = 1.0
    data["total_deduction"] = 2.0
    rows = _rows(_deduction_table(story(data)))
    assert rows[-2][2] == "<b>1.00</b>"
    assert rows[-1][2] == "<b>2.00</b>"


# ---------------------------------------------------------------------------
# C. หมายเหตุเพดาน
# ---------------------------------------------------------------------------
def test_life_cut_by_combined_cap_note_mentions_health(story):
    # ชีวิต 90,000 ไม่เกินเพดานรวมถ้านับเดี่ยว ๆ แต่รวมสุขภาพ 20,000 เกิน 100,000 → ใช้ได้ 80,000
    rows = _rows(_deduction_table(story(make_data(480000.0, {'life_insurance': 90000, 'health_insurance': 20000}))))
    life = next(r for r in rows if r[0].startswith("เบี้ยประกันชีวิต"))
    assert life == ("เบี้ยประกันชีวิต | " + LIMIT_NOTES["life_health_combined_cap"].format(limit=100000.0),
                    "90,000.00", "80,000.00")
    assert "เบี้ยประกันสุขภาพ" in life[0].split(" | ")[1]
    health = next(r for r in rows if r[0].startswith("เบี้ยประกันสุขภาพ"))
    assert health == ("เบี้ยประกันสุขภาพ", "20,000.00", "20,000.00")


def test_limit_numbers_come_from_data(story):
    # เพดานและอัตราที่ไม่ใช่ค่าตั้งต้น ต้องขึ้นตามที่ส่งมา (ไม่มีค่าคงที่ใน pdf_generator)
    data = make_data(480000.0, {})
    data["deduction_detail"]["items"]["home_loan_interest"] = {
        "entered": 90000.0, "used": 12345.0, "limits": [{"rule": "home_loan_interest_cap", "limit": 12345.0, "rate": None}]}
    data["deduction_detail"]["items"]["rmf"] = {
        "entered": 200000.0, "used": 129600.0, "limits": [{"rule": "rmf_income_rate", "limit": 129600.0, "rate": 0.27}]}
    data["deduction_detail"]["donation"] = {
        "entered": 50000.0, "used": 7777.0, "limits": [{"rule": "donation_rate", "limit": 7777.0, "rate": 0.07}]}
    rows = _rows(_deduction_table(story(data)))
    notes = " ".join(r[0] for r in rows)
    assert "ใช้ได้ไม่เกิน 12,345.00 บาท" in notes
    assert "ใช้ได้ไม่เกิน 27% ของเงินได้พึงประเมิน (129,600.00 บาท)" in notes
    assert "ใช้ได้ไม่เกิน 7% ของเงินได้หลังหักค่าใช้จ่ายและค่าลดหย่อนอื่น (7,777.00 บาท)" in notes


def test_admin_rules_reach_note(story, monkeypatch):
    # ค่าที่ระบบ Admin แทน TAX_RULES → deduction_breakdown → หมายเหตุใน PDF
    rules = {**tax_rules.TAX_RULES, "deduction": {**tax_rules.TAX_RULES["deduction"], "home_loan_interest_cap": 50000.0}}
    monkeypatch.setattr(tax_rules, "TAX_RULES", rules)
    rows = _rows(_deduction_table(story(make_data(480000.0, {'home_loan_interest': 80000}))))
    home = next(r for r in rows if r[0].startswith(DEDUCTION_LABELS["home_loan_interest"]))
    assert home == (DEDUCTION_LABELS["home_loan_interest"] + " | ใช้ได้ไม่เกิน 50,000.00 บาท", "80,000.00", "50,000.00")


def test_two_caps_at_once_joined(story):
    # 30% × 1,000,000 = 300,000 เท่ากับ thai_esg_cap → หมายเหตุสองเพดานต่อกัน
    rows = _rows(_deduction_table(story(make_data(1000000.0, {'thai_esg': 400000}))))
    esg = next(r for r in rows if r[0].startswith(DEDUCTION_LABELS["thai_esg"]))
    assert esg[0].endswith("ใช้ได้ไม่เกิน 30% ของเงินได้พึงประเมิน (300,000.00 บาท) และ ใช้ได้ไม่เกิน 300,000.00 บาท")


def test_every_limit_rule_has_note(monkeypatch):
    # rule ทุกตัวที่ deduction_breakdown ให้ได้ (และ donation_rate จาก handle_deductions) ต้องมีหมายเหตุของตัวเอง
    cases = [(480000.0, FULL), (2000000.0, {'ssf_rmf': 600000, 'thai_esg': 400000}), (0, {'life_insurance': 120000})]
    rules_seen = {"donation_rate"}
    for income, params in cases:
        for item in deduction_breakdown(income, params).values():
            rules_seen |= {limit["rule"] for limit in item["limits"]}
    group = {**tax_rules.TAX_RULES, "deduction": {**tax_rules.TAX_RULES["deduction"], "retirement_group_cap": 400000.0}}
    monkeypatch.setattr(tax_rules, "TAX_RULES", group)
    rules_seen |= {limit["rule"] for limit in deduction_breakdown(3000000.0, {'ssf_rmf': 450000})["rmf"]["limits"]}
    assert rules_seen == set(LIMIT_NOTES)


# ---------------------------------------------------------------------------
# D. ข้อสมมติ และข้อความทั้งหมด
# ---------------------------------------------------------------------------
def _assumption_lines(story_):
    texts = list(_texts(story_))
    if not any(t == "<b>ข้อสมมติในการคำนวณ</b>" for t in texts):
        return []
    start = texts.index("<b>ข้อสมมติในการคำนวณ</b>") + 1
    return [t[2:] for t in texts[start:] if t.startswith("- ")]


def test_assumptions_follow_what_user_entered(story):
    donation = (102773.0, 30950.0, [{"rule": "donation_rate", "limit": 30950.0, "rate": 0.10}])
    assert _assumption_lines(story(make_data(480000.0, FULL, donation=donation))) == [
        ASSUMPTION_CHILDREN.format(year=2561), ASSUMPTION_PARENTS, ASSUMPTION_SPOUSE,
        ASSUMPTION_SOCIAL_SECURITY, ASSUMPTION_DONATION]
    assert _assumption_lines(story(make_data(480000.0, {}))) == []
    # บุตรคนเดียวไม่ต้องสมมติปีเกิด
    assert _assumption_lines(story(make_data(480000.0, {'num_children': 1}))) == []


def test_child_year_from_data(story):
    lines = _assumption_lines(story(make_data(480000.0, {'num_children': 2}, born_from=2599)))
    assert lines == [ASSUMPTION_CHILDREN.format(year=2599)]


ALL_TEXT_CONSTANTS = ([ASSUMPTION_CHILDREN, ASSUMPTION_PARENTS, ASSUMPTION_SPOUSE, ASSUMPTION_SOCIAL_SECURITY,
                       ASSUMPTION_DONATION, CHILD_BORN_FROM_LABEL, LIMIT_NOTE_FALLBACK]
                      + list(LIMIT_NOTES.values()) + list(DEDUCTION_LABELS.values()))


@pytest.mark.parametrize("text", ALL_TEXT_CONSTANTS, ids=lambda t: f"text{ALL_TEXT_CONSTANTS.index(t)}")
def test_texts_have_no_code_names_or_krub(text):
    # PDF เป็นเอกสารทางการ: ไม่มีภาษาพูด ชื่อไฟล์ ชื่อตัวแปร หรือชื่อ key
    assert "ครับ" not in text
    assert ".py" not in text
    plain = re.sub(r"\{[^}]*\}", "", text)  # ตัดช่องที่เติมค่าจาก data เช่น {limit:,.2f}
    assert not re.search(r"[A-Za-z]+_[A-Za-z_]+", plain)  # snake_case เช่น social_security_cap
    # ตัวเลขในข้อความคงที่: มีได้แค่ลำดับบุตร "คนที่ 2" ซึ่งเป็นชื่อรายการในใบแนบ ตัวเลขอื่นต้องมาจาก data
    assert not re.search(r"\d", plain.replace("คนที่ 2", ""))


def test_full_story_has_no_krub(story):
    donation = (102773.0, 30950.0, [{"rule": "donation_rate", "limit": 30950.0, "rate": 0.10}])
    texts = list(_texts(story(make_data(480000.0, FULL, donation=donation))))
    assert not any("ครับ" in t for t in texts)


# ---------------------------------------------------------------------------
# E. smoke test: PDF จริงลง tmp_path · static/reports ไม่เปลี่ยน
# ---------------------------------------------------------------------------
def test_real_pdf_with_detail(tmp_path):
    before = set(REPORTS_DIR.iterdir()) if REPORTS_DIR.exists() else set()
    donation = (102773.0, 30950.0, [{"rule": "donation_rate", "limit": 30950.0, "rate": 0.10}])
    out = tmp_path / "detail.pdf"
    generate_tax_pdf(str(out), make_data(480000.0, FULL, donation=donation))
    assert out.read_bytes().startswith(b"%PDF")
    assert (set(REPORTS_DIR.iterdir()) if REPORTS_DIR.exists() else set()) == before
