"""utils/knowledge_search.py — ข้อความตอบเมื่อคำถามอยู่นอกขอบเขตฐานความรู้ และค่าที่ ask_llm ส่งให้ Ollama
FAISS ปลอมด้วย MagicMock (ไม่โหลดโมเดล embedding จริง — กับดักข้อ 4) ไม่เรียก Ollama จริง
(ask_llm ปลอม หรือ requests.post ปลอม)
เส้นทางผ่าน /webhook และการลงท้าย "ครับ" ทดสอบใน test_webhook.py ส่วน E (เคส out_of_scope)
"""
import json
import time
from unittest.mock import MagicMock

import pytest

import utils.knowledge_search as knowledge_search

THRESHOLD = knowledge_search.TAX_DISTANCE_THRESHOLD


def doc(text="เนื้อหาจากฐานความรู้"):
    return MagicMock(page_content=text)


@pytest.fixture
def ask(monkeypatch):
    """ถามด้วยผลค้นที่กำหนดเอง คืน (คำตอบ, ask_llm ปลอม)"""
    def _ask(results, llm_answer=None):
        fake_db = MagicMock()
        fake_db.similarity_search_with_score.return_value = results
        monkeypatch.setattr(knowledge_search, "get_db", lambda: fake_db)
        llm = MagicMock(return_value=llm_answer)
        monkeypatch.setattr(knowledge_search, "ask_llm", llm)
        return knowledge_search.query_tax_knowledge("คำถามทดสอบ"), llm
    return _ask


def test_beyond_threshold_gets_out_of_scope_reply(ask):
    answer, llm = ask([(doc(), THRESHOLD + 0.01), (doc(), THRESHOLD + 5)])
    assert answer == knowledge_search.OUT_OF_SCOPE_REPLY
    llm.assert_not_called()  # ไม่มี chunk ที่ใช้ได้ ไม่ต้องเสียเวลาเรียก LLM


def test_no_results_gets_out_of_scope_reply(ask):
    answer, llm = ask([])
    assert answer == knowledge_search.OUT_OF_SCOPE_REPLY
    llm.assert_not_called()


def test_chunk_at_threshold_is_still_used(ask):
    # ขอบ threshold ไม่เปลี่ยน (score <= threshold ใช้ได้) — LLM ไม่ตอบ จึงได้ข้อความดิบจากฐานความรู้
    answer, llm = ask([(doc("ลดหย่อนบุตรคนละ 30,000 บาท"), THRESHOLD)], llm_answer=None)
    assert answer == "📖 ข้อมูลจากฐานข้อมูลภาษี:\nลดหย่อนบุตรคนละ 30,000 บาท"
    llm.assert_called_once()


def test_out_of_scope_reply_says_what_bot_can_do():
    reply = knowledge_search.OUT_OF_SCOPE_REPLY
    lines = reply.split("\n")
    assert "วิธีใช้" in lines  # คำสั่งอยู่บรรทัดของตัวเอง
    for topic in ("คำนวณภาษี", "ดูประวัติ", "คำถามความรู้"):
        assert topic in reply
    assert "เมนู" in reply


def test_ask_llm_request_settings(monkeypatch):
    # keep_alive 10h ครอบช่วงเปิดทดสอบ ส่วน timeout และ num_predict ต้องเท่าเดิม
    # (CLAUDE.md กฎเหล็กข้อ 1: ห้ามเพิ่ม timeout ของการเรียก Ollama)
    response = MagicMock(status_code=200)
    response.iter_lines.return_value = [json.dumps({"response": "คำตอบ", "done": True}).encode("utf-8")]
    post = MagicMock()
    post.return_value.__enter__.return_value = response
    monkeypatch.setattr(knowledge_search.requests, "post", post)

    assert knowledge_search.ask_llm("prompt", deadline=time.monotonic() + 10) == "คำตอบ"

    post.assert_called_once()
    (url,), kwargs = post.call_args
    assert url == "http://127.0.0.1:11434/api/generate"
    assert kwargs["json"]["model"] == "llama3.2:3b"
    assert kwargs["json"]["keep_alive"] == "10h"
    assert kwargs["json"]["options"] == {"temperature": 0, "num_predict": 120}
    assert kwargs["timeout"] == (1.0, 3.0)
    assert kwargs["stream"] is True
