"""
เทสก้อน 6B: ย้อนเวอร์ชัน — GET /admin/edit?from_version=N (หรือ default) → หน้ายืนยัน → /admin/save เส้นทางเดิม

- Firestore เป็นตัวปลอมที่เก็บข้อมูลจริง (fake_firestore.py) · ทุกเทสตรวจท้ายเทสว่าไม่มีการเขียนนอก transaction
- restored_from จากฟอร์มเป็นแค่คำอ้าง: บันทึกเฉพาะเมื่อค่าที่บันทึกตรงกับเวอร์ชันนั้นทุกช่อง (ตรวจใน transaction)
- doc ประวัติสร้างแบบเดียวกับ test_admin_history.py (hist, active_of)
"""
import re
from html import unescape

import pytest

import admin_form
import admin_routes
import app as app_module
from utils import rules_store
from utils.rules_store import CONFIG_COLLECTION, CONFIG_DOCUMENT, DEFAULT_TAX_RULES, HISTORY_COLLECTION
from fake_firestore import FakeFirestore
from test_admin_history import PERSONAL, V1, V2, YEAR, active_of, hist, money
from test_rules_loading import apply_doc, mocks, run_case  # noqa: F401

TOKEN = "test-csrf-token"
ACTIVE = (CONFIG_COLLECTION, CONFIG_DOCUMENT)
DEFAULT_OVERRIDE = rules_store.to_override(DEFAULT_TAX_RULES)
HIDDEN = re.compile(r'<input type="hidden" name="([^"]+)" value="([^"]*)"')
TEXT = re.compile(r'<input type="text"[^>]*name="([^"]+)" value="([^"]*)"')


@pytest.fixture(autouse=True)
def admin_on(monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", "test-placeholder-not-a-real-hash")


@pytest.fixture
def store(monkeypatch):
    """store(history={id: doc}, active=doc) · ท้ายเทสตรวจว่าไม่มีการเขียนนอก transaction"""
    fakes = []

    def _make(history=None, active=None, **kwargs):
        docs = {(HISTORY_COLLECTION, str(doc_id)): doc for doc_id, doc in (history or {}).items()}
        if active is not None:
            docs[ACTIVE] = active
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


def two_versions(store):
    """v1 = ค่าลดหย่อนส่วนตัว +10,000 · v2 = กลับค่าตั้งต้น (active) — แบบเดียวกับการทดสอบ end-to-end"""
    return store({1: V1, 2: V2}, active=active_of(V2))


def form_of(html):
    """ค่าทุกช่องของฟอร์มแบบที่เบราว์เซอร์จะส่ง (ช่องตัวเลข + hidden + textarea)"""
    data = {name: unescape(v) for name, v in TEXT.findall(html)}
    data.update({name: unescape(v) for name, v in HIDDEN.findall(html)})
    for name in ("source_reference", "note"):
        found = re.search(rf'<textarea id="{name}" name="{name}"[^>]*>(.*?)</textarea>', html, re.S)
        data[name] = unescape(found.group(1)) if found else ""
    return data


def open_restore(client, source):
    resp = client.get(f"/admin/edit?from_version={source}")
    return resp, resp.get_data(as_text=True)


def confirm(client, data):
    html = client.post("/admin/edit", data=data).get_data(as_text=True)
    assert 'id="changes"' in html, html
    return {name: unescape(v) for name, v in HIDDEN.findall(html)}, html


def save(client, hidden):
    resp = client.post("/admin/save", data=hidden)
    return resp, resp.get_data(as_text=True)


def restore_and_save(client, source, **changes):
    _, html = open_restore(client, source)
    data = form_of(html)
    data.update(changes)
    hidden, confirm_html = confirm(client, data)
    resp, saved_html = save(client, hidden)
    return resp, saved_html, confirm_html


def history(fake):
    return fake.collection_docs(HISTORY_COLLECTION)


# ---------------------------------------------------------------------------
# A. ย้อนไปเวอร์ชันเก่าแล้วบันทึก — ตัวโหลดและภาษีได้ค่าของเวอร์ชันนั้น
# ---------------------------------------------------------------------------
def test_restore_v1_saves_v3_and_tax_matches_v1(client, store, mocks, apply_doc):
    # ภาษีตอนใช้ v1 โดยตรง (รันผ่าน /webhook ไม่คิดเลขเอง)
    apply_doc(FakeFirestore({ACTIVE: active_of(V1)}))
    net_with_v1 = run_case("ไม่มี", {}, mocks)["net_income"]

    fake = two_versions(store)
    resp, html, confirm_html = restore_and_save(client, 1)
    assert "ค่าตรงกับเวอร์ชัน 1 ทุกช่อง — จะบันทึกว่าย้อนจากเวอร์ชัน 1" in confirm_html
    assert resp.status_code == 200 and "บันทึกเวอร์ชัน 3 แล้ว" in html
    assert 'id="restored"' in html

    active = fake.doc(*ACTIVE)
    assert active["restored_from"] == 1 and active["version"] == 3
    assert history(fake)["3"]["restored_from"] == 1 and history(fake)["3"]["previous_version"] == 2
    rules, info = rules_store.load_effective_rules(fake)
    assert rules["deduction"]["personal"] == PERSONAL + 10000 and info["version"] == 3

    apply_doc(fake)
    assert run_case("ไม่มี", {}, mocks)["net_income"] == net_with_v1


def test_restore_form_is_filled_from_version_with_active_base_token(client, store):
    fake = two_versions(store)
    resp, html = open_restore(client, 1)
    assert resp.status_code == 200
    data = form_of(html)
    v1_values = admin_form.values_from_override(rules_store.to_override(rules_store.rules_from_doc(V1)[0]))
    assert {k: data[k] for k in v1_values} == v1_values
    # base มาจาก active ปัจจุบัน ไม่ใช่ของ doc ประวัติ — การบันทึกจึงยังกันแท็บเก่าได้
    assert data["base_token"] == rules_store.base_token(fake.doc(*ACTIVE))
    assert data["base_token"] != rules_store.base_token(V1)
    assert data["restored_from"] == "1"
    assert data["source_reference"] == f"ย้อนไปใช้ค่าของเวอร์ชัน 1 (แหล่งอ้างอิงเดิม: {V1['source_reference']})"
    assert 'id="notice">เติมค่าของเวอร์ชัน 1 ไว้ให้' in html


def test_restore_defaults(client, store):
    fake = store({1: V1}, active=active_of(V1))
    resp, html, confirm_html = restore_and_save(client, "default")
    assert "ค่าตรงกับค่าตั้งต้นในโค้ด ทุกช่อง" in confirm_html
    assert "บันทึกเวอร์ชัน 2 แล้ว" in html
    assert fake.doc(*ACTIVE)["restored_from"] == rules_store.RESTORE_DEFAULT
    assert fake.doc(*ACTIVE)["override"] == DEFAULT_OVERRIDE


# ---------------------------------------------------------------------------
# B. GET ไม่เขียนอะไร
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("source", ["1", "default", "99", "abc", "0"])
def test_opening_restore_form_writes_nothing(client, store, source):
    fake = two_versions(store)
    before = dict(fake.docs)
    open_restore(client, source)
    assert fake.commits == 0 and fake.begins == 0 and fake.docs == before


# ---------------------------------------------------------------------------
# C. เวอร์ชันที่ใช้ไม่ได้
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("source", ["99", "abc", "0", "-1", "๑", ""])
def test_missing_or_bad_version_is_404(client, store, source):
    two_versions(store)
    resp, html = open_restore(client, source)
    assert resp.status_code == 404
    assert "ย้อนเวอร์ชันไม่ได้" in html and 'name="deduction.personal"' not in html


@pytest.mark.parametrize("doc, reason", [
    (hist(1, based_on_tax_year=YEAR - 1), f"บันทึกไว้สำหรับปีภาษี {YEAR - 1} ใช้กับปีภาษี {YEAR} ไม่ได้"),
    (hist(1, {"deduction.personal": -1.0}), "ค่าลดหย่อนส่วนตัว: ต้องไม่ติดลบ"),
    (hist(1, override={"salary_capp": 1.0}), "มีค่าที่ระบบไม่รู้จักหรือห้ามแก้"),
    (hist(2), "ข้อมูลผิดรูป"),                    # version ไม่ตรงกับ doc id 1
    ({"note": "สร้างมือ"}, "ข้อมูลผิดรูป"),
], ids=["other_year", "negative", "unknown_key", "version_not_id", "no_fields"])
def test_version_that_fails_current_checks_is_422(client, store, doc, reason):
    store({1: doc}, active=active_of(V2))
    resp, html = open_restore(client, 1)
    assert resp.status_code == 422
    assert reason in unescape(html)
    assert 'name="deduction.personal"' not in html


@pytest.mark.parametrize("kwargs", [{"no_db": True}, {"fail_reads": RuntimeError()}], ids=["no_db", "read_error"])
def test_unreadable_firestore_is_503(client, store, monkeypatch, kwargs):
    store({1: V1}, active=active_of(V2), fail_reads=kwargs.get("fail_reads"))
    if kwargs.get("no_db"):
        monkeypatch.setattr(admin_routes, "_config_db", lambda: None)
    resp, _ = open_restore(client, 1)
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# D. restored_from เป็นคำอ้าง — บันทึกเฉพาะเมื่อค่าตรงทุกช่อง
# ---------------------------------------------------------------------------
def test_edited_after_prefill_saves_as_ordinary_edit(client, store):
    fake = two_versions(store)
    resp, html, confirm_html = restore_and_save(
        client, 1, **{"deduction.personal": money(PERSONAL + 20000)})
    assert "ค่าต่างจากเวอร์ชัน 1 แล้ว — จะบันทึกเป็นการแก้ปกติ" in confirm_html
    assert "บันทึกเวอร์ชัน 3 แล้ว" in html and 'id="restored"' not in html
    assert "restored_from" not in fake.doc(*ACTIVE) and "restored_from" not in history(fake)["3"]


@pytest.mark.parametrize("forged", ["2", "99", "abc", "default", ""])
def test_forged_restored_from_is_not_recorded(client, store, forged):
    # ค่าเป็นของ v1 แต่ hidden field อ้างอย่างอื่น — v2 และค่าตั้งต้นต่างจาก v1, 99 ไม่มี, abc รูปผิด
    fake = two_versions(store)
    _, html = open_restore(client, 1)
    hidden, _ = confirm(client, form_of(html))
    resp, html = save(client, {**hidden, "restored_from": forged})
    assert "บันทึกเวอร์ชัน 3 แล้ว" in html
    assert "restored_from" not in fake.doc(*ACTIVE)


def test_restored_from_checked_against_history_at_save_time(client, store):
    # doc v1 ถูกแก้ใน Console หลังหน้ายืนยัน — ค่าที่บันทึกไม่ตรงกับ v1 ตอนบันทึกแล้ว
    fake = two_versions(store)
    _, html = open_restore(client, 1)
    hidden, confirm_html = confirm(client, form_of(html))
    assert "ค่าตรงกับเวอร์ชัน 1 ทุกช่อง" in confirm_html
    fake.put(HISTORY_COLLECTION, "1", hist(1, {"deduction.personal": PERSONAL + 30000}))
    resp, html = save(client, hidden)
    assert "บันทึกเวอร์ชัน 3 แล้ว" in html
    assert "restored_from" not in fake.doc(*ACTIVE)


def test_restored_from_compared_inside_transaction(client, store):
    # history/1 ถูกแก้ระหว่างกดบันทึก: หลัง transaction อ่าน active แล้ว แต่ก่อนอ่าน history/1
    # ถ้าเทียบใน route ก่อนเข้า transaction จะเห็นค่าเดิมของ v1 แล้วบันทึก restored_from = 1 ผิด
    fake = two_versions(store)
    _, html = open_restore(client, 1)
    hidden, _ = confirm(client, form_of(html))
    edited_v1 = hist(1, {"deduction.personal": PERSONAL + 30000})
    fake.after_transaction_read = lambda f: f.put(HISTORY_COLLECTION, "1", edited_v1)

    resp, html = save(client, hidden)
    assert resp.status_code == 200 and "บันทึกเวอร์ชัน 3 แล้ว" in html
    assert 'id="restored"' not in html
    assert "restored_from" not in fake.doc(*ACTIVE) and "restored_from" not in history(fake)["3"]
    assert fake.doc(*ACTIVE)["override"]["deduction"]["personal"] == PERSONAL + 10000  # ค่าที่กดบันทึก (ของ v1 เดิม)
    assert history(fake)["1"] == edited_v1
    # transaction เห็นค่าที่ถูกแก้ตั้งแต่การอ่านครั้งแรกของ history/1 จึงไม่ต้องลองใหม่
    assert fake.begins == 1


def test_saving_elsewhere_after_restore_form_opened_is_409(client, store):
    fake = two_versions(store)
    _, html = open_restore(client, 1)
    hidden, _ = confirm(client, form_of(html))
    fake.put(*ACTIVE, active_of(hist(3, {"deduction.spouse": 1.0}, previous=2)))
    resp, _ = save(client, hidden)
    assert resp.status_code == 409 and fake.commits == 0


def test_restore_to_values_equal_active_is_no_change(client, store):
    # v2 = active: ย้อนไป v2 ได้ค่าเท่าเดิม → ไม่มีค่าที่เปลี่ยน (ไม่ไปหน้ายืนยัน)
    fake = two_versions(store)
    _, html = open_restore(client, 2)
    html = client.post("/admin/edit", data=form_of(html)).get_data(as_text=True)
    assert 'id="notice">ไม่มีค่าที่เปลี่ยน<' in html
    assert '<input type="hidden" name="restored_from" value="2">' in html  # คำอ้างไม่หายระหว่างทาง
    assert fake.commits == 0


def test_back_and_field_errors_keep_restored_from(client, store):
    two_versions(store)
    _, html = open_restore(client, 1)
    data = form_of(html)
    back = client.post("/admin/edit", data={**data, "action": "back"}).get_data(as_text=True)
    assert '<input type="hidden" name="restored_from" value="1">' in back
    error = client.post("/admin/edit", data={**data, "deduction.personal": "abc"}).get_data(as_text=True)
    assert '<input type="hidden" name="restored_from" value="1">' in error


# ---------------------------------------------------------------------------
# E. แหล่งอ้างอิงที่เติมไว้
# ---------------------------------------------------------------------------
def test_long_old_reference_is_cut_to_fit(client, store):
    store({1: hist(1, {"deduction.personal": PERSONAL + 10000}, reference="ก" * 300)}, active=active_of(V2))
    _, html = open_restore(client, 1)
    reference = form_of(html)["source_reference"]
    assert len(reference) == admin_form.REFERENCE_MAX_LENGTH and reference.endswith("…)")
    hidden, _ = confirm(client, form_of(html))  # ผ่านการตรวจความยาวของฟอร์ม
    assert hidden["source_reference"] == reference


def test_old_reference_with_script_is_escaped(client, store):
    store({1: hist(1, {"deduction.personal": PERSONAL + 10000}, reference="<script>alert(1)</script>")},
          active=active_of(V2))
    _, html = open_restore(client, 1)
    assert "<script>" not in html and "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_restore_reference_for_missing_old_reference():
    assert admin_form.restore_reference(4, None) == "ย้อนไปใช้ค่าของเวอร์ชัน 4 (แหล่งอ้างอิงเดิม: —)"
    assert admin_form.restore_reference(rules_store.RESTORE_DEFAULT) == "ย้อนไปใช้ค่าตั้งต้นในโค้ด (utils/tax_rules.py)"


@pytest.mark.parametrize("text, expected", [("3", 3), (" 3 ", 3), ("default", "default"), ("", None),
                                            ("0", None), ("-1", None), ("abc", None), ("๓", None), ("²", None),
                                            ("3.0", None)])
def test_parse_restored_from(text, expected):
    assert admin_form.parse_restored_from(text) == expected


# ---------------------------------------------------------------------------
# F. หน้าประวัติ: ปุ่มและป้ายย้อน
# ---------------------------------------------------------------------------
def history_page(client):
    return client.get("/admin/history").get_data(as_text=True)


def test_history_buttons(client, store):
    store({1: V1, 2: V2, 3: hist(3, based_on_tax_year=YEAR - 1, previous=2)}, active=active_of(V2))
    html = history_page(client)
    links = re.findall(r'href="/admin/edit\?from_version=([^"]+)"', html)
    assert links == ["1"]  # v3 ใช้ไม่ได้ (ปีภาษีอื่น) · v2 เป็น active
    assert "เป็นค่าที่บันทึกล่าสุดแล้ว" in html
    # active (v2) เท่าค่าตั้งต้นแล้ว — ไม่มีปุ่มใช้ค่าตั้งต้น
    assert 'id="restore-default"' not in html


@pytest.mark.parametrize("active, shown", [
    (active_of(V1), True),                     # ค่าที่บันทึกต่างจากค่าตั้งต้น
    (None, False),                             # ยังไม่มี doc = ใช้ค่าตั้งต้นอยู่แล้ว
    (active_of(V2), False),                    # เท่าค่าตั้งต้น
    ({**active_of(hist(5)), "version": "abc"}, True),  # doc ผิด — เขียนทับด้วยค่าตั้งต้นได้
], ids=["changed", "no_doc", "equal_defaults", "rejected_doc"])
def test_defaults_button(client, store, active, shown):
    store({1: V1}, active=active)
    assert ('href="/admin/edit?from_version=default" id="restore-default"' in history_page(client)) is shown


def test_history_shows_restored_badge(client, store):
    store({1: V1, 2: V2, 3: hist(3, {"deduction.personal": PERSONAL + 10000}, previous=2, restored_from=1),
           4: hist(4, previous=3, restored_from="default"), 5: hist(5, previous=4, restored_from="แปลก")},
          active=active_of(hist(5, previous=4)))
    html = history_page(client)
    assert "ย้อนจากเวอร์ชัน 1" in html and "ย้อนจากค่าตั้งต้นในโค้ด" in html
    assert html.count('class="badge restored"') == 2  # ค่ารูปอื่นไม่แสดง


def test_log_mentions_restore_without_values(client, store, capsys):
    two_versions(store)
    restore_and_save(client, 1)
    out = capsys.readouterr().out
    assert "[ADMIN] บันทึกค่าภาษีเวอร์ชัน 3 (แก้ 1 รายการ: deduction.personal) · ย้อนจากเวอร์ชัน 1" in out
    assert money(PERSONAL + 10000) not in out and V1["source_reference"] not in out
