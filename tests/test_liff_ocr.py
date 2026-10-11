"""route POST /liff/ocr ใน app.py ผ่าน Flask test client

mock ทั้ง verify token, ocr_available และ read_text ทุกเทส — ไม่ต่อ LINE ไม่เรียก Tesseract
(โมดูลปลอมของ firebase_db อยู่ใน conftest.py)
"""
import io
import types

import pytest

import app as app_module
from utils import liff_auth, ocr

LIFF_ID = "1234567890-TestLiff"
CHANNEL_ID = "1234567890"
TOKEN = "eyJTEST.secret-token-value"
RECEIPT_TEXT = "ใบอนุโมทนาบัตร\nมูลนิธิทดสอบ นายลับเฉพาะ ระบบ\nจำนวนเงิน 5,000 บาท"
# ใบเสร็จที่ไม่ใช่การบริจาค: parser หายอดเจอ (35,000) แต่ต้องไม่ถูกส่งกลับไปเติม "บริจาค 35000"
INSURANCE_TEXT = (
    "บริษัท ประกันชีวิตตัวอย่าง จำกัด\n"
    "ใบเสร็จรับเงิน\n"
    "เบี้ยประกันชีวิต\n"
    "จำนวนเงินที่ได้รับ 35,000.00 บาท"
)


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    f = types.SimpleNamespace(
        verify_calls=[],
        verify_error=None,
        available=True,
        read_calls=[],
        text=RECEIPT_TEXT,
        read_error=None,
    )

    def fake_verify(token, channel_id):
        f.verify_calls.append((token, channel_id))
        if f.verify_error is not None:
            raise f.verify_error

    def fake_read_text(data):
        f.read_calls.append(data)
        if f.read_error is not None:
            raise f.read_error
        return f.text

    monkeypatch.setenv("LIFF_ID", LIFF_ID)
    monkeypatch.setattr(liff_auth, "verify_liff_token", fake_verify)
    monkeypatch.setattr(ocr, "ocr_available", lambda: f.available)
    monkeypatch.setattr(ocr, "read_text", fake_read_text)
    return f


@pytest.fixture
def upload():
    client = app_module.app.test_client()

    def _upload(image=b"fake-image-bytes", token=TOKEN, headers=None):
        data = {} if image is None else {"image": (io.BytesIO(image), "receipt.jpg")}
        if headers is None:
            headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
        resp = client.post("/liff/ocr", data=data, headers=headers, content_type="multipart/form-data")
        return resp.status_code, resp.get_json()

    return _upload


# ---------------------------------------------------------------------------
# อ่านได้
# ---------------------------------------------------------------------------
def test_ok(upload, fakes):
    assert upload() == (200, {"status": "ok", "amount": "5000"})
    assert fakes.verify_calls == [(TOKEN, CHANNEL_ID)]
    assert fakes.read_calls == [b"fake-image-bytes"]


def test_decimal_amount(upload, fakes):
    fakes.text = "ใบอนุโมทนาบัตร\nจำนวนเงิน 1,250.50 บาท"
    assert upload() == (200, {"status": "ok", "amount": "1250.50"})


@pytest.mark.parametrize("text, status", [
    ("ใบอนุโมทนาบัตร\nจำนวนเงิน 1,500 บาท\nจำนวนเงิน 2,000 บาท", "ambiguous"),
    ("ใบอนุโมทนาบัตร", "not_found"),
])
def test_no_amount_when_parser_unsure(upload, fakes, text, status):
    fakes.text = text
    assert upload() == (200, {"status": status, "amount": None})


# ---------------------------------------------------------------------------
# ไม่ใช่หลักฐานการบริจาค
# ---------------------------------------------------------------------------
def test_not_donation(upload, fakes):
    # รูปใบเสร็จเบี้ยประกันชีวิตเคยได้ ok 35000 แล้วหน้า LIFF เติม "บริจาค 35000" ให้ (กฎเหล็กข้อ 2)
    fakes.text = INSURANCE_TEXT
    assert upload() == (200, {"status": "not_donation", "amount": None})


@pytest.mark.parametrize("text", [
    "จำนวนเงิน 1,500 บาท\nจำนวนเงิน 2,000 บาท",  # ไม่มีคำบริจาค + ยอดกำกวม
    "ใบเสร็จรับเงิน",                              # ไม่มีคำบริจาค + หายอดไม่เจอ
], ids=["ambiguous", "not_found"])
def test_not_donation_wins_over_amount_status(upload, fakes, text):
    # ข้อความของ ambiguous / not_found ชวนให้พิมพ์ยอดต่อจาก "บริจาค" ซึ่งผิดถ้ารูปไม่ใช่การบริจาค
    fakes.text = text
    assert upload() == (200, {"status": "not_donation", "amount": None})


# ---------------------------------------------------------------------------
# ตั้งค่า / token
# ---------------------------------------------------------------------------
def test_not_configured(upload, fakes, monkeypatch):
    monkeypatch.delenv("LIFF_ID")
    assert upload() == (503, {"status": "not_configured"})
    assert fakes.verify_calls == []


@pytest.mark.parametrize("headers, expected_token", [
    ({"Authorization": "Bearer abc"}, "abc"),
    ({"Authorization": "abc"}, ""),            # ไม่มี Bearer ไม่นับเป็น token
    ({}, ""),
])
def test_token_from_header(upload, fakes, headers, expected_token):
    upload(headers=headers)
    assert fakes.verify_calls == [(expected_token, CHANNEL_ID)]


def test_invalid_token(upload, fakes):
    fakes.verify_error = liff_auth.InvalidTokenError("x")
    assert upload() == (401, {"status": "invalid_token"})
    assert fakes.read_calls == []


def test_line_unavailable(upload, fakes):
    fakes.verify_error = liff_auth.LineUnavailableError("x")
    assert upload() == (503, {"status": "line_unavailable"})
    assert fakes.read_calls == []


# ---------------------------------------------------------------------------
# รูป
# ---------------------------------------------------------------------------
def test_ocr_unavailable(upload, fakes):
    fakes.available = False
    assert upload() == (503, {"status": "ocr_unavailable"})
    assert fakes.read_calls == []


def test_no_image(upload, fakes):
    assert upload(image=None) == (400, {"status": "no_image"})
    assert fakes.read_calls == []


def test_too_large_rejected_before_token(upload, fakes, monkeypatch):
    # รูปใหญ่เกินตัดจาก Content-Length ก่อน ไม่ต้องเรียก LINE
    monkeypatch.setattr(app_module, "LIFF_OCR_MAX_BYTES", 100)
    assert upload(image=b"x" * 200) == (413, {"status": "too_large"})
    assert fakes.verify_calls == []
    assert fakes.read_calls == []


@pytest.mark.parametrize("error, expected", [
    (ocr.InvalidImageError("x"), (415, {"status": "invalid_image"})),
    (ocr.ImageTooLargeError("x"), (413, {"status": "too_large"})),
    (ocr.OcrUnavailableError("x"), (503, {"status": "ocr_unavailable"})),
    (ocr.OcrFailedError("x"), (500, {"status": "ocr_failed"})),
], ids=["invalid_image", "too_many_pixels", "ocr_unavailable", "ocr_failed"])
def test_ocr_errors(upload, fakes, error, expected):
    fakes.read_error = error
    assert upload() == expected


# ---------------------------------------------------------------------------
# อ่านทีละรูป
# ---------------------------------------------------------------------------
def test_busy_when_another_image_is_being_read(upload, fakes):
    assert app_module._ocr_slot.acquire(blocking=False)
    try:
        assert upload() == (503, {"status": "busy"})
    finally:
        app_module._ocr_slot.release()
    assert fakes.read_calls == []


def test_slot_released_after_error(upload, fakes):
    fakes.read_error = ocr.OcrFailedError("x")
    upload()
    fakes.read_error = None
    assert upload() == (200, {"status": "ok", "amount": "5000"})


# ---------------------------------------------------------------------------
# ข้อมูลส่วนตัว / ขอบเขต
# ---------------------------------------------------------------------------
def test_log_has_no_text_amount_or_token(upload, capsys):
    upload()
    out = capsys.readouterr().out
    assert "[LIFF-OCR] ok" in out
    for secret in ["ลับเฉพาะ", "มูลนิธิทดสอบ", "5,000", "5000", TOKEN]:
        assert secret not in out


def test_log_for_not_donation_has_no_text_or_amount(upload, fakes, capsys):
    fakes.text = INSURANCE_TEXT
    upload()
    out = capsys.readouterr().out
    assert "[LIFF-OCR] not_donation" in out
    for secret in ["ประกันชีวิตตัวอย่าง", "35,000", "35000", TOKEN]:
        assert secret not in out


def test_get_not_allowed(upload):
    resp = app_module.app.test_client().get("/liff/ocr")
    assert resp.status_code == 405


def test_size_limit_does_not_apply_to_webhook():
    # เพดานขนาดตั้งเฉพาะ /liff/ocr ถ้าตั้งที่ config จะมีผลกับ /webhook ด้วย
    assert app_module.app.config["MAX_CONTENT_LENGTH"] is None
