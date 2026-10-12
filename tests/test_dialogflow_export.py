"""
เทสการตั้งค่า Dialogflow จากไฟล์ export ใน dialogflow/

การตั้งค่าบน Console ไม่อยู่ในโค้ด เทสนี้ตรวจได้เท่าที่ export ยังตรงกับ Console
— แก้บน Console แล้วต้อง export ใหม่ลง dialogflow/ แล้ว commit
"""
import json
import re
from pathlib import Path

import pytest

from utils.deduction_terms import find_terms, normalize

DIALOGFLOW_DIR = Path(__file__).resolve().parent.parent / 'dialogflow'


def load_intent_parameters(intent_name):
    path = DIALOGFLOW_DIR / 'intents' / f'{intent_name}.json'
    intent = json.loads(path.read_text(encoding='utf-8'))
    return {p['name']: p for p in intent['responses'][0]['parameters']}


def load_usersays(intent_name):
    path = DIALOGFLOW_DIR / 'intents' / f'{intent_name}_usersays_th.json'
    return json.loads(path.read_text(encoding='utf-8'))


def phrase_text(phrase):
    return ''.join(part['text'] for part in phrase['data'])


def numbers_with_segment(phrase, numbers):
    # ตัวเลขทุกตัวในประโยค คู่กับข้อความตั้งแต่ตัวเลขก่อนหน้าถึงตัวเลขนี้ และ alias (ตัวเลขที่ไม่ได้ annotate ได้ None)
    # ตัดช่วงที่ตัวเลขก่อนหน้า เพราะคำที่อยู่หน้าตัวเลขก่อนหน้าเป็นของตัวเลขนั้น ไม่ใช่ของตัวเลขนี้
    segment = ''
    for part in phrase['data']:
        if part.get('alias') in numbers:
            yield segment, part['alias']
            segment = ''
            continue
        text, start = part['text'], 0
        for match in re.finditer(r'\d[\d,]*', text):
            yield segment + text[start:match.start()], None
            segment, start = '', match.end()
        segment += text[start:]


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


def solo_phrases(name):
    # ประโยคเดี่ยวแท้: annotate parameter นี้ตัวเดียว และไม่มีตัวเลขอื่นที่ไม่ได้ annotate
    # ("ประกันสุขภาพ 25000 ซื้อ SSF 250000" annotate ตัวเดียวแต่ไม่นับ เพราะมียอด SSF ปน)
    result = []
    for phrase in load_usersays('04_Tax_Interview_Deductions'):
        aliases = {part['alias'] for part in phrase['data'] if part.get('alias')}
        other_digits = any(re.search(r'\d', part['text']) for part in phrase['data'] if not part.get('alias'))
        if aliases == {name} and not other_digits:
            result.append(phrase_text(phrase))
    return result


# #26: คำที่ผู้ใช้พิมพ์แล้ว Dialogflow ยังไม่รู้จัก (ทดสอบใน LINE 2026-10-11)
# "บุตร 1 คน", "อาร์เอ็มเอฟ 25000", "ดอกเบี้ยกู้บ้าน 33000" ได้ Fallback
# "กองทุนรวมเพื่อการเลี้ยงชีพ 70000" ได้ donation (ตัวกัน #25 ปฏิเสธ แต่ผู้ใช้ก็ยังกรอกยอดนี้ไม่ได้)
XFAIL_26 = pytest.mark.xfail(strict=True, reason='#26 ยังไม่มีประโยคเดี่ยวที่ใช้คำนี้ใน export')


# คำที่ไม่นับ (ผ่อนบ้าน, ทำบุญ ฯลฯ) และเหตุผลอยู่ใน utils/deduction_terms.py
@pytest.mark.parametrize('name, term', [
    ('num_children', 'ลูก'),
    ('life_insurance', 'ประกันชีวิต'),
    ('ssf_rmf', 'RMF'),
    ('num_parents', 'พ่อแม่'),
    ('num_parents', 'บิดามารดา'),
    ('health_insurance', 'ประกันสุขภาพ'),
    ('home_loan_interest', 'ดอกเบี้ยบ้าน'),
    ('home_loan_interest', 'ดอกเบี้ยเงินกู้บ้าน'),
    ('donation', 'บริจาค'),
    ('thai_esg', 'ThaiESG'),
    pytest.param('num_children', 'บุตร', marks=XFAIL_26),
    pytest.param('ssf_rmf', 'กองทุนรวมเพื่อการเลี้ยงชีพ', marks=XFAIL_26),
    pytest.param('ssf_rmf', 'อาร์เอ็มเอฟ', marks=XFAIL_26),
    pytest.param('home_loan_interest', 'ดอกเบี้ยกู้บ้าน', marks=XFAIL_26),
])
def test_each_deduction_term_has_solo_phrase(name, term):
    # #24: ระหว่างรอค่าลดหย่อน "ประกันสุขภาพ 8000", "บิดามารดา 2 คน", "ดอกเบี้ยเงินกู้บ้าน 35000"
    # ได้ Default Fallback Intent แล้วตกไปสาขาความรู้ ผู้ใช้ไม่รู้ว่ายอดไม่ถูกบันทึก
    # ปัญหาคือคำ ไม่ใช่จำนวนประโยค: "ดอกเบี้ยบ้าน 40000" ผ่านเพราะมีประโยคเดี่ยวที่ใช้คำเดียวกัน
    # คู่ที่ไม่มี xfail กันไม่ให้ประโยคที่ใช้งานได้อยู่หายไปตอนแก้ Console
    # บรรทัดแรกกันไม่ให้รายการคู่ในเทสนี้กับรายการคำใน utils/deduction_terms.py ไม่ตรงกัน
    assert [param for _, param in find_terms(term)] == [name], term
    assert any(normalize(term) in normalize(phrase) for phrase in solo_phrases(name)), (name, term)


def test_home_loan_phrases_say_home():
    # ดอกเบี้ยที่ลดหย่อนได้ต้องเป็นเงินกู้เพื่อที่อยู่อาศัย "ดอกเบี้ยเงินกู้" เฉย ๆ อาจเป็นกู้รถหรือสินเชื่อส่วนบุคคล
    # ถ้าสอนด้วยประโยคที่ไม่บอกว่าเป็นบ้าน ดอกเบี้ยอื่นจะถูกนับเป็นค่าลดหย่อน (กฎเหล็กข้อ 2)
    phrases = [phrase_text(phrase) for phrase in load_usersays('04_Tax_Interview_Deductions')
               if any(part.get('alias') == 'home_loan_interest' for part in phrase['data'])]
    assert phrases
    for text in phrases:
        assert 'home_loan_interest' in [param for _, param in find_terms(text)], text


def test_annotation_follows_term_since_previous_number():
    # ตัวเลขที่ annotate ผิดตัว (เช่น ยอดประกันชีวิตไปเป็น donation) Dialogflow จะเรียนแบบผิด ๆ
    # แล้วยอดเข้าเพดานผิดรายการโดยไม่มี error — คำต้องอยู่ระหว่างตัวเลขก่อนหน้ากับตัวเลขนี้
    # ถ้าค้นทั้งข้อความก่อนหน้า "บริจาค 3000 ดอกเบี้ยเงินกู้รถ 20000" จะเจอ "บริจาค" แล้วผ่าน ทั้งที่ 20000 ไม่ใช่เงินบริจาค
    params = load_intent_parameters('04_Tax_Interview_Deductions')
    numbers = {name for name, p in params.items() if p['dataType'] == '@sys.number'}
    for phrase in load_usersays('04_Tax_Interview_Deductions'):
        for segment, alias in numbers_with_segment(phrase, numbers):
            if alias:
                terms = [param for _, param in find_terms(segment)]
                assert terms, phrase_text(phrase)
                assert terms[-1] == alias, phrase_text(phrase)


def test_numbers_after_keywords_are_annotated():
    # ตัวเลขที่ลืม annotate สอน Dialogflow ว่าไม่ต้องเก็บยอดนั้น (แบบเดียวกับ #23)
    # ยกเว้นตัวเลขหลังคำที่ตั้งใจไม่ annotate (SSF, "กองทุน ESG")
    params = load_intent_parameters('04_Tax_Interview_Deductions')
    numbers = {name for name, p in params.items() if p['dataType'] == '@sys.number'}
    for phrase in load_usersays('04_Tax_Interview_Deductions'):
        for segment, alias in numbers_with_segment(phrase, numbers):
            if not alias:
                terms = [param for _, param in find_terms(segment)]
                assert terms, phrase_text(phrase)
                assert terms[-1] is None, phrase_text(phrase)
