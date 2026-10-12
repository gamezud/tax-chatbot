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


def test_not_donation_message(get_page):
    html = get_page()
    assert ('not_donation: "รูปนี้ไม่พบคำว่าบริจาค ระบบอ่านได้เฉพาะหลักฐานการบริจาค '
            'กรุณาพิมพ์ยอดเองในแชตครับ"') in html


def test_not_donation_clears_message_and_disables_send(get_page):
    # ไม่มี JS test — เช็กว่าโค้ดอยู่ถูกที่แบบ substring
    html = get_page()
    branch = html.split('if (status === "not_donation") {', 1)[1].split("}", 1)[0]
    assert 'message.value = "";' in branch
    assert "sendBtn.disabled = true;" in branch
    # เลือกรูปใหม่แล้วปุ่มกลับมากดได้ — ต้องเปิดก่อนอ่านรูป ไม่งั้นผลของรูปใหม่ไปเปิดปุ่มที่ not_donation เพิ่งปิด
    on_photo = html.split('photo.addEventListener("change"', 1)[1].split("await readAmount", 1)[0]
    assert "sendBtn.disabled = false;" in on_photo
    # ปิดปุ่มแค่สองที่: not_donation และระหว่างกำลังส่ง — ok / not_found / ambiguous ยังกดได้ตามเดิม
    assert html.count("sendBtn.disabled = true;") == 2


def test_thai_digits_message(get_page):
    html = get_page()
    assert ('thai_digits: "ยอดในรูปเป็นเลขไทย ซึ่งระบบอ่านได้ไม่แม่นยำ '
            'กรุณาพิมพ์ยอดเองต่อจากคำว่า \\"บริจาค\\" แล้วกดส่งครับ"') in html


def test_thai_digits_resets_like_not_found(get_page):
    # รูปมีคำบริจาคแล้ว จึงรีเซ็ตช่องเป็น "บริจาค " แบบ not_found ไม่ปิดปุ่มแบบ not_donation
    html = get_page()
    reset = html.split('if (status === "not_found"', 1)[1].split("{", 1)[0]
    assert 'status === "thai_digits"' in reset
    not_donation = html.split('if (status === "not_donation") {', 1)[1].split("}", 1)[0]
    assert "thai_digits" not in not_donation


def test_liff_id_status_line_when_set(monkeypatch):
    # บอกแค่ว่าตั้งแล้ว ไม่พิมพ์ค่า LIFF ID หรือ Channel ID (ตัวเลขหน้า "-")
    monkeypatch.setenv("LIFF_ID", "1234567890-TestLiff")
    line = app_module.liff_id_status_line()
    assert "ตั้ง LIFF_ID แล้ว" in line
    for secret in ["1234567890", "TestLiff"]:
        assert secret not in line


@pytest.mark.parametrize("value", [None, "", "   "], ids=["unset", "empty", "spaces"])
def test_liff_id_status_line_when_not_set(monkeypatch, value):
    # ช่องว่างล้วนถือว่าไม่ได้ตั้ง เหมือน liff_donation_link
    if value is None:
        monkeypatch.delenv("LIFF_ID", raising=False)
    else:
        monkeypatch.setenv("LIFF_ID", value)
    assert "ยังไม่ได้ตั้ง LIFF_ID" in app_module.liff_id_status_line()


def test_liff_id_status_printed_at_startup():
    # บล็อก __main__ ไม่ถูกรันในเทส — เช็กว่าเรียกพิมพ์ไว้ก่อน app.run แบบ substring
    source = open(app_module.__file__, encoding="utf-8").read()
    main_block = source.split("if __name__ == '__main__':", 1)[1]
    assert "print(liff_id_status_line())" in main_block.split("app.run(", 1)[0]


@pytest.mark.parametrize("unsafe",["innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"])
def test_no_html_injection_apis(get_page, unsafe):
    # ข้อความจากเซิร์ฟเวอร์หรือผู้ใช้ใส่ลงหน้าด้วย textContent เท่านั้น
    assert unsafe not in get_page()
