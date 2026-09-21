import re

def clean_number(val):
    """
    สกัดตัวเลข รองรับ '1.5 ล้าน', ป้องกัน Regex กินเลข 25000/250000 (แก้บั๊ก 1)
    """
    if isinstance(val, list):
        val = val[0] if len(val) > 0 else 0
    if val is None:
        return 0.0
    val_str = str(val).strip().replace(',', '')
    if val_str == "":
        return 0.0

    # จัดการกรณีคำว่า "ล้าน"
    if "ล้าน" in val_str:
        matches = re.findall(r'[-+]?\d*\.\d+|\d+', val_str)
        if matches:
            try:
                return float(matches[0]) * 1000000.0
            except ValueError:
                return 0.0

    # แก้บั๊ก 1: ใช้ Lookaround ดักเฉพาะเลข พ.ศ. 4 หลักโดดๆ ไม่ให้กิน 25000 หรือ 250000
    val_str_cleaned = re.sub(r'ปี\s*25\d{2}(?!\d)|(?<!\d)25\d{2}(?!\d)', '', val_str)

    matches = re.findall(r'[-+]?\d*\.\d+|\d+', val_str_cleaned)
    if matches:
        best_match = max(matches, key=len)
        try:
            return float(best_match)
        except ValueError:
            return 0.0
    return 0.0

def compute_tax_from_net(net_income):
    brackets = [
        (150000, 0.00),
        (150000, 0.05),
        (200000, 0.10),
        (250000, 0.15),
        (250000, 0.20),
        (1000000, 0.25),
        (3000000, 0.30),
        (float('inf'), 0.35)
    ]
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
    if non_salary_income >= 120000.0:
        tax_method_2 = non_salary_income * 0.005
        if tax_method_2 > 5000.0:
            return tax_method_2
    return 0.0

def get_rental_expense_rate(property_type):
    prop = str(property_type).lower()
    if any(k in prop for k in ['บ้าน', 'คอนโด', 'ตึก', 'อาคาร', 'สิ่งปลูกสร้าง', 'หอพัก', 'ห้องพัก']):
        return 0.30
    elif 'เกษตร' in prop:
        return 0.20
    elif 'ที่ดิน' in prop:
        return 0.15
    elif any(k in prop for k in ['รถ', 'เรือ', 'ยานพาหนะ']):
        return 0.10
    else:
        return 0.30

def get_tax_bracket_rate(net_income):
    if net_income <= 150000: return 0.00
    elif net_income <= 300000: return 0.05
    elif net_income <= 500000: return 0.10
    elif net_income <= 750000: return 0.15
    elif net_income <= 1000000: return 0.20
    elif net_income <= 2000000: return 0.25
    elif net_income <= 5000000: return 0.30
    else: return 0.35

def calculate_detailed_deductions(total_income, params):
    """
    คำนวณและคุมเพดานค่าลดหย่อนครบตามกฎหมาย (แก้บั๊ก 2 และ 3)
    """
    # 1. ส่วนตัว
    personal = 60000.0

    # 2. คู่สมรส (อ่านค่า yes/no จาก Custom Entity โดยตรง)
    spouse_val = str(params.get('spouse', '')).strip().lower()
    has_spouse = (spouse_val == 'yes')
    spouse_deduct = 60000.0 if has_spouse else 0.0

    # 3. แก้บั๊ก 3: บุตร (คนแรก 30,000 / บุตรคนที่ 2 ขึ้นไปอัตรา 60,000 ตามเกณฑ์เกิด >= 2561)
    num_children = int(clean_number(params.get('num_children', 0)))
    if num_children <= 0:
        child_deduct = 0.0
    elif num_children == 1:
        child_deduct = 30000.0
    else:
        # สมมติฐานตามแนวปฏิบัติกรมสรรพากร: บุตรคนที่ 2 ขึ้นไปเกิดตั้งแต่ปี 2561
        child_deduct = 30000.0 + ((num_children - 1) * 60000.0)

    # 4. บิดามารดา (คนละ 30,000 สูงสุด 4 คน = 120,000)
    num_parents = min(4, int(clean_number(params.get('num_parents', 0))))
    parent_deduct = num_parents * 30000.0

    # 5. ประกันสังคม (สูงสุด 9,000)
    social_sec = min(clean_number(params.get('social_security', 0)), 9000.0)

    # 6. ประกันชีวิต + สุขภาพ (รวมไม่เกิน 100,000 / สุขภาพไม่เกิน 25,000)
    raw_life = clean_number(params.get('life_insurance', 0))
    raw_health = min(clean_number(params.get('health_insurance', 0)), 25000.0)
    life_health_deduct = min(raw_life + raw_health, 100000.0)

    # 7. ดอกเบี้ยกู้ซื้อบ้าน (สูงสุด 100,000)
    home_loan_interest = min(clean_number(params.get('home_loan_interest', 0)), 100000.0)

    # 8. กองทุน SSF/RMF (สูงสุดไม่เกิน 30% ของเงินได้ และเพดานกลุ่มเกษียณไม่เกิน 500,000)
    raw_ssf = clean_number(params.get('ssf_rmf', 0))
    ssf_by_income = min(raw_ssf, total_income * 0.30, 200000.0)
    retirement_deduct = min(ssf_by_income, 500000.0)

    subtotal_deductions = (personal + spouse_deduct + child_deduct + parent_deduct + 
                           social_sec + life_health_deduct + home_loan_interest + retirement_deduct)

    return subtotal_deductions, life_health_deduct, retirement_deduct

def generate_tax_planning_advice(total_income, net_income, current_life_ins, current_ssf):
    current_tax = compute_tax_from_net(net_income)
    bracket_rate = get_tax_bracket_rate(net_income)
    
    if current_tax == 0.0:
        return "💡 สิทธิประโยชน์: เงินได้สุทธิของคุณได้รับการยกเว้นภาษี จึงยังไม่จำเป็นต้องซื้อสิทธิลดหย่อนเพิ่มเติมครับ"

    remain_life = max(0.0, 100000.0 - current_life_ins)
    max_ssf_allowed = min(total_income * 0.30, 200000.0)
    remain_ssf = max(0.0, max_ssf_allowed - current_ssf)

    advices = []
    if remain_ssf > 0:
        suggest_ssf = min(remain_ssf, 50000.0)
        tax_after_ssf = compute_tax_from_net(max(0.0, net_income - suggest_ssf))
        save_tax = current_tax - tax_after_ssf
        advices.append(f"- กองทุน SSF: ซื้อเพิ่มได้อีก {remain_ssf:,.0f} บ. (หากซื้อ {suggest_ssf:,.0f} บ. จะประหยัดภาษีจริง {save_tax:,.0f} บ.)")

    if remain_life > 0:
        suggest_life = min(remain_life, 30000.0)
        tax_after_life = compute_tax_from_net(max(0.0, net_income - suggest_life))
        save_tax = current_tax - tax_after_life
        advices.append(f"- ประกันชีวิต: ซื้อเพิ่มได้อีก {remain_life:,.0f} บ. (หากซื้อ {suggest_life:,.0f} บ. จะประหยัดภาษีจริง {save_tax:,.0f} บ.)")

    if not advices:
        return "💡 คุณใช้สิทธิลดหย่อนหลักครบเต็มเพดานแล้ว เยี่ยมมากครับ!"

    # ปรับข้อความเพื่อระบุอัตราภาษีส่วนเพิ่มตามขั้นบันไดอย่างชัดเจน
    return f"💡 คำแนะนำการวางแผนภาษี (อัตราภาษีขั้นสูงสุดของคุณอยู่ที่ {bracket_rate*100:.0f}%):\n" + "\n".join(advices)