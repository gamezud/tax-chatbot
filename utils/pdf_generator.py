import os
import re
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

def setup_thai_font():
    """
    ค้นหาและลงทะเบียนฟอนต์ภาษาไทย ลำดับความสำคัญ:
    1. THSarabunNew.ttf ใน static/fonts/ (มาตรฐานเอกสารราชการ)
    2. Tahoma จาก Windows Fonts (fallback)
    """
    font_name = 'ThaiFont'
    custom_font_path = os.path.join('static', 'fonts', 'THSarabunNew.ttf')
    
    if os.path.exists(custom_font_path):
        try:
            pdfmetrics.registerFont(TTFont(font_name, custom_font_path))
            print(f"✅ [PDF] ลงทะเบียนฟอนต์ไทยสำเร็จจาก: {custom_font_path}")
            return font_name
        except Exception as e:
            print(f"⚠️ [PDF] ไม่สามารถโหลด {custom_font_path}: {e}")

    # Fallback to Windows Tahoma
    win_fonts = [
        r"C:\Windows\Fonts\tahoma.ttf",
        r"C:\Windows\Fonts\Tahoma.ttf"
    ]
    for p in win_fonts:
        if os.path.exists(p):
            try:
                pdfmetrics.registerFont(TTFont(font_name, p))
                print(f"✅ [PDF] ใช้งานฟอนต์ไทยจาก Windows: {p}")
                return font_name
            except Exception as e:
                print(f"⚠️ [PDF] โหลด Tahoma ล้มเหลว: {e}")

    print("⚠️ [PDF] ไม่พบฟอนต์ไทย รองรับเฉพาะ Helvetica (อาจแสดงผลภาษาไทยผิดพลาด)")
    return 'Helvetica'

# โหลดระดับโมดูล ครั้งเดียวตอน Start Server
THAI_FONT = setup_thai_font()

# ---------------------------------------------------------------------------
# ตารางรายละเอียดค่าลดหย่อน — ข้อความแสดงผลอย่างเดียว ไม่มีตัวเลขกฎหมาย
# ตัวเลขทุกตัว (ยอด เพดาน อัตรา ปีเกิดบุตร) มาจาก tax_data["deduction_detail"] ที่ handle_deductions ส่งมา
# ลำดับแถวตามลำดับของ items (deduction_breakdown เรียงตามใบแนบรายละเอียดการลดหย่อนของ ภ.ง.ด.90/91)
# ---------------------------------------------------------------------------
DEDUCTION_LABELS = {
    "personal": "ผู้มีเงินได้",
    "spouse": "คู่สมรสที่ไม่มีเงินได้",
    "child_first": "บุตรคนแรก",
    "child_second_onward": "บุตรตั้งแต่คนที่ 2",
    "parents": "อุปการะเลี้ยงดูบิดามารดา",
    "life_insurance": "เบี้ยประกันชีวิต",
    "health_insurance": "เบี้ยประกันสุขภาพ",
    "rmf": "ค่าซื้อหน่วยลงทุนในกองทุนรวมเพื่อการเลี้ยงชีพ (RMF)",
    "home_loan_interest": "ดอกเบี้ยเงินกู้ยืมเพื่อซื้อ เช่าซื้อ หรือสร้างอาคารอยู่อาศัย",
    "social_security": "เงินสมทบกองทุนประกันสังคม",
    "thai_esg": "ค่าซื้อหน่วยลงทุนในกองทุนรวมไทยเพื่อความยั่งยืน (Thai ESG)",
}
# ต่อท้ายชื่อแถวบุตรคนที่ 2 ขึ้นไป เมื่อ data มีปีเกิด (child_second_onward_born_from)
CHILD_BORN_FROM_LABEL = " ที่เกิดในหรือหลังปี พ.ศ. {year}"
# ยอดที่กรอกของแถวเหล่านี้เป็นจำนวนคน
COUNT_ITEMS = {"child_first", "child_second_onward", "parents"}
# หมายเหตุใต้ชื่อรายการเมื่อถูกตัด — key คือ rule ใน limits · {limit} = ยอดเพดาน (บิดามารดาเป็นจำนวนคน) · {percent} = อัตรา × 100
LIMIT_NOTES = {
    "parent_max_count": "นับได้ไม่เกิน {limit} คน",
    "social_security_cap": "ใช้ได้ไม่เกิน {limit:,.2f} บาท (ยอดเงินสมทบสูงสุดทั้งปี)",
    "health_insurance_cap": "ใช้ได้ไม่เกิน {limit:,.2f} บาท",
    "life_health_combined_cap": "รวมกับเบี้ยประกันสุขภาพแล้วใช้ได้ไม่เกิน {limit:,.2f} บาท",
    "home_loan_interest_cap": "ใช้ได้ไม่เกิน {limit:,.2f} บาท",
    "rmf_income_rate": "ใช้ได้ไม่เกิน {percent:g}% ของเงินได้พึงประเมิน ({limit:,.2f} บาท)",
    "rmf_cap": "ใช้ได้ไม่เกิน {limit:,.2f} บาท",
    "retirement_group_cap": "รวมกับกองทุนกลุ่มเพื่อการเกษียณแล้วใช้ได้ไม่เกิน {limit:,.2f} บาท",
    "thai_esg_income_rate": "ใช้ได้ไม่เกิน {percent:g}% ของเงินได้พึงประเมิน ({limit:,.2f} บาท)",
    "thai_esg_cap": "ใช้ได้ไม่เกิน {limit:,.2f} บาท",
    "donation_rate": "ใช้ได้ไม่เกิน {percent:g}% ของเงินได้หลังหักค่าใช้จ่ายและค่าลดหย่อนอื่น ({limit:,.2f} บาท)",
}
LIMIT_NOTE_FALLBACK = "ใช้ได้ไม่เกินเพดานที่กฎหมายกำหนด"
# ข้อสมมติของบอท แสดงเฉพาะข้อที่เกี่ยวกับยอดที่ผู้ใช้กรอก (ห้ามมีชื่อไฟล์ ชื่อตัวแปร หรือภาษาพูด — เทสตรวจ)
ASSUMPTION_CHILDREN = "ถือว่าบุตรตั้งแต่คนที่ 2 ทุกคนเกิดในหรือหลังปี พ.ศ. {year} ถ้าเกิดก่อนปีนั้นจะได้ลดหย่อนน้อยกว่าที่แสดง"
ASSUMPTION_PARENTS = "ไม่ได้ตรวจเงื่อนไขอายุและเงินได้ของบิดามารดา"
ASSUMPTION_SPOUSE = "ถือว่าคู่สมรสไม่มีเงินได้"
ASSUMPTION_SOCIAL_SECURITY = "เงินสมทบกองทุนประกันสังคม: ยอดที่กรอกถือเป็นยอดทั้งปี"
ASSUMPTION_DONATION = ("นับเงินบริจาคทั้งหมดเป็นการบริจาคทั่วไป ไม่รวมประเภทที่หักได้มากกว่ายอดที่จ่าย "
                       "เช่น เพื่อการศึกษา การกีฬา หรือโรงพยาบาลของรัฐ")


def _limit_note(limits):
    # เพดานสองตัวที่ตัดพร้อมกัน (ยอดเท่ากันพอดี) ต่อกันด้วย "และ"
    return " และ ".join(
        LIMIT_NOTES.get(limit["rule"], LIMIT_NOTE_FALLBACK).format(limit=limit["limit"],
                                                                    percent=(limit["rate"] or 0) * 100)
        for limit in limits)


def _entered_text(key, entered):
    if key == "personal":
        return "-"
    if key == "spouse":
        return "มี"
    if key in COUNT_ITEMS:
        return f"{entered} คน"
    return f"{entered:,.2f}"


def _shown(key, item):
    # ส่วนตัวแสดงเสมอ รายการอื่นแสดงเฉพาะที่ผู้ใช้กรอก
    return key == "personal" or bool(item["entered"])


def _assumptions(detail):
    items = detail["items"]
    lines = []
    year = detail.get("child_second_onward_born_from")
    if year and items.get("child_second_onward", {}).get("entered"):
        lines.append(ASSUMPTION_CHILDREN.format(year=year))
    if items.get("parents", {}).get("entered"):
        lines.append(ASSUMPTION_PARENTS)
    if items.get("spouse", {}).get("entered"):
        lines.append(ASSUMPTION_SPOUSE)
    if items.get("social_security", {}).get("entered"):
        lines.append(ASSUMPTION_SOCIAL_SECURITY)
    if detail.get("donation", {}).get("entered"):
        lines.append(ASSUMPTION_DONATION)
    return lines


def _deduction_section(detail, total_deduction, head_style, cell_style, cell_bold, small_style, limit_style):
    """ตาราง "รายละเอียดค่าลดหย่อนและเงินบริจาค" + ข้อสมมติ — แสดงผลอย่างเดียว ไม่บวกหรือคำนวณเลข
    ยอดรวมใช้ subtotal และ total_deduction ที่ส่งมา (ค่าเดียวกับบรรทัด 3 ของตารางหลักและข้อความ LINE)"""
    def label_cell(text, limits):
        cell = [Paragraph(text, cell_style)]
        if limits:
            cell.append(Paragraph(_limit_note(limits), limit_style))
        return cell

    rows = [[Paragraph("<b>รายการ</b>", cell_bold), Paragraph("<b>ยอดที่กรอก (บาท)</b>", cell_bold),
             Paragraph("<b>ใช้สิทธิได้ (บาท)</b>", cell_bold)]]
    year = detail.get("child_second_onward_born_from")
    for key, item in detail["items"].items():
        if not _shown(key, item):
            continue
        label = DEDUCTION_LABELS[key]
        if key == "child_second_onward" and year:
            label += CHILD_BORN_FROM_LABEL.format(year=year)
        rows.append([label_cell(label, item["limits"]),
                     Paragraph(_entered_text(key, item["entered"]), cell_style),
                     Paragraph(f"{item['used']:,.2f}", cell_style)])
    rows.append([Paragraph("<b>รวมค่าลดหย่อน</b>", cell_bold), "",
                 Paragraph(f"<b>{detail['subtotal']:,.2f}</b>", cell_bold)])
    donation = detail.get("donation", {})
    if donation.get("entered"):
        rows.append([label_cell("เงินบริจาค (หักหลังค่าลดหย่อน)", donation["limits"]),
                     Paragraph(f"{donation['entered']:,.2f}", cell_style),
                     Paragraph(f"{donation['used']:,.2f}", cell_style)])
    rows.append([Paragraph("<b>รวมค่าลดหย่อนและเงินบริจาค (เท่ากับข้อ 3 ด้านบน)</b>", cell_bold), "",
                 Paragraph(f"<b>{total_deduction:,.2f}</b>", cell_bold)])

    # ตารางยาวแตกข้ามหน้าได้ repeatRows=1 ให้หัวคอลัมน์ขึ้นซ้ำในหน้าถัดไป
    table = Table(rows, colWidths=[285, 120, 120], repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F1F5F9')),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#CBD5E1')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#F8FAFC')),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))

    flowables = [Paragraph("<b>รายละเอียดค่าลดหย่อนและเงินบริจาค</b>", head_style), Spacer(1, 5), table, Spacer(1, 4),
                 Paragraph("เรียงลำดับและใช้ชื่อรายการตามใบแนบรายละเอียดการลดหย่อนของแบบ ภ.ง.ด.90/91", small_style)]
    assumptions = _assumptions(detail)
    if assumptions:
        flowables += [Spacer(1, 8), Paragraph("<b>ข้อสมมติในการคำนวณ</b>", head_style), Spacer(1, 3)]
        flowables += [Paragraph(f"- {line}", cell_style) for line in assumptions]
    flowables.append(Spacer(1, 14))
    return flowables

def generate_tax_pdf(output_path, tax_data):
    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        rightMargin=35,
        leftMargin=35,
        topMargin=35,
        bottomMargin=35
    )
    
    styles = getSampleStyleSheet()
    font_family = THAI_FONT

    # ทุก ParagraphStyle ตั้ง shaping=1 ให้ ReportLab วางสระ/วรรณยุกต์ไทยตามตาราง GPOS/GSUB ของฟอนต์ (ผ่าน uharfbuzz)
    # ถ้าไม่ตั้ง วรรณยุกต์ที่ไม่มีสระบน (เช่น "ได้", "ค่า") ลอยสูงและเยื้องไปทางขวา
    # เครื่องที่ไม่มี uharfbuzz ReportLab จะกลับไปวางแบบเดิมเงียบ ๆ — test_pdf_shaping ตรวจว่าเปิดอยู่จริง
    title_style = ParagraphStyle(
        'DocTitle',
        fontName=font_family,
        shaping=1,
        fontSize=20,
        leading=24,
        alignment=1,
        textColor=colors.HexColor('#1E3A8A')
    )
    sub_style = ParagraphStyle(
        'SubTitle',
        fontName=font_family,
        shaping=1,
        fontSize=11,
        leading=15,
        alignment=1,
        textColor=colors.HexColor('#64748B')
    )
    cell_style = ParagraphStyle(
        'TableCell',
        fontName=font_family,
        shaping=1,
        fontSize=11,
        leading=15
    )
    cell_bold = ParagraphStyle(
        'TableCellBold',
        fontName=font_family,
        shaping=1,
        fontSize=11,
        leading=15,
        textColor=colors.HexColor('#1E293B')
    )
    advice_head = ParagraphStyle(
        'AdviceHead',
        fontName=font_family,
        shaping=1,
        fontSize=13,
        leading=17,
        textColor=colors.HexColor('#1E3A8A')
    )
    small_style = ParagraphStyle('SmallText', fontName=font_family, shaping=1, fontSize=9, leading=12,
                                 textColor=colors.HexColor('#64748B'))
    limit_style = ParagraphStyle('LimitNote', fontName=font_family, shaping=1, fontSize=9, leading=12,
                                 textColor=colors.HexColor('#B45309'))

    story = []

    # หัวรายงาน
    story.append(Paragraph("<b>ใบสรุปผลการประเมินภาษีเงินได้บุคคลธรรมดา</b>", title_style))
    story.append(Spacer(1, 4))
    # ปีภาษีมาจาก tax_data (handle_deductions อ่าน tax_rules ให้) ไม่มี key นี้ก็ไม่แสดงบรรทัด
    tax_year = tax_data.get('tax_year')
    if tax_year:
        story.append(Paragraph(f"ปีภาษี {tax_year}", sub_style))
    raw_uid = str(tax_data.get('user_id', 'General User'))
    masked_uid = f"User-{raw_uid[:8]}" if raw_uid != 'General User' else raw_uid
    story.append(Paragraph(f"วันที่ประเมิน: {tax_data.get('date', '')} | รหัสผู้ใช้งาน: {masked_uid}", sub_style))
    # หมายเหตุเมื่อใช้ตัวเลขที่ระบบ Admin แก้ — ข้อความมาจาก tax_data (handle_deductions สร้าง ข้อความเดียวกับใน LINE)
    # ไฟล์นี้ไม่อ่าน tax_rules เอง ไม่มี key นี้หรือว่างก็ไม่แสดงบรรทัด
    rules_note = tax_data.get('rules_note')
    if rules_note:
        rules_note_style = ParagraphStyle(
            'RulesNote',
            fontName=font_family,
            shaping=1,
            fontSize=12,
            leading=16,
            alignment=1,
            textColor=colors.HexColor('#B45309')
        )
        story.append(Spacer(1, 4))
        story.append(Paragraph(rules_note, rules_note_style))
    story.append(Spacer(1, 15))

    # ตารางแยกแจงรายได้พึงประเมิน
    income_rows = [
        [Paragraph("<b>รายละเอียดเงินได้พึงประเมิน</b>", cell_bold), Paragraph("<b>จำนวนเงิน (บาท)</b>", cell_bold)],
        [Paragraph("เงินได้ประเภทที่ 1 (เงินเดือนและโบนัส)", cell_style), Paragraph(f"{tax_data.get('salary_total', 0.0):,.2f}", cell_style)],
        [Paragraph("เงินได้ประเภทที่ 5 (ค่าเช่าทรัพย์สิน)", cell_style), Paragraph(f"{tax_data.get('rental_income', 0.0):,.2f}", cell_style)],
        [Paragraph("เงินได้ประเภทที่ 8 (ธุรกิจ/ขายของออนไลน์)", cell_style), Paragraph(f"{tax_data.get('online_income', 0.0):,.2f}", cell_style)]
    ]
    t_income = Table(income_rows, colWidths=[350, 175])
    t_income.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F1F5F9')),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#CBD5E1')),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    story.append(t_income)
    story.append(Spacer(1, 12))

    # ตารางหลักสรุปผลคำนวณภาษี
    main_rows = [
        [Paragraph("<b>รายการคำนวณภาษี</b>", cell_bold), Paragraph("<b>จำนวนเงิน (บาท)</b>", cell_bold)],
        [Paragraph("1. รวมเงินได้พึงประเมินทั้งสิ้น", cell_style), Paragraph(f"{tax_data.get('total_income', 0.0):,.2f}", cell_style)],
        [Paragraph("2. หัก ค่าใช้จ่ายตามกฎหมาย", cell_style), Paragraph(f"{tax_data.get('total_expense', 0.0):,.2f}", cell_style)],
        [Paragraph("3. หัก ค่าลดหย่อนรวม", cell_style), Paragraph(f"{tax_data.get('total_deduction', 0.0):,.2f}", cell_style)],
        [Paragraph("4. เงินได้สุทธิ (Net Taxable Income)", cell_bold), Paragraph(f"<b>{tax_data.get('net_income', 0.0):,.2f}</b>", cell_bold)],
        [Paragraph("5. ภาษีที่คำนวณได้ทั้งสิ้น", cell_style), Paragraph(f"{tax_data.get('tax_payable', 0.0):,.2f}", cell_style)],
        [Paragraph("6. หัก ภาษี ณ ที่จ่ายสะสม", cell_style), Paragraph(f"{tax_data.get('withholding_tax', 0.0):,.2f}", cell_style)],
        [Paragraph("<b>ยอดภาษีสุทธิ (ชำระเพิ่ม / คืนภาษี)</b>", cell_bold), Paragraph(f"<b>{tax_data.get('net_payable', 0.0):,.2f}</b>", cell_bold)]
    ]
    t_main = Table(main_rows, colWidths=[350, 175])
    t_main.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#E2E8F0')),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#94A3B8')),
        ('BACKGROUND', (0, 4), (-1, 4), colors.HexColor('#F8FAFC')),
        ('BACKGROUND', (0, 7), (-1, 7), colors.HexColor('#FEF3C7') if tax_data.get('net_payable', 0) > 0 else colors.HexColor('#DCFCE7')),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    story.append(t_main)
    story.append(Spacer(1, 14))

    # ตารางค่าลดหย่อนทีละรายการ — dict แบบเก่า (ไม่มี deduction_detail) ไม่แสดงส่วนนี้
    detail = tax_data.get('deduction_detail')
    if detail:
        story.extend(_deduction_section(detail, tax_data.get('total_deduction', 0.0),
                                        advice_head, cell_style, cell_bold, small_style, limit_style))

    # ส่วนคำแนะนำวางแผนภาษี
    advice_raw = str(tax_data.get('tax_advice', '')).strip()
    if advice_raw:
        # เก็บเฉพาะอักษรไทย อังกฤษ ตัวเลข ช่องว่าง และเครื่องหมายจำเป็น ป้องกันสี่เหลี่ยมตกค้าง
        advice_clean = re.sub(r'[^\u0E00-\u0E7F\w\s.,()%/:–-]', '', advice_raw)
        
        story.append(Paragraph("<b>คำแนะนำการวางแผนภาษีเพิ่มเติม</b>", advice_head))
        story.append(Spacer(1, 5))
        for line in advice_clean.split('\n'):
            cleaned_line = line.strip()
            if cleaned_line:
                story.append(Paragraph(cleaned_line, cell_style))
                story.append(Spacer(1, 2))
        story.append(Spacer(1, 10))

    # หมายเหตุท้ายเอกสาร
    note_style = ParagraphStyle('NoteText', fontName=font_family, shaping=1, fontSize=9, leading=12, textColor=colors.HexColor('#64748B'))
    story.append(Paragraph("* เอกสารนี้เป็นการประมาณการเบื้องต้นตามประมวลรัษฎากร ไม่สามารถใช้แทนแบบแสดงรายการภาษี ภ.ง.ด.90/91 ได้จริง", note_style))

    doc.build(story)