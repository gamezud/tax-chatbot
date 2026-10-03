"""แทนข้อมูลระบุตัวตนใน tests/fixtures/*.json ด้วยค่าปลอม ก่อน commit

รันจาก root ของโปรเจกต์:  python scripts/scrub_fixtures.py
จบด้วย exit 1 ถ้ายังเหลือรูปแบบ LINE ID (U + 32 hex) หรือค่าจริงที่เก็บได้ในไฟล์ใด
รันซ้ำได้ — ค่าที่ขึ้นต้น TEST_ อยู่แล้วจะถูกข้าม
"""
import json
import re
import sys
from pathlib import Path

FIXTURE_DIR = Path('tests') / 'fixtures'
LINE_ID_PATTERN = re.compile(r'U[0-9a-f]{32}')


def collect_real_values(req):
    """คืน list ของ (ประเภท, ค่าจริง) ที่ต้องแทน จาก request หนึ่งไฟล์"""
    found = []
    data = req.get('originalDetectIntentRequest', {}).get('payload', {}).get('data', {})
    found.append(('user', data.get('source', {}).get('userId')))
    found.append(('bot', data.get('destination')))

    # session = projects/<PROJECT>/agent/sessions/<SESSION>
    parts = req.get('session', '').split('/')
    if len(parts) >= 4 and parts[0] == 'projects':
        found.append(('project', parts[1]))
        found.append(('session', parts[-1]))

    return [(kind, value) for kind, value in found if value and not value.startswith('TEST_')]


def fake_name(kind, index):
    base = {'user': 'TEST_USER', 'session': 'TEST_SESSION', 'project': 'TEST_PROJECT', 'bot': 'TEST_BOT'}[kind]
    return base if index == 1 else f"{base}_{index}"


def main():
    files = sorted(FIXTURE_DIR.glob('*.json'))
    if not files:
        print(f"ไม่พบไฟล์ใน {FIXTURE_DIR} — ต้องรันจาก root ของโปรเจกต์")
        sys.exit(1)

    texts = {path: path.read_text(encoding='utf-8') for path in files}

    # สร้างตารางแทนค่าทั่วทุกไฟล์ ค่าเดียวกันได้ชื่อปลอมเดียวกันเสมอ
    replacements = {}
    counters = {}
    for path in files:
        for kind, value in collect_real_values(json.loads(texts[path])):
            if value not in replacements:
                counters[kind] = counters.get(kind, 0) + 1
                replacements[value] = fake_name(kind, counters[kind])

    # แทนแบบข้อความทั้งไฟล์ เพราะ session/project โผล่ซ้ำในชื่อ context และชื่อ intent ด้วย
    # แทนค่าที่ยาวกว่าก่อน กันกรณีค่าหนึ่งเป็นส่วนหนึ่งของอีกค่า
    ordered = sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True)
    changed = 0
    for path in files:
        new_text = texts[path]
        for real, fake in ordered:
            new_text = new_text.replace(real, fake)
        json.loads(new_text)  # ยืนยันว่ายังเป็น JSON ที่ถูกต้อง
        if new_text != texts[path]:
            path.write_text(new_text, encoding='utf-8')
            texts[path] = new_text
            changed += 1

    print(f"📁 ไฟล์ทั้งหมด {len(files)} ไฟล์ แก้ไข {changed} ไฟล์")
    for real, fake in replacements.items():
        print(f"   ...{real[-4:]} -> {fake}")

    # ด่านสุดท้ายก่อน git add
    leaks = [path.name for path in files
             if LINE_ID_PATTERN.search(texts[path]) or any(real in texts[path] for real in replacements)]
    if leaks:
        print("❌ ยังพบข้อมูลระบุตัวตนในไฟล์:")
        for name in leaks:
            print(f"   {name}")
        sys.exit(1)
    print("✅ ไม่พบข้อมูลระบุตัวตนเหลืออยู่")


if __name__ == '__main__':
    main()
