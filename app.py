import os
import uuid
import traceback
import time
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory

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
from utils import tax_rules
from utils.pdf_generator import generate_tax_pdf

app = Flask(__name__)
PUBLIC_URL = ""

@app.route('/download/<filename>')
def download_file(filename):
    print(f"\n📥 [DEBUG-PDF] มีคำขอไฟล์: {filename}")
    return send_from_directory('static/reports', filename, as_attachment=True)

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
        "fulfillmentText": "ล้างข้อมูลเก่าเรียบร้อยครับ 🧹 พิมพ์ 'อยากคำนวณภาษี' เพื่อเริ่มใหม่ได้เลยครับ!",
        "outputContexts": cleared_contexts
    }

def handle_history(line_user_id):
    print(f"📜 [DEBUG-HISTORY] กำลังดึงประวัติของ User: {line_user_id}")
    records = firebase_client.get_user_history(line_user_id, limit=3)
    if records:
        history_list = []
        for data in records:
            dt = data.get('created_at', '')
            income_val = data.get('total_income', 0.0)
            tax_val = data.get('tax_payable', 0.0)
            pdf_link = data.get('pdf_file_url', '')
            link_text = f"\n  [ดาวน์โหลด PDF: {pdf_link}]" if pdf_link else ""
            # อ่านปีจาก field ที่บันทึกไว้ ไม่ใช่ tax_rules — รายการเก่าคำนวณด้วยกติกาปีอื่น
            # รายการที่บันทึกก่อนมี field นี้ไม่แสดงปี (ไม่เดา)
            tax_year = data.get('tax_year')
            year_text = f" (ปีภาษี {tax_year})" if tax_year else ""
            history_list.append(f"📅 {dt}{year_text}\n- เงินได้รวม: {income_val:,.2f} บาท\n- ภาษีสุทธิ: {tax_val:,.2f} บาท{link_text}")
        reply_text = "📜 ประวัติการคำนวณภาษีล่าสุดของคุณ:\n\n" + "\n------------------\n".join(history_list)
    else:
        reply_text = "ยังไม่มีประวัติการคำนวณภาษีในระบบครับ พิมพ์ 'อยากคำนวณภาษี' เพื่อเริ่มคำนวณได้เลยครับ"
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
        reply_text = "รับทราบครับ เริ่มเก็บข้อมูลจาก 'เงินเดือน' เป็นอันดับแรกนะครับ 📝\n- ได้รับเงินเดือนเฉลี่ยเดือนละเท่าไหร่ครับ?"
        next_ctx = "awaiting_salary"
    elif has_rental:
        reply_text = "รับทราบครับ เริ่มเก็บข้อมูลจาก 'ค่าเช่า' เป็นอันดับแรกนะครับ 🏠\n- ปล่อยเช่าทรัพย์สินประเภทไหนครับ (เช่น คอนโด, บ้าน, ที่ดิน)?"
        next_ctx = "awaiting_rental"
    elif has_online:
        reply_text = "รับทราบครับ เริ่มเก็บข้อมูลจาก 'ขายของออนไลน์' เป็นอันดับแรกนะครับ 📦\n- ยอดขายรวมทั้งปีประมาณเท่าไหร่ครับ?"
        next_ctx = "awaiting_online"
    else:
        reply_text = "คุณมีรายได้ประเภทไหนบ้างครับในปีนี้ (เช่น เงินเดือน, ค่าเช่า, ขายของออนไลน์)?"
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

    print(f"💵 [DEBUG-STEP2] เงินเดือน/ด: {salary_per_month:,.2f} | ทั้งปี: {total_salary:,.2f} | ประกันสังคม: {social_security:,.2f} | หัก ณ ที่จ่าย: {withholding_tax:,.2f}")

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
        reply_text += "ต่อไปขอสอบถามข้อมูลส่วนของ 'ค่าเช่า' ครับ 🏠\n- ปล่อยเช่าทรัพย์สินประเภทไหน และค่าเช่ารวมทั้งปีประมาณเท่าไหร่ครับ?"
        next_ctx = "awaiting_rental"
    elif has_online:
        reply_text += "ต่อไปขอสอบถามรายได้ส่วนของ 'ขายของออนไลน์' ครับ 📦\n- ยอดขายรวมทั้งปีประมาณเท่าไหร่ครับ?"
        next_ctx = "awaiting_online"
    else:
        reply_text += "ขั้นตอนสุดท้าย คุณมี 'ค่าลดหย่อน' เพิ่มเติมไหมครับ? (เช่น บุตร, ประกันชีวิต, RMF, ThaiESG, ดอกเบี้ยบ้าน, เงินบริจาค - จำนวนเงินให้ตอบเป็นยอดรวมทั้งปี หากไม่มีพิมพ์ 'ไม่มี' ได้เลยครับ)"
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

    print(f"🏠 [DEBUG-STEP3] อสังหาฯ: {property_type} | รายได้ค่าเช่า: {rental_income:,.2f} | อัตราหักเหมา: {rate*100}%")

    all_income_str = " ".join([str(i) for i in merged_params.get('income_types', [])]).lower()
    has_online = any(k in all_income_str for k in ["ออนไลน์", "ขาย", "ธุรกิจ"])

    reply_text = (
        f"เก็บข้อมูลรายได้ค่าเช่าเรียบร้อยครับ 🏠\n"
        f"- ทรัพย์สิน: {property_type}\n"
        f"- ค่าเช่ารวมทั้งปี: {rental_income:,.2f} บาท\n"
        f"- หักค่าใช้จ่ายเหมา ({rate*100:.0f}%): {rental_expense:,.2f} บาท\n\n"
    )
    if has_online:
        reply_text += "ต่อไปขอสอบถามรายได้ส่วนของ 'ขายของออนไลน์' ครับ 📦\n- ยอดขายรวมทั้งปีประมาณเท่าไหร่ครับ?"
        next_ctx = "awaiting_online"
    else:
        reply_text += "ขั้นตอนสุดท้าย คุณมี 'ค่าลดหย่อน' เพิ่มเติมไหมครับ? (เช่น บุตร, ประกันชีวิต, RMF, ThaiESG, ดอกเบี้ยบ้าน, เงินบริจาค - จำนวนเงินให้ตอบเป็นยอดรวมทั้งปี หากไม่มีพิมพ์ 'ไม่มี' ได้เลยครับ)"
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

    print(f"📦 [DEBUG-STEP3.5] ยอดขายออนไลน์: {online_income:,.2f} | ค่าใช้จ่าย {online_rate*100:.0f}%: {online_expense:,.2f}")

    reply_text = (
        f"เก็บข้อมูลขายของออนไลน์เรียบร้อยครับ 📦\n"
        f"- ยอดขายรวมทั้งปี: {online_income:,.2f} บาท\n"
        f"- หักค่าใช้จ่ายเหมา ({online_rate*100:.0f}%): {online_expense:,.2f} บาท\n\n"
        f"ขั้นตอนสุดท้าย คุณมี 'ค่าลดหย่อน' เพิ่มเติมไหมครับ? (เช่น บุตร, ประกันชีวิต, RMF, ThaiESG, ดอกเบี้ยบ้าน, เงินบริจาค - จำนวนเงินให้ตอบเป็นยอดรวมทั้งปี หากไม่มีพิมพ์ 'ไม่มี' ได้เลยครับ)"
    )
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
            "fulfillmentText": "ระบบยังไม่ได้รับข้อมูลรายได้ของคุณครับ รบกวนพิมพ์ 'รีเซ็ต' เพื่อเริ่มต้นใหม่อีกครั้งครับ",
            "outputContexts": [
                {"name": f"{session_id}/contexts/tax_session", "lifespanCount": 0},
                {"name": f"{session_id}/contexts/awaiting_deduction", "lifespanCount": 0}
            ]
        }

    rules = tax_rules.TAX_RULES
    # อ่านครั้งเดียวแล้วส่งต่อผ่าน tax_report_data ให้ LINE, PDF และ Firestore ได้ปีเดียวกัน
    tax_year = rules["tax_year"]
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

    print(f"🧮 [DEBUG-CALC-SUMMARY]")
    print(f"   ├─ รายได้รวม:       {total_income:,.2f} บ.")
    print(f"   ├─ ค่าใช้จ่ายรวม:    {total_expense:,.2f} บ.")
    print(f"   ├─ ค่าลดหย่อนรวม:   {total_deduction:,.2f} บ. (ลดหย่อนทั่วไป: {subtotal_deduct:,.2f}, บริจาค: {capped_donation:,.2f})")
    print(f"   ├─ เงินได้สุทธิ:      {net_income:,.2f} บ.")
    print(f"   ├─ ภาษีวิธี 1:       {tax_method_1:,.2f} บ.")
    print(f"   ├─ ภาษีวิธี 2:       {tax_method_2:,.2f} บ.")
    print(f"   ├─ ภาษีทั้งสิ้น:      {final_tax:,.2f} บ.")
    print(f"   ├─ หัก ณ ที่จ่าย:    {withholding_tax:,.2f} บ.")
    print(f"   └─ ยอดสุทธิชำระ/คืน: {net_payable:,.2f} บ.")

    tax_advice = generate_tax_planning_advice(
        total_income=total_income,
        net_income=net_income,
        current_life_ins=capped_life,
        current_rmf=capped_rmf,
        current_thai_esg=capped_thai_esg
    )

    # SSF ลดหย่อนไม่ได้แล้วหลัง ssf_last_tax_year (docs ข้อ 2.2 แถว SSF [5]) Console ไม่ annotate ยอด SSF จึงไม่ถูกนับ
    # เช็กจากข้อความดิบเพื่อบอกเหตุผล ต่อท้าย tax_advice ให้ไปถึงทั้ง LINE, PDF และ Firestore ในการเขียนครั้งเดิม
    if any(k in user_query.lower() for k in ['ssf', 'เอสเอสเอฟ']):
        ssf_last_tax_year = rules["deduction"]["ssf_last_tax_year"]
        tax_advice += (f"\nหมายเหตุ: SSF ลดหย่อนภาษีไม่ได้แล้วตั้งแต่ปีภาษี {ssf_last_tax_year + 1} "
                       f"ตามกฎกระทรวง ฉบับที่ 357 (พ.ศ. 2563) ระบบจึงไม่นำยอด SSF มาคำนวณครับ")

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
    }

    generate_tax_pdf(pdf_filepath, tax_report_data)
    download_url = f"{PUBLIC_URL}/download/{pdf_filename}" if PUBLIC_URL else ""
    pdf_msg = f"\n📄 ดาวน์โหลดเอกสารสรุป PDF: {download_url}" if download_url else ""  
    tax_report_data["pdf_file_url"] = download_url
    firebase_client.save_tax_report(line_user_id, tax_report_data)

    reply_text = (
        f"📊 สรุปผลการประเมินภาษีประจำปีภาษี {tax_year}\n"
        f"--------------------------------\n"
        f"1. เงินได้พึงประเมินรวม: {total_income:,.2f} บาท\n"
        f"2. หักค่าใช้จ่ายตามกฎหมาย: {total_expense:,.2f} บาท\n"
        f"3. รวมค่าลดหย่อนภาษี: {total_deduction:,.2f} บาท\n"
        f"4. เงินได้สุทธิ: {net_income:,.2f} บาท\n"
        f"5. ภาษีที่คำนวณได้ทั้งสิ้น: {final_tax:,.2f} บาท{method_remark}\n"
        f"6. ภาษีหัก ณ ที่จ่ายสะสม: {withholding_tax:,.2f} บาท\n"
        f"--------------------------------\n"
        f"👉 ผลสรุป: {tax_status_str}\n\n"
        f"{tax_advice}\n"
        f"{pdf_msg}"
    )

    # จบการคำนวณ ล้างบริบททั้งหมด
    return {
        "fulfillmentText": reply_text,
        "outputContexts": [
            {"name": f"{session_id}/contexts/awaiting_deduction", "lifespanCount": 0},
            {"name": f"{session_id}/contexts/tax_session", "lifespanCount": 0}
        ]
    }

def handle_knowledge(user_query, active_contexts, deadline):
    clean_check = user_query.replace(',', '').replace('.', '').strip()
    print(f"🤖 [DEBUG-RAG] กำลังประมวลผลคำถามด้วย Semantic Search: \"{user_query}\"")

    # เช็กว่ามี context การคำนวณค้างอยู่หรือไม่
    has_interview_ctx = any(k in active_contexts for k in ['awaiting_salary', 'awaiting_rental', 'awaiting_online', 'awaiting_deduction'])

    if clean_check.isdigit() and clean_check and not has_interview_ctx:
        reply_text = "หากต้องการคำนวณภาษี รบกวนพิมพ์ 'อยากคำนวณภาษี' เพื่อเริ่มต้นได้เลยครับ"
    elif user_query:
        reply_text = query_tax_knowledge(user_query, deadline=deadline)
    else:
        reply_text = "ขออภัยครับ ระบบไม่ได้รับข้อความของคุณ"

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
        print(f"📡 [INCOMING] User: {line_user_id}")
        print(f"💬 [QUERY]    \"{user_query}\"")
        print(f"🎯 [INTENT]   {intent_name}")
        print(f"🔑 [ACTIVE CONTEXTS] {active_contexts}")
        print(f"📦 [RAW PARAMS]     {parameters}")

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

        print(f"🧠 [DEBUG-STATE] รวมพารามิเตอร์สะสม (merged_params): {merged_params}")

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
            return jsonify(handle_deductions(merged_params, user_query, session_id, line_user_id))

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
            "fulfillmentText": "ขออภัยครับ เกิดข้อผิดพลาดในการประมวลผล รบกวนพิมพ์ 'รีเซ็ต' เพื่อเริ่มใหม่อีกครั้งครับ"
        })

if __name__ == '__main__':
    # โหลดโมเดลและ FAISS ล่วงหน้า ไม่งั้นผู้ใช้คนแรกที่ถามความรู้จะรอโหลดจนเกินงบ 5 วินาทีของ Dialogflow
    get_db()

    from pyngrok import ngrok
    ngrok.kill()
    public_url_obj = ngrok.connect(5000)
    PUBLIC_URL = public_url_obj.public_url
    print(f"\n🔗 อัปเดต URL ใน Dialogflow: {PUBLIC_URL}/webhook\n")
    app.run(port=5000, debug=False)