"""
เทสการโหลดค่าที่ระบบ Admin แก้จาก Firestore ตอนเปิดเซิร์ฟเวอร์
(utils/rules_store.load_effective_rules และ app.apply_effective_rules)

ใช้ db ปลอม (MagicMock) เท่านั้น ไม่ต่อ Firestore จริง
ผลมีได้ 4 แบบ: ไม่มี doc / doc ถูก / doc ผิด / อ่านไม่ได้ — ทุกแบบยกเว้น doc ถูกต้องได้ค่าตั้งต้น
เทสที่เรียก apply_effective_rules ใช้ fixture apply_doc ซึ่งให้ monkeypatch คืน TAX_RULES หลังจบเทส
"""
import copy
import os
import subprocess
import sys
import threading
import time
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# โมดูลปลอมแทน utils.firebase_db จาก conftest.py ต้องอยู่ก่อน import app (เหมือน test_webhook.py)
assert isinstance(sys.modules["utils.firebase_db"].firebase_client, MagicMock), \
    "utils.firebase_db ไม่ใช่โมดูลปลอม — conftest.py ไม่ได้ทำงานหรือมีคน import ของจริงไปก่อน"

import app as app_module  # noqa: E402
import utils.knowledge_search as knowledge_search  # noqa: E402
from utils import rules_store, tax_rules  # noqa: E402
from utils.rules_store import DEFAULT_TAX_RULES, load_effective_rules  # noqa: E402
# ข้อมูล 21 กรณีที่ทดสอบใน LINE แล้ว — import อย่างเดียว ไม่แก้ไฟล์นั้น
from test_line_verified_cases import LINE_CASES, line_payload  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
YEAR = DEFAULT_TAX_RULES["tax_year"]
PERSONAL = DEFAULT_TAX_RULES["deduction"]["personal"]


def fake_db(doc=None, get_side_effect=None):
    """db ปลอมที่คืน doc ที่กำหนด (None = ไม่มี doc)"""
    db = MagicMock(name="fake_db")
    snapshot = MagicMock(name="snapshot")
    snapshot.exists = doc is not None
    snapshot.to_dict.return_value = copy.deepcopy(doc)
    get = db.collection.return_value.document.return_value.get
    if get_side_effect is not None:
        get.side_effect = get_side_effect
    else:
        get.return_value = snapshot
    return db


def config_doc(override, **fields):
    doc = {"based_on_tax_year": YEAR, "version": 3, "override": override}
    doc.update(fields)
    return doc


# override ที่ถ้าหลุดเข้าไปได้ภาษีจะเปลี่ยนแน่นอน — doc ผิดทุกแบบข้างล่างพกค่านี้ไปด้วย
# เทสภาษีจึงพิสูจน์ได้ว่า "ถูกปฏิเสธ" ไม่ใช่แค่ "ค่าที่ผิดไม่มีผลกับเคสนี้"
TAX_CHANGING = {"deduction": {"personal": PERSONAL + 50000.0}}

BAD_DOCS = {
    "year_mismatch": config_doc(TAX_CHANGING, based_on_tax_year=YEAR - 1),
    "year_missing": {"version": 3, "override": TAX_CHANGING},
    "year_as_string": config_doc(TAX_CHANGING, based_on_tax_year=str(YEAR)),
    "version_zero": config_doc(TAX_CHANGING, version=0),
    "version_bool": config_doc(TAX_CHANGING, version=True),
    "version_missing": {"based_on_tax_year": YEAR, "override": TAX_CHANGING},
    "override_missing": {"based_on_tax_year": YEAR, "version": 3},
    "override_not_dict": config_doc(["deduction"]),
    "unknown_key": config_doc({**TAX_CHANGING, "salary_capp": 1.0}),
    "forbidden_key": config_doc({"deduction": {**TAX_CHANGING["deduction"], "parent_max_count": 9}}),
    "negative_value": config_doc({"deduction": {"personal": -1.0}}),
    "bad_bracket_rate": config_doc({**TAX_CHANGING, "tax_brackets": [
        {"upper": upper, "rate": 0.055 if i == 1 else rate}
        for i, (upper, rate) in enumerate(DEFAULT_TAX_RULES["tax_brackets"])]}),
    "cross_key": config_doc({"deduction": {**TAX_CHANGING["deduction"], "health_insurance_cap": 999999.0}}),
}


def _forbidden(name):
    def _fail(*args, **kwargs):
        pytest.fail(f"{name} ถูกเรียกระหว่างเทส — ต้องไม่โหลดโมเดลหรือเรียก Ollama จริง")
    return _fail


@pytest.fixture
def mocks(monkeypatch, tmp_path):
    # แทนของที่ออกนอกระบบ (Firestore, PDF, RAG) แบบเดียวกับ test_line_verified_cases.py
    m = types.SimpleNamespace(
        firebase=MagicMock(name="firebase_client"),
        pdf=MagicMock(name="generate_tax_pdf"),
        rag=MagicMock(name="query_tax_knowledge"),
    )
    monkeypatch.setattr(app_module, "firebase_client", m.firebase)
    monkeypatch.setattr(app_module, "generate_tax_pdf", m.pdf)
    monkeypatch.setattr(app_module, "query_tax_knowledge", m.rag)
    monkeypatch.setattr(knowledge_search, "get_db", _forbidden("get_db"))
    monkeypatch.setattr(knowledge_search, "ask_llm", _forbidden("ask_llm"))
    monkeypatch.chdir(tmp_path)  # static/reports ที่หลุดออกมาไม่ลง repo
    return m


@pytest.fixture
def apply_doc(monkeypatch):
    """เรียก app.apply_effective_rules แบบที่ __main__ เรียก แล้วให้ monkeypatch คืน TAX_RULES หลังเทส"""
    def _apply(db):
        monkeypatch.setattr(tax_rules, "TAX_RULES", tax_rules.TAX_RULES)
        monkeypatch.setattr(tax_rules, "RULES_INFO", tax_rules.RULES_INFO)
        return app_module.apply_effective_rules(db)
    return _apply


def run_case(query_text, params, mocks):
    """ยิงกรณีจาก LINE ผ่าน /webhook คืน dict ที่ส่งเข้า Firestore (ค่าเดียวกับ LINE และ PDF)"""
    mocks.firebase.save_tax_report.reset_mock()
    resp = app_module.app.test_client().post("/webhook", json=line_payload(query_text, params))
    assert resp.status_code == 200
    _, data = mocks.firebase.save_tax_report.call_args.args
    return data


# ---------------------------------------------------------------------------
# A. ผล 4 แบบของ load_effective_rules
# ---------------------------------------------------------------------------
def test_no_doc_uses_defaults_with_info_log(capsys):
    rules, info = load_effective_rules(fake_db(None))
    assert rules == DEFAULT_TAX_RULES
    assert rules is not DEFAULT_TAX_RULES  # สำเนาใหม่ แก้ในที่แล้วค่าตั้งต้นไม่เปลี่ยน
    assert info == {"source": "default", "version": None, "changed_keys": []}
    out = capsys.readouterr().out
    assert out.count("\n") == 1  # บรรทัดเดียว
    assert out.startswith("ℹ️ [TAX-RULES]")


def test_valid_doc_is_used_and_log_names_keys_only(capsys):
    override = {"deduction": {"personal": 70000.0}, "expense": {"online_sale_rate": 0.5}}
    rules, info = load_effective_rules(fake_db(config_doc(override, version=7)))
    assert rules["deduction"]["personal"] == 70000.0
    assert rules["expense"]["online_sale_rate"] == 0.5
    assert info == {"source": "firestore", "version": 7,
                    "changed_keys": ["expense.online_sale_rate", "deduction.personal"]}
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    assert out.startswith("✅ [TAX-RULES]")
    assert "version 7" in out
    assert "deduction.personal" in out and "expense.online_sale_rate" in out
    # ไม่พิมพ์ค่าใน doc (ไม่เช็กอัตรา 0.5 เพราะชนกับเวลาที่ใช้ใน log ได้)
    assert "70000" not in out and "70,000" not in out


def test_valid_doc_equal_to_defaults_has_no_changed_keys():
    rules, info = load_effective_rules(fake_db(config_doc(rules_store.to_override(DEFAULT_TAX_RULES))))
    assert rules == DEFAULT_TAX_RULES
    assert info == {"source": "firestore", "version": 3, "changed_keys": []}


def test_doc_extra_top_level_fields_are_ignored():
    # เวลา ผู้แก้ แหล่งอ้างอิง อยู่ใน doc ได้ ตัวโหลดไม่อ่าน
    doc = config_doc({"deduction": {"personal": 70000.0}}, reference="ประกาศ", updated_by="admin")
    rules, info = load_effective_rules(fake_db(doc))
    assert info["source"] == "firestore"


@pytest.mark.parametrize("name", BAD_DOCS)
def test_bad_doc_uses_defaults_with_error_log(name, capsys):
    rules, info = load_effective_rules(fake_db(BAD_DOCS[name]))
    assert rules == DEFAULT_TAX_RULES
    assert info == {"source": "default", "version": None, "changed_keys": []}
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    assert out.startswith("❌ [TAX-RULES]")


@pytest.mark.parametrize("name, key_in_log", [
    ("year_mismatch", "based_on_tax_year"),
    ("version_zero", "version"),
    ("override_missing", "override"),
    ("unknown_key", "salary_capp"),
    ("forbidden_key", "deduction.parent_max_count"),
    ("negative_value", "deduction.personal"),
    ("bad_bracket_rate", "tax_brackets"),
    ("cross_key", "deduction.health_insurance_cap"),
])
def test_bad_doc_log_names_the_key(name, key_in_log, capsys):
    load_effective_rules(fake_db(BAD_DOCS[name]))
    out = capsys.readouterr().out
    assert key_in_log in out
    assert "999999" not in out and "999,999" not in out  # ไม่พิมพ์ค่าใน doc


def test_bad_doc_reports_every_error(capsys):
    doc = config_doc({"salary_capp": 1.0}, based_on_tax_year=YEAR - 1, version=0)
    load_effective_rules(fake_db(doc))
    out = capsys.readouterr().out
    assert "based_on_tax_year" in out and "version" in out and "salary_capp" in out


def test_no_database_uses_defaults_with_error_log(capsys):
    # FirebaseManager ต่อไม่ได้ตอนเปิด → db เป็น None
    rules, info = load_effective_rules(None)
    assert rules == DEFAULT_TAX_RULES
    assert info["source"] == "default"
    assert capsys.readouterr().out.startswith("❌ [TAX-RULES]")


def test_get_exception_uses_defaults_and_hides_message(capsys):
    db = fake_db(get_side_effect=RuntimeError("ข้อความลับจาก doc"))
    rules, info = load_effective_rules(db)
    assert rules == DEFAULT_TAX_RULES
    assert info["source"] == "default"
    out = capsys.readouterr().out
    assert out.startswith("❌ [TAX-RULES]")
    assert "RuntimeError" in out
    assert "ข้อความลับ" not in out


def test_to_dict_exception_uses_defaults(capsys):
    db = fake_db({"version": 1})
    db.collection.return_value.document.return_value.get.return_value.to_dict.side_effect = ValueError()
    rules, info = load_effective_rules(db)
    assert rules == DEFAULT_TAX_RULES
    assert capsys.readouterr().out.startswith("❌ [TAX-RULES]")


def test_get_that_hangs_times_out(monkeypatch, capsys):
    # get ค้าง (เน็ตค้าง) ต้องไม่ทำให้เซิร์ฟเวอร์เปิดไม่ขึ้น
    monkeypatch.setattr(rules_store, "READ_TIMEOUT_SECONDS", 0.2)
    release = threading.Event()
    db = fake_db(get_side_effect=lambda **kwargs: release.wait(10))
    try:
        started = time.monotonic()
        rules, info = load_effective_rules(db)
        elapsed = time.monotonic() - started
    finally:
        release.set()  # ปล่อย thread ที่ค้างให้จบ
    assert elapsed < 2
    assert rules == DEFAULT_TAX_RULES
    assert info["source"] == "default"
    out = capsys.readouterr().out
    assert out.startswith("❌ [TAX-RULES]") and "TimeoutError" in out


def test_reads_one_doc_with_timeout_and_no_retry():
    db = fake_db(None)
    load_effective_rules(db)
    db.collection.assert_called_once_with("tax_rules_config")
    db.collection.return_value.document.assert_called_once_with("active")
    db.collection.return_value.document.return_value.get.assert_called_once_with(
        timeout=rules_store.READ_TIMEOUT_SECONDS, retry=None)


# ---------------------------------------------------------------------------
# B. apply_effective_rules (ที่ __main__ เรียก)
# ---------------------------------------------------------------------------
def test_apply_replaces_tax_rules_and_returns_info(apply_doc):
    info = apply_doc(fake_db(config_doc({"deduction": {"personal": 70000.0}})))
    assert info["source"] == "firestore"
    assert tax_rules.TAX_RULES["deduction"]["personal"] == 70000.0


def test_apply_with_bad_doc_keeps_defaults(apply_doc):
    apply_doc(fake_db(BAD_DOCS["unknown_key"]))
    assert tax_rules.TAX_RULES == DEFAULT_TAX_RULES


def test_apply_with_no_database_keeps_defaults(apply_doc, capsys):
    # FirebaseManager ต่อไม่ได้ → firebase_client.db เป็น None → ใช้ค่าตั้งต้น มี log ไม่โยน exception
    info = apply_doc(None)
    assert info == {"source": "default", "version": None, "changed_keys": []}
    assert tax_rules.TAX_RULES == DEFAULT_TAX_RULES
    assert capsys.readouterr().out.startswith("❌ [TAX-RULES]")


# รันบล็อก __main__ ของ app.py ใน process ใหม่: firebase ต่อไม่ได้ (db = None), ngrok และ app.run เป็นของปลอม
# GET_DB_LINE แทน get_db ตัวจริง (ไม่โหลดโมเดลจริง)
GET_DB_OK = "knowledge_search.get_db = lambda: None\n"
GET_DB_FAILS = ("def _get_db_fails():\n"
                "    raise OSError('model load failed')\n"
                "knowledge_search.get_db = _get_db_fails\n")
MAIN_BLOCK_TEMPLATE = (
    "import runpy, sys, types\n"
    "from unittest.mock import MagicMock\n"
    "fake_fb = types.ModuleType('utils.firebase_db')\n"
    "fake_fb.firebase_client = MagicMock()\n"
    "fake_fb.firebase_client.db = None\n"
    "sys.modules['utils.firebase_db'] = fake_fb\n"
    "fake_pyngrok = types.ModuleType('pyngrok')\n"
    "fake_pyngrok.ngrok = MagicMock()\n"
    "fake_pyngrok.ngrok.connect.return_value.public_url = 'https://example.invalid'\n"
    "sys.modules['pyngrok'] = fake_pyngrok\n"
    "sys.modules['pyngrok.ngrok'] = fake_pyngrok.ngrok\n"
    "import flask\n"
    "flask.Flask.run = lambda self, *args, **kwargs: print('REACHED app.run')\n"
    "import utils.knowledge_search as knowledge_search\n"
    "GET_DB_LINE"
    "runpy.run_path('app.py', run_name='__main__')\n"
)


def run_main_block(get_db_line):
    code = MAIN_BLOCK_TEMPLATE.replace("GET_DB_LINE", get_db_line)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8", env=env)


def test_main_block_without_firestore_logs_and_reaches_app_run():
    result = run_main_block(GET_DB_OK)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    tax_rules_lines = [line for line in lines if "[TAX-RULES]" in line]
    assert len(tax_rules_lines) == 1 and tax_rules_lines[0].startswith("❌")
    # log [TAX-RULES] มาก่อน app.run และเซิร์ฟเวอร์ไปถึง app.run
    assert lines.index(tax_rules_lines[0]) < lines.index("REACHED app.run")


def test_main_block_stops_when_knowledge_base_fails_to_load():
    # ตั้งใจ ไม่ใช่บั๊ก: โหลดฐานความรู้ไม่ได้ → เซิร์ฟเวอร์เปิดไม่ขึ้น (ตัดสินใจ 2026-10-13)
    # ถ้าเปิดขึ้นมาโดยไม่มีฐานความรู้ คำถามความรู้ทุกข้อจะพยายามโหลดโมเดลใหม่จนเกินงบ 5 วินาทีโดยไม่มีใครรู้
    # ล้มตอนเปิดทำให้เห็นทันที — ถ้าจะเปลี่ยนพฤติกรรมนี้ต้องเป็นการตัดสินใจใหม่ ไม่ใช่แก้เทสให้ผ่าน
    result = run_main_block(GET_DB_FAILS)
    assert result.returncode != 0
    assert "OSError" in result.stderr
    assert "REACHED app.run" not in result.stdout


def test_main_block_applies_rules_before_app_run():
    # บล็อก __main__ ไม่ถูกรันในเทส — เช็กแบบ substring เหมือน test_liff_page.py
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    main_block = source.split("if __name__ == '__main__':", 1)[1]
    assert "apply_effective_rules(firebase_client.db)" in main_block.split("app.run(", 1)[0]
    # และไม่มีการเรียกที่อื่น (ตอน import หรือใน webhook)
    assert source.count("apply_effective_rules(") == 2  # นิยาม 1 + เรียกใน __main__ 1


def test_import_app_does_not_touch_firebase():
    # import app ใน process ใหม่ (process นี้ import app ไปแล้ว) ด้วย firebase ปลอมที่นับการเรียก
    code = (
        "import sys, types\n"
        "from unittest.mock import MagicMock\n"
        "fake = types.ModuleType('utils.firebase_db')\n"
        "fake.firebase_client = MagicMock()\n"
        "sys.modules['utils.firebase_db'] = fake\n"
        "import app\n"
        "from utils import rules_store, tax_rules\n"
        "print(len(fake.firebase_client.mock_calls), tax_rules.TAX_RULES == rules_store.DEFAULT_TAX_RULES)\n"
    )
    # import app พิมพ์ภาษาไทยและ emoji (เช่น ลงทะเบียนฟอนต์) — บังคับ UTF-8 ทั้งฝั่งลูกและฝั่งอ่าน
    # ไม่งั้น Windows ใช้ cp874 แล้วพิมพ์ emoji ไม่ได้
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True,
                            text=True, encoding="utf-8", env=env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "0 True"


def test_webhook_does_not_read_rules_doc(mocks):
    for query_text, params, _ in LINE_CASES:
        run_case(query_text, params, mocks)
    names = [c.args[0] for c in mocks.firebase.db.collection.call_args_list if c.args]
    assert "tax_rules_config" not in names


def test_leak_guard_is_active(request):
    # fixture กันค่ารั่วใน conftest.py ต้องครอบทุกเทส — ถ้าหายไป ไม่มีเทสไหนล้มเอง
    # เพราะเทสที่ commit ไว้ไม่ได้ทำค่ารั่ว จึงต้องเช็กว่ามันยังทำงานอยู่
    assert "tax_rules_not_leaked" in request.fixturenames


# ---------------------------------------------------------------------------
# C. ผลต่อภาษี
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", BAD_DOCS)
def test_bad_doc_does_not_change_line_verified_tax(name, apply_doc, mocks):
    apply_doc(fake_db(BAD_DOCS[name]))
    for query_text, params, tax in LINE_CASES:
        data = run_case(query_text, params, mocks)
        assert data["tax_payable"] == pytest.approx(tax), query_text


@pytest.mark.parametrize("side_effect", [RuntimeError(), TimeoutError()])
def test_read_failure_does_not_change_line_verified_tax(side_effect, apply_doc, mocks):
    apply_doc(fake_db(get_side_effect=side_effect))
    for query_text, params, tax in LINE_CASES:
        data = run_case(query_text, params, mocks)
        assert data["tax_payable"] == pytest.approx(tax), query_text


# เพิ่มค่าลดหย่อนส่วนตัว EXTRA ผ่าน doc ปลอม แล้วเทียบเงินได้สุทธิก่อน/หลัง
# ผลที่คาดหวังมาจากนิยามของค่าลดหย่อนและเพดานบริจาค ไม่พึ่งสูตรขั้นบันได
# 21 กรณีแบ่งเป็น 3 กลุ่ม (19 + 1 + 1) แต่ละกลุ่มมีเทสของตัวเองพร้อมเหตุผล
EXTRA = 10000.0

# หมายเหตุที่ handle_deductions ต่อท้าย tax_advice เมื่อเงินบริจาคถูกตัดที่ donation_rate
DONATION_CAPPED_NOTE = "หมายเหตุ: เงินบริจาคใช้ลดหย่อนได้"


@pytest.fixture
def before_after(apply_doc, mocks):
    """[(query_text, ก่อน, หลัง)] ของทั้ง 21 กรณี — ก่อน = ค่าตั้งต้น หลัง = personal เพิ่ม EXTRA ผ่าน doc ปลอม"""
    before = {q: run_case(q, p, mocks) for q, p, _ in LINE_CASES}
    apply_doc(fake_db(config_doc({"deduction": {"personal": PERSONAL + EXTRA}})))
    return [(q, before[q], run_case(q, p, mocks)) for q, p, _ in LINE_CASES]


def _capped(data):
    return DONATION_CAPPED_NOTE in data["tax_advice"]


def test_valid_doc_lowers_net_income_by_extra_and_lowers_tax(before_after):
    # กรณีที่เสียภาษีและบริจาคไม่ถูกตัด: ค่าลดหย่อนเพิ่ม EXTRA → เงินได้สุทธิลด EXTRA พอดี ภาษีต้องลดลง
    group = [(q, b, a) for q, b, a in before_after if b["tax_payable"] > 0 and not _capped(b)]
    assert len(group) == 19
    for query_text, before, after in group:
        assert before["net_income"] >= EXTRA, query_text  # ไม่ติดขอบที่ 0
        assert after["net_income"] == pytest.approx(before["net_income"] - EXTRA), query_text
        assert after["tax_payable"] < before["tax_payable"], query_text


def test_valid_doc_zero_tax_case_net_income_drops_tax_stays_zero(before_after):
    # กรณีลดหย่อนครบ 8 รายการ: ภาษีเป็น 0 เพราะเงินได้สุทธิไม่เกินเพดานขั้นแรกของขั้นบันไดซึ่งมีอัตรา 0
    # เงินได้สุทธิยังลด EXTRA พอดี (เดิมมากกว่า EXTRA จึงไม่ติดขอบที่ 0) แต่ภาษีลดต่อไม่ได้ ต้องเป็น 0 เท่าเดิม
    first_upper, first_rate = DEFAULT_TAX_RULES["tax_brackets"][0]
    group = [(q, b, a) for q, b, a in before_after if b["tax_payable"] == 0]
    assert len(group) == 1
    query_text, before, after = group[0]
    assert query_text.startswith("ประกันชีวิต 20000, ประกันสุขภาพ 10000")
    assert first_rate == 0 and before["net_income"] <= first_upper
    assert not _capped(before)
    assert before["net_income"] >= EXTRA
    assert after["net_income"] == pytest.approx(before["net_income"] - EXTRA)
    assert after["tax_payable"] == 0


def test_valid_doc_capped_donation_net_income_drops_by_extra_times_one_minus_rate(before_after):
    # กรณีบริจาค 102,773: เพดานบริจาค = donation_rate × เงินได้ก่อนหักบริจาค
    # ค่าลดหย่อนเพิ่ม EXTRA → ฐานลด EXTRA → เพดานลด donation_rate × EXTRA
    # เงินได้สุทธิ = ฐาน − เพดาน จึงลด EXTRA × (1 − donation_rate) พอดี (ต้องถูกตัดทั้งก่อนและหลัง)
    donation_rate = DEFAULT_TAX_RULES["deduction"]["donation_rate"]
    group = [(q, b, a) for q, b, a in before_after if _capped(b)]
    assert len(group) == 1
    query_text, before, after = group[0]
    assert query_text == "บริจาค 102773"
    assert _capped(after)
    assert after["net_income"] == pytest.approx(before["net_income"] - EXTRA * (1 - donation_rate))
    assert after["tax_payable"] < before["tax_payable"]
