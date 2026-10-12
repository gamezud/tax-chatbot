"""
เทส find_terms / normalize ใน utils/deduction_terms.py (ไม่อ่าน dialogflow/)
"""
import pytest

from utils.deduction_terms import find_terms, normalize


def params(text):
    return [param for _, param in find_terms(text)]


@pytest.mark.parametrize('text, expected', [
    ('ประกันสุขภาพ 8000', 'health_insurance'),
    ('ประกันสังคม 9000', None),
    ('ประกันรถ 20000', None),
    ('ประกันบ้าน 3000', None),
    ('ประกันอัคคีภัย 3000', None),
    ('ประกันชีวิต 20000', 'life_insurance'),
    # ข้อจำกัด: "ประกัน" คำเดียวนับเป็นประกันชีวิต
    ('ซื้อประกัน 15000', 'life_insurance'),
])
def test_longer_term_wins(text, expected):
    # "ประกันสุขภาพ" ฯลฯ ขึ้นต้นด้วย "ประกัน" ถ้าคำสั้นชนะ ยอดจะเข้าประกันชีวิตผิดรายการ
    assert params(text) == [expected]


def test_spaces_inside_terms():
    assert params('Thai ESG 50000') == params('ThaiESG 50000') == ['thai_esg']
    assert params('ประกัน สุขภาพ 8000') == ['health_insurance']


@pytest.mark.parametrize('text', [
    'ดอกเบี้ยบ้าน 40000',
    'ดอกเบี้ยกู้บ้าน 33000',
    'จ่ายดอกเบี้ยเงินกู้ซื้อบ้านไป 52000',
    'ดอกเบี้ยเงินกู้ยืมเพื่อที่อยู่อาศัย 60000',
])
def test_home_loan_interest(text):
    assert params(text) == ['home_loan_interest']


def test_car_loan_interest_is_not_home_loan():
    # ดอกเบี้ยที่ลดหย่อนได้ต้องเป็นเงินกู้เพื่อที่อยู่อาศัย
    assert 'home_loan_interest' not in params('ดอกเบี้ยเงินกู้รถ 20000')


def test_home_loan_does_not_swallow_other_terms():
    # ถ้ารับอะไรก็ได้ระหว่าง "ดอกเบี้ย" กับ "บ้าน" ทั้งก้อนจะกลายเป็นดอกเบี้ยบ้าน
    assert params('ดอกเบี้ยรถ ประกันบ้าน 5000') == [None]


@pytest.mark.parametrize('text', ['อาร์เอ็มเอฟ 25000', 'กองทุนรวมเพื่อการเลี้ยงชีพ 70000', 'RMF 50000'])
def test_rmf_terms(text):
    # "กองทุนรวมเพื่อการเลี้ยงชีพ" ต้องได้คำเดียว ไม่ถูกแยกเป็นคำอื่น
    assert params(text) == ['ssf_rmf']


@pytest.mark.parametrize('text', ['พ่อแม่ 2 คน', 'บิดามารดา 2 คน', 'พ่อ 1 คน', 'แม่ 1 คน', 'บิดา 1 คน', 'มารดา 1 คน'])
def test_parent_terms(text):
    # "พ่อแม่" ต้องได้คำเดียว ไม่ใช่ "พ่อ" กับ "แม่" แยกกัน
    assert params(text) == ['num_parents']


def test_thai_digits_are_converted():
    assert normalize('บริจาค ๕,๐๐๐') == 'บริจาค5000'
    # ตำแหน่งนับในข้อความหลัง normalize: "บริจาค" 6 ตัว + "5000" 4 ตัว
    assert find_terms('บริจาค ๕๐๐๐ ประกันชีวิต') == [(0, 'donation'), (10, 'life_insurance')]
