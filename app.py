import os
import uuid
import traceback
import time
import threading
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, send_from_directory, render_template
from werkzeug.exceptions import RequestEntityTooLarge

from utils.firebase_db import firebase_client
from utils.knowledge_search import query_tax_knowledge, get_db
from utils.tax_calculator import (
    clean_number,
    get_rental_expense_rate,
    calculate_detailed_deductions,
    compute_tax_from_net,
    compute_method_2_tax,
    generate_tax_planning_advice
)
from utils import tax_rules, rules_store
from utils.pdf_generator import generate_tax_pdf
# import เป็นโมดูล แล้วเรียก ocr.read_text ฯลฯ ตอนใช้ — เทส patch ที่ utils.ocr / utils.liff_auth ได้
from utils import ocr, liff_auth
# สวิตช์ TAX_DEBUG — import เป็นโมดูลแบบเดียวกัน อ่าน env ตอนถูกเรียก
# log ที่มี LINE user ID ข้อความ ยอดเงิน หรือชื่อไฟล์ PDF ใช้ debug_log.debug_print (ค่าตั้งต้นไม่พิมพ์)
from utils import debug_log
from utils.receipt_parser import extract_donation_amount, format_amount, has_donation_keyword
from utils.deduction_guard import check_deduction_parameters, has_number
# หน้า /admin อยู่ในไฟล์แยก — ตัวกันในไฟล์นั้นทำงานเฉพาะ path /admin ไม่กระทบ /webhook ที่มาทาง ngrok
from admin_routes import admin_bp, admin_status_line

app = Flask(__name__)
# session ใช้เฉพาะหน้า admin — สุ่ม secret_key ใหม่ทุกครั้งที่เปิด ไม่ต้องเก็บ secret ไว้ที่ไหน (รีสตาร์ตแล้วต้อง login ใหม่)
app.secret_key = os.urandom(32)
# Secure: หน้า admin เปิดผ่าน http://127.0.0.1 ไม่มี HTTPS แต่ Chrome ถือว่า 127.0.0.1 และ localhost
# เป็น secure context จึงเก็บและส่ง cookie ที่มี Secure แม้เปิดผ่าน http
# (ทดลอง Chrome 2026-10-13: ทั้ง http://127.0.0.1:5090 และ http://localhost:5090 เก็บ cookie ที่มี Secure และส่งกลับมา)
# ถ้าวันหน้าเปิดหน้า admin ด้วยชื่อเครื่องอื่นที่ไม่ใช่สองชื่อนี้ผ่าน http เบราว์เซอร์จะไม่เก็บ cookie แล้ว login ไม่ติด
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
    SESSION_COOKIE_SECURE=True,
    PERMANENT_SESSION_LIFETIME=timedelta(minutes=30),
)
app.register_blueprint(admin_bp)
PUBLIC_URL = ""

@app.route('/download/<filename>')
def download_file(filename):
    debug_log.debug_print(f"\n📥 [DEBUG-PDF] มีคำขอไฟล์: {filename}")
    return send_from_directory('static/reports', filename, as_attachment=True)

@app.route('/liff/donation')
def liff_donation():
    # หน้า LIFF อ่านยอดเงินบริจาคจากรูป แล้วส่งข้อความเข้าแชตแทนผู้ใช้
    # อ่าน LIFF ID ตอนถูกเรียก ไม่ hardcode — ไม่ได้ตั้งค่า → ส่ง None ให้หน้าเว็บแสดงว่าไม่มี LIFF ID
    liff_id = os.environ.get('LIFF_ID', '').strip() or None
    # "ข้อมูลสำหรับทดสอบ" (OS, เวอร์ชัน LINE, ขนาดรูป, EXIF) แสดงเฉพาะเมื่อตั้ง TAX_DEBUG=1
    return render_template('liff_donation.html', liff_id=liff_id,
                           show_debug=debug_log.debug_enabled())

# ขนาดไฟล์รูปสูงสุดของ /liff/ocr — ตั้งเฉพาะ route นี้ผ่าน request.max_content_length
# ห้ามตั้ง app.config['MAX_CONTENT_LENGTH'] เพราะจะมีผลกับ /webhook ด้วย
LIFF_OCR_MAX_BYTES = 10 * 1024 * 1024

# อ่านรูปได้ทีละรูป: OCR ใช้ CPU เครื่องเดียวกับ webhook ถ้าอ่านพร้อมกันหลายรูป
# คำตอบของ Dialogflow อาจเกินงบ 5 วินาที (CLAUDE.md กฎเหล็กข้อ 1) — รูปที่มาตอนไม่ว่างตอบ busy ทันที ไม่รอคิว
_ocr_slot = threading.Semaphore(1)

def liff_channel_id():
    # Channel ID ของ LINE Login channel คือตัวเลขหน้า "-" ของ LIFF ID
    # (เทียบกับ Channel ID บน LINE Developers Console แล้วว่าตรงกัน 2026-10-11)
    liff_id = os.environ.get('LIFF_ID', '').strip()
    return liff_id.split('-')[0] if liff_id else ''

def liff_donation_link():
    # ต่อท้ายคำถามค่าลดหย่อน — อ่าน LIFF_ID ตอนถูกเรียก (CLAUDE.md กับดักข้อ 9) ไม่ได้ตั้ง → ไม่แสดงลิงก์
    # หน้า LIFF ส่งข้อความเดียวแล้วสาขา 04 คำนวณทันที ค่าลดหย่อนอื่นที่ไม่ได้อยู่ในข้อความนั้นจะไม่ถูกนับ
    liff_id = os.environ.get('LIFF_ID', '').strip()
    if not liff_id:
        return ""
    return (
        "\n\n📷 ถ้ามีเฉพาะเงินบริจาค ถ่ายรูปใบเสร็จให้ระบบอ่านยอดได้ที่ลิงก์นี้ครับ\n"
        f"https://liff.line.me/{liff_id}\n"
        "(ถ้ามีค่าลดหย่อนอื่นด้วย กรุณาพิมพ์ทั้งหมดในแชตครับ)"
    )

def deduction_question():
    # คำถามค่าลดหย่อนของสาขาเงินเดือน ค่าเช่า และขายออนไลน์ — เขียนที่เดียว ทั้งสามสาขาจึงถามเหมือนกันเสมอ
    # ต่อลิงก์ LIFF ตอนถูกเรียก (อ่าน LIFF_ID ตอนนั้น — CLAUDE.md กับดักข้อ 9)
    return (
        "ขั้นตอนสุดท้าย คุณมี 'ค่าลดหย่อน' เพิ่มเติมไหมครับ?\n\n"
        "เช่น บุตร, ประกันชีวิต, RMF, ThaiESG, ดอกเบี้ยบ้าน, เงินบริจาค\n"
        "จำนวนเงินให้ตอบเป็นยอดรวมทั้งปีครับ\n\n"
        "ถ้าไม่มีค่าลดหย่อน พิมพ์\n"
        "ไม่มี\n"
        "แล้วระบบจะคำนวณภาษีให้ทันทีครับ"
        + liff_donation_link()
    )

def liff_id_status_line():
    # พิมพ์ตอนเริ่มเซิร์ฟเวอร์ ให้รู้ทันทีถ้าลืมตั้ง LIFF_ID (ลิงก์ในคำถามค่าลดหย่อนจะหายเงียบ ๆ)
    # บอกแค่ตั้งแล้วหรือไม่ ไม่พิมพ์ค่า — ใช้เงื่อนไขเดียวกับ liff_donation_link (ช่องว่างล้วนถือว่าไม่ได้ตั้ง)
    if os.environ.get('LIFF_ID', '').strip():
        return "📱 [LIFF] ตั้ง LIFF_ID แล้ว — คำถามค่าลดหย่อนมีลิงก์อ่านใบเสร็จ"
    return "⚠️ [LIFF] ยังไม่ได้ตั้ง LIFF_ID — คำถามค่าลดหย่อนไม่มีลิงก์อ่านใบเสร็จ และ /liff/ocr ตอบ not_configured"

def apply_effective_rules(db):
    # เรียกครั้งเดียวตอนเปิดเซิร์ฟเวอร์ (บล็อก __main__) ห้ามเรียกตอน import หรือใน webhook
    # เพราะอ่าน Firestore ใช้เวลาหลายร้อยมิลลิวินาที (CLAUDE.md กฎเหล็กข้อ 1)
    # แทนที่ทั้งก้อน ไม่แก้ในที่ — ทุกจุดอ่าน tax_rules.TAX_RULES ตอนถูกเรียก จึงเห็นค่าใหม่
    # RULES_INFO ตั้งพร้อมกัน ที่มาของกติกาจึงตรงกับกติกาที่ใช้คำนวณเสมอ
    rules, info = rules_store.load_effective_rules(db)
    tax_rules.TAX_RULES = rules
    tax_rules.RULES_INFO = info
    return info

@app.route('/liff/ocr', methods=['POST'])
def liff_ocr():
    # รับรูปจากหน้า LIFF คืนยอดเงินบริจาค — ไม่บันทึกรูปลงดิสก์
    started = time.time()
    body, http_status = read_donation_amount()
    # log แค่ผลกับเวลา ไม่มีข้อความที่อ่านได้ ยอดเงิน หรือ token (ข้อมูลส่วนตัว)
    print(f"🧾 [LIFF-OCR] {body['status']} ({time.time() - started:.2f}s)")
    return jsonify(body), http_status

def read_donation_amount():
    """คืน (body, http_status) — body["status"] บอกหน้า LIFF ว่าควรแสดงข้อความอะไร"""
    channel_id = liff_channel_id()
    if not channel_id:
        return {"status": "not_configured"}, 503

    # เช็กขนาดจาก header ก่อน จะได้ไม่ต้องเรียก LINE ถ้ารูปใหญ่เกินอยู่แล้ว
    request.max_content_length = LIFF_OCR_MAX_BYTES
    if request.content_length is not None and request.content_length > LIFF_OCR_MAX_BYTES:
        return {"status": "too_large"}, 413

    auth = request.headers.get('Authorization', '')
    token = auth[len('Bearer '):] if auth.startswith('Bearer ') else ''
    try:
        liff_auth.verify_liff_token(token, channel_id)
    except liff_auth.InvalidTokenError:
        return {"status": "invalid_token"}, 401
    except liff_auth.LineUnavailableError:
        return {"status": "line_unavailable"}, 503

    if not ocr.ocr_available():
        return {"status": "ocr_unavailable"}, 503

    try:
        image = request.files.get('image')
    except RequestEntityTooLarge:
        # ไม่มี Content-Length (ส่งแบบ chunked) แต่อ่านแล้วเกิน max_content_length
        return {"status": "too_large"}, 413
    if image is None:
        return {"status": "no_image"}, 400

    if not _ocr_slot.acquire(blocking=False):
        return {"status": "busy"}, 503
    try:
        text = ocr.read_text(image.read())
    except ocr.InvalidImageError:
        return {"status": "invalid_image"}, 415
    except ocr.ImageTooLargeError:
        return {"status": "too_large"}, 413
    except ocr.OcrUnavailableError:
        return {"status": "ocr_unavailable"}, 503
    except ocr.OcrFailedError:
        return {"status": "ocr_failed"}, 500
    finally:
        _ocr_slot.release()

    # ตรวจก่อนหายอด: รูปที่ไม่ใช่การบริจาค (เช่น ใบเสร็จเบี้ยประกัน) ต้องไม่ได้ยอดไปเติม "บริจาค N"
    # และไม่ได้ not_found / ambiguous ซึ่งชวนให้พิมพ์ยอดต่อจาก "บริจาค"
    if not has_donation_keyword(text):
        return {"status": "not_donation", "amount": None}, 200
    result = extract_donation_amount(text)
    amount = format_amount(result["amount"]) if result["amount"] is not None else None
    return {"status": result["status"], "amount": amount}, 200

# -------------------------------------------------------------
# Handler ของแต่ละสาขาใน webhook()
# รับเฉพาะค่าที่สาขานั้นใช้ และคืน dict ให้ webhook() เป็นคน jsonify
# เงื่อนไขเลือกสาขาและลำดับการตรวจอยู่ใน webhook() (CLAUDE.md กับดักข้อ 1)
# -------------------------------------------------------------
def handle_greeting():
    greeting_text = (
        "สวัสดีครับ! ยินดีต้อนรับสู่ผู้ช่วยภาษีเงินได้บุคคลธรรมดา ภาษีพร้อม (Tax Assistant) ครับ 🤖📄\n\n"
        "ผมสามารถช่วยคุณได้ดังนี้ครับ:\n"
        "1. 🧮 คำนวณภาษีประจำปี: พิมพ์ 'อยากคำนวณภาษี'\n"
        "2. 📜 ดูประวัติการประเมินภาษี: พิมพ์ 'ดูประวัติ'\n"
        "3. 💡 สอบถามความรู้ภาษี: เช่น 'ลดหย่อนบุตรได้เท่าไหร่', 'ประกันสังคมลดหย่อนได้สูงสุดเท่าไหร่', 'ยื่นภาษีได้ถึงวันไหน'\n"
        "4. 🔄 เริ่มต้นใหม่: พิมพ์ 'รีเซ็ต'\n\n"
        "มีข้อมูลภาษีส่วนไหนให้ผมช่วยเหลือ สอบถามเข้ามาได้เลยครับ!"
    )
    return {"fulfillmentText": greeting_text}

def handle_reset(output_contexts):
    cleared_contexts = []
    for ctx in output_contexts:
        ctx_name = ctx.get('name', '')
        if ctx_name:
            cleared_contexts.append({
                "name": ctx_name,
                "lifespanCount": 0
            })
    print("🔄 [DEBUG-RESET] สั่งรีเซ็ตและเคลียร์ Context ทั้งหมดทิ้ง")
    return {
        "fulfillmentText": (
            "ล้างข้อมูลเก่าเรียบร้อยครับ 🧹\n\n"
            "เริ่มคำนวณใหม่ได้โดยพิมพ์\n"
            "อยากคำนวณภาษี\n"
            "หรือกดปุ่ม \"คำนวณภาษี\" ที่เมนูด้านล่างครับ"
        ),
        "outputContexts": cleared_contexts
    }

def handle_history(line_user_id):
    debug_log.debug_print(f"📜 [DEBUG-HISTORY] กำลังดึงประวัติของ User: {line_user_id}")
    records = firebase_client.get_user_history(line_user_id, limit=3)
    if records:
        history_list = []
        for data in records:
            dt = data.get('created_at', '')
            income_val = data.get('total_income', 0.0)
            tax_val = data.get('tax_payable', 0.0)
            pdf_link = data.get('pdf_file_url', '')
            # URL อยู่บรรทัดของตัวเอง ไม่งั้น LINE อาจลากตัวอักษรที่ติดกันเข้าไปในลิงก์
            link_text = f"\n- ดาวน์โหลด PDF:\n{pdf_link}" if pdf_link else ""
            # อ่านปีจาก field ที่บันทึกไว้ ไม่ใช่ tax_rules — รายการเก่าคำนวณด้วยกติกาปีอื่น
            # รายการที่บันทึกก่อนมี field นี้ไม่แสดงปี (ไม่เดา)
            tax_year = data.get('tax_year')
            year_text = f" (ปีภาษี {tax_year})" if tax_year else ""
            # tax_payable คือภาษีก่อนหัก ณ ที่จ่าย (บรรทัด 5 ของสรุปผล) จึงใช้ป้ายเดียวกับสรุปผล ไม่ใช่ "ภาษีสุทธิ"
            history_list.append(f"📅 {dt}{year_text}\n- เงินได้รวม: {income_val:,.2f} บาท\n- ภาษีที่คำนวณได้: {tax_val:,.2f} บาท{link_text}")
        reply_text = (
            "📜 ประวัติการคำนวณภาษีล่าสุดของคุณครับ\n\n"
            + "\n------------------\n".join(history_list)
            + "\n\nรายการเรียงจากล่าสุดไปเก่าสุดครับ"
        )
    else:
        reply_text = (
            "ยังไม่มีประวัติการคำนวณภาษีในระบบครับ\n\n"
            "เริ่มคำนวณได้โดยพิมพ์\n"
            "อยากคำนวณภาษี\n"
            "หรือกดปุ่ม \"คำนวณภาษี\" ที่เมนูด้านล่างครับ"
        )
    return {"fulfillmentText": reply_text}

def handle_interview_start(user_query, parameters, merged_params, session_id):
    full_text = user_query.lower()
    income_types = parameters.get('income_types', [])
    if isinstance(income_types, str):
        income_types = [income_types]

    has_salary = "เงินเดือน" in full_text or any("เงินเดือน" in str(i) for i in income_types)
    has_rental = any(k in full_text for k in ["เช่า", "คอนโด", "บ้าน", "ที่ดิน"]) or any(any(k in str(i) for k in ["เช่า", "คอนโด", "บ้าน", "ที่ดิน"]) for i in income_types)
    has_online = any(k in full_text for k in ["ออนไลน์", "ขาย", "ธุรกิจ"]) or any(any(k in str(i) for k in ["ออนไลน์", "ขาย", "ธุรกิจ"]) for i in income_types)

    print(f"🚦 [DEBUG-STEP1] การวิเคราะห์รายได้ -> Salary: {has_salary}, Rental: {has_rental}, Online: {has_online}")

    if has_salary:
        reply_text = "รับทราบครับ เริ่มเก็บข้อมูลจาก 'เงินเดือน' เป็นอันดับแรกนะครับ 📝\n\nได้รับเงินเดือนเฉลี่ยเดือนละเท่าไหร่ครับ?"
        next_ctx = "awaiting_salary"
    elif has_rental:
        reply_text = "รับทราบครับ เริ่มเก็บข้อมูลจาก 'ค่าเช่า' เป็นอันดับแรกนะครับ 🏠\n\nปล่อยเช่าทรัพย์สินประเภทไหนครับ (เช่น คอนโด, บ้าน, ที่ดิน)?"
        next_ctx = "awaiting_rental"
    elif has_online:
        reply_text = "รับทราบครับ เริ่มเก็บข้อมูลจาก 'ขายของออนไลน์' เป็นอันดับแรกนะครับ 📦\n\nยอดขายรวมทั้งปีประมาณเท่าไหร่ครับ?"
        next_ctx = "awaiting_online"
    else:
        reply_text = (
            "ตอนนี้ระบบคำนวณได้เฉพาะเงินได้จากเงินเดือน ค่าเช่า และขายของออนไลน์ครับ\n\n"
            "คุณมีรายได้ประเภทไหนบ้างครับ?"
        )
        next_ctx = None

    out_contexts = [
        {"name": f"{session_id}/contexts/tax_session", "lifespanCount": 20, "parameters": merged_params},
        {"name": f"{session_id}/contexts/awaiting_salary", "lifespanCount": 0},
        {"name": f"{session_id}/contexts/awaiting_rental", "lifespanCount": 0},
        {"name": f"{session_id}/contexts/awaiting_online", "lifespanCount": 0},
        {"name": f"{session_id}/contexts/awaiting_deduction", "lifespanCount": 0}
    ]
    if next_ctx:
        out_contexts.append({"name": f"{session_id}/contexts/{next_ctx}", "lifespanCount": 2})

    print(f"🔑 [DEBUG-STEP1-EMIT] ส่ง Contexts ไปยัง Dialogflow: {[c['name'].split('/')[-1] for c in out_contexts]}")
    return {
        "fulfillmentText": reply_text,
        "outputContexts": out_contexts
    }

def handle_salary(merged_params, user_query, session_id):
    salary_per_month = clean_number(merged_params.get('salary_per_month'))
    bonus = clean_number(merged_params.get('bonus'))
    social_security = clean_number(merged_params.get('social_security'))
    withholding_tax = clean_number(merged_params.get('withholding_tax'))
    total_salary = (salary_per_month * 12) + bonus

    debug_log.debug_print(f"💵 [DEBUG-STEP2] เงินเดือน/ด: {salary_per_month:,.2f} | ทั้งปี: {total_salary:,.2f} | ประกันสังคม: {social_security:,.2f} | หัก ณ ที่จ่าย: {withholding_tax:,.2f}")

    all_income_str = " ".join([str(i) for i in merged_params.get('income_types', [])]).lower() + " " + user_query.lower()
    has_rental = any(k in all_income_str for k in ["เช่า", "คอนโด", "บ้าน", "ที่ดิน"])
    has_online = any(k in all_income_str for k in ["ออนไลน์", "ขาย", "ธุรกิจ"])

    reply_text = (
        f"เก็บข้อมูลเงินเดือนเรียบร้อยครับ 📝\n"
        f"- เงินเดือนรวมทั้งปี: {total_salary:,.2f} บาท\n"
        f"- หักประกันสังคมทั้งปี: {social_security:,.2f} บาท\n"
        f"- ภาษีหัก ณ ที่จ่าย: {withholding_tax:,.2f} บาท\n\n"
    )
    if has_rental:
        reply_text += "ต่อไปขอสอบถามข้อมูลส่วนของ 'ค่าเช่า' ครับ 🏠\n\nปล่อยเช่าทรัพย์สินประเภทไหน และค่าเช่ารวมทั้งปีประมาณเท่าไหร่ครับ?"
        next_ctx = "awaiting_rental"
    elif has_online:
        reply_text += "ต่อไปขอสอบถามรายได้ส่วนของ 'ขายของออนไลน์' ครับ 📦\n\nยอดขายรวมทั้งปีประมาณเท่าไหร่ครับ?"
        next_ctx = "awaiting_online"
    else:
        reply_text += deduction_question()
        next_ctx = "awaiting_deduction"

    out_contexts = [
        {"name": f"{session_id}/contexts/tax_session", "lifespanCount": 20, "parameters": merged_params},
        {"name": f"{session_id}/contexts/awaiting_salary", "lifespanCount": 0},
        {"name": f"{session_id}/contexts/{next_ctx}", "lifespanCount": 2}
    ]

    print(f"🔑 [DEBUG-STEP2-EMIT] ส่งกิ่งถัดไป: {next_ctx}")
    return {"fulfillmentText": reply_text, "outputContexts": out_contexts}

def handle_rental(merged_params, session_id):
    property_type = merged_params.get('property_type', 'บ้าน/คอนโด')
    rental_income = clean_number(merged_params.get('rental_income'))
    rate = get_rental_expense_rate(property_type)
    rental_expense = rental_income * rate

    debug_log.debug_print(f"🏠 [DEBUG-STEP3] อสังหาฯ: {property_type} | รายได้ค่าเช่า: {rental_income:,.2f} | อัตราหักเหมา: {rate*100}%")

    all_income_str = " ".join([str(i) for i in merged_params.get('income_types', [])]).lower()
    has_online = any(k in all_income_str for k in ["ออนไลน์", "ขาย", "ธุรกิจ"])

    reply_text = (
        f"เก็บข้อมูลรายได้ค่าเช่าเรียบร้อยครับ 🏠\n"
        f"- ทรัพย์สิน: {property_type}\n"
        f"- ค่าเช่ารวมทั้งปี: {rental_income:,.2f} บาท\n"
        f"- หักค่าใช้จ่ายเหมา ({rate*100:.0f}%): {rental_expense:,.2f} บาท\n\n"
    )
    if has_online:
        reply_text += "ต่อไปขอสอบถามรายได้ส่วนของ 'ขายของออนไลน์' ครับ 📦\n\nยอดขายรวมทั้งปีประมาณเท่าไหร่ครับ?"
        next_ctx = "awaiting_online"
    else:
        reply_text += deduction_question()
        next_ctx = "awaiting_deduction"

    out_contexts = [
        {
            "name": f"{session_id}/contexts/tax_session",
            "lifespanCount": 20,
            "parameters": merged_params
        },
        {
            "name": f"{session_id}/contexts/{next_ctx}",
            "lifespanCount": 2
        }
    ]
    print(f"🔑 [DEBUG-STEP3-EMIT] ส่งกิ่งถัดไป: {next_ctx}")
    return {"fulfillmentText": reply_text, "outputContexts": out_contexts}

def handle_online(merged_params, user_query, session_id):
    online_income = clean_number(merged_params.get('online_income'))
    if online_income == 0.0:
        online_income = clean_number(user_query)
    online_rate = tax_rules.TAX_RULES["expense"]["online_sale_rate"]
    online_expense = online_income * online_rate
    merged_params['online_income'] = online_income

    debug_log.debug_print(f"📦 [DEBUG-STEP3.5] ยอดขายออนไลน์: {online_income:,.2f} | ค่าใช้จ่าย {online_rate*100:.0f}%: {online_expense:,.2f}")

    reply_text = (
        f"เก็บข้อมูลขายของออนไลน์เรียบร้อยครับ 📦\n"
        f"- ยอดขายรวมทั้งปี: {online_income:,.2f} บาท\n"
        f"- หักค่าใช้จ่ายเหมา ({online_rate*100:.0f}%): {online_expense:,.2f} บาท\n\n"
    )
    reply_text += deduction_question()
    out_contexts = [
        {"name": f"{session_id}/contexts/tax_session", "lifespanCount": 20, "parameters": merged_params},
        {"name": f"{session_id}/contexts/awaiting_online", "lifespanCount": 0},
        {"name": f"{session_id}/contexts/awaiting_deduction", "lifespanCount": 2}
    ]

    print("🔑 [DEBUG-STEP3.5-EMIT] ส่งกิ่งถัดไป: awaiting_deduction")
    return {"fulfillmentText": reply_text, "outputContexts": out_contexts}

def handle_deductions(merged_params, user_query, session_id, line_user_id):
    salary_per_month = clean_number(merged_params.get('salary_per_month'))
    bonus = clean_number(merged_params.get('bonus'))
    withholding_tax = clean_number(merged_params.get('withholding_tax'))
    rental_income = clean_number(merged_params.get('rental_income'))
    property_type = merged_params.get('property_type', 'บ้าน/คอนโด')
    online_income = clean_number(merged_params.get('online_income'))

    salary_total = (salary_per_month * 12) + bonus
    total_income = salary_total + rental_income + online_income

    # 🛡️ Safety Guard
    if total_income <= 0:
        print("⚠️ [CALC-ABORT] รายได้รวมเป็น 0.00 บาท")
        return {
            "fulfillmentText": (
                "ระบบยังไม่ได้รับข้อมูลรายได้ของคุณครับ\n\n"
                "รบกวนพิมพ์\n"
                "เริ่มใหม่\n"
                "หรือกดปุ่ม \"เริ่มใหม่\" ที่เมนูด้านล่างครับ"
            ),
            "outputContexts": [
                {"name": f"{session_id}/contexts/tax_session", "lifespanCount": 0},
                {"name": f"{session_id}/contexts/awaiting_deduction", "lifespanCount": 0}
            ]
        }

    rules = tax_rules.TAX_RULES
    # อ่านครั้งเดียวแล้วส่งต่อผ่าน tax_report_data ให้ LINE, PDF และ Firestore ได้ปีเดียวกัน
    tax_year = rules["tax_year"]
    # ที่มาของกติกา (ตั้งพร้อม TAX_RULES ใน apply_effective_rules) อ่านครั้งเดียวแบบเดียวกับ rules
    rules_info = tax_rules.RULES_INFO
    salary_expense = min(salary_total * rules["expense"]["salary_rate"], rules["expense"]["salary_cap"])
    rental_rate = get_rental_expense_rate(property_type)
    rental_expense = rental_income * rental_rate
    online_expense = online_income * rules["expense"]["online_sale_rate"]
    total_expense = salary_expense + rental_expense + online_expense

    subtotal_deduct, capped_life, capped_rmf, capped_thai_esg = calculate_detailed_deductions(total_income, merged_params)
    income_before_donation = max(0.0, total_income - total_expense - subtotal_deduct)
    raw_donation = clean_number(merged_params.get('donation', 0))
    capped_donation = min(raw_donation, income_before_donation * rules["deduction"]["donation_rate"])
    
    total_deduction = subtotal_deduct + capped_donation
    net_income = max(0.0, income_before_donation - capped_donation)

    tax_method_1 = compute_tax_from_net(net_income)
    non_salary_income = rental_income + online_income
    tax_method_2 = compute_method_2_tax(non_salary_income)

    final_tax = max(tax_method_1, tax_method_2)
    method_remark = ""
    if final_tax == tax_method_2 and tax_method_2 > 0:
        method_remark = f" (คิดตามวิธีคำนวณร้อยละ {rules['method_2']['rate']*100:g} ของเงินได้ที่ไม่ใช่เงินเดือน เนื่องจากสูงกว่า)"

    net_payable = final_tax - withholding_tax
    if net_payable > 0:
        tax_status_str = f"ต้องชำระภาษีเพิ่มเติม: {net_payable:,.2f} บาท"
    elif net_payable < 0:
        tax_status_str = f"ได้รับเงินคืนภาษี: {abs(net_payable):,.2f} บาท"
    else:
        tax_status_str = "ภาษีที่ชำระไว้พอดีแล้ว"

    debug_log.debug_print(f"🧮 [DEBUG-CALC-SUMMARY]")
    debug_log.debug_print(f"   ├─ รายได้รวม:       {total_income:,.2f} บ.")
    debug_log.debug_print(f"   ├─ ค่าใช้จ่ายรวม:    {total_expense:,.2f} บ.")
    debug_log.debug_print(f"   ├─ ค่าลดหย่อนรวม:   {total_deduction:,.2f} บ. (ลดหย่อนทั่วไป: {subtotal_deduct:,.2f}, บริจาค: {capped_donation:,.2f})")
    debug_log.debug_print(f"   ├─ เงินได้สุทธิ:      {net_income:,.2f} บ.")
    debug_log.debug_print(f"   ├─ ภาษีวิธี 1:       {tax_method_1:,.2f} บ.")
    debug_log.debug_print(f"   ├─ ภาษีวิธี 2:       {tax_method_2:,.2f} บ.")
    debug_log.debug_print(f"   ├─ ภาษีทั้งสิ้น:      {final_tax:,.2f} บ.")
    debug_log.debug_print(f"   ├─ หัก ณ ที่จ่าย:    {withholding_tax:,.2f} บ.")
    debug_log.debug_print(f"   └─ ยอดสุทธิชำระ/คืน: {net_payable:,.2f} บ.")

    tax_advice = generate_tax_planning_advice(
        total_income=total_income,
        net_income=net_income,
        current_life_ins=capped_life,
        current_rmf=capped_rmf,
        current_thai_esg=capped_thai_esg
    )

    # บอกเมื่อเงินบริจาคถูกตัดที่ donation_rate ใช้ยอดเดียวกับที่หักจริง (capped_donation) ไม่คำนวณใหม่
    # เทียบหลังปัดเป็นสตางค์ กันเศษ float ของเพดานทำให้ขึ้นข้อความ "ใช้ได้ X จาก X บาท"
    if round(capped_donation, 2) < round(raw_donation, 2):
        donation_rate = rules["deduction"]["donation_rate"]
        tax_advice += (f"\nหมายเหตุ: เงินบริจาคใช้ลดหย่อนได้ {capped_donation:,.2f} จาก {raw_donation:,.2f} บาท "
                       f"เพราะหักได้ไม่เกิน {donation_rate*100:g}% ของเงินได้หลังหักค่าใช้จ่ายและค่าลดหย่อนอื่นครับ")

    # SSF ลดหย่อนไม่ได้แล้วหลัง ssf_last_tax_year (docs ข้อ 2.2 แถว SSF [5]) Console ไม่ annotate ยอด SSF จึงไม่ถูกนับ
    # เช็กจากข้อความดิบเพื่อบอกเหตุผล ต่อท้าย tax_advice ให้ไปถึงทั้ง LINE และ PDF
    # (Firestore ไม่ได้เก็บ tax_advice — save_tax_report บันทึกเฉพาะตัวเลขสรุปกับปีภาษี)
    if any(k in user_query.lower() for k in ['ssf', 'เอสเอสเอฟ']):
        ssf_last_tax_year = rules["deduction"]["ssf_last_tax_year"]
        tax_advice += (f"\nหมายเหตุ: SSF ลดหย่อนภาษีไม่ได้แล้วตั้งแต่ปีภาษี {ssf_last_tax_year + 1} "
                       f"ตามกฎกระทรวง ฉบับที่ 357 (พ.ศ. 2563) ระบบจึงไม่นำยอด SSF มาคำนวณครับ")

    # ประวัติเก็บชุดกติกาที่ใช้จริงทุกครั้งที่มาจาก Firestore แม้ค่าจะเท่าค่าตั้งต้น (None = ค่าตั้งต้นในโค้ด)
    rules_version = rules_info["version"] if rules_info["source"] == "firestore" else None
    # หมายเหตุบอกผู้ใช้เฉพาะเมื่อค่าต่างจากค่าตั้งต้นจริง ข้อความเดียวกันไปทั้ง LINE และ PDF
    # บอกสถานะของระบบ ไม่ได้บอกว่าผลของผู้ใช้คนนี้เปลี่ยน (ค่าที่แก้อาจไม่เกี่ยวกับเงินได้ของเขา)
    rules_note = (f"หมายเหตุ: ระบบใช้อัตราและเกณฑ์ภาษีที่ผู้ดูแลระบบปรับปรุง (เวอร์ชัน {rules_version}) ครับ"
                  if rules_info["changed_keys"] else "")

    pdf_filename = f"tax_report_{uuid.uuid4().hex[:8]}.pdf"
    pdf_filepath = os.path.join("static", "reports", pdf_filename)
    os.makedirs(os.path.dirname(pdf_filepath), exist_ok=True)

    tax_report_data = {
        "date": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "tax_year": tax_year,
        "user_id": line_user_id,
        "salary_total": salary_total,        
        "rental_income": rental_income,     
        "online_income": online_income,      
        "total_income": total_income,
        "total_expense": total_expense,
        "total_deduction": total_deduction,
        "net_income": net_income,
        "tax_payable": final_tax,
        "withholding_tax": withholding_tax,
        "net_payable": net_payable,
        "tax_advice": tax_advice,         
        "rules_version": rules_version,
        "rules_note": rules_note,
    }

    generate_tax_pdf(pdf_filepath, tax_report_data)
    download_url = f"{PUBLIC_URL}/download/{pdf_filename}" if PUBLIC_URL else ""
    # URL อยู่บรรทัดของตัวเอง ไม่งั้น LINE อาจลากตัวอักษรที่ติดกันเข้าไปในลิงก์
    pdf_msg = f"📄 ดาวน์โหลดเอกสารสรุป PDF:\n{download_url}\n\n" if download_url else ""
    tax_report_data["pdf_file_url"] = download_url
    firebase_client.save_tax_report(line_user_id, tax_report_data)

    rules_note_line = f"{rules_note}\n" if rules_note else ""
    reply_text = (
        f"📊 สรุปผลการประเมินภาษีประจำปีภาษี {tax_year}\n"
        f"{rules_note_line}"
        f"--------------------------------\n"
        f"1. เงินได้พึงประเมินรวม: {total_income:,.2f} บาท\n"
        f"2. หักค่าใช้จ่ายตามกฎหมาย: {total_expense:,.2f} บาท\n"
        f"3. รวมค่าลดหย่อนภาษี: {total_deduction:,.2f} บาท\n"
        f"4. เงินได้สุทธิ: {net_income:,.2f} บาท\n"
        f"5. ภาษีที่คำนวณได้ทั้งสิ้น: {final_tax:,.2f} บาท{method_remark}\n"
        f"6. ภาษีหัก ณ ที่จ่ายสะสม: {withholding_tax:,.2f} บาท\n"
        f"--------------------------------\n"
        f"👉 ผลสรุป: {tax_status_str}\n\n"
        f"{pdf_msg}"
        f"{tax_advice}\n\n"
        # บรรทัดปิดท้ายเฉพาะข้อความ LINE — PDF ได้ tax_advice จาก tax_report_data ซึ่งไม่มีบรรทัดนี้
        # (PDF เป็นเอกสารทางการ ไม่ใส่ภาษาพูด)
        f"ถ้าต้องการคำนวณใหม่ พิมพ์\n"
        f"เริ่มใหม่\n"
        f"หรือกดปุ่ม \"เริ่มใหม่\" ที่เมนูด้านล่างครับ"
    )

    # จบการคำนวณ ล้างบริบททั้งหมด
    return {
        "fulfillmentText": reply_text,
        "outputContexts": [
            {"name": f"{session_id}/contexts/awaiting_deduction", "lifespanCount": 0},
            {"name": f"{session_id}/contexts/tax_session", "lifespanCount": 0}
        ]
    }

# ตัวอย่างในข้อความ ก — ทุกตัวต้องผ่านตัวกัน ทั้งทีละตัวและรวมเป็นข้อความเดียว (เทสวนลิสต์นี้)
UNCLEAR_EXAMPLES = [
    "ประกันชีวิต 20000", "ประกันสุขภาพ 10000", "ดอกเบี้ยบ้าน 50000", "RMF 30000",
    "ThaiESG 30000", "บริจาค 5000", "ลูก 1 คน", "พ่อแม่ 2 คน",
]

def handle_unclear_deductions(reason, merged_params, parameters, session_id):
    # #25 ไม่คำนวณ ไม่สร้าง PDF ไม่เขียน Firestore — ตอบแล้วรอค่าลดหย่อนต่อ
    if reason == "parent_health":
        reply_text = (
            "ขออภัยครับ ระบบยังไม่รองรับเบี้ยประกันสุขภาพของบิดามารดา ซึ่งเป็นสิทธิแยกจากประกันสุขภาพของตัวเอง "
            "จึงยังไม่ได้บันทึกและยังไม่ได้คำนวณครับ\n\n"
            "กรุณาพิมพ์ค่าลดหย่อนรายการอื่นทั้งหมดอีกครั้งโดยไม่รวมรายการนี้\n\n"
            "ถ้าไม่มีรายการอื่น พิมพ์\n"
            "ไม่มี\n"
            "แล้วระบบจะคำนวณภาษีให้ทันทีครับ"
        )
    else:
        # ขอให้พิมพ์ "ทั้งหมด" ในข้อความเดียว เพราะค่าทุกตัวของรอบนี้ถูกล้าง ถ้าพิมพ์ใหม่บางรายการ ที่เหลือจะหายเงียบ
        reply_text = (
            "ขออภัยครับ ระบบไม่แน่ใจว่าตัวเลขในข้อความนี้เป็นค่าลดหย่อนรายการไหน จึงยังไม่ได้บันทึกและยังไม่ได้คำนวณครับ\n\n"
            "กรุณาพิมพ์ค่าลดหย่อนทั้งหมดอีกครั้งในข้อความเดียว โดยใส่ชื่อรายการหน้าจำนวนเงินทั้งปี เช่น\n"
            f"{', '.join(UNCLEAR_EXAMPLES)}\n\n"
            "ถ้าไม่มีค่าลดหย่อน พิมพ์\n"
            "ไม่มี\n"
            "แล้วระบบจะคำนวณภาษีให้ทันทีครับ"
        )

    # 04 มี tax_session เป็น affected context ค่าของรอบนี้จึงอยู่ใน tax_session แล้ว
    # ถ้าไม่ล้าง ผู้ใช้พิมพ์ "ไม่มี" ต่อจะคำนวณด้วยยอดที่เพิ่งปฏิเสธไป
    session_params = dict(merged_params)
    for name, value in parameters.items():
        if value not in ("", None):
            session_params[name] = ""

    debug_log.debug_print(f"🚫 [DEDUCTION-GUARD] ปฏิเสธ ({reason}): {parameters}")
    return {
        "fulfillmentText": reply_text,
        "outputContexts": [
            {"name": f"{session_id}/contexts/tax_session", "lifespanCount": 20, "parameters": session_params},
            {"name": f"{session_id}/contexts/awaiting_deduction", "lifespanCount": 2}
        ]
    }

def handle_knowledge(user_query, active_contexts, deadline):
    clean_check = user_query.replace(',', '').replace('.', '').strip()
    debug_log.debug_print(f"🤖 [DEBUG-RAG] กำลังประมวลผลคำถามด้วย Semantic Search: \"{user_query}\"")

    # เช็กว่ามี context การคำนวณค้างอยู่หรือไม่
    has_interview_ctx = any(k in active_contexts for k in ['awaiting_salary', 'awaiting_rental', 'awaiting_online', 'awaiting_deduction'])

    if clean_check.isdigit() and clean_check and not has_interview_ctx:
        reply_text = (
            "หากต้องการคำนวณภาษี พิมพ์\n"
            "อยากคำนวณภาษี\n"
            "หรือกดปุ่ม \"คำนวณภาษี\" ที่เมนูด้านล่างครับ"
        )
    elif user_query:
        reply_text = query_tax_knowledge(user_query, deadline=deadline)
    else:
        reply_text = "ขออภัยครับ ระบบไม่ได้รับข้อความ กรุณาพิมพ์ใหม่อีกครั้งครับ"

    return {"fulfillmentText": reply_text}

@app.route('/webhook', methods=['POST'])
def webhook():
    # 1. ตั้ง Budget เวลารวมทั้งหมดของ Webhook ไม่ให้เกิน 4.0 วินาที (เผื่อ Dialogflow 5 วิ)
    start_time = time.monotonic()
    req_deadline = start_time + 4.0
    try:
        req = request.get_json(silent=True, force=True) or {}
        query_result = req.get('queryResult', {})
        intent_name = query_result.get('intent', {}).get('displayName', 'NO_INTENT_MATCHED')
        user_query = query_result.get('queryText', '').strip()
        parameters = query_result.get('parameters', {})
        output_contexts = query_result.get('outputContexts', [])
        session_id = req.get('session', '')
        orig_req = req.get('originalDetectIntentRequest', {})
        
        line_user_id = orig_req.get('payload', {}).get('data', {}).get('source', {}).get('userId')
        if not line_user_id:
            line_user_id = session_id.split('/')[-1] if session_id else 'anonymous_user'

        # -------------------------------------------------------------
        # 🔍 DEBUG 1: Request Ingestion & Context Parsing
        # -------------------------------------------------------------
        active_contexts = [ctx.get('name', '').split('/')[-1] for ctx in output_contexts if ctx.get('name')]
        print("\n" + "="*75)
        debug_log.debug_print(f"📡 [INCOMING] User: {line_user_id}")
        debug_log.debug_print(f"💬 [QUERY]    \"{user_query}\"")
        print(f"🎯 [INTENT]   {intent_name}")
        print(f"🔑 [ACTIVE CONTEXTS] {active_contexts}")
        debug_log.debug_print(f"📦 [RAW PARAMS]     {parameters}")

        # -------------------------------------------------------------
        # INTENT: ทักทายเริ่มต้น (Welcome Intent & Greeting Text)
        # -------------------------------------------------------------
        greeting_keywords = ['สวัสดี', 'หวัดดี', 'ดีครับ', 'ดีค่ะ', 'hello', 'hi', 'hey']
        if intent_name in ['Welcome', 'Default Welcome Intent'] or user_query.lower() in greeting_keywords:
            return jsonify(handle_greeting())

        # 🧹 ล้างข้อมูลรีเซ็ต (บังคับ Lifespan = 0 เพื่อเคลียร์ Context ทั้งหมด)
        if intent_name == '99_Reset_Chat' or user_query.lower() in ['รีเซ็ต', 'reset', 'เริ่มใหม่', 'ล้างข้อมูล']:
            return jsonify(handle_reset(output_contexts))

        # ดึงประวัติผ่าน firebase_db.py
        if user_query in ['ดูประวัติ', 'ดูประวัติการคำนวณ', 'ประวัติภาษี', 'ประวัติ']:
            return jsonify(handle_history(line_user_id))

        # กรอง Context — เอาเฉพาะ tax_session
        merged_params = {}
        for ctx in output_contexts:
            if ctx.get('name', '').lower().endswith('/contexts/tax_session'):
                merged_params.update(ctx.get('parameters', {}))
        merged_params.update(parameters)

        debug_log.debug_print(f"🧠 [DEBUG-STATE] รวมพารามิเตอร์สะสม (merged_params): {merged_params}")

        # ==========================================
        # STEP 1: เริ่มต้นเลือกประเภทเงินได้
        # ==========================================
        if intent_name == '01_Tax_Interview_Start':
            return jsonify(handle_interview_start(user_query, parameters, merged_params, session_id))

        # ==========================================
        # STEP 2: เงินเดือน
        # ==========================================
        elif intent_name == '02_Tax_Interview_Salary':
            return jsonify(handle_salary(merged_params, user_query, session_id))

        # ==========================================
        # STEP 3: ค่าเช่า
        # ==========================================
        elif intent_name == '03_Tax_Interview_Rental':
            return jsonify(handle_rental(merged_params, session_id))

        # ==========================================
        # STEP 3.5: ออนไลน์
        # ==========================================
        elif intent_name == '05_Tax_Interview_Online' or 'awaiting_online' in active_contexts:
            return jsonify(handle_online(merged_params, user_query, session_id))
        
        # ==========================================
        # STEP 4: ลดหย่อนและการคำนวณสรุปผล
        # ==========================================
        elif intent_name == '04_Tax_Interview_Deductions':
            # ตรวจจาก parameters ของรอบนี้ ไม่ใช่ merged_params ที่มีค่าของรอบก่อนปนอยู่
            reason = check_deduction_parameters(user_query, parameters)
            if reason:
                return jsonify(handle_unclear_deductions(reason, merged_params, parameters, session_id))
            return jsonify(handle_deductions(merged_params, user_query, session_id, line_user_id))

        # ข้อความที่มีตัวเลขระหว่างรอค่าลดหย่อนแต่ Dialogflow จับไม่ได้ (เช่น "บุตร 1 คน" ทดสอบใน LINE 2026-10-11)
        # เดิมตกไปสาขาความรู้ ผู้ใช้ไม่รู้ว่ายอดไม่ถูกบันทึก — ไม่มีตัวเลขยังเป็นคำถามความรู้ตามเดิม
        # ตรวจด้วย parameters ว่างเพื่อให้กรณี ง ได้ข้อความของตัวเอง ที่เหลือ (รวมที่ตัวกันปล่อยผ่าน เช่น "SSF 50000") ได้ข้อความ ก
        elif (intent_name == 'Default Fallback Intent' and 'awaiting_deduction' in active_contexts
              and has_number(user_query)):
            reason = check_deduction_parameters(user_query, {}) or "unclear"
            return jsonify(handle_unclear_deductions(reason, merged_params, parameters, session_id))

        # ==========================================
        # FALLBACK / KNOWLEDGE BASE (RAG)
        # ==========================================
        else:
            return jsonify(handle_knowledge(user_query, active_contexts, req_deadline))
        
    except Exception as e:
        print("\n" + "!"*75)
        print("❌ [CRITICAL ERROR TRACEBACK]")
        traceback.print_exc()
        print("!"*75 + "\n")
        return jsonify({
            "fulfillmentText": (
                "ขออภัยครับ เกิดข้อผิดพลาดในการประมวลผล\n\n"
                "รบกวนพิมพ์\n"
                "เริ่มใหม่\n"
                "หรือกดปุ่ม \"เริ่มใหม่\" ที่เมนูด้านล่าง แล้วลองอีกครั้งครับ"
            )
        })

if __name__ == '__main__':
    # โหลดโมเดลและ FAISS ล่วงหน้า ไม่งั้นผู้ใช้คนแรกที่ถามความรู้จะรอโหลดจนเกินงบ 5 วินาทีของ Dialogflow
    get_db()

    # ค่าภาษีที่ระบบ Admin แก้ไว้ มีผลหลังรีสตาร์ตเท่านั้น — อ่าน Firestore ครั้งเดียวตรงนี้
    apply_effective_rules(firebase_client.db)

    from pyngrok import ngrok
    ngrok.kill()
    public_url_obj = ngrok.connect(5000)
    PUBLIC_URL = public_url_obj.public_url
    print(f"\n🔗 อัปเดต URL ใน Dialogflow: {PUBLIC_URL}/webhook\n")
    print(liff_id_status_line())
    print(admin_status_line())
    print(debug_log.debug_status_line())
    app.run(port=5000, debug=False)