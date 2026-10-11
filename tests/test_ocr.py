"""utils/ocr.py

เทสส่วนใหญ่ไม่ต้องมี Tesseract หรือ OpenCV
เทสที่อ่านรูปจริง (requires_tesseract) skip เองถ้าเครื่องไม่มี — ลองซ่อนได้ด้วย
$env:TESSERACT_CMD = "C:\\nope\\tesseract.exe" แล้วรัน pytest
"""
import io
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont, ImageOps

from utils import ocr
from utils.receipt_parser import extract_donation_amount

FONT = Path(__file__).resolve().parents[1] / "static" / "fonts" / "THSarabunNew.ttf"

requires_tesseract = pytest.mark.skipif(
    not ocr.ocr_available(), reason="เครื่องนี้ไม่มี Tesseract ที่มีภาษา tha+eng หรือไม่มี OpenCV"
)


def image_bytes(fmt="PNG", size=(40, 20), lines=()):
    img = Image.new("L", size, 255)
    if lines:
        draw = ImageDraw.Draw(img)
        font = ImageFont.truetype(str(FONT), 64)
        for i, text in enumerate(lines):
            draw.text((20, 20 + i * 90), text, font=font, fill=0)
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


# EXIF Orientation 6 = ต้องหมุนตามเข็มนาฬิกา 90 องศาจึงจะตั้งตรง
EXIF_ORIENTATION = 0x0112
ROTATE_CW_TO_DISPLAY = 6


def phone_style_jpeg(size, lines):
    """JPEG แบบรูปจากมือถือ: พิกเซลถูกเก็บหมุนทวนเข็ม 90 องศา และ EXIF บอกให้หมุนกลับ"""
    upright = Image.open(io.BytesIO(image_bytes(size=size, lines=lines)))
    stored = upright.rotate(90, expand=True)   # Pillow หมุนทวนเข็มนาฬิกา
    exif = Image.Exif()
    exif[EXIF_ORIENTATION] = ROTATE_CW_TO_DISPLAY
    buf = io.BytesIO()
    stored.save(buf, format="JPEG", exif=exif.tobytes())
    return buf.getvalue()


# ---------------------------------------------------------------------------
# หา Tesseract
# ---------------------------------------------------------------------------
def test_tesseract_cmd_missing_file_means_unavailable(monkeypatch, tmp_path):
    # ตั้ง TESSERACT_CMD ผิด → ไม่หาที่อื่นต่อ ต้องรู้ตัวว่าตั้งผิด
    monkeypatch.setenv("TESSERACT_CMD", str(tmp_path / "nope" / "tesseract.exe"))
    monkeypatch.setattr(ocr.shutil, "which", lambda name: "C:/somewhere/tesseract.exe")
    assert ocr.find_tesseract() is None
    assert ocr.ocr_available() is False


def test_tesseract_cmd_used_when_file_exists(monkeypatch, tmp_path):
    fake = tmp_path / "tesseract.exe"
    fake.write_bytes(b"")
    monkeypatch.setenv("TESSERACT_CMD", str(fake))
    assert ocr.find_tesseract() == str(fake)


def test_tesseract_found_on_path(monkeypatch):
    monkeypatch.delenv("TESSERACT_CMD", raising=False)
    monkeypatch.setattr(ocr.shutil, "which", lambda name: "C:/on-path/tesseract.exe")
    assert ocr.find_tesseract() == "C:/on-path/tesseract.exe"


def test_tesseract_default_windows_path(monkeypatch, tmp_path):
    fake = tmp_path / "tesseract.exe"
    fake.write_bytes(b"")
    monkeypatch.delenv("TESSERACT_CMD", raising=False)
    monkeypatch.setattr(ocr.shutil, "which", lambda name: None)
    monkeypatch.setattr(ocr, "DEFAULT_WINDOWS_PATH", str(fake))
    assert ocr.find_tesseract() == str(fake)


def test_no_tesseract_anywhere(monkeypatch, tmp_path):
    monkeypatch.delenv("TESSERACT_CMD", raising=False)
    monkeypatch.setattr(ocr.shutil, "which", lambda name: None)
    monkeypatch.setattr(ocr, "DEFAULT_WINDOWS_PATH", str(tmp_path / "nope.exe"))
    assert ocr.find_tesseract() is None
    assert ocr.ocr_available() is False


def test_read_text_without_tesseract_raises_unavailable(monkeypatch, tmp_path):
    monkeypatch.setenv("TESSERACT_CMD", str(tmp_path / "nope.exe"))
    with pytest.raises(ocr.OcrUnavailableError):
        ocr.read_text(image_bytes())


# ---------------------------------------------------------------------------
# ตรวจรูปก่อนอ่าน (Pillow อ่านแค่หัวไฟล์ ไม่ต้องมี Tesseract/OpenCV)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("data", [
    b"",
    b"not an image",
    b"%PDF-1.7\n",
], ids=["empty", "text", "pdf"])
def test_not_an_image(data):
    with pytest.raises(ocr.InvalidImageError):
        ocr.read_text(data)


def test_gif_not_allowed():
    with pytest.raises(ocr.InvalidImageError):
        ocr.read_text(image_bytes(fmt="GIF"))


def test_too_many_pixels(monkeypatch):
    # อ่าน MAX_PIXELS ตอนถูกเรียก ลดเพดานแทนการสร้างรูปใหญ่จริง
    monkeypatch.setattr(ocr, "MAX_PIXELS", 40 * 20 - 1)
    with pytest.raises(ocr.ImageTooLargeError):
        ocr.read_text(image_bytes(size=(40, 20)))


def test_image_checked_before_tesseract(monkeypatch, tmp_path):
    # ไฟล์ที่ไม่ใช่รูปต้องได้ InvalidImageError แม้เครื่องไม่มี Tesseract
    # (route จะตอบ 415 ได้ถูกต้องไม่ว่าเครื่องมี Tesseract หรือไม่)
    monkeypatch.setenv("TESSERACT_CMD", str(tmp_path / "nope.exe"))
    with pytest.raises(ocr.InvalidImageError):
        ocr.read_text(b"not an image")


# ---------------------------------------------------------------------------
# อ่านรูปจริง — skip เองถ้าไม่มี Tesseract
# ---------------------------------------------------------------------------
@requires_tesseract
@pytest.mark.parametrize("fmt", ["PNG", "JPEG"])
def test_read_text_real(fmt):
    data = image_bytes(fmt=fmt, size=(900, 200),
                       lines=["จำนวนเงิน 5,000 บาท", "Amount 12,345.50 THB"])
    text = ocr.read_text(data)
    assert "5,000" in text
    assert "12,345.50" in text


@requires_tesseract
def test_read_text_then_extract_amount():
    data = image_bytes(size=(900, 110), lines=["จำนวนเงิน 5,000 บาท"])
    assert extract_donation_amount(ocr.read_text(data)) == {"status": "ok", "amount": 5000}


def test_phone_style_jpeg_fixture_is_rotated():
    # ยืนยันว่ารูปทดสอบสร้างถูก: พิกเซลที่เก็บอยู่ตะแคง แต่หมุนตาม EXIF แล้วได้ขนาดตั้งตรงเดิม
    with Image.open(io.BytesIO(phone_style_jpeg((900, 110), ["x"]))) as img:
        assert img.size == (110, 900)
        assert img.getexif()[EXIF_ORIENTATION] == ROTATE_CW_TO_DISPLAY
        assert ImageOps.exif_transpose(img).size == (900, 110)


@requires_tesseract
def test_read_text_follows_exif_orientation():
    # รูปจากมือถือเก็บทิศทางไว้ใน EXIF — cv2.imdecode ต้องหมุนให้ ไม่งั้นอ่านตัวหนังสือตะแคง
    data = phone_style_jpeg((900, 110), ["จำนวนเงิน 5,000 บาท"])
    assert "5,000" in ocr.read_text(data)


@requires_tesseract
def test_large_image_is_downscaled(monkeypatch):
    # ลด MAX_SIDE ให้ทางย่อรูปทำงาน รูปยังต้องอ่านได้
    monkeypatch.setattr(ocr, "MAX_SIDE", 600)
    data = image_bytes(size=(1200, 200), lines=["Amount 5,000 THB"])
    assert "5,000" in ocr.read_text(data)
