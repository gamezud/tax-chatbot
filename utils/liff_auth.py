"""ตรวจ LIFF access token ของผู้ใช้กับ LINE

ไม่ใช่การเรียก Messaging API และไม่ใช้ token ของบอท — ถามแค่ว่า token ที่หน้า LIFF ส่งมา
ออกให้ LINE Login channel ของเราและยังไม่หมดอายุ (ใครไม่ได้เปิดผ่าน LIFF app ของเราจะไม่มี token แบบนี้)

แยก error สองแบบ เพราะผู้ใช้ควรได้ข้อความต่างกัน
- InvalidTokenError    token ผิด/หมดอายุ/ไม่ใช่ของ channel เรา → ให้เปิดลิงก์ใหม่จาก LINE
- LineUnavailableError เรียก LINE ไม่ได้ (เน็ตล่ม timeout 5xx) → ให้พิมพ์ยอดในแชตเอง

ห้าม log token หรือใส่ token ใน error: token อยู่ใน query string ของ URL
และ exception ของ requests ใส่ URL นั้นไว้ในข้อความ ทุกจุดจึง raise ใหม่ด้วย from None
"""
import requests

VERIFY_URL = "https://api.line.me/oauth2/v2.1/verify"

# (connect, read) รวมไม่เกิน 3 วินาที — timeout ตัวเดียวของ requests ใช้กับแต่ละช่วงแยกกัน
# ถ้าใส่ 3 จะรอได้ถึง 6 วินาที (ไม่รวมเวลาค้นหา DNS ซึ่ง requests คุมไม่ได้)
TIMEOUT_SECONDS = (1.5, 1.5)


class InvalidTokenError(Exception):
    """token ใช้ไม่ได้ → ผู้ใช้ควรเปิดลิงก์ใหม่จาก LINE"""


class LineUnavailableError(Exception):
    """เรียก LINE ไม่ได้ → ผู้ใช้ควรพิมพ์ยอดในแชตเอง"""


def verify_liff_token(access_token, channel_id):
    """ผ่าน = คืน None / ไม่ผ่าน = raise InvalidTokenError หรือ LineUnavailableError

    channel_id คือ Channel ID ของ LINE Login channel ที่ LIFF app อยู่ (ผู้เรียกเป็นคนหามา)
    ไม่ลองใหม่เมื่อล้ม ผู้ใช้ยังพิมพ์ยอดเองได้เสมอ
    """
    if not channel_id:
        raise ValueError("ไม่ได้ระบุ channel_id")
    if not access_token:
        raise InvalidTokenError("ไม่มี access token")

    try:
        resp = requests.get(VERIFY_URL, params={"access_token": access_token}, timeout=TIMEOUT_SECONDS)
    except requests.RequestException:
        raise LineUnavailableError("เรียก LINE verify ไม่ได้") from None

    # 429 = LINE จำกัดจำนวนครั้ง ไม่ใช่ความผิดของ token
    if resp.status_code >= 500 or resp.status_code == 429:
        raise LineUnavailableError(f"LINE verify ตอบ {resp.status_code}")
    # LINE ตอบ 400 เมื่อ token ผิดหรือหมดอายุ
    if resp.status_code != 200:
        raise InvalidTokenError(f"LINE verify ตอบ {resp.status_code}")

    try:
        data = resp.json()
    except ValueError:
        raise LineUnavailableError("LINE verify ตอบไม่ใช่ JSON") from None
    if not isinstance(data, dict):
        raise LineUnavailableError("LINE verify ตอบรูปแบบไม่ถูก")

    if str(data.get("client_id")) != str(channel_id):
        raise InvalidTokenError("token ไม่ได้ออกให้ channel นี้")
    expires_in = data.get("expires_in")
    if not isinstance(expires_in, (int, float)) or expires_in <= 0:
        raise InvalidTokenError("token หมดอายุ")
