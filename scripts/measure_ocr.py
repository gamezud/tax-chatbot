"""วัดความแม่นของ pipeline อ่านใบเสร็จ (ocr.read_text → has_donation_keyword → extract_donation_amount)

รันจาก root ของโปรเจกต์ หลังรัน scripts/make_sample_receipts.py:
  python scripts/measure_ocr.py                       ชุดสังเคราะห์ใน ocr_eval/
  python scripts/measure_ocr.py --limit 7             ลองไม่กี่รูปก่อน (ดูเวลา)
  python scripts/measure_ocr.py --condition rotate    เฉพาะสภาพเดียว
ชุดภาพถ่ายจริง (พิมพ์ใบต้นฉบับแล้วถ่าย) ตั้งชื่อไฟล์ <receipt_id>.jpg หรือ <receipt_id>__<ป้าย>.jpg แล้ว:
  python scripts/measure_ocr.py --dir photos --key ocr_eval/answer_key.json --liff-step
  (--liff-step: หมุนตาม EXIF + ย่อ + JPEG 90 แบบหน้า LIFF ก่อนอ่าน เพราะภาพจากกล้องยังไม่ผ่านหน้า LIFF)

เขียน results.csv และ report.md ลงโฟลเดอร์ --dir
ไม่ print/เขียนข้อความที่ OCR อ่านได้ — ผลมีแค่สถานะ ยอดที่คาด ยอดที่ได้ และเวลา (ข้อมูลสมมติ)
exit 1 ถ้ามี confident wrong (ได้ ok แต่ผิด)
"""
import argparse
import csv
import io
import json
import math
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from PIL import Image, ImageOps, features

ROOT = Path(__file__).resolve().parents[1]
# ให้ import utils ได้เมื่อรันเป็น python scripts/measure_ocr.py (sys.path[0] คือ scripts/)
sys.path.insert(0, str(ROOT))
from utils import ocr  # noqa: E402
from utils.receipt_parser import extract_donation_amount, has_donation_keyword  # noqa: E402
from make_sample_receipts import liff_step  # noqa: E402  (อยู่ใน scripts/ โฟลเดอร์เดียวกัน)

EXPECTED_STATUSES = ["ok", "ambiguous", "not_donation"]
GOT_STATUSES = ["ok", "not_found", "ambiguous", "not_donation", "error"]
OUTCOMES = ["correct", "confident_wrong", "safe_miss", "false_not_donation", "error"]
OUTCOME_LABELS = {
    "correct": "ถูก",
    "confident_wrong": "**ok แต่ผิด**",
    "safe_miss": "ไม่ได้ยอด (พิมพ์เอง)",
    "false_not_donation": "ใบบริจาคได้ not_donation",
    "error": "error",
}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
CSV_FIELDS = ["file", "receipt_id", "template", "font", "condition", "expected_status", "expected_amount",
              "got_status", "got_amount", "outcome", "ocr_seconds", "total_seconds"]


def canonical(amount):
    """Decimal → ข้อความแบบเดียวกับ answer key ("5000", "1250.50")"""
    return str(int(amount)) if amount == amount.to_integral_value() else f"{amount:.2f}"


def run_pipeline(image_bytes):
    """คืน (status, amount, ocr_seconds, total_seconds)

    ลำดับตัดสินลอกมาจาก read_donation_amount() ใน app.py — ถ้า route เปลี่ยนต้องแก้ตรงนี้ตาม
    ข้อความที่อ่านได้อยู่ในฟังก์ชันนี้เท่านั้น ไม่คืนออกไป
    """
    start = time.perf_counter()
    try:
        text = ocr.read_text(image_bytes)
    except (ocr.OcrUnavailableError, ocr.InvalidImageError, ocr.ImageTooLargeError, ocr.OcrFailedError) as exc:
        elapsed = time.perf_counter() - start
        return f"error:{type(exc).__name__}", None, elapsed, elapsed
    ocr_done = time.perf_counter()
    if not has_donation_keyword(text):
        status, amount = "not_donation", None
    else:
        result = extract_donation_amount(text)
        status, amount = result["status"], result["amount"]
    end = time.perf_counter()
    return status, amount, ocr_done - start, end - start


def classify(expected_status, expected_amount, got_status, got_amount):
    if got_status.startswith("error"):
        return "error"
    if got_status == expected_status and (got_status != "ok" or got_amount == Decimal(expected_amount)):
        return "correct"
    if got_status == "ok":
        return "confident_wrong"
    if got_status == "not_donation":
        return "false_not_donation"
    return "safe_miss"


def list_images(image_dir, key, key_path, condition):
    """คืน list ของ (path, receipt_id, condition)

    key อยู่ในโฟลเดอร์ --dir (ชุดสังเคราะห์) → ใช้รายการรูปใน key
    key อยู่ที่อื่น (ชุดภาพถ่าย) → หาไฟล์รูปใน --dir แล้วจับคู่ใบด้วยชื่อไฟล์
    """
    if key_path.parent.resolve() == image_dir.resolve():
        items = [(image_dir / img["file"], img["receipt_id"], img["condition"]) for img in key["images"]]
    else:
        items, unknown = [], []
        for path in sorted(p for p in image_dir.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES):
            receipt_id, _, label = path.stem.partition("__")
            if receipt_id in key["receipts"]:
                items.append((path, receipt_id, label or "photo"))
            else:
                unknown.append(path.name)
        if unknown:
            print(f"ℹ️ ข้าม {len(unknown)} ไฟล์ที่ชื่อไม่ตรงกับ receipt_id ใน answer key: {', '.join(unknown[:5])}")
    if condition:
        items = [item for item in items if item[2] == condition]
    return items


def load_bytes(path, apply_liff_step):
    data = path.read_bytes()
    if not apply_liff_step:
        return data
    # หน้า LIFF วาดรูปตามทิศทางที่เบราว์เซอร์แสดง (หมุนตาม EXIF แล้ว) ก่อนย่อ
    with Image.open(io.BytesIO(data)) as img:
        return liff_step(ImageOps.exif_transpose(img))


def percentile(values, p):
    """nearest-rank: ค่าที่อันดับ ceil(p/100 × n) ของข้อมูลที่เรียงแล้ว"""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(1, math.ceil(p / 100 * len(ordered))) - 1]


def fmt_seconds(value):
    return "-" if value is None else f"{value:.2f}"


def fmt_ratio(k, n):
    return f"{k}/{n} ({100 * k / n:.1f}%)" if n else "-"


def tesseract_version():
    cmd = ocr.find_tesseract()
    try:
        result = subprocess.run([cmd, "--version"], capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired, TypeError):
        return "ไม่ทราบ"
    lines = (result.stdout or result.stderr).decode("utf-8", errors="replace").splitlines()
    return lines[0].strip() if lines else "ไม่ทราบ"


def git_commit():
    try:
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT)
        dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, cwd=ROOT)
    except OSError:
        return "ไม่ทราบ"
    return head.stdout.strip() + (" (มีไฟล์ที่ยังไม่ commit)" if dirty.stdout.strip() else "")


def breakdown_table(rows, field, title):
    groups = list(dict.fromkeys(row[field] for row in rows))
    lines = [f"### {title}", "",
             "| " + field + " | n | " + " | ".join(OUTCOME_LABELS[o] for o in OUTCOMES) + " |",
             "|---|---|" + "---|" * len(OUTCOMES)]
    for group in groups:
        subset = [row for row in rows if row[field] == group]
        counts = Counter(row["outcome"] for row in subset)
        lines.append(f"| {group} | {len(subset)} | " + " | ".join(str(counts[o]) for o in OUTCOMES) + " |")
    return lines + [""]


def write_report(path, rows, header):
    n = len(rows)
    counts = Counter(row["outcome"] for row in rows)
    wrong = [row for row in rows if row["outcome"] == "confident_wrong"]
    out = ["# ผลวัดความแม่น OCR ใบเสร็จบริจาค", ""]
    out += [f"- {label}: {value}" for label, value in header] + [""]

    # 1. ตัวชี้วัดหลัก
    out += [f"## ⚠️ confident wrong (ได้ ok แต่ผิด): {len(wrong)} รูป", ""]
    if wrong:
        out += ["| รูป | สภาพ | สถานะที่คาด | ยอดที่คาด | ยอดที่ได้ |", "|---|---|---|---|---|"]
        out += [f"| {r['file']} | {r['condition']} | {r['expected_status']} | {r['expected_amount'] or '-'} | {r['got_amount']} |"
                for r in wrong]
    else:
        out += ["ไม่มีรูปที่ได้สถานะ ok พร้อมยอดผิด หรือได้ ok ทั้งที่ควรเป็น ambiguous / not_donation"]
    out += [""]

    # 2. expected × got
    out += ["## สถานะที่คาด × สถานะที่ได้", "",
            "(ช่อง ok นับเฉพาะสถานะ — ยอดผิดอยู่ในหัวข้อ confident wrong)", "",
            "| คาด \\ ได้ | " + " | ".join(GOT_STATUSES) + " | รวม |",
            "|---|" + "---|" * (len(GOT_STATUSES) + 1)]
    for expected in EXPECTED_STATUSES:
        subset = [r for r in rows if r["expected_status"] == expected]
        if not subset:
            continue
        got = Counter("error" if r["got_status"].startswith("error") else r["got_status"] for r in subset)
        out.append(f"| {expected} | " + " | ".join(str(got[s]) for s in GOT_STATUSES) + f" | {len(subset)} |")
    out += [""]

    # 3. สรุป
    expect_ok = [r for r in rows if r["expected_status"] == "ok"]
    expect_nd = [r for r in rows if r["expected_status"] == "not_donation"]
    donation = [r for r in rows if r["expected_status"] != "not_donation"]
    out += ["## สรุป", "",
            f"- ถูกทั้งหมด: {fmt_ratio(counts['correct'], n)}",
            f"- ใบที่ควรได้ ok ได้ ok พร้อมยอดถูก: {fmt_ratio(sum(r['outcome'] == 'correct' for r in expect_ok), len(expect_ok))}",
            f"- ใบที่ไม่ใช่การบริจาค (ประกัน/ค่าบริการ) ได้ not_donation: "
            f"{fmt_ratio(sum(r['got_status'] == 'not_donation' for r in expect_nd), len(expect_nd))}",
            f"- ใบบริจาคที่ถูกตีเป็น not_donation (OCR อ่านคำบริจาคไม่ได้): "
            f"{fmt_ratio(sum(r['outcome'] == 'false_not_donation' for r in donation), len(donation))}",
            f"- ไม่ได้ยอด ผู้ใช้ต้องพิมพ์เอง (not_found / ambiguous ที่ไม่ควรได้): {fmt_ratio(counts['safe_miss'], n)}",
            f"- error: {fmt_ratio(counts['error'], n)}",
            ""]

    # 4. แยกตามสภาพ / แม่แบบ / ฟอนต์
    out += ["## แยกกลุ่ม", ""]
    out += breakdown_table(rows, "condition", "แยกตามสภาพรูป")
    out += breakdown_table(rows, "template", "แยกตามแม่แบบ")
    out += breakdown_table(rows, "font", "แยกตามฟอนต์")

    # 5. เวลา
    out += ["## เวลาต่อรูป (วินาที, nearest-rank)", "",
            "read_text = OpenCV + Tesseract · ทั้งหมด = read_text + ตรวจคำบริจาค + หายอด (ไม่รวมอ่านไฟล์)", "",
            "| กลุ่ม | n | read_text p50 | read_text p95 | ทั้งหมด p50 | ทั้งหมด p95 |", "|---|---|---|---|---|---|"]
    groups = [("ทั้งหมด", rows)] + [(c, [r for r in rows if r["condition"] == c])
                                     for c in dict.fromkeys(r["condition"] for r in rows)]
    for name, subset in groups:
        ocr_t = [r["ocr_seconds"] for r in subset]
        total_t = [r["total_seconds"] for r in subset]
        out.append(f"| {name} | {len(subset)} | {fmt_seconds(percentile(ocr_t, 50))} | {fmt_seconds(percentile(ocr_t, 95))} "
                   f"| {fmt_seconds(percentile(total_t, 50))} | {fmt_seconds(percentile(total_t, 95))} |")
    out += [""]

    # 6. baseline
    out += ["## baseline: parser บนข้อความต้นฉบับ", "",
            "make_sample_receipts.py รัน parser บนข้อความที่วาดลงรูป (source_lines) ก่อนเขียนรูปทุกครั้ง "
            "และหยุด (exit 1) ถ้าผลไม่ตรงกับ answer key — ทุกใบในชุดนี้จึงได้ผลถูกเมื่ออ่านข้อความได้ครบ "
            "ผลที่ผิดในรายงานนี้จึงมาจากการอ่านรูป (OCR) ไม่ใช่จากแม่แบบ", ""]

    # 7. ข้อจำกัด
    out += ["## ข้อจำกัด", "",
            "- รูปสังเคราะห์ ไม่ใช่ภาพถ่ายจริง: พื้นขาวเรียบ ไม่มีแสงเงา มุมกล้อง รอยพับ หรือกระดาษยับ "
            "ผลที่ได้จึงดีกว่าที่จะเกิดกับรูปจริง",
            "- เลย์เอาต์โล่ง: บรรทัดเดียวต่อข้อมูล ไม่มีโลโก้ ตาราง หรือตราประทับ",
            "- Pillow ไม่มี raqm: วรรณยุกต์ที่ซ้อนบนสระบนวาดผิด (Tahoma วรรณยุกต์หาย, TH Sarabun เยื้อง) "
            "บรรทัดยอดเงินและคำบริจาคไม่โดน แต่ข้อความประกอบโดน รูปจึงอ่านยากกว่าใบจริงในส่วนนี้",
            "- ฟอนต์สองแบบ (THSarabunNew และฟอนต์ที่สองตามหัวรายงาน) ใบเสร็จจริงใช้ฟอนต์หลากหลายกว่านี้",
            "- ขั้นจำลองหน้า LIFF ย่อรูปด้วย bilinear ของ Pillow ส่วนเบราว์เซอร์ย่อด้วยวิธีของตัวเอง",
            "- จำนวนต่อกลุ่มน้อย (3 ยอดต่อแม่แบบ) เปอร์เซ็นต์ของกลุ่มย่อยจึงแกว่งได้มาก",
            "- ผลผูกกับเวอร์ชัน Tesseract และเครื่องที่รัน (เวลาโดยเฉพาะ)",
            "- เนื้อหาทั้งหมดเป็นข้อมูลสมมติ (องค์กร ผู้บริจาค ที่อยู่ เลขผู้เสียภาษี)",
            ""]

    # 8. ต่อรูป
    out += ["## ผลต่อรูป", "",
            "| รูป | สภาพ | คาด | ยอดที่คาด | ได้ | ยอดที่ได้ | ผล | read_text (s) |", "|---|---|---|---|---|---|---|---|"]
    out += [f"| {r['file']} | {r['condition']} | {r['expected_status']} | {r['expected_amount'] or '-'} "
            f"| {r['got_status']} | {r['got_amount'] or '-'} | {r['outcome']} | {r['ocr_seconds']:.2f} |" for r in rows]
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", default="ocr_eval", help="โฟลเดอร์รูป และที่เขียน results.csv / report.md")
    parser.add_argument("--key", help="answer_key.json (ค่าตั้งต้น: <dir>/answer_key.json)")
    parser.add_argument("--limit", type=int, help="วัดแค่ N รูปแรก")
    parser.add_argument("--condition", help="วัดเฉพาะสภาพนี้")
    parser.add_argument("--liff-step", action="store_true", help="จำลองหน้า LIFF ก่อนอ่าน (สำหรับภาพถ่ายจริง)")
    args = parser.parse_args()

    image_dir = Path(args.dir)
    key_path = Path(args.key) if args.key else image_dir / "answer_key.json"
    if not key_path.is_file():
        sys.exit(f"❌ ไม่พบ {key_path} — รัน python scripts/make_sample_receipts.py ก่อน")
    if not ocr.ocr_available():
        sys.exit("❌ เครื่องนี้ไม่มี Tesseract ที่มีภาษา tha+eng หรือไม่มี OpenCV")
    key = json.loads(key_path.read_text(encoding="utf-8"))

    items = list_images(image_dir, key, key_path, args.condition)
    if args.limit:
        items = items[:args.limit]
    if not items:
        sys.exit("❌ ไม่มีรูปให้วัด")

    rows = []
    for index, (path, receipt_id, condition) in enumerate(items, start=1):
        receipt = key["receipts"][receipt_id]
        status, amount, ocr_seconds, total_seconds = run_pipeline(load_bytes(path, args.liff_step))
        row = {
            "file": path.name,
            "receipt_id": receipt_id,
            "template": receipt["template"],
            "font": receipt["font"],
            "condition": condition,
            "expected_status": receipt["expected_status"],
            "expected_amount": receipt["expected_amount"],
            "got_status": status,
            "got_amount": canonical(amount) if amount is not None else None,
            "outcome": classify(receipt["expected_status"], receipt["expected_amount"], status, amount),
            "ocr_seconds": ocr_seconds,
            "total_seconds": total_seconds,
        }
        rows.append(row)
        if row["outcome"] == "confident_wrong":
            print(f"⚠️ {path.name}: คาด {row['expected_status']} {row['expected_amount']} ได้ ok {row['got_amount']}")
        if index % 20 == 0 or index == len(items):
            print(f"   {index}/{len(items)} รูป")

    header = [
        ("วันที่รัน", datetime.now().strftime("%Y-%m-%d %H:%M")),
        ("commit", git_commit()),
        ("Tesseract", tesseract_version()),
        ("Pillow มี raqm (จัดตำแหน่งอักษรไทย)", "มี" if features.check("raqm") else "ไม่มี"),
        ("answer key", f"{key_path.as_posix()} (seed {key.get('seed')})"),
        ("โฟลเดอร์รูป", image_dir.as_posix()),
        ("ฟอนต์", ", ".join(key.get("fonts", {}))),
        ("จำลองหน้า LIFF", "ย่อด้านยาวไม่เกิน {max_side}px + JPEG คุณภาพ {jpeg_quality} บนพื้นขาว (ตาม {source})".format(**key["liff_step"])
         + (" — ทำตอนวัด (--liff-step)" if args.liff_step else " — ทำตอนสร้างรูป")),
        ("จำนวนรูป", f"{len(rows)}" + (f" (--condition {args.condition})" if args.condition else "")
         + (f" (--limit {args.limit})" if args.limit else "")),
    ]

    with open(image_dir / "results.csv", "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, "ocr_seconds": f"{row['ocr_seconds']:.3f}", "total_seconds": f"{row['total_seconds']:.3f}"})
    write_report(image_dir / "report.md", rows, header)

    counts = Counter(row["outcome"] for row in rows)
    ocr_times = [row["ocr_seconds"] for row in rows]
    print(f"📊 {len(rows)} รูป: " + " · ".join(f"{OUTCOME_LABELS[o].strip('*')} {counts[o]}" for o in OUTCOMES))
    print(f"   read_text p50 {fmt_seconds(percentile(ocr_times, 50))} s · p95 {fmt_seconds(percentile(ocr_times, 95))} s")
    print(f"📁 {image_dir / 'report.md'} · {image_dir / 'results.csv'}")
    if counts["confident_wrong"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
