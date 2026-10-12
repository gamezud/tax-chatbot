"""ปรับข้อความภาษาไทยให้อยู่รูปเดียวกันก่อนเทียบคำหรือหาตัวเลข

ใช้ร่วมกันหลายที่ (ข้อความจาก OCR ใน receipt_parser) — ไม่ import อะไรเลย ไม่มี I/O
"""

# เลขไทย ๐-๙ → 0-9
_THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")

# Tesseract เขียน ำ (U+0E33) เป็นสองตัว ํ + า (U+0E4D U+0E32) ซึ่งตาเปล่าแยกไม่ออก
# ถ้าไม่รวมกลับ คำสำคัญอย่าง "จำนวนเงิน" จะไม่ตรงกับข้อความที่ OCR อ่านได้
_SPLIT_SARA_AM = "\N{THAI CHARACTER NIKHAHIT}\N{THAI CHARACTER SARA AA}"
_SARA_AM = "\N{THAI CHARACTER SARA AM}"


def normalize_thai_text(text):
    """แปลงเลขไทยเป็นอารบิก และรวม ํ + า ที่ Tesseract แยกกลับเป็น ำ — None ได้ข้อความว่าง"""
    return (text or "").translate(_THAI_DIGITS).replace(_SPLIT_SARA_AM, _SARA_AM)
