import re

from utils import tax_rules

def clean_number(val):
    """
    สกัดตัวเลข รองรับ '1.5 ล้าน' และตัดปี พ.ศ. ที่มี "ปี"/"พ.ศ." นำหน้าออกก่อน
    """
    if isinstance(val, list):
        val = val[0] if len(val) > 0 else 0
    if val is None:
        return 0.0

    # Dialogflow ตีความมาแล้วว่าเป็นตัวเลข ไม่ต้องผ่าน regex ตัดปี (#9)
    # ไม่รับ bool เพราะ bool เป็น int ใน Python — เดิม True ได้ 0.0 ให้คงไว้
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return float(val)
    if isinstance(val, dict) and 'amount' in val:  # รูปแบบ sys.unit-currency
        return clean_number(val['amount'])

    val_str = str(val).strip().replace(',', '')
    if val_str == "":
        return 0.0

    # ตัดปี พ.ศ. เฉพาะที่มี "ปี" หรือ "พ.ศ." นำหน้า เลข 25xx โดด ๆ ถือเป็นเงิน (#9)
    # (?!\d) กันไม่ให้ตัด "ปี 25000" เหลือ "0"
    # ต้องตัดก่อนสาขา "ล้าน" ไม่งั้นปีจะถูกเอาไปคูณล้าน (#10)
    val_str = re.sub(r'(?:ปี|พ\.ศ\.)\s*25\d{2}(?!\d)', '', val_str)

    # จัดการกรณีคำว่า "ล้าน"
    if "ล้าน" in val_str:
        matches = re.findall(r'[-+]?\d*\.\d+|\d+', val_str)
        if matches:
            try:
                return float(matches[0]) * 1000000.0
            except ValueError:
                return 0.0

    matches = re.findall(r'[-+]?\d*\.\d+|\d+', val_str)
    if matches:
        best_match = max(matches, key=len)
        try:
            return float(best_match)
        except ValueError:
            return 0.0
    return 0.0

def compute_tax_from_net(net_income):
    # config เก็บเพดานบนของขั้น แปลงเป็นความกว้างของขั้นเพื่อใช้ลูปเดิม
    brackets = []
    prev_upper = 0
    for upper, rate in tax_rules.TAX_RULES["tax_brackets"]:
        if upper is None:
            brackets.append((float('inf'), rate))
        else:
            brackets.append((upper - prev_upper, rate))
            prev_upper = upper
    tax = 0.0
    remain = max(0.0, net_income)
    for cap, rate in brackets:
        if remain <= 0:
            break
        taxable = min(remain, cap)
        tax += taxable * rate
        remain -= taxable
    return tax

def compute_method_2_tax(non_salary_income):
    method_2 = tax_rules.TAX_RULES["method_2"]
    if non_salary_income >= method_2["income_threshold"]:
        tax_method_2 = non_salary_income * method_2["rate"]
        if tax_method_2 > method_2["exempt_tax_up_to"]:
            return tax_method_2
    return 0.0

def get_rental_expense_rate(property_type):
    rates = tax_rules.TAX_RULES["expense"]["rental_rate"]
    prop = str(property_type).lower()
    if any(k in prop for k in ['บ้าน', 'คอนโด', 'ตึก', 'อาคาร', 'สิ่งปลูกสร้าง', 'หอพัก', 'ห้องพัก']):
        return rates["building"]
    elif 'เกษตร' in prop:
        return rates["agricultural_land"]
    elif 'ที่ดิน' in prop:
        return rates["land"]
    # ยานพาหนะใช้อัตราเดียวกับโรงเรือน (key building) ตาม พ.ร.ฎ. ฉบับที่ 11 ม.5
    # "โรงเรือน" มี "เรือ" อยู่ข้างใน จึงตกสาขานี้ด้วย ได้อัตราถูกเพราะใช้ key building เหมือนกัน
    elif any(k in prop for k in ['รถ', 'เรือ', 'ยานพาหนะ']):
        return rates["building"]
    else:
        return rates["unmatched"]

def get_tax_bracket_rate(net_income):
    for upper, rate in tax_rules.TAX_RULES["tax_brackets"]:
        if upper is None or net_income <= upper:
            return rate

def calculate_detailed_deductions(total_income, params):
    """
    คำนวณและคุมเพดานค่าลดหย่อนครบตามกฎหมาย (แก้บั๊ก 2 และ 3)
    """
    deduction_rules = tax_rules.TAX_RULES["deduction"]

    # 1. ส่วนตัว
    personal = deduction_rules["personal"]

    # 2. คู่สมรส (อ่านค่า yes/no จาก Custom Entity โดยตรง)
    spouse_val = str(params.get('spouse', '')).strip().lower()
    has_spouse = (spouse_val == 'yes')
    spouse_deduct = deduction_rules["spouse"] if has_spouse else 0.0

    # 3. แก้บั๊ก 3: บุตร (คนแรก child_first / บุตรคนที่ 2 ขึ้นไป child_second_onward ตามเกณฑ์เกิด >= 2561)
    num_children = int(clean_number(params.get('num_children', 0)))
    if num_children <= 0:
        child_deduct = 0.0
    elif num_children == 1:
        child_deduct = deduction_rules["child_first"]
    else:
        # สมมติฐานตามแนวปฏิบัติกรมสรรพากร: บุตรคนที่ 2 ขึ้นไปเกิดตั้งแต่ปี 2561
        child_deduct = deduction_rules["child_first"] + ((num_children - 1) * deduction_rules["child_second_onward"])

    # 4. บิดามารดา (คนละ parent_per_person ไม่เกิน parent_max_count คน)
    num_parents = min(deduction_rules["parent_max_count"], int(clean_number(params.get('num_parents', 0))))
    parent_deduct = num_parents * deduction_rules["parent_per_person"]

    # 5. ประกันสังคม (ไม่เกิน social_security_cap)
    social_sec = min(clean_number(params.get('social_security', 0)), deduction_rules["social_security_cap"])

    # 6. ประกันชีวิต + สุขภาพ (รวมไม่เกิน life_health_combined_cap / สุขภาพไม่เกิน health_insurance_cap)
    raw_life = clean_number(params.get('life_insurance', 0))
    raw_health = min(clean_number(params.get('health_insurance', 0)), deduction_rules["health_insurance_cap"])
    life_health_deduct = min(raw_life + raw_health, deduction_rules["life_health_combined_cap"])

    # 7. ดอกเบี้ยกู้ซื้อบ้าน (ไม่เกิน home_loan_interest_cap)
    home_loan_interest = min(clean_number(params.get('home_loan_interest', 0)), deduction_rules["home_loan_interest_cap"])

    # 8. กองทุน RMF (ไม่เกิน rmf_income_rate ของเงินได้และไม่เกิน rmf_cap แล้วคุมด้วยเพดานกลุ่มเกษียณ retirement_group_cap)
    #    parameter ยังชื่อ ssf_rmf เพราะผูกกับ Dialogflow แต่ถือเป็น RMF อย่างเดียว (SSF หมดสิทธิลดหย่อนหลังปีภาษี 2567)
    raw_rmf = clean_number(params.get('ssf_rmf', 0))
    rmf_by_income = min(raw_rmf, total_income * deduction_rules["rmf_income_rate"], deduction_rules["rmf_cap"])
    retirement_deduct = min(rmf_by_income, deduction_rules["retirement_group_cap"])

    # 9. ThaiESG (ไม่เกิน thai_esg_income_rate ของเงินได้และไม่เกิน thai_esg_cap) แยกจาก retirement_group_cap (docs หัวข้อ 3 [8])
    #    parameter thai_esg ยังไม่มีบน Dialogflow Console ถ้าไม่มีค่า clean_number คืน 0
    raw_thai_esg = clean_number(params.get('thai_esg', 0))
    thai_esg_deduct = min(raw_thai_esg, total_income * deduction_rules["thai_esg_income_rate"], deduction_rules["thai_esg_cap"])

    subtotal_deductions = (personal + spouse_deduct + child_deduct + parent_deduct +
                           social_sec + life_health_deduct + home_loan_interest + retirement_deduct +
                           thai_esg_deduct)

    # คืนยอด ThaiESG แยกด้วย เพราะ generate_tax_planning_advice ต้องรู้ยอดที่ใช้ไปแล้ว
    return subtotal_deductions, life_health_deduct, retirement_deduct, thai_esg_deduct

def generate_tax_planning_advice(total_income, net_income, current_life_ins, current_rmf, current_thai_esg):
    """
    บอกสิทธิทางภาษีที่ผู้ใช้ยังใช้ได้อีก เป็นข้อมูลสิทธิ ไม่ใช่คำแนะนำการลงทุน
    current_* คือยอดหลังคุมเพดานแล้วจาก calculate_detailed_deductions
    """
    if compute_tax_from_net(net_income) == 0.0:
        return "💡 เงินได้สุทธิของคุณอยู่ในขั้นที่ได้รับยกเว้นภาษี การใช้สิทธิเพิ่มจึงไม่ทำให้ภาษีลดลงครับ"

    deduction_rules = tax_rules.TAX_RULES["deduction"]

    # RMF: ไม่เกิน rmf_income_rate ของเงินได้และไม่เกิน rmf_cap แล้วต้องไม่เกินที่เหลือของ retirement_group_cap
    #      บอทรองรับกลุ่มเกษียณแค่ RMF ยอดที่ใช้ไปของกลุ่มจึงเท่ากับ current_rmf
    rmf_limit = min(total_income * deduction_rules["rmf_income_rate"], deduction_rules["rmf_cap"])
    remain_rmf = max(0.0, min(rmf_limit - current_rmf,
                              deduction_rules["retirement_group_cap"] - current_rmf))

    # ThaiESG: ไม่เกิน thai_esg_income_rate ของเงินได้และไม่เกิน thai_esg_cap แยกจาก retirement_group_cap (docs หัวข้อ 3 [8])
    thai_esg_limit = min(total_income * deduction_rules["thai_esg_income_rate"], deduction_rules["thai_esg_cap"])
    remain_thai_esg = max(0.0, thai_esg_limit - current_thai_esg)

    # ประกันชีวิต: current_life_ins เป็นยอดประกันชีวิต+สุขภาพรวมกัน จึงเทียบกับ life_health_combined_cap
    remain_life = max(0.0, deduction_rules["life_health_combined_cap"] - current_life_ins)

    lines = []
    for label, remain in (("กองทุน RMF", remain_rmf),
                          ("กองทุน ThaiESG", remain_thai_esg),
                          ("ประกันชีวิต", remain_life)):
        if remain > 0:
            lines.append(f"- {label}: ยังใช้สิทธิได้อีก {remain:,.0f} บาท")

    if not lines:
        return "💡 คุณใช้สิทธิกองทุน RMF, ThaiESG และประกันชีวิตครบเพดานแล้วครับ"

    bracket_rate = get_tax_bracket_rate(net_income)
    return (f"💡 สิทธิทางภาษีที่ยังใช้ได้ (อัตราภาษีขั้นสูงสุดของคุณอยู่ที่ {bracket_rate*100:.0f}%):\n"
            + "\n".join(lines)
            + "\n(เป็นข้อมูลสิทธิทางภาษีตามกฎหมาย กองทุนแต่ละประเภทมีเงื่อนไขการถือครอง ไม่ใช่คำแนะนำการลงทุน)")