"""
เทส webhook() ใน app.py โดยยิง fixture จริงใน tests/fixtures/ เข้า /webhook ผ่าน Flask test client
เป้าหมายคือล็อกพฤติกรรมไว้ก่อน refactor — ย้ายโค้ดแล้วเทสต้องผ่านเหมือนเดิม

หลักการแบ่งเคส (เหมือน test_tax_calculator.py)
- เทสธรรมดา      : ค่าคาดหวังมาจากสเปกใน CLAUDE.md หรือคำนวณมือจากกฎหมาย
- test_current_behavior_ : เรื่องที่ยังไม่ได้ตัดสินใจ ล็อกค่าที่ได้จากการรันจริงไว้

การกันไม่ให้เทสออกไปนอกเครื่อง (Firestore, Ollama, ไฟล์ PDF) มีสองชั้น

ชั้นที่ 1 — ใส่โมดูลปลอมแทน utils.firebase_db ลง sys.modules ก่อน import app
  ไฟล์จริงสร้าง firebase_client = FirebaseManager() ทันทีที่ถูก import ซึ่งอ่าน firebase_key.json
  และสร้าง client ที่เขียน Firestore จริงได้ โมดูลปลอมทำให้ FirebaseManager() ไม่ถูกสร้างเลย
  ** โมดูลปลอมนี้อยู่ใน sys.modules ตลอดการรัน pytest ทั้งชุด ไม่ได้หายไปเมื่อจบไฟล์นี้ **
  ถ้าวันหน้ามีไฟล์ test อื่นต้อง import app ให้ย้ายการใส่โมดูลปลอมไป tests/conftest.py
  (ไม่งั้นลำดับการเก็บไฟล์ของ pytest จะเป็นตัวตัดสินว่าใคร import ก่อน)

ชั้นที่ 2 — fixture mocks (autouse) แทนของที่ออกนอกระบบด้วย MagicMock ใหม่ทุกเทส
  ต้อง patch ที่ app_module.xxx ไม่ใช่ utils.xxx เพราะ app.py ใช้ from ... import
  ชื่อจึงถูกผูกไว้ใน namespace ของ app แล้ว
  ตาข่ายกันพลาด: get_db / ask_llm ของจริงถูกแทนด้วยตัวที่สั่ง pytest.fail ถ้าถูกเรียก
  และ chdir ไป tmp_path เพื่อให้ไฟล์ที่หลุดออกมา (static/reports) ไม่ลง repo
"""
import json
import sys
import time
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ---- ชั้นที่ 1: โมดูลปลอมต้องอยู่ก่อน import app ----
# ถ้ามีใคร import ของจริงไปก่อน FirebaseManager() ได้ทำงานไปแล้ว โมดูลปลอมจะสายเกินไป
assert "app" not in sys.modules, "app ถูก import ไปก่อนแล้ว — ย้ายโมดูลปลอมไป conftest.py"
assert "utils.firebase_db" not in sys.modules, "utils.firebase_db ตัวจริงถูก import ไปก่อนแล้ว"
_fake_firebase_db = types.ModuleType("utils.firebase_db")
_fake_firebase_db.firebase_client = MagicMock(name="fake_module_firebase_client")
sys.modules["utils.firebase_db"] = _fake_firebase_db

import app as app_module  # noqa: E402
import utils.knowledge_search as knowledge_search  # noqa: E402

# path อิงตำแหน่งไฟล์นี้ ไม่ใช่ cwd เพราะเทส chdir ไป tmp_path
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
SESSION = "projects/TEST_PROJECT/agent/sessions/TEST_SESSION"

# ข้อความที่บอกว่าเข้าสาขาไหน (ตัดมาจาก app.py)
ERROR_TEXT = "ขออภัยครับ เกิดข้อผิดพลาดในการประมวลผล รบกวนพิมพ์ 'รีเซ็ต' เพื่อเริ่มใหม่อีกครั้งครับ"
GREETING = "ยินดีต้อนรับสู่ผู้ช่วยภาษีเงินได้บุคคลธรรมดา"
RESET = "ล้างข้อมูลเก่าเรียบร้อยครับ 🧹 พิมพ์ 'อยากคำนวณภาษี' เพื่อเริ่มใหม่ได้เลยครับ!"
HISTORY_EMPTY = "ยังไม่มีประวัติการคำนวณภาษีในระบบครับ พิมพ์ 'อยากคำนวณภาษี' เพื่อเริ่มคำนวณได้เลยครับ"
START = "เริ่มเก็บข้อมูลจาก"
SALARY = "เก็บข้อมูลเงินเดือนเรียบร้อยครับ"
RENTAL = "เก็บข้อมูลรายได้ค่าเช่าเรียบร้อยครับ"
ONLINE = "เก็บข้อมูลขายของออนไลน์เรียบร้อยครับ"
SUMMARY = "📊 สรุปผลการประเมินภาษีประจำปี"
NUMBER_HINT = "หากต้องการคำนวณภาษี รบกวนพิมพ์ 'อยากคำนวณภาษี' เพื่อเริ่มต้นได้เลยครับ"
NO_INCOME = "ระบบยังไม่ได้รับข้อมูลรายได้ของคุณครับ รบกวนพิมพ์ 'รีเซ็ต' เพื่อเริ่มต้นใหม่อีกครั้งครับ"
RAG_ANSWER = "📖 MOCK_RAG_ANSWER"
PDF_LINK = "ดาวน์โหลดเอกสารสรุป PDF"


# ---------------------------------------------------------------------------
# เครื่องมือ
# ---------------------------------------------------------------------------
def load(name):
    """อ่าน fixture ใหม่ทุกครั้ง แก้ dict ที่ได้ได้เลยโดยไม่กระทบเทสอื่น"""
    return json.loads((FIXTURES_DIR / f"{name}.json").read_text(encoding="utf-8"))


def ctx_list(body):
    """outputContexts ที่ตอบกลับ เป็น list ของ (ชื่อท้าย, lifespanCount) ตามลำดับ
    เทียบตามลำดับเพราะสาขา 01 ส่ง awaiting_salary ซ้ำสองครั้ง (0 แล้วตามด้วย 2)"""
    result = []
    for ctx in body["outputContexts"]:
        prefix, _, short = ctx["name"].rpartition("/contexts/")
        assert prefix == SESSION
        result.append((short, ctx["lifespanCount"]))
    return result


def ctx_params(body, short):
    for ctx in body["outputContexts"]:
        if ctx["name"].endswith(f"/contexts/{short}"):
            return ctx["parameters"]
    raise AssertionError(f"ไม่มี context {short}")


def _forbidden(name):
    def _fail(*args, **kwargs):
        # pytest.fail เป็น BaseException จึงไม่ถูก except Exception ใน webhook() กลืน
        pytest.fail(f"{name} ของจริงถูกเรียก — mock ไม่ครอบ (refactor ย้ายที่ import หรือเปล่า?)")
    return _fail


@pytest.fixture(autouse=True)
def mocks(monkeypatch, tmp_path):
    # ---- ชั้นที่ 2 ----
    m = types.SimpleNamespace(
        firebase=MagicMock(name="firebase_client"),
        pdf=MagicMock(name="generate_tax_pdf"),
        rag=MagicMock(name="query_tax_knowledge", return_value=RAG_ANSWER),
    )
    m.firebase.get_user_history.return_value = []
    monkeypatch.setattr(app_module, "firebase_client", m.firebase)
    monkeypatch.setattr(app_module, "generate_tax_pdf", m.pdf)
    monkeypatch.setattr(app_module, "query_tax_knowledge", m.rag)
    monkeypatch.setattr(knowledge_search, "get_db", _forbidden("get_db"))
    monkeypatch.setattr(knowledge_search, "ask_llm", _forbidden("ask_llm"))
    monkeypatch.chdir(tmp_path)
    return m


@pytest.fixture
def post():
    client = app_module.app.test_client()

    def _post(payload, expect_error=False):
        if isinstance(payload, str):
            payload = load(payload)
        resp = client.post("/webhook", json=payload)
        assert resp.status_code == 200
        body = resp.get_json()
        # webhook() กลืนทุก exception เป็นข้อความเดียว ต้องเช็กเสมอ ไม่งั้นโค้ดพังแล้วเทสยังผ่าน
        if expect_error:
            assert body["fulfillmentText"] == ERROR_TEXT
        else:
            assert body["fulfillmentText"] != ERROR_TEXT
        return body

    return _post


def assert_summary_side_effects(body, mocks):
    """สิ่งที่สาขา 04 (สรุปผล) ต้องทำทุกครั้ง"""
    mocks.pdf.assert_called_once()  # ไม่เทียบชื่อไฟล์ เพราะสุ่มด้วย uuid
    mocks.firebase.save_tax_report.assert_called_once()
    user_id, data = mocks.firebase.save_tax_report.call_args.args
    assert user_id == "TEST_USER"
    assert data["pdf_file_url"] == ""  # PUBLIC_URL ว่างตอนเทส (ตั้งค่าเฉพาะใน __main__)
    assert PDF_LINK not in body["fulfillmentText"]
    assert ctx_list(body) == [("awaiting_deduction", 0), ("tax_session", 0)]
    return data


def summary_lines(income, expense, deduction, net, tax, withholding, status):
    return [
        f"1. เงินได้พึงประเมินรวม: {income} บาท",
        f"2. หักค่าใช้จ่ายตามกฎหมาย: {expense} บาท",
        f"3. รวมค่าลดหย่อนภาษี: {deduction} บาท",
        f"4. เงินได้สุทธิ: {net} บาท",
        f"5. ภาษีที่คำนวณได้ทั้งสิ้น: {tax} บาท",
        f"6. ภาษีหัก ณ ที่จ่ายสะสม: {withholding} บาท",
        f"👉 ผลสรุป: {status}",
    ]


# ---------------------------------------------------------------------------
# A. fixture ทุกไฟล์เข้าสาขาถูกตามลำดับการตรวจ 7 ขั้น (CLAUDE.md กับดักข้อ 1)
#    1 ทักทาย · 2 รีเซ็ต · 3 ดูประวัติ · 4 intent 01/02/03
#    5 intent 05 หรือ awaiting_online · 6 intent 04 · 7 ที่เหลือ (ความรู้)
# ---------------------------------------------------------------------------
ROUTES = [
    # (fixture, ขั้น, ข้อความที่บอกว่าเข้าสาขาไหน)
    ("001_Welcome", 1, GREETING),
    ("002_Default_Fallback_Intent", 3, HISTORY_EMPTY),     # "ดูประวัติ" ตัดสินจากข้อความล้วน
    ("003_01_Tax_Interview_Start", 4, START),
    ("004_02_Tax_Interview_Salary", 4, SALARY),
    ("005_04_Tax_Interview_Deductions", 6, SUMMARY),
    ("006_01_Tax_Interview_Start", 4, START),
    ("007_02_Tax_Interview_Salary", 4, SALARY),
    ("008_03_Tax_Interview_Rental", 4, RENTAL),
    ("009_Default_Fallback_Intent", 5, ONLINE),             # Fallback แต่มี awaiting_online
    ("010_04_Tax_Interview_Deductions", 6, SUMMARY),
    ("011_Default_Fallback_Intent", 7, RAG_ANSWER),
    ("012_Default_Fallback_Intent", 7, NUMBER_HINT),
    ("014_99_Reset_Chat", 2, RESET),
    ("015_Default_Fallback_Intent", 7, NUMBER_HINT),        # ตัวเลขหลังรีเซ็ต
    ("017_02_Tax_Interview_Salary", 4, SALARY),
    ("018_99_Reset_Chat", 2, RESET),
    ("020_02_Tax_Interview_Salary", 4, SALARY),
    ("021_04_Tax_Interview_Deductions", 6, SUMMARY),
]


def test_routes_cover_every_fixture():
    # เพิ่ม fixture ใหม่แล้วต้องมาระบุสาขาในตาราง ROUTES ด้วย
    assert {name for name, _, _ in ROUTES} == {p.stem for p in FIXTURES_DIR.glob("*.json")}


@pytest.mark.parametrize("name, step, marker", ROUTES)
def test_fixture_reaches_expected_branch(post, name, step, marker):
    assert marker in post(name)["fulfillmentText"]


@pytest.mark.parametrize("name, step, marker", ROUTES)
def test_only_summary_branch_writes_pdf_and_firestore(post, mocks, name, step, marker):
    post(name)
    expected = 1 if step == 6 else 0
    assert mocks.pdf.call_count == expected
    assert mocks.firebase.save_tax_report.call_count == expected


@pytest.mark.parametrize("name, step, marker", ROUTES)
def test_only_history_branch_reads_firestore(post, mocks, name, step, marker):
    post(name)
    assert mocks.firebase.get_user_history.call_count == (1 if step == 3 else 0)


@pytest.mark.parametrize("name, step, marker", ROUTES)
def test_only_knowledge_question_calls_rag(post, mocks, name, step, marker):
    post(name)
    assert mocks.rag.call_count == (1 if marker == RAG_ANSWER else 0)


# ---------------------------------------------------------------------------
# B. รายละเอียดทีละสาขา
# ---------------------------------------------------------------------------
def test_welcome_sends_no_contexts(post):
    body = post("001_Welcome")
    assert body["fulfillmentText"].startswith("สวัสดีครับ! ยินดีต้อนรับ")
    assert "outputContexts" not in body


def test_history_empty(post, mocks):
    body = post("002_Default_Fallback_Intent")
    mocks.firebase.get_user_history.assert_called_once_with("TEST_USER", limit=3)
    assert body["fulfillmentText"] == HISTORY_EMPTY
    assert "outputContexts" not in body


@pytest.mark.parametrize("name", ["014_99_Reset_Chat", "018_99_Reset_Chat"])
def test_reset_sets_every_context_to_zero(post, name):
    sent = [ctx["name"] for ctx in load(name)["queryResult"]["outputContexts"]]
    body = post(name)
    assert body["fulfillmentText"] == RESET
    # ทุก context ที่ส่งเข้ามา (รวม __system_counters__) ถูกส่งกลับด้วย lifespan 0 ตามลำดับเดิม
    assert [ctx["name"] for ctx in body["outputContexts"]] == sent
    assert all(ctx["lifespanCount"] == 0 for ctx in body["outputContexts"])


def test_start_salary_only(post):
    body = post("003_01_Tax_Interview_Start")
    assert "เริ่มเก็บข้อมูลจาก 'เงินเดือน'" in body["fulfillmentText"]
    assert ctx_list(body) == [
        ("tax_session", 20),
        ("awaiting_salary", 0),
        ("awaiting_rental", 0),
        ("awaiting_online", 0),
        ("awaiting_deduction", 0),
        ("awaiting_salary", 2),   # ตัวท้ายเปิดสาขาถัดไป
    ]


def test_start_finds_salary_in_raw_text(post):
    # กับดักข้อ 5: Dialogflow ตัด "เงินเดือน" ออกจาก income_types แต่ข้อความดิบยังมี
    payload = load("006_01_Tax_Interview_Start")
    assert payload["queryResult"]["parameters"]["income_types"] == ["ค่าเช่า", "ขายของออนไลน์"]
    body = post(payload)
    assert "เริ่มเก็บข้อมูลจาก 'เงินเดือน'" in body["fulfillmentText"]
    assert ctx_list(body)[-1] == ("awaiting_salary", 2)


@pytest.mark.parametrize("name, total, next_prompt, next_ctx", [
    # 50,000 × 12 + โบนัส 50,000
    ("004_02_Tax_Interview_Salary", "650,000.00", "'ค่าลดหย่อน'", "awaiting_deduction"),
    # 30,000 × 12 · income_types มีค่าเช่า จึงถามค่าเช่าต่อ
    ("007_02_Tax_Interview_Salary", "360,000.00", "'ค่าเช่า'", "awaiting_rental"),
    # 50,000 × 12
    ("017_02_Tax_Interview_Salary", "600,000.00", "'ค่าลดหย่อน'", "awaiting_deduction"),
    # 30,000 × 12
    ("020_02_Tax_Interview_Salary", "360,000.00", "'ค่าลดหย่อน'", "awaiting_deduction"),
])
def test_salary_branch(post, name, total, next_prompt, next_ctx):
    body = post(name)
    text = body["fulfillmentText"]
    assert f"- เงินเดือนรวมทั้งปี: {total} บาท" in text
    assert next_prompt in text
    assert ctx_list(body) == [("tax_session", 20), ("awaiting_salary", 0), (next_ctx, 2)]


def test_salary_branch_details_and_carries_params(post):
    body = post("004_02_Tax_Interview_Salary")
    text = body["fulfillmentText"]
    assert "- หักประกันสังคมทั้งปี: 9,000.00 บาท" in text
    assert "- ภาษีหัก ณ ที่จ่าย: 12,000.00 บาท" in text
    # tax_session ส่งค่าที่สะสมกลับไปให้ Dialogflow เก็บต่อ
    params = ctx_params(body, "tax_session")
    assert params["salary_per_month"] == 50000.0
    assert params["income_types"] == ["เงินเดือน"]


def test_rental_branch(post):
    body = post("008_03_Tax_Interview_Rental")
    text = body["fulfillmentText"]
    assert "- ทรัพย์สิน: คอนโด" in text
    assert "- ค่าเช่ารวมทั้งปี: 120,000.00 บาท" in text
    assert "- หักค่าใช้จ่ายเหมา (30%): 36,000.00 บาท" in text  # คอนโด = โรงเรือน 30%
    assert "'ขายของออนไลน์'" in text
    # ปัญหา #4: สาขานี้ไม่ปิด awaiting_rental (ต่างจากสาขาเงินเดือนและออนไลน์)
    assert ctx_list(body) == [("tax_session", 20), ("awaiting_online", 2)]


def test_online_branch_via_context_and_raw_text(post):
    # กับดักข้อ 6: Dialogflow จัด "400000" เป็น Fallback และ parameter ว่าง
    payload = load("009_Default_Fallback_Intent")
    assert payload["queryResult"]["intent"]["displayName"] == "Default Fallback Intent"
    assert payload["queryResult"]["parameters"] == {}
    body = post(payload)
    text = body["fulfillmentText"]
    # ยอดขายมาจาก clean_number(user_query) เพราะ online_income ไม่มีที่ไหนเลย
    assert "- ยอดขายรวมทั้งปี: 400,000.00 บาท" in text
    assert "- หักค่าใช้จ่ายเหมา (60%): 240,000.00 บาท" in text
    assert ctx_params(body, "tax_session")["online_income"] == 400000.0
    assert ctx_list(body) == [("tax_session", 20), ("awaiting_online", 0), ("awaiting_deduction", 2)]


def _rental_without_online():
    # ไม่มี fixture ที่ค่าเช่าเป็นสาขาสุดท้าย (008 มีขายออนไลน์ต่อ) จึงตัดออนไลน์ออกจาก income_types
    payload = load("008_03_Tax_Interview_Rental")
    for ctx in payload["queryResult"]["outputContexts"]:
        if ctx["name"].endswith("/contexts/tax_session"):
            ctx["parameters"]["income_types"] = ["ค่าเช่า"]
            ctx["parameters"]["income_types.original"] = ["ค่าเช่า"]
    return payload


@pytest.mark.parametrize("payload", [
    "004_02_Tax_Interview_Salary",   # เงินเดือน → ค่าลดหย่อน
    _rental_without_online(),        # ค่าเช่า → ค่าลดหย่อน
    "009_Default_Fallback_Intent",   # ขายออนไลน์ → ค่าลดหย่อน
], ids=["salary", "rental", "online"])
def test_deduction_question_asks_yearly(post, payload):
    # #19: โค้ดนับยอดค่าลดหย่อนเป็นยอดทั้งปี คำถามจึงต้องบอกหน่วย
    text = post(payload)["fulfillmentText"]
    # ดูเฉพาะส่วนคำถาม เพราะส่วนสรุปก่อนหน้ามี "รวมทั้งปี" อยู่แล้ว
    question = text.split("'ค่าลดหย่อน'", 1)[1]
    assert "ทั้งปี" in question


@pytest.mark.parametrize("payload", [
    "004_02_Tax_Interview_Salary",   # เงินเดือน → ค่าลดหย่อน
    _rental_without_online(),        # ค่าเช่า → ค่าลดหย่อน
    "009_Default_Fallback_Intent",   # ขายออนไลน์ → ค่าลดหย่อน
], ids=["salary", "rental", "online"])
def test_deduction_question_lists_rmf_and_thai_esg(post, payload):
    # #3: SSF ลดหย่อนไม่ได้แล้วหลังปีภาษี 2567 (docs ข้อ 2.2 แถว SSF [5]) คำถามจึงยกตัวอย่าง RMF และ ThaiESG แทน
    text = post(payload)["fulfillmentText"]
    question = text.split("'ค่าลดหย่อน'", 1)[1]
    assert "RMF" in question
    assert "ThaiESG" in question
    assert "SSF" not in question.upper()


def test_summary_salary_with_child_and_home_loan(post, mocks):
    # 005: คำนวณมือตามกฎหมาย ปีภาษี 2569 (ค่าที่โค้ดใช้อยู่)
    # เงินเดือน 50,000/เดือน โบนัส 50,000 ลูก 1 คน ดอกเบี้ยบ้าน 20,000 ประกันสังคม 9,000 หัก ณ ที่จ่าย 12,000
    #
    # 1. เงินได้ ม.40(1): 50,000 × 12 + 50,000                    = 650,000
    # 2. ค่าใช้จ่าย ม.42 ทวิ: 50% = 325,000 เกินเพดาน 100,000      = 100,000
    # 3. ลดหย่อน:
    #      ส่วนตัว ม.47(1)(ก)                                      =  60,000
    #      บุตร 1 คน ม.47(1)(ค)                                    =  30,000
    #      ประกันสังคมตามที่จ่ายจริง                                =   9,000
    #      ดอกเบี้ยกู้ซื้อบ้าน (ไม่เกิน 100,000)                     =  20,000
    #                                                    รวม       = 119,000
    # 4. เงินได้สุทธิ: 650,000 − 100,000 − 119,000                 = 431,000
    # 5. ภาษีขั้นบันได ม.48(1):
    #      0–150,000 ยกเว้น                                        =       0
    #      150,001–300,000: 150,000 × 5%                           =   7,500
    #      300,001–431,000: 131,000 × 10%                          =  13,100
    #                                                    รวม       =  20,600
    #    วิธีที่ 2 ม.48(2) (0.5%) ใช้กับเงินได้นอกเงินเดือนเท่านั้น ที่นี่ไม่มี = 0
    # 6. หักภาษี ณ ที่จ่าย 12,000 → ชำระเพิ่ม 20,600 − 12,000      =   8,600
    body = post("005_04_Tax_Interview_Deductions")
    text = body["fulfillmentText"]
    for line in summary_lines("650,000.00", "100,000.00", "119,000.00", "431,000.00",
                              "20,600.00", "12,000.00", "ต้องชำระภาษีเพิ่มเติม: 8,600.00 บาท"):
        assert line in text
    data = assert_summary_side_effects(body, mocks)
    assert data["tax_payable"] == pytest.approx(20600.0)
    assert data["net_payable"] == pytest.approx(8600.0)
    # สิทธิที่ยังใช้ได้: ไม่มี RMF/ThaiESG/ประกันชีวิต → 30% × 650,000 = 195,000 (handle_deductions ส่งค่าถูกตัว)
    assert "- กองทุน RMF: ยังใช้สิทธิได้อีก 195,000 บาท" in text
    assert "- กองทุน ThaiESG: ยังใช้สิทธิได้อีก 195,000 บาท" in text
    assert "SSF" not in text


SSF_NOTE = ("หมายเหตุ: SSF ลดหย่อนภาษีไม่ได้แล้วตั้งแต่ปีภาษี 2568 "
            "ตามกฎกระทรวง ฉบับที่ 357 (พ.ศ. 2563) ระบบจึงไม่นำยอด SSF มาคำนวณครับ")


@pytest.mark.parametrize("query_text", ["SSF 50000", "ซื้อ ssf ไว้ 20000", "เอสเอสเอฟ 30000"])
def test_ssf_note_and_ssf_not_counted(post, mocks, query_text):
    # #3: SSF ลดหย่อนได้เฉพาะเงินได้ถึง 31 ธ.ค. 2567 (กฎกระทรวง ฉบับที่ 357 ข้อ 3 — docs ข้อ 2.2 แถว SSF [5])
    # Console ไม่ annotate ยอด SSF เข้า parameter ไหน ssf_rmf จึงว่าง (ค่าใน fixture 021)
    # ค่าลดหย่อนจึงเท่ากับ test_summary_salary_only_no_deductions: 69,000 → เงินได้สุทธิ 191,000 → ภาษี 2,050
    payload = load("021_04_Tax_Interview_Deductions")
    payload["queryResult"]["queryText"] = query_text
    body = post(payload)
    text = body["fulfillmentText"]
    for line in summary_lines("360,000.00", "100,000.00", "69,000.00", "191,000.00",
                              "2,050.00", "0.00", "ต้องชำระภาษีเพิ่มเติม: 2,050.00 บาท"):
        assert line in text
    assert SSF_NOTE in text
    # หมายเหตุอยู่ใน tax_advice จึงไปถึง Firestore และ PDF ด้วย
    _, data = mocks.firebase.save_tax_report.call_args.args
    assert SSF_NOTE in data["tax_advice"]


def test_ssf_note_with_rmf_in_same_message(post):
    # "RMF 100000 SSF 50000": Dialogflow annotate เฉพาะยอด RMF → ssf_rmf = 100,000
    # 3. ลดหย่อน: 69,000 + RMF min(100,000, 30% × 360,000 = 108,000, 500,000) = 169,000
    # 4. เงินได้สุทธิ: 360,000 − 100,000 − 169,000                = 91,000 → อยู่ในขั้นยกเว้น ภาษี 0
    payload = load("021_04_Tax_Interview_Deductions")
    payload["queryResult"]["queryText"] = "RMF 100000 SSF 50000"
    payload["queryResult"]["parameters"]["ssf_rmf"] = 100000.0
    text = post(payload)["fulfillmentText"]
    for line in summary_lines("360,000.00", "100,000.00", "169,000.00", "91,000.00",
                              "0.00", "0.00", "ภาษีที่ชำระไว้พอดีแล้ว"):
        assert line in text
    assert SSF_NOTE in text


def test_thai_esg_counted_in_summary(post):
    # parameter thai_esg จาก Console ต้องเข้าการคำนวณจริง (fixture 021 + ThaiESG 20,000)
    # 3. ลดหย่อน: 69,000 + ThaiESG min(20,000, 30% × 360,000 = 108,000, 300,000)  =  89,000 (docs หัวข้อ 3 [8])
    # 4. เงินได้สุทธิ: 360,000 − 100,000 − 89,000                                = 171,000
    # 5. ภาษี: 150,001–171,000: 21,000 × 5%                                       =   1,050
    # สิทธิ ThaiESG ที่เหลือ: 108,000 − 20,000                                    =  88,000
    payload = load("021_04_Tax_Interview_Deductions")
    payload["queryResult"]["queryText"] = "ThaiESG 20000"
    payload["queryResult"]["parameters"]["thai_esg"] = 20000.0
    text = post(payload)["fulfillmentText"]
    for line in summary_lines("360,000.00", "100,000.00", "89,000.00", "171,000.00",
                              "1,050.00", "0.00", "ต้องชำระภาษีเพิ่มเติม: 1,050.00 บาท"):
        assert line in text
    assert "- กองทุน ThaiESG: ยังใช้สิทธิได้อีก 88,000 บาท" in text


def test_no_ssf_note_without_ssf(post):
    assert "SSF" not in post("021_04_Tax_Interview_Deductions")["fulfillmentText"]


def test_summary_salary_only_no_deductions(post, mocks):
    # 021: คำนวณมือตามกฎหมาย ปีภาษี 2569 ผู้ใช้ตอบ "ไม่มี" ค่าลดหย่อนเพิ่ม
    # เงินเดือน 30,000/เดือน ไม่มีโบนัส ประกันสังคม 9,000 ไม่มีหัก ณ ที่จ่าย
    #
    # 1. เงินได้ ม.40(1): 30,000 × 12                              = 360,000
    # 2. ค่าใช้จ่าย ม.42 ทวิ: 50% = 180,000 เกินเพดาน 100,000      = 100,000
    # 3. ลดหย่อน: ส่วนตัว ม.47(1)(ก) 60,000 + ประกันสังคม 9,000   =  69,000
    # 4. เงินได้สุทธิ: 360,000 − 100,000 − 69,000                  = 191,000
    # 5. ภาษีขั้นบันได: 0–150,000 ยกเว้น
    #      150,001–191,000: 41,000 × 5%                            =   2,050
    # 6. ไม่มีหัก ณ ที่จ่าย → ชำระเพิ่ม                             =   2,050
    body = post("021_04_Tax_Interview_Deductions")
    text = body["fulfillmentText"]
    for line in summary_lines("360,000.00", "100,000.00", "69,000.00", "191,000.00",
                              "2,050.00", "0.00", "ต้องชำระภาษีเพิ่มเติม: 2,050.00 บาท"):
        assert line in text
    data = assert_summary_side_effects(body, mocks)
    assert data["tax_payable"] == pytest.approx(2050.0)


def test_summary_all_income_types_with_spouse(post, mocks):
    # 010: คำนวณมือตามกฎหมาย ปีภาษี 2569
    # เงินเดือน 30,000/เดือน + ค่าเช่าคอนโด 120,000 + ขายของออนไลน์ 400,000
    # คู่สมรสไม่มีเงินได้ ประกันสังคม 9,000 ไม่มีหัก ณ ที่จ่าย
    #
    # 1. เงินได้: ม.40(1) 30,000 × 12 = 360,000 + ม.40(5) 120,000 + ม.40(8) 400,000
    #                                                              = 880,000
    # 2. ค่าใช้จ่าย:
    #      เงินเดือน ม.42 ทวิ: 50% = 180,000 เกินเพดาน             = 100,000
    #      ค่าเช่าคอนโด (โรงเรือน) 30% ตาม พ.ร.ฎ. ฉบับที่ 11 ม.5    =  36,000
    #      ขายของออนไลน์ 60% ตาม พ.ร.ฎ. ฉบับที่ 11 ม.8(25)
    #        (การขายของซึ่งผู้ขายมิได้เป็นผู้ผลิต)                    = 240,000
    #                                                    รวม       = 376,000
    # 3. ลดหย่อน:
    #      ส่วนตัว ม.47(1)(ก)                                      =  60,000
    #      คู่สมรสไม่มีเงินได้ ม.47(1)(ข)                            =  60,000
    #      ประกันสังคม                                             =   9,000
    #                                                    รวม       = 129,000
    # 4. เงินได้สุทธิ: 880,000 − 376,000 − 129,000                 = 375,000
    # 5. ภาษีวิธีที่ 1 ขั้นบันได ม.48(1):
    #      0–150,000 ยกเว้น                                        =       0
    #      150,001–300,000: 150,000 × 5%                           =   7,500
    #      300,001–375,000:  75,000 × 10%                          =   7,500
    #                                                    รวม       =  15,000
    #    ภาษีวิธีที่ 2 ม.48(2): เงินได้นอกเงินเดือน 120,000 + 400,000 = 520,000
    #      (ไม่ต่ำกว่า 120,000 จึงต้องคำนวณ) × 0.5%               =   2,600
    #      ไม่เกิน 5,000 จึงได้รับยกเว้นตาม พ.ร.ฎ. ฉบับที่ 480 พ.ศ. 2552  =       0
    #    เสียตามวิธีที่มากกว่า: max(15,000, 0)                        =  15,000
    # 6. ไม่มีหัก ณ ที่จ่าย → ชำระเพิ่ม                             =  15,000
    body = post("010_04_Tax_Interview_Deductions")
    text = body["fulfillmentText"]
    for line in summary_lines("880,000.00", "376,000.00", "129,000.00", "375,000.00",
                              "15,000.00", "0.00", "ต้องชำระภาษีเพิ่มเติม: 15,000.00 บาท"):
        assert line in text
    # ภาษีมาจากวิธีที่ 1 จึงต้องไม่มีหมายเหตุวิธีที่ 2 ต่อท้ายบรรทัด 5
    assert "(คิดตามวิธีคำนวณร้อยละ 0.5 ของเงินได้ที่ไม่ใช่เงินเดือน เนื่องจากสูงกว่า)" not in text
    data = assert_summary_side_effects(body, mocks)
    assert data["tax_payable"] == pytest.approx(15000.0)


def test_knowledge_question_passes_deadline(post, mocks):
    # กฎเหล็กข้อ 1: webhook ตั้ง deadline = เวลารับ request + 4.0 แล้วส่งต่อ ห้ามตั้งใหม่กลางทาง
    before = time.monotonic()
    body = post("011_Default_Fallback_Intent")
    after = time.monotonic()
    assert body["fulfillmentText"] == RAG_ANSWER
    assert "outputContexts" not in body
    mocks.rag.assert_called_once()
    assert mocks.rag.call_args.args == ("ลดหย่อนบุตรได้เท่าไหร่",)
    deadline = mocks.rag.call_args.kwargs["deadline"]
    assert before + 4.0 <= deadline <= after + 4.0


@pytest.mark.parametrize("name", [
    "012_Default_Fallback_Intent",   # "50000" ไม่มี awaiting_* ค้าง
    "015_Default_Fallback_Intent",   # "30000" หลังรีเซ็ต — ต้องไม่ถูกรับเป็นเงินเดือน
])
def test_bare_number_without_interview_context(post, name):
    body = post(name)
    assert body["fulfillmentText"] == NUMBER_HINT
    assert SALARY not in body["fulfillmentText"]
    assert "outputContexts" not in body


# ---------------------------------------------------------------------------
# C. เคสสังเคราะห์ — แก้ fixture ทีละจุด เพื่อล็อกลำดับการตรวจที่ fixture จริงไม่ครอบ
# ---------------------------------------------------------------------------
def test_greeting_text_overrides_intent(post):
    # ขั้น 1 ดูข้อความล้วนด้วย ไม่ใช่แค่ชื่อ intent
    payload = load("012_Default_Fallback_Intent")
    payload["queryResult"]["queryText"] = "สวัสดี"
    assert GREETING in post(payload)["fulfillmentText"]


def test_default_welcome_intent_still_routes_to_greeting(post):
    # กับดักข้อ 7: ไม่มี intent ชื่อนี้ใน export แต่ห้ามลบเงื่อนไขระหว่าง refactor
    payload = load("001_Welcome")
    payload["queryResult"]["intent"]["displayName"] = "Default Welcome Intent"
    payload["queryResult"]["queryText"] = "อะไรก็ได้"
    assert GREETING in post(payload)["fulfillmentText"]


def test_reset_text_overrides_intent(post):
    # ขั้น 2 (รีเซ็ต) อยู่ก่อนขั้น 4 — พิมพ์ "รีเซ็ต" ระหว่างที่ Dialogflow จับเป็น 02 ก็ยังรีเซ็ต
    payload = load("004_02_Tax_Interview_Salary")
    payload["queryResult"]["queryText"] = "รีเซ็ต"
    body = post(payload)
    assert body["fulfillmentText"] == RESET
    assert [name for name, _ in ctx_list(body)] == ["tax_session", "awaiting_salary", "__system_counters__"]
    assert all(lifespan == 0 for _, lifespan in ctx_list(body))


def test_history_formats_records(post, mocks):
    # รูปแบบข้อความประวัติ — ค่าจากการรันจริง
    mocks.firebase.get_user_history.return_value = [{
        "created_at": "01/10/2026 10:00:00",
        "total_income": 360000.0,
        "tax_payable": 2050.0,
        "pdf_file_url": "https://example.test/download/tax_report_x.pdf",
    }]
    body = post("002_Default_Fallback_Intent")
    assert body["fulfillmentText"] == (
        "📜 ประวัติการคำนวณภาษีล่าสุดของคุณ:\n\n"
        "📅 01/10/2026 10:00:00\n"
        "- เงินได้รวม: 360,000.00 บาท\n"
        "- ภาษีสุทธิ: 2,050.00 บาท\n"
        "  [ดาวน์โหลด PDF: https://example.test/download/tax_report_x.pdf]"
    )


def test_current_behavior_raw_text_is_what_finds_salary(post):
    # คู่กับ test_start_finds_salary_in_raw_text: ตัด "เงินเดือน" ออกจากข้อความดิบแล้วเงินเดือนหาย
    # ยืนยันว่าเทส 006 ผ่านเพราะการเช็กข้อความดิบ — ถ้าลบการเช็กนั้น เทส 006 จะล้ม
    payload = load("006_01_Tax_Interview_Start")
    payload["queryResult"]["queryText"] = "ค่าเช่า และขายของออนไลน์"
    body = post(payload)
    assert "เริ่มเก็บข้อมูลจาก 'ค่าเช่า'" in body["fulfillmentText"]
    assert ctx_list(body)[-1] == ("awaiting_rental", 2)


def test_current_behavior_online_context_preempts_deductions(post, mocks):
    # ปัญหา #5: awaiting_online ค้างอยู่ แม้ Dialogflow จับได้ 04 ก็เข้าสาขาออนไลน์ก่อน (ขั้น 5 ก่อนขั้น 6)
    payload = load("010_04_Tax_Interview_Deductions")
    payload["queryResult"]["outputContexts"].append(
        {"name": f"{SESSION}/contexts/awaiting_online", "lifespanCount": 1}
    )
    body = post(payload)
    assert ONLINE in body["fulfillmentText"]
    assert "- ยอดขายรวมทั้งปี: 400,000.00 บาท" in body["fulfillmentText"]
    mocks.pdf.assert_not_called()
    mocks.firebase.save_tax_report.assert_not_called()


def test_current_behavior_deductions_with_zero_income(post, mocks):
    # สาขา 04 เมื่อไม่มีรายได้เลย (เช่น context หมดอายุ) — หยุดก่อนคำนวณ
    payload = load("021_04_Tax_Interview_Deductions")
    for ctx in payload["queryResult"]["outputContexts"]:
        if ctx["name"].endswith("/contexts/tax_session"):
            ctx["parameters"]["salary_per_month"] = 0.0
    body = post(payload)
    assert body["fulfillmentText"] == NO_INCOME
    assert ctx_list(body) == [("tax_session", 0), ("awaiting_deduction", 0)]
    mocks.pdf.assert_not_called()
    mocks.firebase.save_tax_report.assert_not_called()


def test_pdf_link_uses_public_url_at_request_time(post, mocks, monkeypatch):
    # กับดักข้อ 2: PUBLIC_URL ถูกเขียนทับตอนรัน ต้องอ่านค่าตอนรับ request
    # ถ้า refactor ไปใช้ from app import PUBLIC_URL จะได้ "" ที่คัดลอกไว้ตอน import แล้วเทสนี้ล้ม
    monkeypatch.setattr(app_module, "PUBLIC_URL", "https://example.test")
    body = post("021_04_Tax_Interview_Deductions")
    prefix = "https://example.test/download/tax_report_"
    assert f"📄 {PDF_LINK}: {prefix}" in body["fulfillmentText"]
    _, data = mocks.firebase.save_tax_report.call_args.args
    assert data["pdf_file_url"].startswith(prefix)
    assert data["pdf_file_url"].endswith(".pdf")


def test_current_behavior_pdf_failure_returns_error_text(post, mocks):
    # สร้าง PDF ล้ม → except กว้างของ webhook() ตอบข้อความ error และไม่ได้บันทึก Firestore
    mocks.pdf.side_effect = RuntimeError("disk full")
    post("021_04_Tax_Interview_Deductions", expect_error=True)
    mocks.firebase.save_tax_report.assert_not_called()
