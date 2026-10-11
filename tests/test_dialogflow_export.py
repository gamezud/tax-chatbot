"""
เทสการตั้งค่า Dialogflow จากไฟล์ export ใน dialogflow/

การตั้งค่าบน Console ไม่อยู่ในโค้ด เทสนี้ตรวจได้เท่าที่ export ยังตรงกับ Console
— แก้บน Console แล้วต้อง export ใหม่ลง dialogflow/ แล้ว commit
"""
import json
import re
from pathlib import Path

import pytest

DIALOGFLOW_DIR = Path(__file__).resolve().parent.parent / 'dialogflow'

# คำสำคัญหน้าตัวเลขใน training phrase ของ 04 → parameter ที่ตัวเลขนั้นต้องถูก annotate
# None = คงประโยคไว้ให้ match 04 แต่ห้าม annotate (SSF ตาม #3, "กองทุน ESG" ตามขั้น E)
DEDUCTION_KEYWORDS = {
    'ประกันชีวิต': 'life_insurance', 'ประกัน': 'life_insurance',  # "ซื้อประกัน 15000 บาท"
    'ประกันสุขภาพ': 'health_insurance',
    'ดอกเบี้ยบ้าน': 'home_loan_interest',
    'บริจาค': 'donation',
    'rmf': 'ssf_rmf',
    'thaiesg': 'thai_esg', 'thai esg': 'thai_esg', 'tesg': 'thai_esg',
    'ไทยอีเอสจี': 'thai_esg', 'กองทุนไทยเพื่อความยั่งยืน': 'thai_esg',
    'ลูก': 'num_children', 'บุตร': 'num_children',
    'พ่อแม่': 'num_parents',
    'ssf': None, 'เอสเอสเอฟ': None, 'กองทุน esg': None,
}


def load_intent_parameters(intent_name):
    path = DIALOGFLOW_DIR / 'intents' / f'{intent_name}.json'
    intent = json.loads(path.read_text(encoding='utf-8'))
    return {p['name']: p for p in intent['responses'][0]['parameters']}


def load_usersays(intent_name):
    path = DIALOGFLOW_DIR / 'intents' / f'{intent_name}_usersays_th.json'
    return json.loads(path.read_text(encoding='utf-8'))


def phrase_text(phrase):
    return ''.join(part['text'] for part in phrase['data'])


def nearest_keyword(text):
    # คำสำคัญที่อยู่ท้ายสุดของ text (ใกล้ตัวเลขที่ตามมาที่สุด) ไม่เจอคืน None
    # ตำแหน่งเท่ากันเลือกคำที่ยาวกว่า: "ประกันสุขภาพ" ต้องชนะ "ประกัน" ที่ขึ้นต้นตำแหน่งเดียวกัน
    lower = text.lower()
    found = [(lower.rfind(k), len(k), k) for k in DEDUCTION_KEYWORDS if k in lower]
    return max(found)[2] if found else None


def test_spouse_is_not_list():
    # #12: calculate_detailed_deductions เทียบ str(spouse) == 'yes'
    # ถ้า spouse เป็น list จะได้ "['yes']" แล้วค่าลดหย่อนคู่สมรส 60,000 หายเงียบ ๆ
    spouse = load_intent_parameters('04_Tax_Interview_Deductions')['spouse']
    assert spouse['isList'] is False


def test_social_security_prompt_asks_yearly():
    # #18: โค้ดนับ social_security เป็นยอดทั้งปี (ไม่คูณ 12) prompt จึงต้องถามยอดทั้งปี
    social_security = load_intent_parameters('02_Tax_Interview_Salary')['social_security']
    prompts = [p['value'] for p in social_security['prompts']]
    assert prompts
    for prompt in prompts:
        assert 'ทั้งปี' in prompt
        assert 'เดือนละ' not in prompt


def test_thai_esg_parameter_matches_ssf_rmf():
    # calculate_detailed_deductions อ่าน thai_esg ด้วย clean_number แบบเดียวกับ ssf_rmf
    # ชื่อต้องตรงตัว (กฎเหล็กข้อ 3) และต้องไม่เป็น list เหมือน ssf_rmf
    params = load_intent_parameters('04_Tax_Interview_Deductions')
    thai_esg = params['thai_esg']
    assert thai_esg['dataType'] == params['ssf_rmf']['dataType']
    assert thai_esg['required'] is False
    assert thai_esg['isList'] is False


def test_deduction_numbers_are_not_lists():
    # clean_number รับ list แล้วหยิบแค่ตัวแรก ถ้า parameter ตัวเลขเป็น list
    # ยอดที่สองที่ Dialogflow จับได้จะหายไปเงียบ ๆ (เคยเกิดกับ life_insurance ตอน export ขั้น E)
    params = load_intent_parameters('04_Tax_Interview_Deductions')
    numbers = {name: p for name, p in params.items() if p['dataType'] == '@sys.number'}
    assert numbers
    for name, p in numbers.items():
        assert p['isList'] is False, name


def test_ssf_rmf_annotation_follows_rmf_not_ssf():
    # #3: SSF ลดหย่อนไม่ได้แล้วหลังปีภาษี 2567 (docs ข้อ 2.2 แถว SSF [5]) ยอด SSF จึงต้องไม่ถูก annotate เป็น ssf_rmf
    # ดูคำนำหน้าที่ใกล้ตัวเลขที่สุด ไม่ใช่ทั้งประโยค เพราะ "RMF 100000 SSF 50000" annotate ยอด RMF ได้ถูกต้อง
    for phrase in load_usersays('04_Tax_Interview_Deductions'):
        before = ''
        for part in phrase['data']:
            if part.get('alias') == 'ssf_rmf':
                lower = before.lower()
                last_ssf = max(lower.rfind('ssf'), lower.rfind('เอสเอสเอฟ'))
                assert lower.rfind('rmf') > last_ssf, phrase_text(phrase)
            before += part['text']


def test_phrase_separates_rmf_and_thai_esg():
    # ต้องมีประโยคที่มีทั้งสองตัว ไม่งั้น Dialogflow ไม่เคยเห็นตัวอย่างการแยก RMF กับ ThaiESG ในข้อความเดียว
    aliases = [{part.get('alias') for part in phrase['data']}
               for phrase in load_usersays('04_Tax_Interview_Deductions')]
    assert any({'ssf_rmf', 'thai_esg'} <= a for a in aliases)


@pytest.mark.parametrize('other', [
    'life_insurance', 'health_insurance', 'ssf_rmf', 'thai_esg', 'home_loan_interest',
])
def test_donation_paired_with_each_deduction_both_orders(other):
    # #23: "บริจาค 3500 ประกันชีวิต 20000" ได้แค่ donation ค่าอื่นหายเงียบ ๆ ภาษีสูงเกินจริง
    # เพราะ donation มีแต่ประโยคที่บริจาคอย่างเดียว ต้องมีตัวอย่างทั้งบริจาคอยู่หน้าและอยู่หลัง
    orders = set()
    for phrase in load_usersays('04_Tax_Interview_Deductions'):
        aliases = [part['alias'] for part in phrase['data'] if part.get('alias')]
        if 'donation' in aliases and other in aliases:
            orders.add(aliases.index('donation') < aliases.index(other))
    assert orders == {True, False}


def test_annotation_follows_nearest_keyword():
    # ตัวเลขที่ annotate ผิดตัว (เช่น ยอดประกันชีวิตไปเป็น donation) Dialogflow จะเรียนแบบผิด ๆ
    # แล้วยอดเข้าเพดานผิดรายการโดยไม่มี error — ดูคำสำคัญที่ใกล้ตัวเลขที่สุด ไม่ใช่ทั้งประโยค
    params = load_intent_parameters('04_Tax_Interview_Deductions')
    numbers = {name for name, p in params.items() if p['dataType'] == '@sys.number'}
    for phrase in load_usersays('04_Tax_Interview_Deductions'):
        before = ''
        for part in phrase['data']:
            if part.get('alias') in numbers:
                keyword = nearest_keyword(before)
                assert keyword is not None, phrase_text(phrase)
                assert DEDUCTION_KEYWORDS[keyword] == part['alias'], phrase_text(phrase)
            before += part['text']


def test_numbers_after_keywords_are_annotated():
    # ตัวเลขที่ลืม annotate สอน Dialogflow ว่าไม่ต้องเก็บยอดนั้น (แบบเดียวกับ #23)
    # ยกเว้นตัวเลขหลังคำที่ตั้งใจไม่ annotate (SSF, "กองทุน ESG")
    for phrase in load_usersays('04_Tax_Interview_Deductions'):
        before = ''
        for part in phrase['data']:
            if not part.get('alias'):
                for match in re.finditer(r'\d[\d,]*', part['text']):
                    keyword = nearest_keyword(before + part['text'][:match.start()])
                    assert keyword is not None, phrase_text(phrase)
                    assert DEDUCTION_KEYWORDS[keyword] is None, phrase_text(phrase)
            before += part['text']
