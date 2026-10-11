"""หน้า LIFF GET /liff/donation — ตรวจแค่ฝั่ง Flask (เรนเดอร์ได้ ค่า LIFF_ID ถูก escape)
พฤติกรรมของ JavaScript ทดสอบในแอป LINE บนมือถือจริง
"""
import pytest

import app as app_module


@pytest.fixture
def get_page():
    client = app_module.app.test_client()

    def _get():
        resp = client.get("/liff/donation")
        assert resp.status_code == 200
        return resp.get_data(as_text=True)

    return _get


def test_renders_without_liff_id(get_page, monkeypatch):
    monkeypatch.delenv("LIFF_ID", raising=False)
    html = get_page()
    assert "const LIFF_ID = null;" in html
    # หน้าไม่ขาว: มีข้อความบอกว่าไม่มี LIFF ID ให้ JavaScript แสดง
    assert "ไม่มี LIFF ID" in html


def test_renders_with_liff_id(get_page, monkeypatch):
    monkeypatch.setenv("LIFF_ID", "1234567890-TestLiff")
    assert 'const LIFF_ID = "1234567890-TestLiff";' in get_page()


def test_liff_id_is_escaped(get_page, monkeypatch):
    # ค่าใน env ทำให้สคริปต์ในหน้าพังหรือแทรกโค้ดไม่ได้
    monkeypatch.setenv("LIFF_ID", '1"</script><script>alert(1)</script>')
    html = get_page()
    assert "<script>alert(1)" not in html
    assert "\\u003c/script\\u003e" in html


def test_photo_input_allows_camera_and_gallery(get_page):
    html = get_page()
    assert 'accept="image/*"' in html
    # capture บังคับให้เปิดกล้อง เลือกจากคลังรูปไม่ได้
    assert "capture=" not in html


@pytest.mark.parametrize("unsafe", ["innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"])
def test_no_html_injection_apis(get_page, unsafe):
    # ข้อความจากเซิร์ฟเวอร์หรือผู้ใช้ใส่ลงหน้าด้วย textContent เท่านั้น
    assert unsafe not in get_page()
