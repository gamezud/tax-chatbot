"""
เทสก้อน 6A: หน้า /admin/history (อ่านอย่างเดียว)

- Firestore เป็นตัวปลอมที่เก็บข้อมูลจริง (fake_firestore.py) — ทุกเทสตรวจท้ายเทสว่าไม่มีการเขียนเลย
  (GET ต้องไม่เขียนอะไร: commits == 0, begins == 0, violations == [])
- doc ประวัติสร้างแบบที่ save_config เขียน: override ครบทุก key + previous_version
"""
import datetime
import re
from html import unescape

import pytest

import admin_form
import admin_history
import admin_routes
import app as app_module
from utils import rules_store, tax_rules
from utils.rules_store import CONFIG_COLLECTION, CONFIG_DOCUMENT, DEFAULT_TAX_RULES, HISTORY_COLLECTION
from fake_firestore import FakeFirestore

TOKEN = "test-csrf-token"
REFERENCE = "ประกาศกรมสรรพากร ฉบับทดสอบ เลขที่ 1"
YEAR = DEFAULT_TAX_RULES["tax_year"]
PERSONAL = DEFAULT_TAX_RULES["deduction"]["personal"]
ACTIVE = (CONFIG_COLLECTION, CONFIG_DOCUMENT)
UTC = datetime.timezone.utc
SAVED_AT = datetime.datetime(2026, 10, 15, 7, 5, tzinfo=UTC)  # 14:05 เวลาไทย


def money(value):
    return admin_form.format_value(value, admin_form.MONEY_FIELD)


def hist(version, changes=None, previous=None, at=SAVED_AT, reference=REFERENCE, note="", **fields):
    """doc ประวัติแบบที่ save_config เขียน — changes: {ชื่อช่องในฟอร์ม: ค่า}"""
    override = rules_store.to_override(DEFAULT_TAX_RULES)
    for name, value in (changes or {}).items():
        if name.startswith("tax_brackets."):
            _, i, part = name.split(".")
            override["tax_brackets"][int(i) - 1][part] = value
        else:
            *parents, leaf = name.split(".")
            node = override
            for key in parents:
                node = node[key]
            node[leaf] = value
    doc = {"based_on_tax_year": YEAR, "version": version, "override": override, "source_reference": reference,
           "note": note, "updated_at": at, "previous_version": previous}
    doc.update(fields)
    return doc


def active_of(doc):
    # active มีเนื้อหาเดียวกับประวัติ ยกเว้น previous_version
    return {k: v for k, v in doc.items() if k != "previous_version"}


V1 = hist(1, {"deduction.personal": PERSONAL + 10000})
V2 = hist(2, previous=1, at=SAVED_AT + datetime.timedelta(hours=1), note="กลับค่าตั้งต้น")


@pytest.fixture(autouse=True)
def admin_on(monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", "test-placeholder-not-a-real-hash")


@pytest.fixture
def store(monkeypatch):
    """store(history={id: doc}, active=doc) — ท้ายเทสตรวจว่าไม่มีการเขียนใด ๆ (หน้านี้อ่านอย่างเดียว)"""
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
        assert fake.violations == [] and fake.commits == 0 and fake.begins == 0, "หน้าประวัติเขียน Firestore"


@pytest.fixture
def client():
    client = app_module.app.test_client()
    with client.session_transaction() as sess:
        sess["admin"] = True
        sess["csrf_token"] = TOKEN
    return client


def get_history(client):
    resp = client.get("/admin/history")
    return resp, resp.get_data(as_text=True)


def versions_in_order(html):
    return [int(v) for v in re.findall(r'<tr class="version" data-version="(\d+)">', html)]


def row_html(html, version):
    """html ของแถวเวอร์ชันนั้น (รวมตารางรายการที่เปลี่ยนข้างใน) ถึงก่อนแถวถัดไป"""
    start = html.index(f'<tr class="version" data-version="{version}">')
    rest = html[start + 1:]
    ends = [i for i in (rest.find('<tr class="version"'), rest.find('<tr class="malformed"'),
                        rest.find("</main>")) if i != -1]
    return html[start:start + 1 + min(ends)]


def change_rows(row):
    """[(ชื่อช่อง, ค่าเดิม, ค่าใหม่)] ของตารางรายการที่เปลี่ยนในแถวนั้น"""
    found = re.findall(r'data-name="([^"]+)">.*?<td class="num">([^<]*)</td>\s*<td class="num">([^<]*)</td>',
                       row, re.S)
    return [(name, unescape(old).strip(), unescape(new).strip()) for name, old, new in found]


# ---------------------------------------------------------------------------
# A. เรียงลำดับ เวลาไทย และรายการที่เปลี่ยน
# ---------------------------------------------------------------------------
def test_versions_newest_first_by_number_not_text(client, store):
    # doc id "10" มาก่อน "2" ถ้าเรียงแบบข้อความ — ต้องเรียงตามตัวเลข
    store({1: V1, 2: V2, 10: hist(10, previous=2)}, active=active_of(hist(10, previous=2)))
    resp, html = get_history(client)
    assert resp.status_code == 200
    assert versions_in_order(html) == [10, 2, 1]


@pytest.mark.parametrize("at, expected", [
    (SAVED_AT, "15 ต.ค. 2569 14:05 น."),
    (datetime.datetime(2026, 12, 31, 20, 0, tzinfo=UTC), "1 ม.ค. 2570 03:00 น."),  # ข้ามวันและข้ามปี
    (datetime.datetime(2026, 10, 15, 7, 5), "15 ต.ค. 2569 14:05 น."),                # ไม่มี tzinfo ถือเป็น UTC
    (None, "—"),
    ("15/10/2026", "—"),
])
def test_saved_time_is_thai_time(at, expected):
    assert admin_history.thai_time(at) == expected


def test_saved_time_shown_on_page(client, store):
    store({1: V1})
    _, html = get_history(client)
    assert '<td class="saved-at">15 ต.ค. 2569 14:05 น.</td>' in row_html(html, 1)


def test_changes_from_previous_version_with_old_and_new_values(client, store):
    store({1: V1, 2: V2}, active=active_of(V2))
    _, html = get_history(client)
    v1, v2 = row_html(html, 1), row_html(html, 2)
    # รูปแบบเดียวกับหน้ายืนยัน: เงินมี , และหน่วย
    assert "เทียบกับค่าตั้งต้น" in v1
    assert change_rows(v1) == [("deduction.personal", f"{money(PERSONAL)} บาท", f"{money(PERSONAL + 10000)} บาท")]
    assert "เทียบกับเวอร์ชัน 1" in v2
    assert change_rows(v2) == [("deduction.personal", f"{money(PERSONAL + 10000)} บาท", f"{money(PERSONAL)} บาท")]
    assert admin_form.LABELS["deduction.personal"] in v2


def test_rates_and_bracket_steps_shown_as_percent_and_step_names(client, store):
    old_rate = DEFAULT_TAX_RULES["method_2"]["rate"]
    store({1: hist(1, {"method_2.rate": old_rate * 2,
                       "tax_brackets.3.upper": DEFAULT_TAX_RULES["tax_brackets"][2][0] + 1000})})
    _, html = get_history(client)
    row = row_html(html, 1)
    rows = dict((name, (old, new)) for name, old, new in change_rows(row))
    pct = admin_form.PERCENT_FIELD
    assert rows["method_2.rate"] == (f"{admin_form.format_value(old_rate, pct)} %",
                                     f"{admin_form.format_value(old_rate * 2, pct)} %")
    assert admin_form.step_label(3, "upper") in row


def test_version_equal_to_previous_says_no_difference(client, store):
    store({1: V1, 2: hist(2, {"deduction.personal": PERSONAL + 10000}, previous=1)})
    _, html = get_history(client)
    assert '<span class="no-change">ไม่มีค่าที่ต่าง</span>' in row_html(html, 2)


@pytest.mark.parametrize("previous", [7, "abc", [1]], ids=["missing_doc", "string", "list"])
def test_previous_version_that_cannot_be_compared(client, store, previous):
    store({2: hist(2, previous=previous)})
    resp, html = get_history(client)
    assert resp.status_code == 200
    row = row_html(html, 2)
    assert admin_history.NOT_COMPARABLE in row and change_rows(row) == []


# ---------------------------------------------------------------------------
# B. ป้าย active / เซิร์ฟเวอร์ใช้อยู่
# ---------------------------------------------------------------------------
def test_badges_on_different_rows_mean_pending_restart(client, store, monkeypatch):
    store({1: V1, 2: V2}, active=active_of(V2))
    monkeypatch.setattr(tax_rules, "RULES_INFO", {"source": "firestore", "version": 1,
                                                  "changed_keys": ["deduction.personal"]})
    _, html = get_history(client)
    assert "ค่าที่บันทึกล่าสุด" in row_html(html, 2) and "เซิร์ฟเวอร์ใช้อยู่" not in row_html(html, 2)
    assert "เซิร์ฟเวอร์ใช้อยู่" in row_html(html, 1) and "ค่าที่บันทึกล่าสุด" not in row_html(html, 1)
    assert 'id="pending-restart"' in html


def test_badges_on_same_row_after_restart(client, store, monkeypatch):
    store({1: V1, 2: V2}, active=active_of(V2))
    monkeypatch.setattr(tax_rules, "RULES_INFO", {"source": "firestore", "version": 2, "changed_keys": []})
    _, html = get_history(client)
    assert "ค่าที่บันทึกล่าสุด" in row_html(html, 2) and "เซิร์ฟเวอร์ใช้อยู่" in row_html(html, 2)
    assert 'id="pending-restart"' not in html
    assert "เซิร์ฟเวอร์ใช้อยู่" not in html.split('id="defaults"')[1].split("</p>")[0]


def test_server_on_defaults_marks_defaults_row(client, store):
    # RULES_INFO เป็นค่าตั้งต้น (conftest) แต่มีเวอร์ชันที่บันทึกแล้ว = รอรีสตาร์ต
    store({1: V1}, active=active_of(V1))
    _, html = get_history(client)
    assert "เซิร์ฟเวอร์ใช้อยู่" in html.split('id="defaults"')[1].split("</p>")[0]
    assert "เซิร์ฟเวอร์ใช้อยู่" not in row_html(html, 1)
    assert "ค่าที่บันทึกล่าสุด" in row_html(html, 1)


def test_active_badge_follows_active_doc_not_rules_info(client, store, monkeypatch):
    # active ถูกบันทึกเป็น 2 แล้ว แต่เซิร์ฟเวอร์ยังใช้ 1 — ป้าย "ค่าที่บันทึกล่าสุด" ต้องดู doc active
    store({1: V1, 2: V2}, active=active_of(V2))
    monkeypatch.setattr(tax_rules, "RULES_INFO", {"source": "firestore", "version": 1, "changed_keys": []})
    _, html = get_history(client)
    assert "ค่าที่บันทึกล่าสุด" in row_html(html, 2)


# ---------------------------------------------------------------------------
# C. doc ผิดรูป / ใช้กับปีภาษีนี้ไม่ได้ — หน้าต้องไม่พัง
# ---------------------------------------------------------------------------
MALFORMED_DOCS = {
    "version_string": ("3", hist("abc")),
    "version_missing": ("3", {k: v for k, v in hist(3).items() if k != "version"}),
    "version_not_id": ("3", hist(4)),
    "override_missing": ("3", {k: v for k, v in hist(3).items() if k != "override"}),
    "override_not_map": ("3", hist(3, override=["deduction"])),
    "only_note": ("manual", {"note": "สร้างมือ"}),
    "override_weird_values": ("3", hist(3, override={"deduction": {"personal": {"x": [1]}}})),
}


@pytest.mark.parametrize("doc_id, doc", MALFORMED_DOCS.values(), ids=MALFORMED_DOCS.keys())
def test_malformed_history_doc_is_a_row_not_a_crash(client, store, doc_id, doc):
    store({1: V1, doc_id: doc})
    resp, html = get_history(client)
    assert resp.status_code == 200
    if doc_id == "3" and isinstance(doc.get("override"), dict) and rules_store.valid_version(doc) == 3:
        # รูปถูกแต่ค่าผิด → แถวปกติพร้อมเหตุผล
        assert "ค่าไม่ผ่านการตรวจปัจจุบัน" in row_html(html, 3)
    else:
        assert f'<tr class="malformed" data-doc-id="{doc_id}">' in html
        assert admin_history.MALFORMED in html
    assert versions_in_order(html)[-1] == 1  # แถวที่ถูกยังแสดงครบ


def test_unexpected_exception_becomes_malformed_row_and_is_logged(client, store, monkeypatch, capsys):
    # บั๊กในโค้ดสร้างแถว: หน้ายังเปิดได้ แต่ต้องมีร่องรอยใน log ว่าเป็น exception ไม่ใช่ข้อมูลเสีย
    store({1: hist(1, reference="แหล่งอ้างอิงลับ", note="หมายเหตุลับ")})

    def broken(doc):
        raise ValueError("ข้อความของ exception ที่อาจมีเนื้อหา doc")

    monkeypatch.setattr(admin_history, "unusable_reason", broken)
    resp, html = get_history(client)
    assert resp.status_code == 200
    assert '<tr class="malformed" data-doc-id="1">' in html
    out = capsys.readouterr().out
    assert "[ADMIN] แถวประวัติ 1 แสดงเป็นข้อมูลผิดรูป เพราะ ValueError" in out
    for secret in ("ข้อความของ exception", "แหล่งอ้างอิงลับ", "หมายเหตุลับ"):
        assert secret not in out


def test_ordinary_malformed_doc_is_not_logged(client, store, capsys):
    store({"manual": {"note": "สร้างมือ"}, 3: hist("abc")})
    get_history(client)
    assert "แถวประวัติ" not in capsys.readouterr().out


def test_malformed_rows_come_after_versions(client, store):
    store({1: V1, "zzz": {"note": "x"}, "aaa": {"note": "y"}, 2: V2})
    _, html = get_history(client)
    assert html.index('data-version="1"') < html.index('data-doc-id="aaa"') < html.index('data-doc-id="zzz"')


def test_other_tax_year_explains_in_thai(client, store):
    store({1: hist(1, based_on_tax_year=YEAR - 1)})
    _, html = get_history(client)
    row = row_html(html, 1)
    assert f"บันทึกไว้สำหรับปีภาษี {YEAR - 1} ใช้กับปีภาษี {YEAR} ไม่ได้" in row
    assert admin_history.NOT_COMPARABLE in row


def test_bad_value_reason_uses_thai_label(client, store):
    store({1: hist(1, {"deduction.personal": -1.0})})
    _, html = get_history(client)
    row = row_html(html, 1)
    assert f"{admin_form.LABELS['deduction.personal']}: ต้องไม่ติดลบ" in row
    assert "deduction.personal ต้อง" not in row


def test_page_has_no_english_key_names_in_text(client, store):
    store({1: V1, 2: V2, 3: hist(3, {"deduction.personal": -1.0}, previous=2),
           4: hist(4, previous=3, override={"salary_capp": 1.0})})
    _, html = get_history(client)
    text = re.sub(r'data-name="[^"]*"', "", html)  # data-name เป็น attribute ที่เทสใช้ ไม่ใช่ข้อความที่แสดง
    english = {name for name, _, _ in admin_form.all_fields()} | set(admin_form.LABELS) | {"salary_capp"}
    assert [name for name in english if name in text] == []


# ---------------------------------------------------------------------------
# D. อ่านไม่ได้ / ว่าง / escape / ตัวกัน
# ---------------------------------------------------------------------------
def test_no_history_yet(client, store):
    store()
    resp, html = get_history(client)
    assert resp.status_code == 200 and 'id="no-history">ยังไม่มีประวัติ' in html


@pytest.mark.parametrize("kwargs", [{"no_db": True}, {"fail_reads": RuntimeError()},
                                    {"fail_reads": TimeoutError()}], ids=["no_db", "read_error", "timeout"])
def test_unreadable_firestore_is_503(client, store, monkeypatch, kwargs):
    store({1: V1}, fail_reads=kwargs.get("fail_reads"))
    if kwargs.get("no_db"):
        monkeypatch.setattr(admin_routes, "_config_db", lambda: None)
    resp, html = get_history(client)
    assert resp.status_code == 503
    assert admin_routes.HISTORY_UNREADABLE_MESSAGE in html and 'id="history"' not in html


def test_history_read_failure_after_active_read_is_503(client, store, monkeypatch):
    store({1: V1}, active=active_of(V1))

    def fail(db):
        raise RuntimeError("อ่านประวัติล้ม")

    monkeypatch.setattr(rules_store, "read_history_docs", fail)
    resp, _ = get_history(client)
    assert resp.status_code == 503


def test_script_in_reference_and_note_is_escaped(client, store):
    store({1: hist(1, reference="<script>alert(1)</script>", note="<script>alert(2)</script>")})
    _, html = get_history(client)
    assert "<script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html and "&lt;script&gt;alert(2)&lt;/script&gt;" in html


def test_history_from_outside_is_404(client, store):
    store({1: V1})
    resp = client.get("/admin/history", headers={"X-Forwarded-For": "203.0.113.5"})
    assert resp.status_code == 404


def test_history_without_login_redirects(store):
    store({1: V1})
    resp = app_module.app.test_client().get("/admin/history")
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/admin/login")


def test_admin_page_links_to_history(client, store):
    store()
    assert 'href="/admin/history" id="history-link"' in client.get("/admin").get_data(as_text=True)


def test_reads_whole_collection_without_order_by(monkeypatch):
    # order_by("version") ตัด doc ที่ไม่มี field version ทิ้ง — ต้องอ่านด้วย stream ทั้ง collection
    fake = FakeFirestore({(HISTORY_COLLECTION, "1"): V1, (HISTORY_COLLECTION, "manual"): {"note": "x"}})
    assert sorted(doc_id for doc_id, _ in rules_store.read_history_docs(fake)) == ["1", "manual"]
