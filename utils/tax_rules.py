"""
ตัวเลขภาษีที่เปลี่ยนตามปีภาษี (เพดาน อัตรา ขั้นบันได เกณฑ์วิธีที่ 2)
สเปกและแหล่งอ้างอิงอยู่ใน docs/tax-rules-2569.md คอลัมน์ "โค้ด (2567)"

ไฟล์นี้เป็นค่าคงที่ล้วน ไม่ import อะไรและไม่มี I/O

วิธีอ่านที่ถูก: `from utils import tax_rules` แล้วอ่าน `tax_rules.TAX_RULES` ตอนฟังก์ชันถูกเรียก
- ห้าม `from utils.tax_rules import TAX_RULES` เพราะจะได้ก้อนที่ผูกไว้ตอน import
  ถ้าระบบ Admin แทนที่ `tax_rules.TAX_RULES` ทีหลัง ค่าที่ผูกไว้จะไม่เปลี่ยนตาม (แบบเดียวกับกับดักข้อ 2)
- ห้ามใช้เป็นค่า default ของ parameter (กับดักข้อ 9)
"""

TAX_RULES = {
    "tax_year": 2567,

    # ค่าใช้จ่าย (docs ข้อ 2.1)
    "expense": {
        "salary_rate": 0.50,
        "salary_cap": 100000.0,
        "rental_rate": {
            "building": 0.30,          # บ้าน โรงเรือน สิ่งปลูกสร้าง แพ ยานพาหนะ (พ.ร.ฎ. 11 ม.5 จัดกลุ่มเดียวกัน)
            "agricultural_land": 0.20,
            "land": 0.15,
            "unmatched": 0.30,         # ไม่ใช่ตัวเลขตามกฎหมาย ค่าชั่วคราวจนกว่าจะตัดสินใจ #8 — กฎหมายให้ทรัพย์สินอื่น 10% (พ.ร.ฎ. 11 ม.5)
        },
        "online_sale_rate": 0.60,      # ผู้ขายไม่ได้ผลิตเอง พ.ร.ฎ. 11 ม.8(25)
    },

    # ค่าลดหย่อน (docs ข้อ 2.2)
    "deduction": {
        "personal": 60000.0,
        "spouse": 60000.0,
        "child_first": 30000.0,
        "child_second_onward": 60000.0,
        "parent_per_person": 30000.0,
        "parent_max_count": 4,
        "social_security_cap": 9000.0,
        "health_insurance_cap": 25000.0,
        "life_health_combined_cap": 100000.0,
        "home_loan_interest_cap": 100000.0,
        "ssf_rmf_income_rate": 0.30,
        "ssf_rmf_cap": 200000.0,
        "retirement_group_cap": 500000.0,
        "donation_rate": 0.10,
    },

    # ขั้นบันไดภาษี (docs ข้อ 2.3) — (เพดานบนของขั้น, อัตรา)
    # ขั้นสุดท้ายใช้ None = ไม่มีเพดาน (ไม่ใช้ float('inf') เพราะ Firestore/JSON เก็บไม่ได้)
    "tax_brackets": [
        (150000, 0.00),
        (300000, 0.05),
        (500000, 0.10),
        (750000, 0.15),
        (1000000, 0.20),
        (2000000, 0.25),
        (5000000, 0.30),
        (None, 0.35),
    ],

    # ภาษีวิธีที่ 2 (docs ข้อ 2.3)
    "method_2": {
        "rate": 0.005,
        "income_threshold": 120000.0,
        "exempt_tax_up_to": 5000.0,
    },
}
