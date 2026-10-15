"""สวิตช์โหมดดีบัก TAX_DEBUG — คุม "ข้อมูลสำหรับทดสอบ" ในหน้า LIFF และ log ที่มีข้อมูลผู้ใช้

import แค่ os: app.py import utils/knowledge_search.py อยู่แล้ว ถ้า helper อยู่ใน app.py
แล้ว knowledge_search import กลับไปจะเกิด import วน — ทั้งสองไฟล์จึง import จากที่นี่
อ่าน env ตอนถูกเรียกทุกครั้ง (CLAUDE.md กับดักข้อ 9) ค่าตั้งต้นคือปิด

ไม่ใช้ logging แบ่งระดับ: ถ้าเปิด logging.DEBUG ไว้ urllib3 จะ log URL ตอนตรวจ LIFF token
ซึ่งมี token อยู่ใน query string (CLAUDE.md ตาราง "สภาพแวดล้อม")
"""
import os


def debug_enabled():
    # เปิดเฉพาะเมื่อตั้งเป็น "1" — ค่าอื่น ("true", "0", ช่องว่าง) หรือไม่ได้ตั้งถือว่าปิด
    # ตัดสินแบบเดียวทุกที่ ก่อนเดโมดูบรรทัด [TAX-DEBUG] ตอนเปิดเซิร์ฟเวอร์ก็รู้ทันที
    return os.environ.get("TAX_DEBUG", "").strip() == "1"


def debug_print(*args, **kwargs):
    # ใช้แทน print กับ log ที่มี LINE user ID ข้อความที่ผู้ใช้พิมพ์ ยอดเงิน หรือชื่อไฟล์ PDF
    # รับ argument เหมือน print ข้อความจึงเหมือนเดิมทุกตัวอักษรตอนเปิด
    if debug_enabled():
        print(*args, **kwargs)


def debug_status_line():
    # พิมพ์ตอนเริ่มเซิร์ฟเวอร์ แบบเดียวกับ [LIFF] / [ADMIN]
    if debug_enabled():
        return "🐞 [TAX-DEBUG] เปิดอยู่ — log มีข้อมูลผู้ใช้ และหน้า LIFF แสดงข้อมูลสำหรับทดสอบ (ปิดก่อนเดโม)"
    return "🔒 [TAX-DEBUG] ปิดอยู่ — log ไม่มีข้อมูลผู้ใช้ และหน้า LIFF ไม่แสดงข้อมูลสำหรับทดสอบ"
