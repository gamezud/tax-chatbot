"""
เทสการตั้งค่า Dialogflow จากไฟล์ export ใน dialogflow/

การตั้งค่าบน Console ไม่อยู่ในโค้ด เทสนี้ตรวจได้เท่าที่ export ยังตรงกับ Console
— แก้บน Console แล้วต้อง export ใหม่ลง dialogflow/ แล้ว commit
"""
import json
from pathlib import Path

DIALOGFLOW_DIR = Path(__file__).resolve().parent.parent / 'dialogflow'


def load_intent_parameters(intent_name):
    path = DIALOGFLOW_DIR / 'intents' / f'{intent_name}.json'
    intent = json.loads(path.read_text(encoding='utf-8'))
    return {p['name']: p for p in intent['responses'][0]['parameters']}


def test_spouse_is_not_list():
    # #12: calculate_detailed_deductions เทียบ str(spouse) == 'yes'
    # ถ้า spouse เป็น list จะได้ "['yes']" แล้วค่าลดหย่อนคู่สมรส 60,000 หายเงียบ ๆ
    spouse = load_intent_parameters('04_Tax_Interview_Deductions')['spouse']
    assert spouse['isList'] is False
