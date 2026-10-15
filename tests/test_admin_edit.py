"""
เทสหน้าแก้ตัวเลขภาษี ขั้น 5A: ฟอร์ม /admin/edit + หน้ายืนยัน (ยังไม่บันทึก)

- Firestore เป็น db ปลอม (fake_db จาก test_rules_loading) แทนที่ admin_routes._config_db
- login และ CSRF ตั้งผ่าน session ตรง ๆ (เทส login อยู่ใน test_admin_login.py)
- ฟอร์มไม่ตรวจกฎเอง ข้อผิดพลาดของกฎมาจาก rules_store.build_rules ตัวเดียวกับตัวโหลด
"""
import re
from decimal import Decimal
from html import unescape

import pytest

import admin_form
import admin_routes
import app as app_module
from utils import rules_store, tax_rules
from utils.rules_store import DEFAULT_TAX_RULES, EDITABLE_SCHEMA
from test_rules_loading import config_doc, fake_db

TOKEN = "test-csrf-token"
REFERENCE = "ประกาศกรมสรรพากร ฉบับทดสอบ เลขที่ 1"
DEFAULT_OVERRIDE = rules_store.to_override(DEFAULT_TAX_RULES)


@pytest.fixture(autouse=True)
def admin_on(monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", "test-placeholder-not-a-real-hash")


@pytest.fixture
def saved(monkeypatch):
    """saved(doc) ตั้งค่า doc ใน Firestore ปลอม (None = ยังไม่มี doc) · saved(error=...) = อ่านไม่ได้"""
    def _set(doc=None, error=None, no_db=False):
        db = None if no_db else fake_db(doc, get_side_effect=error)
        monkeypatch.setattr(admin_routes, "_config_db", lambda: db)
    _set(None)
    return _set


@pytest.fixture
def client():
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess["admin"] = True
        sess["csrf_token"] = TOKEN
    return client


def form_data(**changes):
    """ฟอร์มที่เติมค่าตั้งต้น + แหล่งอ้างอิง แล้วแทนบางช่อง (ชื่อช่องมีจุด จึงส่งผ่าน dict)"""
    data = admin_form.values_from_override(DEFAULT_OVERRIDE)
    data.update({"csrf_token": TOKEN, "base_token": rules_store.NO_DOC_TOKEN, "source_reference": REFERENCE,
                 "note": ""})
    data.update(changes)
    return data


def post_edit(client, data):
    resp = client.post("/admin/edit", data=data)
    return resp, resp.get_data(as_text=True)


def input_values(html):
    return {name: unescape(value) for name, value in re.findall(r'<input type="text"[^>]*name="([^"]+)" value="([^"]*)"', html)}


def is_confirm_page(html):
    return 'id="changes"' in html


# ---------------------------------------------------------------------------
# A. ชื่อไทยและช่องของฟอร์ม
# ---------------------------------------------------------------------------
def _schema_names(schema, prefix=""):
    names = set()
    for key, kind in schema.items():
        name = f"{prefix}{key}"
        names |= _schema_names(kind, name + ".") if isinstance(kind, dict) else {name}
    return names


def test_every_editable_key_has_thai_label():
    # key ใหม่ใน EDITABLE_SCHEMA ต้องมีชื่อไทยด้วย (แบบเดียวกับเทส editable/not-editable)
    assert set(admin_form.LABELS) == _schema_names(EDITABLE_SCHEMA)


def test_form_fields_cover_schema_and_bracket_steps():
    names = [name for name, _, _ in admin_form.all_fields()]
    n = admin_form.bracket_count()
    brackets = {f"tax_brackets.{i}.upper" for i in range(1, n)} | {f"tax_brackets.{i}.rate" for i in range(1, n + 1)}
    assert set(names) == (_schema_names(EDITABLE_SCHEMA) - {"tax_brackets"}) | brackets
    assert f"tax_brackets.{n}.upper" not in names  # ขั้นสุดท้ายไม่มีเพดาน แก้ไม่ได้
    assert all(label for _, label, _ in admin_form.all_fields())
    assert [title for title, _ in admin_form.form_fields()] == [
        "ขั้นบันไดภาษี (คิดจากเงินได้สุทธิ)", "ค่าใช้จ่าย", "ค่าลดหย่อน", "ภาษีวิธีที่ 2"]


# ---------------------------------------------------------------------------
# B. แปลงข้อความเป็นตัวเลข
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", ["0.5", "2.3", "5", "35", "0.7", "1.1"])
def test_percent_converted_with_decimal(text):
    number, error = admin_form.parse_number(text, admin_form.PERCENT_FIELD)
    assert error is None
    assert number == float(Decimal(text) / 100)


def test_percent_with_float_would_drift():
    # เหตุผลที่ต้องใช้ Decimal: float("0.7") / 100 ไม่เท่ากับ 0.007 ที่พิมพ์ (0.5 2.3 5 35 บังเอิญเท่ากัน)
    assert float("0.7") / 100 != float(Decimal("0.7") / 100)
    assert float("1.1") / 100 != float(Decimal("1.1") / 100)


@pytest.mark.parametrize("text, expected", [("60,000", 60000.0), ("1,234.5", 1234.5), (" 60000 ", 60000.0),
                                            ("0", 0.0), ("-5", -5.0)])
def test_money_parsed(text, expected):
    # ติดลบผ่านการแปลงได้ แล้ว build_rules ปฏิเสธที่ช่องนั้น (เทส C)
    assert admin_form.parse_number(text, admin_form.MONEY_FIELD) == (expected, None)


@pytest.mark.parametrize("text", ["", "   ", "abc", "NaN", "nan", "inf", "Infinity", "-inf", "60,000บาท", "1.2.3"])
def test_bad_number_text_is_error(text):
    number, error = admin_form.parse_number(text, admin_form.MONEY_FIELD)
    assert number is None and error


def test_default_values_round_trip_unchanged():
    # ฟอร์มที่เติมค่าตั้งต้นแล้วไม่แก้อะไร ต้องได้ override เท่ากับ to_override(DEFAULT_TAX_RULES) ทุกตัว
    result = admin_form.parse_form(form_data())
    assert result["field_errors"] == {} and result["general_errors"] == []
    assert result["override"] == DEFAULT_OVERRIDE


def test_default_values_text():
    values = admin_form.values_from_override(DEFAULT_OVERRIDE)
    assert values["deduction.personal"] == "60,000"
    assert values["tax_brackets.1.upper"] == "150,000"
    assert values["tax_brackets.1.rate"] == "0"
    assert values["method_2.rate"] == "0.5"
    assert values["expense.salary_rate"] == "50"


# ---------------------------------------------------------------------------
# C. ฟอร์มผ่าน HTTP
# ---------------------------------------------------------------------------
def test_get_form_filled_with_defaults_when_no_doc(client, saved):
    resp = client.get("/admin/edit")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert input_values(html) == admin_form.values_from_override(DEFAULT_OVERRIDE)
    assert '<input type="hidden" name="base_token" value="none">' in html
    assert 'id="pending-restart"' not in html


def test_get_form_filled_from_saved_doc(client, saved):
    doc = config_doc({"deduction": {"personal": 70000.0}}, version=4)
    saved(doc)
    html = client.get("/admin/edit").get_data(as_text=True)
    assert input_values(html)["deduction.personal"] == "70,000"
    assert input_values(html)["deduction.spouse"] == "60,000"
    assert f'<input type="hidden" name="base_token" value="{rules_store.base_token(doc)}">' in html


def test_form_html_round_trip_has_no_changes(client, saved):
    # ค่าที่เติมใน HTML ส่งกลับไปตรง ๆ ต้องไม่มีอะไรเปลี่ยน (การแสดงผลและแปลงกลับไม่ทำค่าเพี้ยน)
    html = client.get("/admin/edit").get_data(as_text=True)
    data = dict(input_values(html), csrf_token=TOKEN, base_token=rules_store.NO_DOC_TOKEN, source_reference=REFERENCE)
    resp, html = post_edit(client, data)
    assert not is_confirm_page(html)
    assert 'id="notice">ไม่มีค่าที่เปลี่ยน<' in html


@pytest.mark.parametrize("field, text, message", [
    ("deduction.personal", "abc", "ต้องเป็นตัวเลข"),
    ("deduction.personal", "", "กรุณากรอกตัวเลข"),
    ("deduction.personal", "NaN", "ต้องเป็นตัวเลข"),
    ("deduction.personal", "inf", "ต้องเป็นตัวเลข"),
    ("deduction.personal", "-5", "ต้องไม่ติดลบ"),
    ("deduction.donation_rate", "150", "ต้องอยู่ระหว่าง 0% ถึง 100%"),
    ("expense.online_sale_rate", "60.5", "ต้องเป็นจำนวนเต็ม %"),
    ("tax_brackets.2.rate", "5.5", "ต้องเป็นจำนวนเต็ม %"),
    ("tax_brackets.3.upper", "200,000", "เพดานบนต้องมากกว่าขั้นก่อนหน้า"),
    ("tax_brackets.4.rate", "5", "อัตราต้องไม่ต่ำกว่าขั้นก่อนหน้า"),
    ("deduction.health_insurance_cap", "150,000", "ต้องไม่เกิน ประกันชีวิตรวมประกันสุขภาพ: เพดานรวม"),
    ("deduction.rmf_cap", "600,000", "ต้องไม่เกิน กลุ่มเงินออมเพื่อการเกษียณ: เพดานรวม"),
])
def test_field_error_shown_next_to_field(client, saved, field, text, message):
    resp, html = post_edit(client, form_data(**{field: text}))
    assert not is_confirm_page(html)
    error = re.search(rf'id="error-{re.escape(field)}">([^<]*)<', html)
    assert error, html
    assert message in unescape(error.group(1))
    assert "deduction." not in unescape(error.group(1))  # ไม่มีชื่อ key ภาษาอังกฤษในข้อความข้างช่อง


@pytest.mark.parametrize("reference", ["", "   ", "สั้นไป", "  ก" + "ข" * 8 + "  ", "ก" * 301])
def test_reference_required_10_to_300(client, saved, reference):
    resp, html = post_edit(client, form_data(**{"deduction.personal": "70,000", "source_reference": reference}))
    assert not is_confirm_page(html)
    assert 'id="error-source_reference"' in html


@pytest.mark.parametrize("reference", ["ก" * 10, "ก" * 300, "  " + "ก" * 10 + "  "])
def test_reference_length_accepted(client, saved, reference):
    resp, html = post_edit(client, form_data(**{"deduction.personal": "70,000", "source_reference": reference}))
    assert is_confirm_page(html)


def test_note_max_300(client, saved):
    _, html = post_edit(client, form_data(**{"deduction.personal": "70,000", "note": "ก" * 301}))
    assert 'id="error-note"' in html and not is_confirm_page(html)
    _, html = post_edit(client, form_data(**{"deduction.personal": "70,000", "note": "ก" * 300}))
    assert is_confirm_page(html)


@pytest.mark.parametrize("token", ["abc", "", "0", "4", "A" * 64, "a" * 63, "a" * 65])
def test_bad_base_token_is_general_error(client, saved, token):
    # รับเฉพาะรูปที่ rules_store.base_token สร้าง ("none" หรือ sha256 hex ตัวเล็ก 64 ตัว) — เลข version แบบเดิมไม่รับแล้ว
    _, html = post_edit(client, form_data(**{"deduction.personal": "70,000", "base_token": token}))
    assert 'id="general-errors"' in html and not is_confirm_page(html)


def test_error_keeps_what_user_typed(client, saved):
    resp, html = post_edit(client, form_data(**{"deduction.personal": "abc", "deduction.spouse": "61,000",
                                                "source_reference": "สั้น", "note": "ทดสอบเก็บค่า"}))
    values = input_values(html)
    assert values["deduction.personal"] == "abc"
    assert values["deduction.spouse"] == "61,000"
    assert ">สั้น</textarea>" in html and ">ทดสอบเก็บค่า</textarea>" in html


# ---------------------------------------------------------------------------
# D. หน้ายืนยัน
# ---------------------------------------------------------------------------
def confirm_rows(html):
    return re.findall(r'<tr class="change( big-change)?" data-name="([^"]+)">', html)


def test_confirm_shows_only_changed_rows(client, saved):
    resp, html = post_edit(client, form_data(**{"deduction.personal": "70,000", "expense.online_sale_rate": "50",
                                                "note": "หมายเหตุทดสอบ"}))
    assert is_confirm_page(html)
    assert [name for _, name in confirm_rows(html)] == ["expense.online_sale_rate", "deduction.personal"]
    assert "60,000 บาท" in html and "70,000 บาท" in html
    assert "60 %" in html and "50 %" in html
    assert REFERENCE in html and "หมายเหตุทดสอบ" in html
    assert '<button type="submit" formaction="/admin/save" id="confirm-save">' in html  # 5B เปิดปุ่มแล้ว


def test_confirm_compares_with_saved_doc_not_defaults(client, saved):
    doc = config_doc({"deduction": {"personal": 70000.0}}, version=2)
    saved(doc)
    _, html = post_edit(client, form_data(**{"deduction.personal": "70,000", "deduction.spouse": "65,000",
                                             "base_token": rules_store.base_token(doc)}))
    # personal เท่ากับค่าที่บันทึกแล้ว จึงไม่นับว่าเปลี่ยน
    assert [name for _, name in confirm_rows(html)] == ["deduction.spouse"]


@pytest.mark.parametrize("new, highlighted", [
    ("600,000", True),    # 10 เท่า (เลข 0 เกิน)
    ("6,000", True),      # 1/10 (เลข 0 ขาด)
    ("180,000", False),   # 3 เท่าพอดี ไม่เกิน
    ("180,001", True),
    ("20,000", False),    # 1/3 พอดี
    ("19,999", True),
    ("0", True),          # จาก 60,000 เป็น 0
])
def test_confirm_highlights_more_than_3x(client, saved, new, highlighted):
    _, html = post_edit(client, form_data(**{"deduction.personal": new}))
    rows = confirm_rows(html)
    assert rows == [(" big-change" if highlighted else "", "deduction.personal")]
    assert ("ตรวจอีกครั้ง: เปลี่ยนมากกว่า 3 เท่า" in html) is highlighted


def test_no_change_stays_on_form(client, saved):
    resp, html = post_edit(client, form_data())
    assert resp.status_code == 200
    assert not is_confirm_page(html)
    assert 'id="notice">ไม่มีค่าที่เปลี่ยน<' in html


def test_back_from_confirm_refills_form_without_checking(client, saved):
    data = form_data(**{"deduction.personal": "70,000", "action": "back", "note": "จากหน้ายืนยัน"})
    _, html = post_edit(client, data)
    assert not is_confirm_page(html)
    assert input_values(html)["deduction.personal"] == "70,000"
    assert ">จากหน้ายืนยัน</textarea>" in html


def test_confirm_page_carries_all_values_for_next_step(client, saved):
    _, html = post_edit(client, form_data(**{"deduction.personal": "70,000"}))
    hidden = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', html))
    expected = admin_form.values_from_override(DEFAULT_OVERRIDE)
    expected["deduction.personal"] = "70,000"
    assert {k: unescape(v) for k, v in hidden.items() if k in expected} == expected
    assert unescape(hidden["source_reference"]) == REFERENCE and hidden["base_token"] == rules_store.NO_DOC_TOKEN


def test_confirm_does_not_touch_tax_rules(client, saved):
    before = tax_rules.TAX_RULES
    post_edit(client, form_data(**{"deduction.personal": "70,000"}))
    assert tax_rules.TAX_RULES is before and tax_rules.TAX_RULES == DEFAULT_TAX_RULES


# ---------------------------------------------------------------------------
# E. อ่าน Firestore ไม่ได้ → ไม่เปิดฟอร์ม
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("kwargs", [{"no_db": True}, {"error": RuntimeError()}, {"error": TimeoutError()}],
                         ids=["no_db", "read_error", "timeout"])
def test_unreadable_firestore_does_not_open_form(client, saved, kwargs):
    saved(**kwargs)
    for resp in (client.get("/admin/edit"), client.post("/admin/edit", data=form_data(**{"deduction.personal": "70,000"}))):
        html = resp.get_data(as_text=True)
        assert resp.status_code == 503
        assert "แก้ตัวเลขภาษีไม่ได้ตอนนี้" in html
        assert 'name="deduction.personal"' not in html  # ไม่มีฟอร์มที่เติมค่าตั้งต้นให้บันทึกทับ
        assert not is_confirm_page(html)


# ---------------------------------------------------------------------------
# F. แถบ "รอรีสตาร์ต" และ doc ที่ผิด
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", ["/admin", "/admin/edit"])
def test_pending_restart_banner(client, saved, monkeypatch, path):
    saved(None)
    assert 'id="pending-restart"' not in client.get(path).get_data(as_text=True)
    saved(config_doc({"deduction": {"personal": 70000.0}}, version=3))
    assert 'id="pending-restart"' in client.get(path).get_data(as_text=True)  # เซิร์ฟเวอร์ใช้ค่าตั้งต้นอยู่
    monkeypatch.setattr(tax_rules, "RULES_INFO",
                        {"source": "firestore", "version": 3, "changed_keys": ["deduction.personal"]})
    assert 'id="pending-restart"' not in client.get(path).get_data(as_text=True)  # โหลด version 3 แล้ว


def test_admin_page_still_opens_when_firestore_unreadable(client, saved):
    saved(error=RuntimeError())
    resp = client.get("/admin")
    assert resp.status_code == 200
    assert 'id="saved-unavailable"' in resp.get_data(as_text=True)


def test_invalid_saved_doc_fills_defaults_with_warning(client, saved):
    doc = config_doc({"salary_capp": 1.0}, version=5)  # ตัวโหลดก็ปฏิเสธ doc นี้
    saved(doc)
    html = client.get("/admin/edit").get_data(as_text=True)
    assert 'id="saved-problem"' in html
    assert input_values(html) == admin_form.values_from_override(DEFAULT_OVERRIDE)
    assert f'<input type="hidden" name="base_token" value="{rules_store.base_token(doc)}">' in html


# ---------------------------------------------------------------------------
# G. RuleError ชี้ช่อง (ข้อความเดิมไม่เปลี่ยน)
# ---------------------------------------------------------------------------
def test_rule_errors_carry_field_and_keep_text():
    _, errors = rules_store.build_rules({"deduction": {"personal": -1.0, "health_insurance_cap": 999999.0}})
    personal = next(e for e in errors if e.field == "deduction.personal")
    assert personal == "deduction.personal ต้องไม่ติดลบ" and personal.detail == "ต้องไม่ติดลบ"
    _, errors = rules_store.build_rules({"deduction": {"health_insurance_cap": 999999.0}})
    assert errors == ["deduction.health_insurance_cap ต้องไม่เกิน deduction.life_health_combined_cap"]
    assert errors[0].field == "deduction.health_insurance_cap"
    assert errors[0].related == "deduction.life_health_combined_cap"
    brackets = [dict(item) for item in DEFAULT_OVERRIDE["tax_brackets"]]
    brackets[2]["upper"] = 200000
    _, errors = rules_store.build_rules({"tax_brackets": brackets})
    assert errors == ["tax_brackets ขั้นที่ 3 เพดานบนต้องมากกว่าขั้นก่อนหน้า (ขั้นแรกต้องมากกว่า 0)"]
    assert errors[0].field == "tax_brackets.3.upper"


def test_changed_a_lot():
    assert not admin_form.changed_a_lot(60000, 60000)
    assert not admin_form.changed_a_lot(60000, 180000)
    assert admin_form.changed_a_lot(60000, 180001)
    assert admin_form.changed_a_lot(0, 1)
    assert admin_form.changed_a_lot(0.05, 0)
