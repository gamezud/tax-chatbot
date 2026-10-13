"""
เทสหน้า /admin ขั้น 4B: login, CSRF, การล็อกเมื่อรหัสผิด และ scripts/make_admin_hash.py

- hash สร้างในเทสเอง ไม่ใช้ค่า ADMIN_PASSWORD_HASH จริงในเครื่อง
- เวลาเป็นนาฬิกาปลอม (แทน admin_routes._now) ไม่ sleep
- ตัวนับรหัสผิดสร้างใหม่ทุกเทส (แทน admin_routes._throttle)
"""
import hmac
import importlib.util
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import pytest
from werkzeug.security import check_password_hash, generate_password_hash

import admin_routes
import app as app_module

ROOT = Path(__file__).resolve().parents[1]
TEST_PASSWORD = "Ultra-Secret-Test-Password-123!"
TEST_HASH = generate_password_hash(TEST_PASSWORD)
LOCK_MESSAGE = "ระบบล็อกการเข้าสู่ระบบชั่วคราว"
EXPIRED_MESSAGE = "หน้านี้หมดอายุแล้ว (อาจเพราะเซิร์ฟเวอร์เพิ่งเริ่มใหม่) กรุณาเข้าสู่ระบบอีกครั้งครับ"


@pytest.fixture(autouse=True)
def admin_setup(monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", TEST_HASH)
    monkeypatch.setattr(admin_routes, "_throttle", admin_routes.LoginThrottle())


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    now = {"t": 1000.0}
    monkeypatch.setattr(admin_routes, "_now", lambda: now["t"])
    return now


@pytest.fixture
def client():
    return app_module.app.test_client()


def csrf_from(html):
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, html
    return match.group(1)


def login_page_token(client):
    return csrf_from(client.get("/admin/login").get_data(as_text=True))


def post_login(client, password):
    return client.post("/admin/login", data={"csrf_token": login_page_token(client), "password": password})


def is_logged_in(client):
    return client.get("/admin").status_code == 200


# ---------------------------------------------------------------------------
# A. login / logout
# ---------------------------------------------------------------------------
def test_admin_without_login_redirects_to_login(client):
    resp = client.get("/admin")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/admin/login")


def test_correct_password_logs_in(client):
    resp = post_login(client, TEST_PASSWORD)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/admin")
    page = client.get("/admin")
    assert page.status_code == 200
    assert "ออกจากระบบ" in page.get_data(as_text=True)


def test_wrong_password_is_rejected(client):
    resp = post_login(client, "wrong")
    assert resp.status_code == 401
    assert "รหัสผ่านไม่ถูกต้อง" in resp.get_data(as_text=True)
    assert not is_logged_in(client)


def test_login_page_when_logged_in_goes_to_admin(client):
    post_login(client, TEST_PASSWORD)
    resp = client.get("/admin/login")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/admin")


def test_logout_is_post_only_and_clears_session(client):
    post_login(client, TEST_PASSWORD)
    assert client.get("/admin/logout").status_code == 405
    assert is_logged_in(client)
    token = csrf_from(client.get("/admin").get_data(as_text=True))
    resp = client.post("/admin/logout", data={"csrf_token": token})
    assert resp.status_code == 302
    assert not is_logged_in(client)


def test_csrf_token_changes_after_login(client):
    before = login_page_token(client)
    client.post("/admin/login", data={"csrf_token": before, "password": TEST_PASSWORD})
    after = csrf_from(client.get("/admin").get_data(as_text=True))
    assert after != before  # session ใหม่ทั้งก้อนหลัง login


# ---------------------------------------------------------------------------
# B. ล็อกเมื่อรหัสผิดติดกัน
# ---------------------------------------------------------------------------
def fail_times(client, n):
    return [post_login(client, f"wrong-{i}") for i in range(n)]


def test_locked_after_max_failures(client):
    responses = fail_times(client, admin_routes.MAX_FAILED_LOGINS)
    assert all(r.status_code == 401 for r in responses)
    assert LOCK_MESSAGE not in responses[-2].get_data(as_text=True)
    assert LOCK_MESSAGE in responses[-1].get_data(as_text=True)  # ครั้งที่ครบแจ้งว่าล็อก


def test_correct_password_rejected_while_locked(client):
    fail_times(client, admin_routes.MAX_FAILED_LOGINS)
    resp = post_login(client, TEST_PASSWORD)
    assert resp.status_code == 429
    assert LOCK_MESSAGE in resp.get_data(as_text=True)
    assert not is_logged_in(client)


def test_no_password_check_while_locked(client, monkeypatch):
    calls = []

    def counting_check(pwhash, password):
        calls.append(1)
        return check_password_hash(pwhash, password)

    monkeypatch.setattr(admin_routes, "check_password_hash", counting_check)
    fail_times(client, admin_routes.MAX_FAILED_LOGINS)
    assert len(calls) == admin_routes.MAX_FAILED_LOGINS
    post_login(client, TEST_PASSWORD)
    post_login(client, "wrong-again")
    assert len(calls) == admin_routes.MAX_FAILED_LOGINS  # ระหว่างล็อกไม่ตรวจรหัสเลย


def test_lock_expires_after_lockout_seconds(client, clock):
    fail_times(client, admin_routes.MAX_FAILED_LOGINS)
    clock["t"] += admin_routes.LOCKOUT_SECONDS - 1
    assert post_login(client, TEST_PASSWORD).status_code == 429
    clock["t"] += 1
    assert post_login(client, TEST_PASSWORD).status_code == 302
    assert is_logged_in(client)


def test_success_resets_failure_count(client):
    fail_times(client, admin_routes.MAX_FAILED_LOGINS - 1)
    post_login(client, TEST_PASSWORD)
    client.post("/admin/logout", data={"csrf_token": csrf_from(client.get("/admin").get_data(as_text=True))})
    # ถ้าไม่เริ่มนับใหม่หลัง login สำเร็จ ผิดอีก (MAX - 1) ครั้งจะครบแล้วล็อก
    fail_times(client, admin_routes.MAX_FAILED_LOGINS - 1)
    assert post_login(client, TEST_PASSWORD).status_code == 302


def test_counts_failures_across_clients(client):
    # นับรวมทั้งระบบ ไม่แยกตาม session — เปลี่ยน client (cookie ใหม่) ก็ไม่เริ่มนับใหม่
    fail_times(client, admin_routes.MAX_FAILED_LOGINS - 1)
    other = app_module.app.test_client()
    assert LOCK_MESSAGE in post_login(other, "wrong").get_data(as_text=True)


# ---------------------------------------------------------------------------
# C. CSRF — ทุก POST ใต้ /admin รวม login และ logout
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", ["/admin/login", "/admin/logout", "/admin"])
@pytest.mark.parametrize("token", [None, "", "wrong-token", "โทเคนภาษาไทย"], ids=["missing", "empty", "wrong", "non_ascii"])
def test_post_without_valid_csrf_is_400(client, path, token):
    login_page_token(client)  # มี token ใน session แล้ว แต่ส่งค่าอื่นมา
    data = {"password": TEST_PASSWORD}
    if token is not None:
        data["csrf_token"] = token
    resp = client.post(path, data=data)
    assert resp.status_code == 400
    # หน้าเดียวกันทุกกรณี ไม่บอกว่า token ผิดแบบไหน
    assert resp.get_data(as_text=True) == expired_page(client)
    assert admin_routes._throttle.failures == 0  # ไม่นับเป็นรหัสผิด
    assert not is_logged_in(client)


def expired_page(client):
    html = client.post("/admin/login", data={}).get_data(as_text=True)
    assert EXPIRED_MESSAGE in html
    assert 'href="/admin/login"' in html
    return html


def test_post_without_session_token_is_400(client):
    # ยังไม่เคยเปิดหน้า login (ไม่มี token ใน session) ส่ง token อะไรมาก็ไม่ผ่าน
    resp = client.post("/admin/login", data={"csrf_token": "anything", "password": TEST_PASSWORD})
    assert resp.status_code == 400
    assert EXPIRED_MESSAGE in resp.get_data(as_text=True)


def test_old_tab_after_restart_gets_expired_page(client, monkeypatch):
    # วันเดโม: เปิดหน้า login → แก้ค่าแล้วรีสตาร์ต (secret_key ใหม่) → กดปุ่มในแท็บเดิม
    token = login_page_token(client)
    monkeypatch.setattr(app_module.app, "secret_key", os.urandom(32))
    resp = client.post("/admin/login", data={"csrf_token": token, "password": TEST_PASSWORD})
    assert resp.status_code == 400
    assert EXPIRED_MESSAGE in resp.get_data(as_text=True)
    assert not is_logged_in(client)


def test_logged_in_tab_after_restart_gets_expired_page(client, monkeypatch):
    post_login(client, TEST_PASSWORD)
    token = csrf_from(client.get("/admin").get_data(as_text=True))
    monkeypatch.setattr(app_module.app, "secret_key", os.urandom(32))
    resp = client.post("/admin/logout", data={"csrf_token": token})
    assert resp.status_code == 400
    assert EXPIRED_MESSAGE in resp.get_data(as_text=True)


@pytest.mark.parametrize("path", ["/admin/login", "/admin/logout", "/admin"])
def test_not_local_post_without_csrf_is_plain_404(client, path):
    # หน้าหมดอายุใช้เฉพาะ request ที่ผ่านตัวกันในเครื่องแล้ว — ผ่าน ngrok ยังได้ 404 เหมือนหน้าที่ไม่มีอยู่
    headers = {"X-Forwarded-For": "203.0.113.5"}
    missing = client.get("/no-such-page", headers=headers).get_data()
    resp = client.post(path, data={"csrf_token": "wrong"}, headers=headers)
    assert resp.status_code == 404
    assert resp.get_data() == missing
    assert EXPIRED_MESSAGE not in resp.get_data(as_text=True)


def test_csrf_compared_with_compare_digest(client, monkeypatch):
    # ต้องเช็กว่า compare_digest ถูกเรียกด้วย token ของเราจริง — การตรวจลายเซ็น cookie ของ Flask
    # (itsdangerous) ก็เรียก compare_digest ทุก request ถ้านับแค่ "ถูกเรียก" เทสจะผ่านแม้เทียบ token ด้วย ==
    calls = []
    real = hmac.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(admin_routes.hmac, "compare_digest", spy)
    token = login_page_token(client)
    resp = client.post("/admin/login", data={"csrf_token": token, "password": TEST_PASSWORD})
    assert resp.status_code == 302
    assert (token.encode("utf-8"), token.encode("utf-8")) in calls  # ไม่ได้เทียบด้วย ==


# ---------------------------------------------------------------------------
# D. cookie ของ session
# ---------------------------------------------------------------------------
def test_session_cookie_flags_and_lifetime(client):
    resp = post_login(client, TEST_PASSWORD)
    cookie = next(v for k, v in resp.headers.items() if k == "Set-Cookie" and v.startswith("session="))
    assert "HttpOnly" in cookie
    assert "SameSite=Strict" in cookie
    assert app_module.app.permanent_session_lifetime == timedelta(minutes=30)
    # session.permanent → cookie มีวันหมดอายุ (ไม่ใช่ cookie ที่หายตอนปิดเบราว์เซอร์อย่างเดียว)
    expires = re.search(r"Expires=([^;]+)", cookie)
    assert expires
    remaining = parsedate_to_datetime(expires.group(1)) - datetime.now(timezone.utc)
    assert timedelta(minutes=29) <= remaining <= timedelta(minutes=30, seconds=5)


def test_session_cookie_is_secure(client):
    # ตั้ง Secure ได้เพราะ Chrome เก็บ cookie ที่มี Secure บน http://127.0.0.1 และ http://localhost (ทดลอง 2026-10-13)
    resp = post_login(client, TEST_PASSWORD)
    cookie = next(v for k, v in resp.headers.items() if k == "Set-Cookie" and v.startswith("session="))
    assert "Secure" in [part.strip() for part in cookie.split(";")]
    assert app_module.app.config["SESSION_COOKIE_SECURE"] is True


def test_secret_key_is_random_bytes():
    assert isinstance(app_module.app.secret_key, bytes) and len(app_module.app.secret_key) == 32


# ---------------------------------------------------------------------------
# E. log ไม่มีรหัส hash หรือ token
# ---------------------------------------------------------------------------
def test_logs_have_no_password_hash_or_token(client, capsys, caplog):
    # log ของโปรเจกต์เป็น print (capsys) ส่วน caplog จับ logging ของไลบรารี (เช่น werkzeug) — ตรวจทั้งคู่
    caplog.set_level(logging.DEBUG)
    tokens = []
    tokens.append(login_page_token(client))
    client.post("/admin/login", data={"csrf_token": tokens[-1], "password": "wrong-once"})
    tokens.append(login_page_token(client))
    client.post("/admin/login", data={"csrf_token": tokens[-1], "password": TEST_PASSWORD})
    tokens.append(csrf_from(client.get("/admin").get_data(as_text=True)))
    client.post("/admin/logout", data={"csrf_token": tokens[-1]})
    fail_times(client, admin_routes.MAX_FAILED_LOGINS)
    post_login(client, TEST_PASSWORD)  # ระหว่างล็อก

    output = capsys.readouterr().out + caplog.text
    for secret in [TEST_PASSWORD, TEST_HASH, "wrong-once", *tokens]:
        assert secret not in output
    assert "[ADMIN] login สำเร็จ" in output
    assert "[ADMIN] login ผิด (ครั้งที่ 1)" in output
    assert "[ADMIN] login ถูกล็อก" in output


# ---------------------------------------------------------------------------
# F. scripts/make_admin_hash.py
# ---------------------------------------------------------------------------
@pytest.fixture
def make_hash_module():
    spec = importlib.util.spec_from_file_location("make_admin_hash", ROOT / "scripts" / "make_admin_hash.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_make_admin_hash_asks_again_then_prints_only_hash(make_hash_module, capsys, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    # สั้นเกิน → ว่าง → ยาวพอแต่พิมพ์สองครั้งไม่ตรงกัน → ถูกต้อง
    answers = iter(["short-pw", "", "long-enough-pass-1", "long-enough-pass-2", TEST_PASSWORD, TEST_PASSWORD])
    make_hash_module.main(prompt=lambda _: next(answers))
    out = capsys.readouterr().out
    lines = out.strip().splitlines()
    assert out.count("อย่างน้อย 12 ตัวอักษร") == 2 and "ไม่ตรงกัน" in out
    assert check_password_hash(lines[-1], TEST_PASSWORD)
    for typed in [TEST_PASSWORD, "short-pw", "long-enough-pass-1", "long-enough-pass-2"]:
        assert typed not in out
    assert list(tmp_path.iterdir()) == []  # ไม่เขียนไฟล์


@pytest.mark.parametrize("password, ok", [
    ("", False), ("a" * 11, False), ("a" * 12, True), ("รหัสผ่านยาวสิบสองตัว", True), ("ก" * 11, False),
])
def test_make_admin_hash_password_length(make_hash_module, password, ok):
    assert make_hash_module.MIN_PASSWORD_LENGTH == 12
    problem = make_hash_module.password_problem(password)
    assert (problem is None) is ok
    if problem:
        assert "12" in problem and (not password or password not in problem)
