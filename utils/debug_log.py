"""สวิตช์โหมดดีบัก TAX_DEBUG — คุม "ข้อมูลสำหรับทดสอบ" ในหน้า LIFF

import แค่ os: app.py import utils/knowledge_search.py อยู่แล้ว ถ้า helper อยู่ใน app.py
แล้ว knowledge_search import กลับไปจะเกิด import วน — ทั้งสองไฟล์จึง import จากที่นี่
อ่าน env ตอนถูกเรียกทุกครั้ง (CLAUDE.md กับดักข้อ 9) ค่าตั้งต้นคือปิด
"""
import os


def debug_enabled():
    # เปิดเฉพาะเมื่อตั้งเป็น "1" — ค่าอื่น ("true", "0", ช่องว่าง) หรือไม่ได้ตั้งถือว่าปิด
    # ตัดสินแบบเดียวทุกที่ ก่อนเดโมดูบรรทัด [TAX-DEBUG] ตอนเปิดเซิร์ฟเวอร์ก็รู้ทันที
    return os.environ.get("TAX_DEBUG", "").strip() == "1"


def debug_status_line():
    # พิมพ์ตอนเริ่มเซิร์ฟเวอร์ แบบเดียวกับ [LIFF] / [ADMIN]
    if debug_enabled():
        return "🐞 [TAX-DEBUG] เปิดอยู่ — หน้า LIFF แสดงข้อมูลสำหรับทดสอบ (ปิดก่อนเดโม)"
    return "🔒 [TAX-DEBUG] ปิดอยู่ — หน้า LIFF ไม่แสดงข้อมูลสำหรับทดสอบ"
