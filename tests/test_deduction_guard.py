"""
เทสตัวกันค่าลดหย่อน utils/deduction_guard.py (#25) — ฟังก์ชันล้วน ไม่ใช้ Flask

parameters ในเทสเขียนตามค่าที่ Dialogflow ส่งจริง: ตัวเลขเป็น float ช่องที่ไม่ได้กรอกเป็น ""
"""
import json
from pathlib import Path

import pytest

from utils import deduction_guard
from utils.deduction_guard import check_deduction_parameters, has_number

DIALOGFLOW_DIR = Path(__file__).resolve().parent.parent / 'dialogflow'


def load_json(*parts):
    return json.loads(DIALOGFLOW_DIR.joinpath(*parts).read_text(encoding='utf-8'))


def spouse_values():
    # คำที่ผู้ใช้พิมพ์ → ค่าที่ @marital_status ส่งมา ("yes"/"no")
    return {synonym: entry['value']
            for entry in load_json('entities', 'marital_status_entries_th.json')
            for synonym in entry['synonyms']}


def training_phrases():
    # (ข้อความ, parameters ที่ Dialogflow จะส่งตาม annotation) ของทุก training phrase ใน 04
    spouse = spouse_values()
    result = []
    for phrase in load_json('intents', '04_Tax_Interview_Deductions_usersays_th.json'):
        text = ''.join(part['text'] for part in phrase['data'])
        params = {}
        for part in phrase['data']:
            if part.get('alias') == 'spouse':
                params['spouse'] = spouse[part['text']]
            elif part.get('alias'):
                params[part['alias']] = float(part['text'].replace(',', ''))
        result.append((text, params))
    return result


@pytest.mark.parametrize('text, params', training_phrases())
def test_training_phrases_pass(text, params):
    # ประโยคที่สอน Dialogflow เองต้องผ่าน ไม่งั้นตัวกันปฏิเสธคำตอบที่ Dialogflow จับถูก
    assert check_deduction_parameters(text, params) is None


@pytest.mark.parametrize('text, params', [
    # #23 holdout ที่ทดสอบใน LINE แล้ว (ค่าจริงจาก Dialogflow)
    ('บริจาค 3500 ประกันชีวิต 20000', {'life_insurance': 20000.0, 'donation': 3500.0}),
    ('ประกันสุขภาพ 7000 บริจาค 900', {'health_insurance': 7000.0, 'donation': 900.0}),
    ('RMF 30000 บริจาค 5500', {'ssf_rmf': 30000.0, 'donation': 5500.0}),
    ('พ่อแม่ 2 คน ประกันชีวิต 15000', {'life_insurance': 15000.0, 'num_parents': 2.0}),
    ('บริจาค 3000 ประกันชีวิต 20000 RMF 50000',
     {'donation': 3000.0, 'life_insurance': 20000.0, 'ssf_rmf': 50000.0}),
    # #24 holdout
    ('ประกันสุขภาพ 8000', {'health_insurance': 8000.0}),
    ('เบี้ยประกันสุขภาพ 12000', {'health_insurance': 12000.0}),
    ('ดอกเบี้ยบ้าน 40000 ประกันสุขภาพ 8000', {'health_insurance': 8000.0, 'home_loan_interest': 40000.0}),
    ('บิดามารดา 2 คน', {'num_parents': 2.0}),
    ('ดอกเบี้ยเงินกู้บ้าน 35000', {'home_loan_interest': 35000.0}),
    # คำอื่นที่ทดสอบใน LINE แล้ว
    ('แม่ 1 คน', {'num_parents': 1.0}),
    ('เบี้ยประกันชีวิต 45000', {'life_insurance': 45000.0}),
    ('เงินบริจาค 4000', {'donation': 4000.0}),
    ('ลูกสองคน', {'num_children': 2.0}),
    ('พ่อแม่สองคน', {'num_parents': 2.0}),
    ('ไม่มี', {}),
    ('ไม่มี', {'num_children': '', 'life_insurance': '', 'ssf_rmf': '', 'spouse': '',
               'num_parents': '', 'health_insurance': '', 'home_loan_interest': '', 'donation': ''}),
    # "สาม" ใน "สามี" ไม่ใช่ตัวเลข เพราะไม่มี "คน" ติดท้าย
    ('มีสามี ลูก 2 คน', {'spouse': 'yes', 'num_children': 2.0}),
    ('บริจาค 5000.', {'donation': 5000.0}),
    ('ประกันชีวิต 20000, บริจาค 5000', {'life_insurance': 20000.0, 'donation': 5000.0}),
    ('บริจาค 3,500.50 บาท', {'donation': 3500.5}),
    ('บริจาค ๕,๐๐๐', {'donation': 5000.0}),
    # ตัวเลขหลังคำ None (ประกันสังคมเก็บในสาขา 02) ไม่ต้องมี parameter รองรับ
    ('ประกันสังคม 9000 ประกันชีวิต 20000', {'life_insurance': 20000.0}),
])
def test_passes(text, params):
    assert check_deduction_parameters(text, params) is None


@pytest.mark.parametrize('text, params', [
    # #25 จาก LINE: Dialogflow ยัดตัวเลขเข้า donation
    ('ดอกเบี้ยเงินกู้รถ 20000', {'donation': 20000.0}),
    ('กองทุนรวมเพื่อการเลี้ยงชีพ 70000', {'donation': 70000.0}),
    # คำของตัวเลขก่อนหน้าไม่นับ: ช่วงก่อน 20000 มีแค่ "ดอกเบี้ยเงินกู้รถ"
    ('บริจาค 3000 ดอกเบี้ยเงินกู้รถ 20000', {'donation': 20000.0}),
    ('ประกันรถ 20000', {'life_insurance': 20000.0}),
    # เงินต้องเป็นตัวเลข คำ หนึ่ง–สิบ ใช้ได้เฉพาะจำนวนคน
    ('บริจาคห้าพันบาท', {'donation': 5000.0}),
    ('บริจาคสองคน', {'donation': 2.0}),
    # ตัวเลขที่ตามด้วย "คน" เป็นจำนวนคน ไม่ใช่เงิน
    ('บริจาค 2 คน', {'donation': 2.0}),
    # ค่าไม่ตรงกับตัวเลขในข้อความ
    ('บริจาค 5000', {'donation': 50000.0}),
    # spouse "yes" แต่ข้อความไม่พูดถึงคู่สมรส
    ('ลูก 1 คน', {'spouse': 'yes', 'num_children': 1.0}),
    # parameter ที่ตัวกันไม่รู้จัก
    ('บริจาค 5000', {'donation': 5000.0, 'new_parameter': 5000.0}),
    # ตรวจย้อนกลับ: ตัวเลขในข้อความที่ไม่มี parameter รองรับ — แบบ #23 ยอดประกันชีวิตหายเงียบ ภาษีสูงเกินจริง
    ('บริจาค 3500 ประกันชีวิต 20000', {'donation': 3500.0}),
    ('ดอกเบี้ยเงินกู้รถ 20000', {}),
    ('ลูก 2 คน อายุ 5 ปี', {'num_children': 2.0}),
    ('ลูกสองคน', {}),
    # #26 ประโยคตรวจจาก LINE (2026-10-12): Dialogflow จับผิดช่อง ตัวกันต้องปฏิเสธ
    ('เอสเอสเอฟ 50000', {'ssf_rmf': 50000.0}),
    ('กองทุนรวมเพื่อการออม 50000', {'home_loan_interest': 50000.0}),
])
def test_unclear(text, params):
    assert check_deduction_parameters(text, params) == 'unclear'


@pytest.mark.parametrize('text, params', [
    ('ประกันสุขภาพพ่อแม่ 10000', {'donation': 10000.0}),     # #25 จาก LINE
    ('ประกันสุขภาพพ่อแม่ 10000', {'health_insurance': 10000.0}),
    ('เบี้ยประกันสุขภาพบิดามารดา 15000', {}),
    ('ประกันสุขภาพของแม่ 5000', {'health_insurance': 5000.0}),
])
def test_parent_health(text, params):
    # ตรวจก่อนหลักฐานตัวเลข: ปฏิเสธทั้งข้อความแม้ parameters จะดูถูก
    assert check_deduction_parameters(text, params) == 'parent_health'


def test_parent_health_needs_no_number_between():
    # ตัวเลขคั่น → ยอดเป็นของประกันสุขภาพตัวเอง และ "แม่ 1 คน" เป็นรายการแยก
    params = {'health_insurance': 8000.0, 'num_parents': 1.0}
    assert check_deduction_parameters('ประกันสุขภาพ 8000 แม่ 1 คน', params) is None


def test_count_needs_person_even_without_parent_health_check(monkeypatch):
    # สองชั้น: ถ้าไม่มีการตรวจกรณี ง. ตัวเลข 10000 อยู่หลัง "พ่อแม่" ก็จริง แต่ไม่มี "คน" ตามหลัง
    # ถ้าผ่าน ตัวคำนวณจะตัดเหลือ parent_max_count คน ได้ลดหย่อนบิดามารดาเต็มเพดาน
    monkeypatch.setattr(deduction_guard, '_mentions_parent_health', lambda text: False)
    assert check_deduction_parameters('ประกันสุขภาพพ่อแม่ 10000', {'num_parents': 10000.0}) == 'unclear'


def test_spouse_words_cover_marital_status_yes():
    # ทุกคำฝั่ง yes ของ @marital_status ต้องมีคำใน SPOUSE_WORDS ไม่งั้นตัวกันปฏิเสธคำตอบที่ Dialogflow จับถูก
    yes = [synonym for synonym, value in spouse_values().items() if value == 'yes']
    assert yes
    for synonym in yes:
        assert any(word in synonym for word in deduction_guard.SPOUSE_WORDS), synonym


def test_guard_knows_every_deduction_parameter():
    # parameter ใหม่บน Console ที่ตัวกันไม่รู้จักจะถูกปฏิเสธทุกข้อความ ต้องเพิ่มที่ตัวกันด้วย
    intent = load_json('intents', '04_Tax_Interview_Deductions.json')
    names = {p['name'] for p in intent['responses'][0]['parameters']}
    known = set(deduction_guard.COUNT_PARAMETERS) | set(deduction_guard.MONEY_PARAMETERS) | {'spouse'}
    assert names == known


@pytest.mark.parametrize('text, expected', [
    ('บุตร 1 คน', True),
    ('๕๐๐๐', True),
    ('ยื่นภาษีได้ถึงวันไหน', False),
    ('ลูกสองคน', False),
])
def test_has_number(text, expected):
    assert has_number(text) is expected
