"""
หน้า /admin ของระบบ Admin แก้อัตราภาษี — เข้าได้จากเครื่องที่รันเซิร์ฟเวอร์เท่านั้น

ตัวกัน: request ที่ไม่ได้มาจากเครื่องนี้โดยตรง (เช่น ผ่าน ngrok) หรือยังไม่ได้ตั้ง ADMIN_PASSWORD_HASH
ได้ 404 แบบเดียวกับหน้าที่ไม่มีอยู่ — ไม่ใช่ 403 เพราะไม่ต้องการบอกว่ามีหน้านี้
ผลทดลองผ่าน ngrok จริง (2026-10-13): remote_addr เป็น 127.0.0.1 เหมือน request ตรง
ต่างกันที่ ngrok เติม X-Forwarded-For/-Host/-Proto ทุกครั้ง (client ลบไม่ได้ ngrok ต่อท้ายค่าเอง)
และ Host เป็นโดเมน ngrok — จึงเช็ก header กับ Host สองชั้น ถ้าวันหน้าตั้ง ngrok ให้ลบ header ยังมี Host กันอยู่
"""
import ipaddress
import os

from flask import Blueprint, abort, render_template, request

from utils import tax_rules

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def admin_enabled():
    # อ่าน env ตอนถูกเรียก (CLAUDE.md กับดักข้อ 9) ช่องว่างล้วนถือว่าไม่ได้ตั้ง — ไม่พิมพ์ค่าไม่ว่ากรณีไหน
    return bool(os.environ.get("ADMIN_PASSWORD_HASH", "").strip())


def admin_status_line():
    # พิมพ์ตอนเปิดเซิร์ฟเวอร์ บอกแค่เปิดหรือปิด ไม่พิมพ์ค่า hash
    if admin_enabled():
        return "🛠️ [ADMIN] เปิดอยู่ — เข้าได้จากเครื่องนี้เท่านั้นที่ http://127.0.0.1:5000/admin"
    return "🔒 [ADMIN] ปิดอยู่ — ยังไม่ได้ตั้ง ADMIN_PASSWORD_HASH หน้า /admin ตอบ 404"


def is_admin_path(path):
    # "/administrator" ไม่ใช่หน้า admin — เทียบทั้งส่วนของ path ไม่ใช่แค่ขึ้นต้นด้วยตัวอักษร
    return path == "/admin" or path.startswith("/admin/")


def strip_port(host):
    """ตัด port ออกจากค่า Host: "127.0.0.1:5000" → "127.0.0.1", "[::1]:5000" → "::1"
    รูปที่ผิด (เช่น "[::1" ไม่มีวงเล็บปิด, "[::1]x:5000", "localhost:abc") คืน "" ซึ่งไม่อยู่ใน LOCAL_HOSTS จึงถูกปฏิเสธ"""
    host = (host or "").strip().lower()
    if host.startswith("["):
        end = host.find("]")
        if end == -1:
            return ""
        name, rest = host[1:end], host[end + 1:]
        # หลัง "]" มีได้แค่ ":port" หรือไม่มีอะไรเลย
        if rest and not (rest.startswith(":") and rest[1:].isdigit()):
            return ""
        return name
    if host.count(":") == 1:
        name, port = host.split(":", 1)
        return name if port.isdigit() else ""
    # ไม่มี port หรือเป็น IPv6 ไม่มีวงเล็บ (เช่น "::1")
    return host


def is_local_request(remote_addr, host, header_names):
    """ฟังก์ชันล้วน ไม่แตะ request — True เมื่อ request มาจากเครื่องนี้โดยตรงเท่านั้น"""
    for name in header_names:
        name = name.lower()
        if name.startswith("x-forwarded-") or name == "forwarded":
            return False
    try:
        if not ipaddress.ip_address(remote_addr).is_loopback:
            return False
    except (TypeError, ValueError):
        return False
    return strip_port(host) in LOCAL_HOSTS


@admin_bp.before_app_request
def hide_admin_unless_local():
    # ลงทะเบียนกับทั้ง app แต่ทำงานเฉพาะ path /admin
    # ใช้ before_request ของ blueprint ไม่ได้ เพราะ Flask จับคู่ route ก่อน ถ้าจับคู่ไม่ได้ (POST /admin → 405,
    # /admin/x/ → 308) hook ของ blueprint ไม่ถูกเรียก คนภายนอกจะรู้ว่ามีหน้านี้ (ทดลองแล้ว 2026-10-13)
    if not is_admin_path(request.path):
        return None
    if not admin_enabled() or not is_local_request(request.remote_addr, request.host, request.headers.keys()):
        abort(404)
    return None


@admin_bp.route("")
def index():
    # อ่านค่าตอนถูกเรียกผ่านชื่อ module (ค่าถูกแทนที่ตอนเปิดเซิร์ฟเวอร์ใน apply_effective_rules)
    return render_template(
        "admin_index.html",
        info=tax_rules.RULES_INFO,
        tax_year=tax_rules.TAX_RULES["tax_year"],
    )
