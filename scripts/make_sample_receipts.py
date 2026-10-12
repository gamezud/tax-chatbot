"""สร้างรูปใบเสร็จจำลองสำหรับวัดความแม่นของ OCR (ก้อน D) พร้อม answer key

รันจาก root ของโปรเจกต์:  python scripts/make_sample_receipts.py [--out ocr_eval] [--seed 2569]
ผลลัพธ์ (โฟลเดอร์ ocr_eval/ อยู่ใน .gitignore — ห้าม commit รูป):
  images/<receipt_id>__<condition>.jpg   รูปที่จะส่งเข้า ocr.read_text
  answer_key.json                        ยอดที่คาดของแต่ละใบ + รายการรูป
  manifest.sha256                        hash ของทุกรูป — รันซ้ำด้วย seed เดิมต้องได้ไฟล์เดียวกัน

เนื้อหาเป็นข้อมูลสมมติทั้งหมด (ชื่อองค์กร ผู้บริจาค ที่อยู่ เบอร์โทร เลขผู้เสียภาษี)
ทุกรูปมีแถบ "ตัวอย่างสำหรับทดสอบระบบ" บนและล่าง อยู่ในขอบที่จองไว้ ไม่ทับบรรทัดยอดเงิน

ขั้นตอนต่อรูป: วาดใบเสร็จ (ด้านยาว 2000px) → ใส่สภาพรูป → จำลองหน้า LIFF (liff_step) → บันทึก
ก่อนเขียนรูป ตรวจว่า parser ให้ผลตามที่คาดบนข้อความที่วาดเอง ถ้าไม่ตรง exit 1
(แยก "แม่แบบผิด/parser ตัดสินผิด" ออกจาก "OCR อ่านผิด")

ข้อจำกัด: Pillow ในเครื่องนี้ไม่มี raqm (PIL.features.check('raqm') เป็น False) จึงจัดตำแหน่งอักษรไทยแบบพื้นฐาน
วรรณยุกต์ที่ซ้อนบนสระบนวาดผิด — Tahoma วรรณยุกต์หาย ("เลขที่" เป็น "เลขที") TH Sarabun วรรณยุกต์เยื้องไปทางขวา
บรรทัดยอดเงิน (จำนวนเงิน/บาท/Amount/THB) และคำบริจาค (บริจาค/อนุโมทนา/e-Donation) ไม่มีวรรณยุกต์ซ้อนจึงไม่โดน
แต่ข้อความประกอบโดน รูปจึงอ่านยากกว่าใบเสร็จจริงในส่วนนี้ — source_lines ใน answer key คือข้อความที่ตั้งใจวาด
ไม่ใช่สิ่งที่ปรากฏในรูปทุกตัวอักษร
"""
import argparse
import hashlib
import io
import json
import random
import sys
from decimal import Decimal
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
# ให้ import utils ได้เมื่อรันเป็น python scripts/make_sample_receipts.py (sys.path[0] คือ scripts/)
sys.path.insert(0, str(ROOT))
from utils.receipt_parser import extract_donation_amount, has_donation_keyword  # noqa: E402

DEFAULT_SEED = 2569

# ---------------------------------------------------------------------------
# ฟอนต์: THSarabunNew ของโปรเจกต์ + ฟอนต์ที่สองจากเครื่อง (ถ้ามี) ใช้กับบางแม่แบบ
# ขนาดต่างกันเพราะ TH Sarabun ตัวเล็กกว่าฟอนต์อื่นที่ขนาด px เดียวกันมาก
# ---------------------------------------------------------------------------
PRIMARY_FONT = ("THSarabunNew", ROOT / "static" / "fonts" / "THSarabunNew.ttf", 64)
SECOND_FONT_CANDIDATES = [
    ("Tahoma", Path(r"C:\Windows\Fonts\tahoma.ttf"), 42),
    ("Leelawadee UI", Path(r"C:\Windows\Fonts\LeelawUI.ttf"), 46),
]
SECOND_FONT_TEMPLATES = {"D1_comma", "D4_thai_digits", "D6_distractors", "D8_edonation", "N1_insurance"}

# ---------------------------------------------------------------------------
# หน้ากระดาษ: แนวตั้ง ด้านยาว 2000px ใกล้ขนาดที่เซิร์ฟเวอร์ได้รับจากหน้า LIFF
# ---------------------------------------------------------------------------
PAGE_SIZE = (1414, 2000)
MARGIN_X = 90
BANNER_TEXT = "ตัวอย่างสำหรับทดสอบระบบ"
BANNER_HEIGHT = 110
BANNER_FILL = 230
LINE_SPACING = 1.6
TITLE_SCALE = 1.25

# ---------------------------------------------------------------------------
# จำลองหน้า LIFF (templates/liff_donation.html): ย่อด้านยาวไม่เกิน MAX_SIDE วาดบนพื้นขาว
# แล้ว canvas.toBlob("image/jpeg", 0.9) — ใช้ค่าเดียวกับในไฟล์นั้น ถ้าหน้า LIFF เปลี่ยนต้องแก้ตาม
# ข้อต่าง: เบราว์เซอร์ย่อรูปด้วยวิธีของตัวเอง ที่นี่ใช้ bilinear ของ Pillow แทน
# ---------------------------------------------------------------------------
LIFF_MAX_SIDE = 2000
LIFF_JPEG_QUALITY = 90

# ---------------------------------------------------------------------------
# ข้อมูลสมมติ
# ---------------------------------------------------------------------------
DONATION_TITLES = ["ใบเสร็จรับเงินบริจาค", "ใบอนุโมทนาบัตร", "หลักฐานการรับเงิน e-Donation"]
RECIPIENTS = ["มูลนิธิสมมติเพื่อการทดสอบ", "วัดตัวอย่าง (สมมติ)"]
ADDRESS = "99 ถนนสมมติ แขวงทดสอบ เขตตัวอย่าง"
DONOR = "นายทดสอบ ระบบ"
PHONE = "02-000-0000"
THAI_MONTHS = ["มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
               "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"]

# ยอดแบ่งชั้นตามจำนวนหลัก ให้ครอบกรณี OCR อ่านหลักหาย (15,000 → 5,000)
AMOUNT_RANGES = [(100, 9_999), (10_000, 99_999), (100_000, 500_000)]

_THAI_DIGITS = str.maketrans("0123456789", "๐๑๒๓๔๕๖๗๘๙")
_WORD_DIGITS = ["", "หนึ่ง", "สอง", "สาม", "สี่", "ห้า", "หก", "เจ็ด", "แปด", "เก้า"]
_WORD_UNITS = ["", "สิบ", "ร้อย", "พัน", "หมื่น", "แสน"]


def thai_words(n):
    """จำนวนเต็ม 1–999,999 → คำอ่านภาษาไทย (ไม่มี "บาทถ้วน")"""
    digits = str(n)
    out = []
    for i, ch in enumerate(digits):
        d, pos = int(ch), len(digits) - i - 1
        if d == 0:
            continue
        if pos == 1 and d == 1:
            out.append("สิบ")
        elif pos == 1 and d == 2:
            out.append("ยี่สิบ")
        elif pos == 0 and d == 1 and len(digits) > 1:
            out.append("เอ็ด")
        else:
            out.append(_WORD_DIGITS[d] + _WORD_UNITS[pos])
    return "".join(out)


def canonical(amount):
    """Decimal → ข้อความใน answer key ("5000", "1250.50")"""
    return str(int(amount)) if amount == amount.to_integral_value() else f"{amount:.2f}"


# ---------------------------------------------------------------------------
# แม่แบบ: คืน (lines, expected_status, expected_amount, keyword)
# lines คือ list ของ (ข้อความ, ชนิด) ชนิด = "title" | "body" | "amount"
# ---------------------------------------------------------------------------
def donation_header(title, recipient):
    return [(title, "title"), (recipient, "body"), (ADDRESS, "body"), (f"ได้รับเงินจาก {DONOR}", "body")]


def build_receipt(template, template_index, amount_index, rng):
    low, high = AMOUNT_RANGES[amount_index]
    amount = rng.randint(low, high)
    comma = f"{amount:,}"
    title = DONATION_TITLES[(template_index + amount_index) % len(DONATION_TITLES)]
    recipient = RECIPIENTS[amount_index % len(RECIPIENTS)]

    if template == "D1_comma":
        lines = donation_header(title, recipient) + [(f"จำนวนเงิน {comma} บาท", "amount")]
    elif template == "D2_decimal":
        lines = donation_header(title, recipient) + [(f"จำนวนเงิน {amount:,.2f} บาท", "amount")]
    elif template == "D3_nocomma":
        lines = donation_header(title, recipient) + [(f"จำนวนเงิน {amount} บาท", "amount")]
    elif template == "D4_thai_digits":
        lines = donation_header(title, recipient) + [(f"จำนวนเงิน {comma.translate(_THAI_DIGITS)} บาท", "amount")]
    elif template == "D5_words":
        lines = donation_header(title, recipient) + [
            (f"จำนวนเงิน {comma} บาท", "amount"),
            (f"({thai_words(amount)}บาทถ้วน)", "amount"),
        ]
    elif template == "D6_distractors":
        tax_id = f"0-0000-{rng.randint(0, 99999):05d}-{rng.randint(0, 99):02d}-{rng.randint(0, 9)}"
        date = f"{rng.randint(1, 28)} {rng.choice(THAI_MONTHS)} 2569"
        lines = donation_header(title, recipient)[:3] + [
            (f"โทร {PHONE}", "body"),
            (f"เลขประจำตัวผู้เสียภาษี {tax_id}", "body"),
            (f"เลขที่ใบเสร็จ 2569/{rng.randint(1, 99999):05d}", "body"),
            (f"ได้รับเงินจาก {DONOR}", "body"),
            (f"วันที่ {date} จำนวนเงิน {comma} บาท", "amount"),
        ]
    elif template == "D7_same_twice":
        lines = donation_header(title, recipient) + [
            (f"จำนวนเงิน {comma} บาท", "amount"),
            (f"รวมทั้งสิ้น {amount:,.2f} บาท", "amount"),
        ]
    elif template == "D8_edonation":
        title = DONATION_TITLES[2]
        amount = Decimal(amount) + Decimal(rng.randint(1, 99)) / 100
        lines = [
            (title, "title"),
            ("e-Donation Receipt", "body"),
            (recipient, "body"),
            (f"Ref. ED2569{rng.randint(0, 999999):06d}", "body"),
            (f"Donor: {DONOR}", "body"),
            (f"Amount {amount:,.2f} THB", "amount"),
        ]
    elif template == "A1_two_amounts":
        total = amount + rng.randint(1, 9) * 100
        lines = donation_header(title, recipient) + [
            (f"จำนวนเงิน {comma} บาท", "amount"),
            (f"รวม {total:,} บาท", "amount"),
        ]
        return lines, "ambiguous", None, title
    elif template == "N1_insurance":
        lines = [
            ("ใบเสร็จรับเงิน", "title"),
            ("บริษัท ประกันชีวิตสมมติ จำกัด", "body"),
            (ADDRESS, "body"),
            (f"ผู้เอาประกัน {DONOR}", "body"),
            ("ค่าเบี้ยประกันชีวิต", "body"),
            (f"จำนวนเงิน {comma} บาท", "amount"),
        ]
        return lines, "not_donation", None, None
    elif template == "N2_service_fee":
        lines = [
            ("ใบเสร็จรับเงิน", "title"),
            (RECIPIENTS[0], "body"),
            (ADDRESS, "body"),
            (f"ได้รับเงินจาก {DONOR}", "body"),
            ("ค่าลงทะเบียนอบรม", "body"),
            (f"จำนวนเงิน {comma} บาท", "amount"),
        ]
        return lines, "not_donation", None, None
    else:
        raise ValueError(template)
    return lines, "ok", Decimal(amount), title


TEMPLATES = [
    "D1_comma", "D2_decimal", "D3_nocomma", "D4_thai_digits", "D5_words",
    "D6_distractors", "D7_same_twice", "D8_edonation",
    "A1_two_amounts", "N1_insurance", "N2_service_fee",
    # แม่แบบที่เพิ่มทีหลังต่อท้ายเสมอ — ลำดับใน list กำหนดหัวเรื่องของใบ ถ้าแทรกกลาง ใบเดิมจะเปลี่ยน
    "D4b_thai_digits_sarabun",
]

# แม่แบบที่ใช้เนื้อหาและสภาพรูปชุดเดียวกับอีกแม่แบบ (rng เดียวกัน) ต่างกันแค่ฟอนต์ — เทียบฟอนต์แบบคู่ต่อคู่
SAME_CONTENT_AS = {"D4b_thai_digits_sarabun": "D4_thai_digits"}

# แม่แบบที่ parser ออกแบบให้ตอบต่างจากยอดจริงของใบ: ยอดเลขไทย → thai_digits (ไม่เชื่อเลขไทย ให้ผู้ใช้พิมพ์เอง)
# expected_status ยังเป็น ok ตามยอดจริง ส่วนขั้นตรวจตัวเองเทียบกับ parser_expected_status
PARSER_EXPECTED_OVERRIDE = {"D4_thai_digits": "thai_digits"}


# ---------------------------------------------------------------------------
# วาดรูป
# ---------------------------------------------------------------------------
def render(lines, font_path, body_size):
    """วาดใบเสร็จบนพื้นขาว คืนรูปเทา (L) — exit 1 ถ้าบรรทัดล้นหรือแถบทับเนื้อหา"""
    width, height = PAGE_SIZE
    img = Image.new("L", PAGE_SIZE, 255)
    draw = ImageDraw.Draw(img)
    body = ImageFont.truetype(str(font_path), body_size)
    title = ImageFont.truetype(str(font_path), int(body_size * TITLE_SCALE))
    banner = ImageFont.truetype(str(font_path), int(body_size * 0.8))
    line_height = int(body_size * LINE_SPACING)

    # แถบบนและล่าง
    top_band = (0, 0, width, BANNER_HEIGHT)
    bottom_band = (0, height - BANNER_HEIGHT, width, height)
    for band in (top_band, bottom_band):
        draw.rectangle(band, fill=BANNER_FILL)
        cy = (band[1] + band[3]) // 2
        draw.text((width // 2, cy), BANNER_TEXT, font=banner, fill=0, anchor="mm")

    # เนื้อหาเริ่มหลังแถบบนเว้นหนึ่งบรรทัดครึ่ง
    y = BANNER_HEIGHT + int(line_height * 1.5)
    for text, kind in lines:
        font = title if kind == "title" else body
        box = draw.textbbox((MARGIN_X, y), text, font=font)
        if box[2] > width - MARGIN_X:
            sys.exit(f"❌ บรรทัดล้นขอบขวา ({box[2]}px): {text}")
        # บรรทัดยอดเงินต้องห่างแถบอย่างน้อยหนึ่งบรรทัด บรรทัดอื่นแค่ห้ามทับ
        gap = line_height if kind == "amount" else 0
        if box[1] < top_band[3] + gap or box[3] > bottom_band[1] - gap:
            sys.exit(f"❌ แถบ \"{BANNER_TEXT}\" ทับหรือชิดบรรทัด: {text}")
        draw.text((MARGIN_X, y), text, font=font, fill=0)
        y += int(line_height * TITLE_SCALE) if kind == "title" else line_height
    return img


# ---------------------------------------------------------------------------
# สภาพรูป — ทุกฟังก์ชันรับรูปเทา คืนรูปเทา ใช้ rng ของรูปนั้นเท่านั้น
# ---------------------------------------------------------------------------
def _blur(img, radius):
    return img.filter(ImageFilter.GaussianBlur(radius))


def _noise(img, sigma, nrng):
    arr = np.asarray(img, dtype=np.float64) + nrng.normal(0, sigma, (img.height, img.width))
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), mode="L")


def _rotate(img, rng):
    angle = rng.choice([-1, 1]) * rng.uniform(2.0, 3.0)
    return img.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=255)


def _jpeg(img, quality):
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return Image.open(io.BytesIO(buf.getvalue())).convert("L")


def _downscale(img):
    # จำลองถ่ายไกล — ทำก่อน liff_step ซึ่งไม่ขยายรูปกลับ
    return img.resize((img.width // 2, img.height // 2), Image.LANCZOS)


CONDITIONS = {
    "clean": lambda img, rng, nrng: img,
    "blur": lambda img, rng, nrng: _blur(img, 1.0),
    "noise": lambda img, rng, nrng: _noise(img, 12, nrng),
    "rotate": lambda img, rng, nrng: _rotate(img, rng),
    "jpeg": lambda img, rng, nrng: _jpeg(img, 25),
    "downscale": lambda img, rng, nrng: _downscale(img),
    "combined": lambda img, rng, nrng: _jpeg(_rotate(_noise(_blur(img, 0.6), 8, nrng), rng), 40),
}


def liff_step(img):
    """จำลองหน้า LIFF: ย่อด้านยาวไม่เกิน LIFF_MAX_SIDE (ไม่ขยาย) บนพื้นขาว แล้ว JPEG คุณภาพ LIFF_JPEG_QUALITY คืน bytes"""
    img = img.convert("RGB")   # canvas ของเบราว์เซอร์เป็น RGB เสมอ
    scale = min(1, LIFF_MAX_SIDE / max(img.size))
    if scale < 1:
        img = img.resize((round(img.width * scale), round(img.height * scale)), Image.BILINEAR)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=LIFF_JPEG_QUALITY)
    return buf.getvalue()


def numpy_rng(key):
    # default_rng ต้องการจำนวนเต็ม — แปลงข้อความ key เป็นจำนวนเต็มแบบคงที่ (hash() ของ Python สุ่มทุกครั้งที่รัน)
    return np.random.default_rng(int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:8], "big"))


# ---------------------------------------------------------------------------
# ตรวจตัวเอง: ลำดับเดียวกับ read_donation_amount() ใน app.py
# ---------------------------------------------------------------------------
def parser_result(source_lines):
    text = "\n".join(source_lines)
    if not has_donation_keyword(text):
        return "not_donation", None
    result = extract_donation_amount(text)
    return result["status"], result["amount"]


def pick_second_font():
    for name, path, size in SECOND_FONT_CANDIDATES:
        if path.is_file():
            return name, path, size
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="ocr_eval", help="โฟลเดอร์ผลลัพธ์ (ต้องอยู่ใน .gitignore)")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()

    if not PRIMARY_FONT[1].is_file():
        sys.exit(f"❌ ไม่พบฟอนต์ {PRIMARY_FONT[1]} — ต้องรันจาก root ของโปรเจกต์")
    second = pick_second_font()
    if second is None:
        print("ℹ️ ไม่พบ Tahoma / Leelawadee UI ในเครื่อง — ทุกแม่แบบใช้ THSarabunNew")

    # 1. สร้างเนื้อหาทุกใบ + ตรวจตัวเองก่อนเขียนรูป
    receipts = {}
    mismatches = []
    for template in TEMPLATES:
        source = SAME_CONTENT_AS.get(template, template)
        for a_index in range(len(AMOUNT_RANGES)):
            receipt_id = f"{template}_{a_index + 1}"
            content_id = f"{source}_{a_index + 1}"
            rng = random.Random(f"{args.seed}:{content_id}")
            lines, status, amount, keyword = build_receipt(source, TEMPLATES.index(source), a_index, rng)
            font = second if (second and template in SECOND_FONT_TEMPLATES) else PRIMARY_FONT
            source_lines = [BANNER_TEXT] + [text for text, _ in lines] + [BANNER_TEXT]
            parser_status = PARSER_EXPECTED_OVERRIDE.get(source, status)
            parser_amount = amount if parser_status == "ok" else None
            got_status, got_amount = parser_result(source_lines)
            if got_status != parser_status or got_amount != parser_amount:
                mismatches.append(f"{receipt_id}: คาด {parser_status} {parser_amount} ได้ {got_status} {got_amount}")
            receipts[receipt_id] = {
                "template": template,
                "amount_index": a_index,
                "font": font[0],
                "title": keyword,
                "expected_status": status,
                "expected_amount": canonical(amount) if amount is not None else None,
                "parser_expected_status": parser_status,
                "same_content_as": content_id if content_id != receipt_id else None,
                "source_lines": source_lines,
                "_content_id": content_id,
                "_lines": lines,
                "_font": font,
            }
    if mismatches:
        print("❌ parser ให้ผลไม่ตรงกับที่คาดบนข้อความต้นฉบับ (แม่แบบผิด หรือ parser มีบั๊ก — รายงาน ห้ามแก้ parser ในก้อนนี้):")
        for line in mismatches:
            print(f"   {line}")
        sys.exit(1)
    print(f"✅ ตรวจตัวเอง: parser บนข้อความต้นฉบับตรงกับที่คาด {len(receipts)}/{len(receipts)} ใบ")

    # 2. วาด ใส่สภาพรูป จำลองหน้า LIFF แล้วบันทึก
    out_dir = Path(args.out)
    image_dir = out_dir / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    for old in list(image_dir.glob("*.jpg")) + list(image_dir.glob("*.png")):
        old.unlink()   # ลบเฉพาะรูปที่สคริปต์นี้สร้างไว้รอบก่อน กันรูปของแม่แบบที่เลิกใช้ค้าง

    images = []
    manifest = []
    for receipt_id, receipt in receipts.items():
        _, font_path, body_size = receipt["_font"]
        base = render(receipt["_lines"], font_path, body_size)
        for condition, apply in CONDITIONS.items():
            key = f"{args.seed}:{receipt['_content_id']}:{condition}"
            img = apply(base, random.Random(key), numpy_rng(key))
            data = liff_step(img)
            name = f"{receipt_id}__{condition}.jpg"
            (image_dir / name).write_bytes(data)
            images.append({"file": f"images/{name}", "receipt_id": receipt_id, "condition": condition})
            manifest.append(f"{hashlib.sha256(data).hexdigest()}  images/{name}")

    fonts_used = {receipt["_font"][0]: receipt["_font"][1].name for receipt in receipts.values()}
    for receipt in receipts.values():
        del receipt["_content_id"], receipt["_lines"], receipt["_font"]
    answer_key = {
        "generator": "scripts/make_sample_receipts.py",
        "seed": args.seed,
        "liff_step": {
            "source": "templates/liff_donation.html",
            "max_side": LIFF_MAX_SIDE,
            "jpeg_quality": LIFF_JPEG_QUALITY,
            "background": "white",
        },
        "page_size": list(PAGE_SIZE),
        "fonts": fonts_used,
        "conditions": list(CONDITIONS),
        "receipts": receipts,
        "images": images,
    }
    (out_dir / "answer_key.json").write_text(json.dumps(answer_key, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "manifest.sha256").write_text("\n".join(manifest) + "\n", encoding="utf-8")

    print(f"📁 {out_dir}: {len(receipts)} ใบ × {len(CONDITIONS)} สภาพ = {len(images)} รูป")
    print(f"   ฟอนต์: {', '.join(fonts_used)}")


if __name__ == "__main__":
    main()
