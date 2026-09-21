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

    title_style = ParagraphStyle(
        'DocTitle',
        fontName=font_family,
        fontSize=20,
        leading=24,
        alignment=1,
        textColor=colors.HexColor('#1E3A8A')
    )
    sub_style = ParagraphStyle(
        'SubTitle',
        fontName=font_family,
        fontSize=11,
        leading=15,
        alignment=1,
        textColor=colors.HexColor('#64748B')
    )
    cell_style = ParagraphStyle(
        'TableCell',
        fontName=font_family,
        fontSize=11,
        leading=15
    )
    cell_bold = ParagraphStyle(
        'TableCellBold',
        fontName=font_family,
        fontSize=11,
        leading=15,
        textColor=colors.HexColor('#1E293B')
    )
    advice_head = ParagraphStyle(
        'AdviceHead',
        fontName=font_family,
        fontSize=13,
        leading=17,
        textColor=colors.HexColor('#1E3A8A')
    )

    story = []

    # หัวรายงาน
    story.append(Paragraph("<b>ใบสรุปผลการประเมินภาษีเงินได้บุคคลธรรมดา</b>", title_style))
    story.append(Spacer(1, 4))
    raw_uid = str(tax_data.get('user_id', 'General User'))
    masked_uid = f"User-{raw_uid[:8]}" if raw_uid != 'General User' else raw_uid
    story.append(Paragraph(f"วันที่ประเมิน: {tax_data.get('date', '')} | รหัสผู้ใช้งาน: {masked_uid}", sub_style))
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
    note_style = ParagraphStyle('NoteText', fontName=font_family, fontSize=9, leading=12, textColor=colors.HexColor('#64748B'))
    story.append(Paragraph("* เอกสารนี้เป็นการประมาณการเบื้องต้นตามประมวลรัษฎากร ไม่สามารถใช้แทนแบบแสดงรายการภาษี ภ.ง.ด.90/91 ได้จริง", note_style))

    doc.build(story)