"""อ่านข้อความจากรูปหลักฐานการบริจาคด้วย OpenCV + Tesseract

- ไม่เขียนรูปลงดิสก์: ส่ง PNG ให้ Tesseract ทาง stdin (`tesseract stdin stdout`)
  ไม่ใช้ pytesseract เพราะมันเขียนรูปลงไฟล์ชั่วคราวก่อนเสมอ
- cv2 import ในตัวฟังก์ชัน: import ไฟล์นี้ไม่โหลด OpenCV และเครื่องที่ไม่มี OpenCV/Tesseract
  ยังรันระบบเดิมได้ ได้แค่ OcrUnavailableError ตอนเรียกอ่านรูป
- ไม่ print/log ข้อความที่อ่านได้ (ข้อมูลส่วนตัว)
- ดึงยอดเงินจากข้อความอยู่ใน utils/receipt_parser.py ไม่ใช่ที่นี่
"""
import importlib.util
import io
import os
import shutil
import subprocess

from PIL import Image, UnidentifiedImageError

# path มาตรฐานของตัวติดตั้ง UB Mannheim (ไม่ได้เพิ่มเข้า PATH ให้)
DEFAULT_WINDOWS_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# eng ช่วยอ่าน "THB", "Amount" บนใบเสร็จที่มีภาษาอังกฤษปน
LANGUAGES = ("tha", "eng")

ALLOWED_FORMATS = ("JPEG", "PNG")

# กันรูปที่ไฟล์เล็กแต่ขนาดภาพใหญ่มาก (PNG บีบอัดได้มาก) ไม่ให้กินหน่วยความจำตอนถอดรหัส
# 50 ล้านพิกเซลพอสำหรับรูปจากกล้องมือถือ 48MP
MAX_PIXELS = 50_000_000

# ย่อด้านยาวให้ไม่เกินค่านี้ก่อนส่งให้ Tesseract เพื่อคุมเวลา
MAX_SIDE = 2500

# route นี้ถูกเรียกจากหน้า LIFF ไม่ใช่จาก Dialogflow จึงไม่อยู่ในงบ 5 วินาที (CLAUDE.md กฎเหล็กข้อ 1)
# แต่ต้องมีเพดานไว้ไม่ให้ Tesseract ค้าง
TIMEOUT_SECONDS = 20


class OcrUnavailableError(RuntimeError):
    """เครื่องนี้ไม่มี Tesseract (หรือไม่มีภาษาไทย) หรือไม่มี OpenCV"""


class InvalidImageError(ValueError):
    """ไม่ใช่รูป JPEG/PNG ที่อ่านได้"""


class ImageTooLargeError(ValueError):
    """จำนวนพิกเซลเกิน MAX_PIXELS"""


class OcrFailedError(RuntimeError):
    """Tesseract ทำงานไม่สำเร็จหรือเกินเวลา (ข้อความ error ไม่มีข้อความที่อ่านได้)"""


def find_tesseract():
    """คืน path ของ tesseract หรือ None

    อ่าน TESSERACT_CMD ตอนถูกเรียก ถ้าตั้งไว้จะใช้ค่านั้นค่าเดียว ไม่หาที่อื่นต่อ
    เพื่อให้ตั้งผิดแล้วรู้ตัว (และใช้ซ่อน Tesseract ตอนทดสอบได้)
    """
    configured = os.environ.get("TESSERACT_CMD")
    if configured:
        return configured if os.path.isfile(configured) else None
    found = shutil.which("tesseract")
    if found:
        return found
    if os.path.isfile(DEFAULT_WINDOWS_PATH):
        return DEFAULT_WINDOWS_PATH
    return None


def ocr_available():
    """มี tesseract ที่มีภาษา tha และ eng และมี OpenCV หรือไม่ — เช็กใหม่ทุกครั้งที่ถูกเรียก"""
    cmd = find_tesseract()
    if cmd is None or importlib.util.find_spec("cv2") is None:
        return False
    try:
        result = subprocess.run([cmd, "--list-langs"], capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    langs = result.stdout.decode("utf-8", errors="replace").split()
    return result.returncode == 0 and all(lang in langs for lang in LANGUAGES)


def _check_image(image_bytes):
    # Pillow อ่านแค่หัวไฟล์ ยังไม่ถอดรหัสทั้งรูป จึงเช็กชนิดและขนาดได้ก่อนใช้หน่วยความจำ
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            fmt, (width, height) = img.format, img.size
    except (UnidentifiedImageError, OSError):
        raise InvalidImageError("ไม่ใช่ไฟล์รูป") from None
    if fmt not in ALLOWED_FORMATS:
        raise InvalidImageError(f"ชนิดรูป {fmt} ไม่รองรับ")
    if width * height > MAX_PIXELS:
        raise ImageTooLargeError(f"รูป {width}x{height} ใหญ่เกิน")


def read_text(image_bytes):
    """อ่านข้อความจากรูป (bytes ของไฟล์ JPEG/PNG) คืน str

    ลำดับ: เช็กรูป → หา Tesseract → OpenCV แปลงเป็นภาพเทา ย่อขนาด → Tesseract ผ่าน stdin
    ไม่ทำ threshold เอง เพราะ Tesseract ทำ Otsu ข้างในอยู่แล้ว
    (ถ้าสคริปต์วัดความแม่นในขั้น D พบว่าควรทำ ค่อยเพิ่ม)
    """
    _check_image(image_bytes)

    cmd = find_tesseract()
    if cmd is None:
        raise OcrUnavailableError("ไม่พบ Tesseract")
    try:
        import cv2
        import numpy as np
    except ImportError:
        raise OcrUnavailableError("ไม่พบ OpenCV") from None

    # IMREAD_GRAYSCALE ยังหมุนรูปตาม EXIF ให้ (รูปจากมือถือมักเก็บทิศทางไว้ใน EXIF)
    img = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise InvalidImageError("ถอดรหัสรูปไม่ได้")

    scale = MAX_SIDE / max(img.shape)
    if scale < 1:
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

    ok, png = cv2.imencode(".png", img)
    if not ok:
        raise OcrFailedError("แปลงรูปเป็น PNG ไม่ได้")

    try:
        result = subprocess.run(
            [cmd, "stdin", "stdout", "-l", "+".join(LANGUAGES)],
            input=png.tobytes(), capture_output=True, timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        # from None: ไม่พ่วง TimeoutExpired ซึ่งเก็บ stdout บางส่วน (ข้อความที่อ่านได้) ไว้
        raise OcrFailedError(f"Tesseract เกิน {TIMEOUT_SECONDS} วินาที") from None
    except OSError:
        raise OcrUnavailableError("เรียก Tesseract ไม่ได้") from None
    if result.returncode != 0:
        raise OcrFailedError(f"Tesseract จบด้วยรหัส {result.returncode}")
    return result.stdout.decode("utf-8", errors="replace")
