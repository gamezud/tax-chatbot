"""
เทสก้อน 5B: ปุ่ม "ยืนยันบันทึก" → POST /admin/save → Firestore transaction เดียว (active + ประวัติ)

- Firestore เป็นตัวปลอมที่เก็บข้อมูลจริง (fake_firestore.py) ไม่ใช่ MagicMock — ตรวจ doc ที่ถูกเขียนได้จริง
  และใช้ decorator transactional ของไลบรารีจริง (การลองใหม่เมื่อชนเป็นของจริง)
- บันทึกผ่านหน้าเว็บแบบที่ผู้ใช้กด: เปิดฟอร์ม → แก้ช่อง → หน้ายืนยัน → ส่ง hidden field ของหน้ายืนยันไป /admin/save
- ทุกเทสตรวจท้ายเทสว่าไม่มีการเขียนนอก transaction (fixture store)
"""
import re
import threading
from html import unescape

import pytest

import admin_form
import admin_routes
import app as app_module
from utils import rules_store, tax_rules
from utils.rules_store import CONFIG_COLLECTION, CONFIG_DOCUMENT, DEFAULT_TAX_RULES, HISTORY_COLLECTION
from fake_firestore import FakeFirestore
from test_rules_loading import apply_doc, config_doc, mocks, run_case  # noqa: F401

TOKEN = "test-csrf-token"
REFERENCE = "ประกาศกรมสรรพากร ฉบับทดสอบ เลขที่ 1"
ACTIVE = (CONFIG_COLLECTION, CONFIG_DOCUMENT)
PERSONAL = DEFAULT_TAX_RULES["deduction"]["personal"]
SPOUSE = DEFAULT_TAX_RULES["deduction"]["spouse"]
DEFAULT_OVERRIDE = rules_store.to_override(DEFAULT_TAX_RULES)
TAX_CHANGING = {"deduction": {"personal": PERSONAL + 5000.0}}

HIDDEN = re.compile(r'<input type="hidden" name="([^"]+)" value="([^"]*)"')
TEXT = re.compile(r'<input type="text"[^>]*name="([^"]+)" value="([^"]*)"')


@pytest.fixture(autouse=True)
def admin_on(monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", "test-placeholder-not-a-real-hash")


@pytest.fixture
def store(monkeypatch):
    """store(docs) สร้าง Firestore ปลอมแล้วให้หน้า admin ใช้ · ท้ายเทสตรวจว่าไม่มีการเขียนนอก transaction"""
    fakes = []

    def _make(docs=None, **kwargs):
        fake = FakeFirestore(docs, **kwargs)
        fakes.append(fake)
        monkeypatch.setattr(admin_routes, "_config_db", lambda: fake)
        return fake

    yield _make
    for fake in fakes:
        assert fake.violations == [], f"เขียนนอก transaction: {fake.violations}"


@pytest.fixture
def client():
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess["admin"] = True
        sess["csrf_token"] = TOKEN
    return client


def money(value):
    return admin_form.format_value(value, admin_form.MONEY_FIELD)


def inputs(html, pattern):
    return {name: unescape(value) for name, value in pattern.findall(html)}


def open_form(client):
    """GET /admin/edit แล้วคืนค่าทุกช่อง (รวม hidden: csrf_token, base_token) แบบที่เบราว์เซอร์จะส่ง"""
    html = client.get("/admin/edit").get_data(as_text=True)
    return {**inputs(html, TEXT), **inputs(html, HIDDEN), "source_reference": REFERENCE, "note": ""}


def confirm(client, data):
    """ส่งฟอร์มไปหน้ายืนยัน คืน (hidden field ที่ปุ่มยืนยันบันทึกจะส่ง, html ของหน้ายืนยัน)"""
    html = client.post("/admin/edit", data=data).get_data(as_text=True)
    assert 'id="changes"' in html, html
    return inputs(html, HIDDEN), html


def post_save(client, data):
    resp = client.post("/admin/save", data=data)
    return resp, resp.get_data(as_text=True)


def save_via_form(client, changes, **fields):
    data = open_form(client)
    data.update(changes)
    data.update(fields)
    hidden, _ = confirm(client, data)
    return post_save(client, hidden)


def active(fake):
    return fake.doc(*ACTIVE)


def history(fake):
    return fake.collection_docs(HISTORY_COLLECTION)


def nothing_written(fake, before_docs):
    return fake.commits == 0 and fake.docs == before_docs


# ---------------------------------------------------------------------------
# A. สิ่งที่ฟอร์มเขียน ตัวโหลดอ่านได้จริง และภาษีเปลี่ยนตามนั้น
# ---------------------------------------------------------------------------
def test_saved_values_reach_loader_and_tax(client, store, mocks, apply_doc):
    fake = store()
    before = run_case("ไม่มี", {}, mocks)["net_income"]

    resp, html = save_via_form(client, {"deduction.personal": money(PERSONAL + 10000)})
    assert resp.status_code == 200
    assert "บันทึกเวอร์ชัน 1 แล้ว — มีผลหลังรีสตาร์ตเซิร์ฟเวอร์ครับ" in html

    rules, info = rules_store.load_effective_rules(fake)
    assert rules["deduction"]["personal"] == PERSONAL + 10000
    assert info == {"source": "firestore", "version": 1, "changed_keys": ["deduction.personal"]}

    apply_doc(fake)  # แบบที่ __main__ ทำหลังรีสตาร์ต
    after = run_case("ไม่มี", {}, mocks)["net_income"]
    assert before - after == 10000


def test_versions_count_up_and_history_matches_active(client, store):
    fake = store()
    save_via_form(client, {"deduction.personal": money(PERSONAL + 10000)}, note="ครั้งแรก")
    first = active(fake)
    assert first["version"] == 1
    assert first["based_on_tax_year"] == DEFAULT_TAX_RULES["tax_year"]
    assert first["override"]["deduction"]["personal"] == PERSONAL + 10000
    assert first["source_reference"] == REFERENCE and first["note"] == "ครั้งแรก"
    assert first["updated_at"] is not None
    assert history(fake) == {"1": {**first, "previous_version": None}}

    resp, html = save_via_form(client, {"deduction.spouse": money(SPOUSE + 1000)})
    assert "บันทึกเวอร์ชัน 2 แล้ว" in html
    second = active(fake)
    assert second["version"] == 2
    # ค่าที่แก้ครั้งแรกยังอยู่ (ฟอร์มเติมค่าจาก doc ล่าสุด)
    assert second["override"]["deduction"]["personal"] == PERSONAL + 10000
    assert second["override"]["deduction"]["spouse"] == SPOUSE + 1000
    assert history(fake) == {"1": {**first, "previous_version": None}, "2": {**second, "previous_version": 1}}


def test_saved_override_is_full_and_loader_accepts_it(client, store):
    fake = store()
    save_via_form(client, {"tax_brackets.3.upper": money(DEFAULT_TAX_RULES["tax_brackets"][2][0] + 1000)})
    override = active(fake)["override"]
    assert set(override) == set(DEFAULT_OVERRIDE)  # ทุก key ที่แก้ได้ ไม่ใช่แค่ช่องที่เปลี่ยน
    assert rules_store.rules_from_doc(active(fake))[1] == []


# ---------------------------------------------------------------------------
# B. ลายนิ้วมือของ doc — แท็บเก่าบันทึกทับไม่ได้
# ---------------------------------------------------------------------------
def test_save_elsewhere_after_form_opened_is_rejected(client, store):
    fake = store()
    hidden, _ = confirm(client, {**open_form(client), "deduction.personal": money(PERSONAL + 10000)})
    elsewhere = config_doc(TAX_CHANGING, version=1)
    fake.put(*ACTIVE, elsewhere)
    before = fake.docs.copy()

    resp, html = post_save(client, hidden)
    assert resp.status_code == 409
    assert "มีการบันทึกจากที่อื่นหลังคุณเปิดฟอร์ม กรุณาเปิดฟอร์มใหม่ครับ" in html
    assert nothing_written(fake, before)
    assert active(fake) == elsewhere and history(fake) == {}


def test_console_edit_without_version_change_is_rejected(client, store):
    # แก้ใน Firebase Console โดยไม่เปลี่ยนเลข version — ถ้าเทียบแค่ version แท็บเก่าจะบันทึกทับได้
    fake = store({ACTIVE: config_doc(TAX_CHANGING, version=3)})
    hidden, _ = confirm(client, {**open_form(client), "deduction.spouse": money(SPOUSE + 1000)})
    edited = config_doc({"deduction": {"personal": PERSONAL + 7000.0}}, version=3)
    fake.put(*ACTIVE, edited)
    before = fake.docs.copy()

    resp, _ = post_save(client, hidden)
    assert resp.status_code == 409
    assert nothing_written(fake, before) and active(fake) == edited


def test_concurrent_save_during_transaction_retries_then_rejects(client, store):
    fake = store()
    hidden, _ = confirm(client, {**open_form(client), "deduction.personal": money(PERSONAL + 10000)})
    elsewhere = config_doc(TAX_CHANGING, version=1)
    # อีกแท็บบันทึกระหว่างที่ transaction อ่าน active แล้วกับ commit
    fake.after_transaction_read = lambda f: f.put(*ACTIVE, elsewhere)

    resp, _ = post_save(client, hidden)
    assert resp.status_code == 409
    assert fake.begins == 2  # รอบแรกชน (Aborted) decorator เรียกใหม่ รอบสองเห็น doc ใหม่แล้วยกเลิก
    assert fake.commits == 0
    assert active(fake) == elsewhere and history(fake) == {}


def test_resubmitting_after_success_does_not_save_twice(client, store):
    fake = store()
    hidden, _ = confirm(client, {**open_form(client), "deduction.personal": money(PERSONAL + 10000)})
    assert post_save(client, hidden)[0].status_code == 200
    resp, _ = post_save(client, hidden)  # กดซ้ำ / reload หน้าหลังบันทึก
    assert resp.status_code == 409
    assert fake.commits == 1 and list(history(fake)) == ["1"]


# ---------------------------------------------------------------------------
# C. เลขเวอร์ชันใหม่ไม่ชนกับประวัติ
# ---------------------------------------------------------------------------
def test_new_version_is_above_history_when_active_is_lower(client, store):
    old = {(HISTORY_COLLECTION, str(i)): config_doc({}, version=i) for i in range(1, 6)}
    fake = store({ACTIVE: config_doc({}, version=2), **old})
    resp, html = save_via_form(client, {"deduction.personal": money(PERSONAL + 10000)})
    assert "บันทึกเวอร์ชัน 6 แล้ว" in html
    assert active(fake)["version"] == 6
    assert history(fake)["6"]["previous_version"] == 2
    assert {k: v for k, v in history(fake).items() if k != "6"} == {k[1]: v for k, v in old.items()}


def test_history_doc_without_version_is_not_overwritten(client, store):
    # doc ประวัติที่สร้างมือโดยไม่มี field version — เลขใหม่ชนกับมัน commit ต้องล้มทั้งก้อน ไม่ทับ
    handmade = {"note": "สร้างมือ"}
    fake = store({(HISTORY_COLLECTION, "1"): handmade})
    resp, html = save_via_form(client, {"deduction.personal": money(PERSONAL + 10000)})
    assert resp.status_code == 503
    assert admin_routes.SAVE_UNKNOWN_MESSAGE in html
    assert active(fake) is None and history(fake) == {"1": handmade}


# ---------------------------------------------------------------------------
# D. doc ที่ตัวโหลดปฏิเสธ — แก้ผ่านเว็บได้ รวมถึงกลับเป็นค่าตั้งต้น
# ---------------------------------------------------------------------------
BAD_VERSION_DOCS = {
    "string": config_doc(TAX_CHANGING, version="abc"),
    "zero": config_doc(TAX_CHANGING, version=0),
    "bool": config_doc(TAX_CHANGING, version=True),
    "missing": {"based_on_tax_year": DEFAULT_TAX_RULES["tax_year"], "override": TAX_CHANGING},
}


@pytest.mark.parametrize("doc", BAD_VERSION_DOCS.values(), ids=BAD_VERSION_DOCS.keys())
def test_bad_version_doc_can_be_reset_to_defaults(client, store, doc):
    old = {(HISTORY_COLLECTION, str(i)): config_doc({}, version=i) for i in range(1, 4)}
    fake = store({ACTIVE: doc, **old})
    data = open_form(client)  # ฟอร์มเติมค่าตั้งต้นให้ ไม่แก้อะไร
    hidden, html = confirm(client, data)
    assert 'id="overwrite-invalid"' in html and "ค่าเท่าค่าตั้งต้น" in html

    resp, html = post_save(client, hidden)
    assert "บันทึกเวอร์ชัน 4 แล้ว" in html
    saved = active(fake)
    assert saved["version"] == 4 and saved["override"] == DEFAULT_OVERRIDE
    assert history(fake)["4"]["previous_version"] is None
    assert rules_store.load_effective_rules(fake)[1]["version"] == 4


def test_rejected_override_with_valid_version_can_be_reset(client, store):
    fake = store({ACTIVE: config_doc({"salary_capp": 1.0}, version=5)})
    hidden, _ = confirm(client, open_form(client))
    resp, html = post_save(client, hidden)
    assert "บันทึกเวอร์ชัน 6 แล้ว" in html
    assert history(fake)["6"]["previous_version"] == 5


def test_bad_version_doc_changed_elsewhere_is_rejected(client, store):
    fake = store({ACTIVE: BAD_VERSION_DOCS["string"]})
    hidden, _ = confirm(client, open_form(client))
    edited = config_doc({"deduction": {"personal": PERSONAL + 9000.0}}, version="abc")
    fake.put(*ACTIVE, edited)
    resp, _ = post_save(client, hidden)
    assert resp.status_code == 409 and active(fake) == edited and fake.commits == 0


def test_valid_doc_with_no_change_does_not_go_to_confirm(client, store):
    store({ACTIVE: config_doc(TAX_CHANGING, version=2)})
    html = client.post("/admin/edit", data=open_form(client)).get_data(as_text=True)
    assert 'id="notice">ไม่มีค่าที่เปลี่ยน<' in html and 'id="changes"' not in html


# ---------------------------------------------------------------------------
# E. ตรวจซ้ำตอนบันทึก — ไม่เชื่อ hidden field
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("tampered", [
    {"deduction.personal": "-5"},
    {"deduction.personal": "abc"},
    {"deduction.personal": ""},
    {"tax_brackets.4.rate": "1"},                       # อัตราลดลงเมื่อขั้นสูงขึ้น
    {"deduction.health_insurance_cap": "999,999"},      # เกินเพดานรวม
    {"expense.online_sale_rate": "60.5"},               # ไม่ใช่จำนวนเต็ม %
    {"source_reference": "สั้น"},
    {"note": "ก" * 301},
    {"base_token": "0"},
], ids=["personal_negative", "personal_text", "personal_empty", "bracket_rate_down", "health_over_combined",
        "online_rate_fraction", "reference_short", "note_too_long", "base_token_bad"])
def test_tampered_hidden_field_is_not_saved(client, store, tampered):
    fake = store()
    hidden, _ = confirm(client, {**open_form(client), "deduction.personal": money(PERSONAL + 10000)})
    resp, html = post_save(client, {**hidden, **tampered})
    assert resp.status_code == 400
    assert "บันทึกเวอร์ชัน" not in html
    assert fake.commits == 0 and fake.begins == 0 and active(fake) is None


def test_no_change_is_not_saved(client, store):
    fake = store({ACTIVE: config_doc(TAX_CHANGING, version=2)})
    before = fake.docs.copy()
    resp, html = post_save(client, open_form(client))  # ส่งค่าเดิมตรงไป /admin/save
    assert resp.status_code == 200
    assert 'id="notice">ไม่มีค่าที่เปลี่ยน<' in html
    assert nothing_written(fake, before)


# ---------------------------------------------------------------------------
# F. เขียนไม่สำเร็จ → ไม่มี doc ไหนเปลี่ยน
# ---------------------------------------------------------------------------
def test_second_write_failure_leaves_active_unchanged(client, store):
    original = config_doc(TAX_CHANGING, version=1)
    fake = store({ACTIVE: original})
    fake.fail_on_commit_write = (HISTORY_COLLECTION, "2")  # write ที่สอง (ประวัติ) ล้ม
    resp, html = save_via_form(client, {"deduction.spouse": money(SPOUSE + 1000)})
    assert resp.status_code == 503
    assert admin_routes.SAVE_UNKNOWN_MESSAGE in html
    assert active(fake) == original and history(fake) == {}
    assert fake.rollbacks == 1


@pytest.mark.parametrize("kwargs", [{"no_db": True}, {"fail_reads": RuntimeError()}], ids=["no_db", "read_error"])
def test_unreadable_firestore_saves_nothing(client, store, monkeypatch, kwargs):
    fake = store()
    hidden, _ = confirm(client, {**open_form(client), "deduction.personal": money(PERSONAL + 10000)})
    if kwargs.get("no_db"):
        monkeypatch.setattr(admin_routes, "_config_db", lambda: None)
    else:
        fake.fail_reads = kwargs["fail_reads"]
    resp, html = post_save(client, hidden)
    assert resp.status_code == 503
    assert "บันทึกไม่ได้ตอนนี้" in html and admin_routes.SAVE_UNREADABLE_MESSAGE in html
    assert fake.commits == 0 and fake.begins == 0 and active(fake) is None


def test_save_timeout_says_outcome_unknown(client, store, monkeypatch):
    fake = store()
    hidden, _ = confirm(client, {**open_form(client), "deduction.personal": money(PERSONAL + 10000)})
    release = threading.Event()
    fake.after_transaction_read = lambda f: release.wait(5)  # Firestore ไม่ตอบ
    monkeypatch.setattr(rules_store, "SAVE_TIMEOUT_SECONDS", 0.05)
    try:
        resp, html = post_save(client, hidden)
    finally:
        release.set()
    assert resp.status_code == 503
    assert admin_routes.SAVE_UNKNOWN_MESSAGE in html


# ---------------------------------------------------------------------------
# G. escape, ตัวกัน, ไม่แตะค่าที่ใช้อยู่, แถบรอรีสตาร์ต, log
# ---------------------------------------------------------------------------
def test_script_in_reference_and_note_is_escaped(client, store):
    fake = store()
    reference = "<script>alert(1)</script> ประกาศทดสอบ"
    note = "<script>alert(2)</script>"
    data = {**open_form(client), "deduction.personal": money(PERSONAL + 10000),
            "source_reference": reference, "note": note}
    hidden, confirm_html = confirm(client, data)
    resp, saved_html = post_save(client, hidden)
    assert "บันทึกเวอร์ชัน 1 แล้ว" in saved_html
    for html in (confirm_html, saved_html):
        assert "<script>" not in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
        assert "&lt;script&gt;alert(2)&lt;/script&gt;" in html
    # เก็บตามที่พิมพ์ (escape ตอนแสดง ไม่ใช่ตอนเก็บ)
    assert active(fake)["source_reference"] == reference and active(fake)["note"] == note


def test_save_without_csrf_is_rejected(client, store):
    fake = store()
    hidden, _ = confirm(client, {**open_form(client), "deduction.personal": money(PERSONAL + 10000)})
    hidden.pop("csrf_token")
    resp, _ = post_save(client, hidden)
    assert resp.status_code == 400
    assert fake.begins == 0 and active(fake) is None


def test_save_without_login_is_rejected(client, store):
    fake = store()
    hidden, _ = confirm(client, {**open_form(client), "deduction.personal": money(PERSONAL + 10000)})
    with client.session_transaction() as sess:
        sess.pop("admin")
    resp, _ = post_save(client, hidden)
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/admin/login")
    assert fake.begins == 0 and active(fake) is None


def test_save_from_outside_is_404(client, store):
    fake = store()
    hidden, _ = confirm(client, {**open_form(client), "deduction.personal": money(PERSONAL + 10000)})
    resp = client.post("/admin/save", data=hidden, headers={"X-Forwarded-For": "203.0.113.5"})
    assert resp.status_code == 404
    assert fake.begins == 0 and active(fake) is None


def test_save_does_not_touch_rules_in_use(client, store):
    store()
    rules_before, info_before = tax_rules.TAX_RULES, tax_rules.RULES_INFO
    resp, html = save_via_form(client, {"deduction.personal": money(PERSONAL + 10000)})
    assert "บันทึกเวอร์ชัน 1 แล้ว" in html
    assert tax_rules.TAX_RULES is rules_before and tax_rules.TAX_RULES == DEFAULT_TAX_RULES
    assert tax_rules.RULES_INFO is info_before


def test_admin_page_shows_pending_restart_after_save(client, store):
    store()
    assert 'id="pending-restart"' not in client.get("/admin").get_data(as_text=True)
    save_via_form(client, {"deduction.personal": money(PERSONAL + 10000)})
    assert 'id="pending-restart"' in client.get("/admin").get_data(as_text=True)


def test_log_names_keys_without_values_reference_or_note(client, store, capsys):
    store()
    new_personal = PERSONAL + 10000
    new_upper = DEFAULT_TAX_RULES["tax_brackets"][2][0] + 1000
    save_via_form(client, {"deduction.personal": money(new_personal), "tax_brackets.3.upper": money(new_upper)},
                  note="หมายเหตุลับ")
    out = capsys.readouterr().out
    assert "[ADMIN] บันทึกค่าภาษีเวอร์ชัน 1 (แก้ 2 รายการ: tax_brackets.3.upper, deduction.personal)" in out
    for secret in (money(new_personal), str(int(new_personal)), money(new_upper), str(int(new_upper)),
                   REFERENCE, "หมายเหตุลับ"):
        assert secret not in out
