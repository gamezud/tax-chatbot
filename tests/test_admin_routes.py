"""
เทสหน้า /admin ขั้น 4A: ตัวกันให้เข้าได้จากเครื่องนี้เท่านั้น + หน้าแสดง RULES_INFO แบบอ่านอย่างเดียว
(login, CSRF และการล็อกอยู่ใน test_admin_login.py)

สำคัญที่สุด: ตัวกันต้องไม่กระทบ /webhook ที่ Dialogflow ส่งมาทาง ngrok (มี X-Forwarded-* และ Host เป็นโดเมน ngrok)
ทุกเทสเริ่มจาก "ไม่ได้ตั้ง ADMIN_PASSWORD_HASH" (fixture no_admin_env) ไม่ขึ้นกับค่าจริงในเครื่อง
ค่าที่ตั้งในเทสไฟล์นี้เป็นข้อความทดแทน ไม่ใช่ hash จริง — เทสไฟล์นี้ไม่ได้ login ด้วยรหัส
"""
import json
from pathlib import Path

import pytest

import app as app_module
from admin_routes import admin_status_line, is_admin_path, is_local_request, strip_port
from utils import tax_rules
# fixture mocks แทน Firestore/PDF/RAG (ใช้กับเทส /webhook สาขาสรุปผล)
from test_rules_loading import mocks  # noqa: F401
from test_line_verified_cases import line_payload

ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = ROOT / "tests" / "fixtures"
PLACEHOLDER_HASH = "test-placeholder-not-a-real-hash"

# header ที่ ngrok เติมให้ทุก request (ทดลองจริง 2026-10-13) + Host โดเมน ngrok
NGROK_HEADERS = {"X-Forwarded-For": "203.0.113.5", "X-Forwarded-Host": "abc.ngrok-free.app",
                 "X-Forwarded-Proto": "https"}
NGROK_BASE_URL = "https://abc.ngrok-free.app"
LOCAL_HEADER_NAMES = ["Host", "User-Agent", "Accept"]


@pytest.fixture(autouse=True)
def no_admin_env(monkeypatch):
    monkeypatch.delenv("ADMIN_PASSWORD_HASH", raising=False)


@pytest.fixture
def admin_on(monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", PLACEHOLDER_HASH)


@pytest.fixture
def client():
    return app_module.app.test_client()


@pytest.fixture
def logged_in(client):
    # ข้ามหน้า login (เทส login อยู่ใน test_admin_login.py) — ตั้ง session ตรง ๆ
    with client.session_transaction() as sess:
        sess["admin"] = True
    return client


# ---------------------------------------------------------------------------
# A. ตัวตัดสิน (ฟังก์ชันล้วน)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("remote_addr, host", [
    ("127.0.0.1", "127.0.0.1:5000"),
    ("127.0.0.1", "localhost:5000"),
    ("127.0.0.1", "127.0.0.1"),
    ("127.0.0.1", "localhost"),
    ("127.0.0.1", "LOCALHOST:5000"),
    ("::1", "[::1]:5000"),
    ("::1", "[::1]"),
    ("127.0.0.2", "127.0.0.1:5000"),  # ทั้ง 127.0.0.0/8 เป็น loopback
])
def test_local_request_passes(remote_addr, host):
    assert is_local_request(remote_addr, host, LOCAL_HEADER_NAMES) is True


@pytest.mark.parametrize("header", [
    "X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto", "X-Forwarded-Port",
    "x-forwarded-for", "X-FORWARDED-HOST", "Forwarded", "forwarded",
])
def test_forwarding_header_blocks(header):
    assert is_local_request("127.0.0.1", "127.0.0.1:5000", LOCAL_HEADER_NAMES + [header]) is False


@pytest.mark.parametrize("remote_addr", ["10.0.0.5", "192.168.1.10", "172.16.0.1", "203.0.113.5",
                                         "", None, "not-an-ip"])
def test_non_loopback_remote_addr_blocks(remote_addr):
    assert is_local_request(remote_addr, "127.0.0.1:5000", LOCAL_HEADER_NAMES) is False


@pytest.mark.parametrize("host", [
    "abc.ngrok-free.app", "abc.ngrok-free.app:443", "127.0.0.1.evil.example", "localhost.evil.example:5000",
    "192.168.1.10:5000", "", None, "[::1", "[::1]x:5000", "[::1]:abc", "localhost:abc", "127.0.0.1:",
])
def test_non_local_host_blocks(host):
    assert is_local_request("127.0.0.1", host, LOCAL_HEADER_NAMES) is False


@pytest.mark.parametrize("host, expected", [
    ("127.0.0.1:5000", "127.0.0.1"), ("localhost", "localhost"), ("[::1]:5000", "::1"),
    ("[::1]", "::1"), ("::1", "::1"), ("Example.COM:80", "example.com"), ("[::1", ""), (None, ""),
    ("[::1]x:5000", ""), ("localhost:abc", ""), ("127.0.0.1:", ""),
])
def test_strip_port(host, expected):
    assert strip_port(host) == expected


@pytest.mark.parametrize("path, expected", [
    ("/admin", True), ("/admin/", True), ("/admin/login", True),
    ("/administrator", False), ("/webhook", False), ("/", False), ("/liff/admin", False),
])
def test_is_admin_path(path, expected):
    assert is_admin_path(path) is expected


# ---------------------------------------------------------------------------
# B. หน้า /admin ผ่าน Flask test client (REMOTE_ADDR 127.0.0.1, Host localhost)
# ---------------------------------------------------------------------------
def test_admin_page_local_shows_default_rules_info(admin_on, logged_in):
    resp = logged_in.get("/admin")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "ค่าตั้งต้นในโค้ด" in html
    assert f"<td>{tax_rules.TAX_RULES['tax_year']}</td>" in html
    assert '<td id="version">—</td>' in html
    assert "ไม่มี" in html


def test_admin_page_shows_firestore_rules_info(admin_on, logged_in, monkeypatch):
    monkeypatch.setattr(tax_rules, "RULES_INFO", {"source": "firestore", "version": 7,
                                                  "changed_keys": ["deduction.personal", "tax_brackets"]})
    html = logged_in.get("/admin").get_data(as_text=True)
    assert "ค่าที่แก้ไว้ใน Firestore" in html
    assert '<td id="version">7</td>' in html
    assert "<code>deduction.personal</code>" in html and "<code>tax_brackets</code>" in html


def test_admin_page_accepts_ipv6_loopback_with_port(admin_on, client):
    # ผ่านตัวกันในเครื่องแล้วไปถึงขั้นต้อง login (ไม่ใช่ 404)
    resp = client.get("/admin", base_url="http://[::1]:5000", environ_base={"REMOTE_ADDR": "::1"})
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/admin/login")


def not_found_body(client):
    return client.get("/no-such-page").get_data()


@pytest.mark.parametrize("kwargs", [
    {"headers": NGROK_HEADERS, "base_url": NGROK_BASE_URL},              # ผ่าน ngrok จริง
    {"headers": {"X-Forwarded-For": "203.0.113.5"}},                    # header อย่างเดียว
    {"headers": {"Forwarded": "for=203.0.113.5"}},
    {"base_url": NGROK_BASE_URL},                                        # Host อย่างเดียว
    {"environ_base": {"REMOTE_ADDR": "10.0.0.5"}},
    {"environ_base": {"REMOTE_ADDR": "192.168.1.10"}},
], ids=["ngrok", "x_forwarded_for", "forwarded", "ngrok_host", "remote_10", "remote_192_168"])
@pytest.mark.parametrize("method, path", [("GET", "/admin"), ("POST", "/admin"), ("GET", "/admin/"),
                                          ("GET", "/admin/login"), ("DELETE", "/admin")])
def test_admin_not_local_is_plain_404(admin_on, client, kwargs, method, path):
    # 404 เหมือนหน้าที่ไม่มีอยู่จริงทุกตัวอักษร รวม method ที่ไม่รองรับ (ไม่ใช่ 405) และ path ที่ไม่มี (ไม่ใช่ 308)
    resp = client.open(path, method=method, **kwargs)
    assert resp.status_code == 404
    assert resp.get_data() == not_found_body(client)


# path แปลก ๆ ส่งเข้า PATH_INFO ตรง ๆ (test client แปลง "//admin" เป็น URL ที่มี host ชื่อ admin จึงใช้ path ธรรมดาไม่ได้)
# merge_slashes ของ Werkzeug เปิดอยู่: "//admin" จับคู่กับ route /admin ได้ ตัวกันต้องจับได้ก่อน
# ทดลองแล้ว (2026-10-13): request.path ของ Werkzeug ยุบ / ที่นำหน้าเหลือตัวเดียว ("//admin" → "/admin")
# is_admin_path จึงเห็น "/admin" — เทสนี้ล็อกไว้ ถ้าเปลี่ยนไปเช็ก PATH_INFO ดิบ "//admin" จะหลุดถึงหน้า admin
@pytest.mark.parametrize("path_info", ["//admin", "//admin/", "/admin//", "///admin", "/./admin",
                                       "/admin/./", "/admin", "/ADMIN", "//admin//x"])
def test_admin_odd_paths_not_local_are_plain_404(admin_on, client, path_info):
    resp = client.get("/placeholder", headers=NGROK_HEADERS, environ_overrides={"PATH_INFO": path_info})
    assert resp.status_code == 404
    assert resp.get_data() == not_found_body(client)


def test_admin_percent_encoded_path_not_local_is_plain_404(admin_on, client):
    # "/%61dmin" ถอดเป็น "/admin" ก่อนถึง Flask
    resp = client.get("/%61dmin", headers=NGROK_HEADERS)
    assert resp.status_code == 404
    assert resp.get_data() == not_found_body(client)


@pytest.mark.parametrize("method", ["HEAD", "OPTIONS"])
def test_admin_head_and_options(admin_on, logged_in, method):
    # ในเครื่อง: Flask ตอบ HEAD/OPTIONS ให้ route GET เอง — ผ่าน ngrok: 404 ไม่บอกว่ามี route (ไม่มี header Allow)
    assert logged_in.open("/admin", method=method).status_code == 200
    resp = logged_in.open("/admin", method=method, headers=NGROK_HEADERS, base_url=NGROK_BASE_URL)
    assert resp.status_code == 404
    assert "Allow" not in resp.headers


@pytest.mark.parametrize("value", [None, "", "   "], ids=["unset", "empty", "spaces"])
def test_admin_without_hash_is_404_even_locally(client, monkeypatch, value):
    if value is not None:
        monkeypatch.setenv("ADMIN_PASSWORD_HASH", value)
    resp = client.get("/admin")
    assert resp.status_code == 404
    assert resp.get_data() == not_found_body(client)


def test_admin_status_line_never_prints_hash(monkeypatch):
    assert "ปิดอยู่" in admin_status_line()
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", PLACEHOLDER_HASH)
    line = admin_status_line()
    assert "เปิดอยู่" in line and PLACEHOLDER_HASH not in line
    assert line.startswith("🛠️ [ADMIN]")


def test_main_block_prints_admin_status_before_app_run():
    # บล็อก __main__ ไม่ถูกรันในเทส — เช็กแบบ substring เหมือน test_liff_page.py
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    main_block = source.split("if __name__ == '__main__':", 1)[1]
    assert "print(admin_status_line())" in main_block.split("app.run(", 1)[0]


# ---------------------------------------------------------------------------
# C. route อื่นที่มาทาง ngrok ต้องทำงานเหมือนเดิม (ตั้ง hash ไว้ด้วย ให้ตัวกันทำงานเต็มที่)
# ---------------------------------------------------------------------------
def test_webhook_greeting_through_ngrok_unchanged(admin_on, client):
    payload = json.loads((FIXTURES_DIR / "001_Welcome.json").read_text(encoding="utf-8"))
    direct = client.post("/webhook", json=payload)
    via_ngrok = client.post("/webhook", json=payload, headers=NGROK_HEADERS, base_url=NGROK_BASE_URL)
    assert via_ngrok.status_code == 200
    assert via_ngrok.get_json() == direct.get_json()


def test_webhook_summary_through_ngrok_unchanged(admin_on, client, mocks):
    # สาขาสรุปผล (คำนวณ + PDF + Firestore ปลอม) ผ่าน ngrok ได้ผลเหมือนยิงตรง
    payload = line_payload("ไม่มี", {})
    direct = client.post("/webhook", json=payload).get_json()
    via_ngrok = client.post("/webhook", json=payload, headers=NGROK_HEADERS, base_url=NGROK_BASE_URL).get_json()
    assert "5. ภาษีที่คำนวณได้ทั้งสิ้น: 8,450.00 บาท" in via_ngrok["fulfillmentText"]
    assert via_ngrok == direct
    assert mocks.firebase.save_tax_report.call_count == 2


def test_download_through_ngrok_unchanged(admin_on, client, monkeypatch, tmp_path):
    # send_from_directory ของ Flask อ้าง path จาก app.root_path (ไม่ใช่ cwd)
    # ชี้ root_path ไป tmp_path เพื่อไม่ต้องวางไฟล์ลง static/reports จริง
    reports = tmp_path / "static" / "reports"
    reports.mkdir(parents=True)
    (reports / "tax_report_test.pdf").write_bytes(b"%PDF-test")
    monkeypatch.setattr(app_module.app, "root_path", str(tmp_path))
    resp = client.get("/download/tax_report_test.pdf", headers=NGROK_HEADERS, base_url=NGROK_BASE_URL)
    assert resp.status_code == 200
    assert resp.data == b"%PDF-test"


def test_liff_page_through_ngrok_unchanged(admin_on, client, monkeypatch):
    monkeypatch.setenv("LIFF_ID", "1234567890-abcdefgh")
    resp = client.get("/liff/donation", headers=NGROK_HEADERS, base_url=NGROK_BASE_URL)
    assert resp.status_code == 200
    assert "1234567890-abcdefgh" in resp.get_data(as_text=True)


def test_liff_ocr_through_ngrok_reaches_route(admin_on, client, monkeypatch):
    # ไม่ได้ตั้ง LIFF_ID → route ตอบ 503 not_configured ของตัวเอง (ไม่ใช่ 404 จากตัวกัน)
    monkeypatch.delenv("LIFF_ID", raising=False)
    resp = client.post("/liff/ocr", headers=NGROK_HEADERS, base_url=NGROK_BASE_URL)
    assert resp.status_code == 503
    assert resp.get_json() == {"status": "not_configured"}
