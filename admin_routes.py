"""
หน้า /admin ของระบบ Admin แก้อัตราภาษี — เข้าได้จากเครื่องที่รันเซิร์ฟเวอร์เท่านั้น

ตัวกัน: request ที่ไม่ได้มาจากเครื่องนี้โดยตรง (เช่น ผ่าน ngrok) หรือยังไม่ได้ตั้ง ADMIN_PASSWORD_HASH
ได้ 404 แบบเดียวกับหน้าที่ไม่มีอยู่ — ไม่ใช่ 403 เพราะไม่ต้องการบอกว่ามีหน้านี้
ผลทดลองผ่าน ngrok จริง (2026-10-13): remote_addr เป็น 127.0.0.1 เหมือน request ตรง
ต่างกันที่ ngrok เติม X-Forwarded-For/-Host/-Proto ทุกครั้ง (client ลบไม่ได้ ngrok ต่อท้ายค่าเอง)
และ Host เป็นโดเมน ngrok — จึงเช็ก header กับ Host สองชั้น ถ้าวันหน้าตั้ง ngrok ให้ลบ header ยังมี Host กันอยู่

ผ่านตัวกันแล้วยังต้อง login (รหัสเทียบกับ ADMIN_PASSWORD_HASH) ทุก POST ต้องมี CSRF token
และรหัสผิด MAX_FAILED_LOGINS ครั้งติดกันล็อก LOCKOUT_SECONDS
log บอกได้แค่ผล (สำเร็จ / ผิดครั้งที่ n / ถูกล็อก) ห้ามมีรหัส hash หรือ token
"""
import hmac
import ipaddress
import os
import secrets
import threading
import time

from flask import Blueprint, abort, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

import admin_form
from utils import firebase_db, rules_store, tax_rules

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}

MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 15 * 60

# path ที่เข้าได้โดยยังไม่ login
LOGIN_PATH = "/admin/login"


def _now():
    # แยกเป็นฟังก์ชันให้เทสเลื่อนเวลาได้โดยไม่ต้อง sleep
    return time.monotonic()


class LoginThrottle:
    """นับรหัสผิดติดกันรวมทั้งระบบ (ทุก request มาจากในเครื่อง แยกตาม IP ไม่ได้) เก็บในหน่วยความจำ
    รีสตาร์ตแล้วเริ่มนับใหม่ — Flask รับหลาย request พร้อมกัน จึงครอบด้วย lock"""

    def __init__(self):
        self._lock = threading.Lock()
        self.failures = 0
        self.locked_until = 0.0

    def is_locked(self, now):
        with self._lock:
            return now < self.locked_until

    def record_failure(self, now):
        """คืน (ครั้งที่ผิด, ถูกล็อกหรือไม่) — ครบ MAX_FAILED_LOGINS แล้วล็อกและเริ่มนับใหม่หลังหมดเวลาล็อก"""
        with self._lock:
            self.failures += 1
            count = self.failures
            if count >= MAX_FAILED_LOGINS:
                self.locked_until = now + LOCKOUT_SECONDS
                self.failures = 0
                return count, True
            return count, False

    def record_success(self):
        with self._lock:
            self.failures = 0


_throttle = LoginThrottle()


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
    # ลำดับ: ตัวกันในเครื่อง (404) → CSRF ของทุก POST รวม login/logout (400) → ต้อง login (redirect)
    if request.method == "POST" and not csrf_token_valid(request.form.get("csrf_token", "")):
        # secret_key สุ่มใหม่ทุกครั้งที่เปิด และค่าที่แก้มีผลหลังรีสตาร์ต — กดปุ่มในแท็บเดิมหลังรีสตาร์ตจึงมาถึงตรงนี้แน่นอน
        # หน้าเดียวกันทุกกรณี ไม่บอกว่า token ผิดแบบไหน (หน้านี้ถึงได้เฉพาะ request ที่ผ่านตัวกันในเครื่องแล้ว)
        return render_template("admin_expired.html"), 400
    if request.path != LOGIN_PATH and not session.get("admin"):
        return redirect(url_for("admin.login"))
    return None


def csrf_token():
    """token ของ session นี้ สร้างเมื่อยังไม่มี — ใส่ใน hidden field ของทุก form"""
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def csrf_token_valid(submitted):
    expected = session.get("csrf_token")
    if not expected or not submitted:
        return False
    # compare_digest ใช้เวลาเท่ากันไม่ว่าจะต่างตรงตัวไหน (== หยุดที่ตัวแรกที่ต่าง ใช้เวลาเดาทีละตัวได้)
    # แปลงเป็น bytes ก่อน เพราะ str ที่ไม่ใช่ ASCII ทำให้ compare_digest โยน TypeError
    return hmac.compare_digest(expected.encode("utf-8"), submitted.encode("utf-8"))


@admin_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if session.get("admin"):
            return redirect(url_for("admin.index"))
        return render_template("admin_login.html", csrf_token=csrf_token(), error=None)

    if _throttle.is_locked(_now()):
        # ระหว่างล็อกไม่ตรวจรหัสเลย (รหัสถูกก็เข้าไม่ได้) และไม่นับเพิ่ม
        print("🔒 [ADMIN] login ถูกล็อก")
        return render_template("admin_login.html", csrf_token=csrf_token(),
                               error="ใส่รหัสผิดหลายครั้ง ระบบล็อกการเข้าสู่ระบบชั่วคราว กรุณารอแล้วลองใหม่"), 429

    password_hash = os.environ.get("ADMIN_PASSWORD_HASH", "").strip()
    if check_password_hash(password_hash, request.form.get("password", "")):
        _throttle.record_success()
        # เริ่ม session ใหม่ทั้งก้อนพร้อม token ใหม่ กัน session ที่ได้มาก่อน login (session fixation)
        session.clear()
        session.permanent = True
        session["admin"] = True
        csrf_token()
        print("✅ [ADMIN] login สำเร็จ")
        return redirect(url_for("admin.index"))

    count, locked = _throttle.record_failure(_now())
    if locked:
        print(f"🔒 [ADMIN] login ผิด (ครั้งที่ {count}) — ถูกล็อก {LOCKOUT_SECONDS // 60} นาที")
        error = "ใส่รหัสผิดหลายครั้ง ระบบล็อกการเข้าสู่ระบบชั่วคราว กรุณารอแล้วลองใหม่"
    else:
        print(f"⚠️ [ADMIN] login ผิด (ครั้งที่ {count})")
        error = "รหัสผ่านไม่ถูกต้อง"
    return render_template("admin_login.html", csrf_token=csrf_token(), error=error), 401


@admin_bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("admin.login"))


@admin_bp.route("")
def index():
    # อ่านค่าตอนถูกเรียกผ่านชื่อ module (ค่าถูกแทนที่ตอนเปิดเซิร์ฟเวอร์ใน apply_effective_rules)
    try:
        saved = saved_state()
    except SavedRulesUnavailable:
        saved = None
    # รายการที่ต่างจากค่าตั้งต้นเป็นชื่อไทยรายช่อง (ขั้นบันไดแยกทีละขั้น) — เทียบจากค่าที่ใช้อยู่ อ่านอย่างเดียว
    # log [TAX-RULES] ยังใช้ชื่อ key จาก RULES_INFO["changed_keys"] ตามเดิม
    changed = admin_form.changes(rules_store.to_override(rules_store.DEFAULT_TAX_RULES),
                                 rules_store.to_override(tax_rules.TAX_RULES))
    return render_template(
        "admin_index.html",
        info=tax_rules.RULES_INFO,
        changed_labels=[row["label"] for row in changed],
        tax_year=tax_rules.TAX_RULES["tax_year"],
        csrf_token=csrf_token(),
        saved=saved,
        pending_restart=pending_restart(saved),
    )


# ---------------------------------------------------------------------------
# ค่าที่บันทึกล่าสุดใน Firestore (tax_rules_config/active)
# ---------------------------------------------------------------------------
class SavedRulesUnavailable(Exception):
    """อ่านค่าที่บันทึกไม่ได้ (ไม่ได้เชื่อม Firestore / อ่านล้ม / เกินเวลา)"""


def _config_db():
    # อ่าน firebase_client ตอนถูกเรียก (CLAUDE.md กับดักข้อ 9) — เทสแทนฟังก์ชันนี้ด้วย db ปลอม
    return getattr(firebase_db.firebase_client, "db", None)


def saved_state():
    """ค่าที่บันทึกล่าสุด: {"exists", "version" (0 ถ้ายังไม่มี doc หรือ version ผิดรูป ใช้กับแถบรอรีสตาร์ต),
    "base" (ลายนิ้วมือของ doc ส่งไปกับฟอร์ม ใช้เทียบตอนบันทึก), "override" (ใช้เติมฟอร์มและเทียบ), "problem"}
    อ่านไม่ได้โยน SavedRulesUnavailable — ผู้เรียกห้ามเปิดฟอร์มด้วยค่าตั้งต้นแทน
    เพราะถ้าบันทึกจากฟอร์มนั้น ค่าที่แก้ไว้ใน Firestore จะถูกทับด้วยค่าตั้งต้นโดยไม่มีใครตั้งใจ
    อ่านด้วย read_config_doc ตัวเดียวกับตัวโหลด (timeout เดียวกัน) — หน้า admin ไม่อยู่ในงบ 5 วินาทีของ webhook"""
    db = _config_db()
    if db is None:
        print("⚠️ [ADMIN] อ่านค่าที่บันทึกไม่ได้ (ไม่ได้เชื่อม Firestore)")
        raise SavedRulesUnavailable()
    try:
        doc = rules_store.read_config_doc(db)
    except Exception as e:
        print(f"⚠️ [ADMIN] อ่านค่าที่บันทึกไม่ได้ ({type(e).__name__})")
        raise SavedRulesUnavailable() from e

    defaults = rules_store.to_override(rules_store.DEFAULT_TAX_RULES)
    base = rules_store.base_token(doc)
    if doc is None:
        return {"exists": False, "version": 0, "base": base, "override": defaults, "problem": None}

    version = doc.get("version") if isinstance(doc, dict) else None
    version = version if isinstance(version, int) and not isinstance(version, bool) and version >= 1 else 0
    rules, errors = rules_store.rules_from_doc(doc)
    if errors:
        # ตัวโหลดก็ปฏิเสธ doc นี้ (เซิร์ฟเวอร์ใช้ค่าตั้งต้นอยู่) — เติมค่าตั้งต้นให้แก้ใหม่ได้ base ยังเป็นของ doc นี้
        return {"exists": True, "version": version, "base": base, "override": defaults,
                "problem": "ค่าที่บันทึกใน Firestore ผิด ระบบจึงใช้ค่าตั้งต้นอยู่ ฟอร์มนี้เติมค่าตั้งต้นไว้ให้"}
    return {"exists": True, "version": version, "base": base, "override": rules_store.to_override(rules),
            "problem": None}


def pending_restart(saved):
    """มีค่าที่บันทึกแล้วแต่เซิร์ฟเวอร์ยังไม่ได้โหลด (version ไม่ตรงกับ RULES_INFO ที่ใช้อยู่)"""
    return bool(saved and saved["exists"] and saved["version"] != tax_rules.RULES_INFO["version"])


# ---------------------------------------------------------------------------
# ฟอร์มแก้ค่า + หน้ายืนยัน
# ---------------------------------------------------------------------------
# ข้อความหน้า 503 — อ่านไม่ได้ตั้งแต่ต้น (ไม่มีอะไรถูกเขียนแน่นอน) กับบันทึกแล้วล้มหรือเกินเวลา (อาจบันทึกไปแล้ว)
UNREADABLE_MESSAGE = ("อ่านค่าที่บันทึกไว้ใน Firestore ไม่ได้ (ไม่ได้เชื่อมต่อ หรืออ่านไม่สำเร็จ) "
                      "ระบบจึงไม่เปิดฟอร์ม เพราะถ้าเติมค่าตั้งต้นแทนแล้วบันทึก ค่าที่แก้ไว้เดิมจะถูกทับ "
                      "กรุณาตรวจการเชื่อมต่อแล้วลองใหม่")
SAVE_UNREADABLE_MESSAGE = ("อ่านค่าที่บันทึกไว้ใน Firestore ไม่ได้ (ไม่ได้เชื่อมต่อ หรืออ่านไม่สำเร็จ) "
                           "ระบบจึงยังไม่ได้บันทึกอะไร กรุณาตรวจการเชื่อมต่อแล้วลองใหม่ครับ")
SAVE_UNKNOWN_MESSAGE = ("บันทึกไม่สำเร็จหรือไม่แน่ใจว่าบันทึกแล้ว "
                        "กรุณาเปิดหน้าผู้ดูแลตรวจเวอร์ชันก่อนลองใหม่ครับ")
CONFLICT_MESSAGE = "มีการบันทึกจากที่อื่นหลังคุณเปิดฟอร์ม กรุณาเปิดฟอร์มใหม่ครับ"


@admin_bp.route("/edit", methods=["GET", "POST"])
def edit():
    try:
        saved = saved_state()
    except SavedRulesUnavailable:
        return render_template("admin_unavailable.html", title="แก้ตัวเลขภาษีไม่ได้ตอนนี้",
                               message=UNREADABLE_MESSAGE), 503

    if request.method == "GET":
        return _render_form(saved, admin_form.values_from_override(saved["override"]), saved["base"])

    if request.form.get("action") == "back":
        # "กลับไปแก้" จากหน้ายืนยัน: เติมค่าที่ส่งมาคืนโดยไม่ตรวจ base_token เป็นของฟอร์มเดิม
        values = {name: request.form.get(name, "") for name, _, _ in admin_form.all_fields()}
        return _render_form(saved, values, request.form.get("base_token", ""),
                            reference=request.form.get("source_reference", ""), note=request.form.get("note", ""))

    result = admin_form.parse_form(request.form)
    if result["override"] is None:
        return _render_form_errors(saved, result)

    # เทียบกับค่าที่บันทึกล่าสุด ไม่คำนวณภาษีตัวอย่าง — การสลับ tax_rules.TAX_RULES ชั่วคราวจะทำให้
    # webhook ที่เข้ามาพร้อมกัน (Flask รับหลาย request พร้อมกัน) คิดภาษีด้วยค่าที่ยังไม่ได้ยืนยัน
    rows = admin_form.changes(saved["override"], result["override"])
    # doc ที่ถูกปฏิเสธ (saved["problem"]) ไปหน้ายืนยันได้แม้ค่าเท่าค่าตั้งต้น — ไม่อย่างนั้นแก้ doc ผิดกลับเป็นค่าตั้งต้นไม่ได้
    if not rows and not saved["problem"]:
        return _render_form(saved, result["raw"], result["base_token"],
                            reference=result["reference"], note=result["note"], notice="ไม่มีค่าที่เปลี่ยน")
    return render_template(
        "admin_confirm.html",
        rows=rows,
        values=admin_form.values_from_override(result["override"]),
        reference=result["reference"],
        note=result["note"],
        base_token=result["base_token"],
        overwrite_invalid=bool(saved["problem"]),
        csrf_token=csrf_token(),
        ratio=admin_form.CHANGE_WARNING_RATIO,
    )


@admin_bp.route("/save", methods=["POST"])
def save():
    """ปุ่ม "ยืนยันบันทึก" ของหน้ายืนยัน — ตรวจค่าทั้งหมดใหม่ด้วยเส้นทางเดียวกับหน้ายืนยัน ไม่เชื่อว่าผ่านมาแล้ว
    (hidden field แก้ได้จากเบราว์เซอร์) แล้วบันทึกใน transaction เดียว
    ไม่แตะ tax_rules.TAX_RULES / RULES_INFO — ค่าที่บันทึกมีผลหลังรีสตาร์ตเท่านั้น"""
    try:
        saved = saved_state()
    except SavedRulesUnavailable:
        return render_template("admin_unavailable.html", title="บันทึกไม่ได้ตอนนี้",
                               message=SAVE_UNREADABLE_MESSAGE), 503

    result = admin_form.parse_form(request.form)
    if result["override"] is None:
        return _render_form_errors(saved, result), 400

    try:
        outcome = rules_store.save_config(_config_db(), result["rules"], result["base_token"],
                                          result["reference"], result["note"])
    except rules_store.VersionConflict:
        print("⚠️ [ADMIN] ไม่บันทึกค่าภาษี — doc เปลี่ยนหลังเปิดฟอร์ม")
        return render_template("admin_conflict.html", message=CONFLICT_MESSAGE), 409
    except rules_store.NoChanges:
        return _render_form(saved, result["raw"], result["base_token"],
                            reference=result["reference"], note=result["note"], notice="ไม่มีค่าที่เปลี่ยน")
    except Exception as e:
        # ไม่พิมพ์ข้อความของ exception (อาจมีเนื้อหาของ doc) — บอกแค่ชนิด
        print(f"❌ [ADMIN] บันทึกค่าภาษีไม่สำเร็จหรือไม่รู้ผล ({type(e).__name__})")
        return render_template("admin_unavailable.html", title="บันทึกไม่สำเร็จ",
                               message=SAVE_UNKNOWN_MESSAGE), 503

    # log นอก transaction (ฟังก์ชันใน transaction ถูกเรียกซ้ำได้) — ชื่อช่องเท่านั้น ไม่มีค่า แหล่งอ้างอิง หรือหมายเหตุ
    names = [row["name"] for row in admin_form.changes(outcome["previous_override"], result["override"])]
    print(f"✅ [ADMIN] บันทึกค่าภาษีเวอร์ชัน {outcome['version']} (แก้ {len(names)} รายการ: {', '.join(names)})")
    return render_template("admin_saved.html", version=outcome["version"],
                           reference=result["reference"], note=result["note"])


def _render_form_errors(saved, result):
    # ผิด: แสดงค่าที่ผู้ใช้กรอกคืน (ไม่ล้าง) พร้อมข้อผิดพลาดข้างช่อง
    return _render_form(saved, result["raw"], request.form.get("base_token", ""),
                        reference=request.form.get("source_reference", ""), note=request.form.get("note", ""),
                        field_errors=result["field_errors"], general_errors=result["general_errors"])


def _render_form(saved, values, base_token, reference="", note="", field_errors=None, general_errors=None,
                 notice=None):
    return render_template(
        "admin_edit.html",
        groups=admin_form.form_fields(),
        values=values,
        base_token=base_token,
        reference=reference,
        note=note,
        field_errors=field_errors or {},
        general_errors=general_errors or [],
        notice=notice,
        saved=saved,
        pending_restart=pending_restart(saved),
        reference_min=admin_form.REFERENCE_MIN_LENGTH,
        reference_max=admin_form.REFERENCE_MAX_LENGTH,
        note_max=admin_form.NOTE_MAX_LENGTH,
        csrf_token=csrf_token(),
    )
