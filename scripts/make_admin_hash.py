"""
สร้างค่า ADMIN_PASSWORD_HASH สำหรับหน้า /admin

    python scripts\\make_admin_hash.py

ถามรหัสผ่านสองครั้ง (ไม่แสดงตัวอักษรที่พิมพ์) ถ้าสั้นเกินหรือไม่ตรงกันให้ถามใหม่
แล้วพิมพ์แค่ hash ไม่เขียนไฟล์และไม่พิมพ์รหัส — นำ hash ไปตั้งเป็นตัวแปรสภาพแวดล้อมเอง
hash มีเครื่องหมาย $ และ : ใน PowerShell ต้องครอบด้วย single quote:  setx ADMIN_PASSWORD_HASH '<hash>'
"""
import getpass

from werkzeug.security import generate_password_hash

# หน้า admin ป้องกันด้วยรหัสนี้อย่างเดียว (ไม่มีชื่อผู้ใช้) และตัวล็อกแค่ทำให้เดาช้าลง ไม่ได้กันการเดาได้หมด
MIN_PASSWORD_LENGTH = 12


def password_problem(password):
    """คืนเหตุผลที่ใช้รหัสนี้ไม่ได้ หรือ None ถ้าใช้ได้ — ข้อความไม่มีตัวรหัส"""
    if len(password) < MIN_PASSWORD_LENGTH:
        return (f"รหัสผ่านต้องยาวอย่างน้อย {MIN_PASSWORD_LENGTH} ตัวอักษร "
                f"เพราะหน้า admin ป้องกันด้วยรหัสนี้อย่างเดียว ลองใหม่")
    return None


def ask_password(prompt=None):
    """ถามจนกว่ารหัสจะยาวพอและพิมพ์ตรงกันสองครั้ง — prompt รับเป็น argument ให้เทสส่งตัวปลอมได้"""
    prompt = prompt or getpass.getpass
    while True:
        first = prompt("รหัสผ่านผู้ดูแล: ")
        problem = password_problem(first)
        if problem:
            print(problem)
            continue
        second = prompt("พิมพ์รหัสผ่านอีกครั้ง: ")
        if first != second:
            print("รหัสผ่านสองครั้งไม่ตรงกัน ลองใหม่")
            continue
        return first


def main(prompt=None):
    print(generate_password_hash(ask_password(prompt)))


if __name__ == "__main__":
    main()
