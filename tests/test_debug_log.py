"""utils/debug_log.py — สวิตช์ TAX_DEBUG (ค่าตั้งต้นปิด) และบรรทัดสถานะตอนเปิดเซิร์ฟเวอร์"""
import ast
from pathlib import Path

import pytest

import app as app_module
from utils import debug_log


@pytest.mark.parametrize("value, expected", [
    (None, False), ("", False), ("0", False), ("true", False), ("yes", False), ("  ", False),
    ("1", True), (" 1 ", True),
], ids=["unset", "empty", "zero", "true", "yes", "spaces", "one", "one-spaces"])
def test_debug_enabled_only_for_1(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("TAX_DEBUG", raising=False)
    else:
        monkeypatch.setenv("TAX_DEBUG", value)
    assert debug_log.debug_enabled() is expected


def test_debug_enabled_reads_env_each_call(monkeypatch):
    # อ่าน env ตอนถูกเรียก ไม่ล็อกค่าไว้ตอน import (CLAUDE.md กับดักข้อ 9)
    monkeypatch.delenv("TAX_DEBUG", raising=False)
    assert debug_log.debug_enabled() is False
    monkeypatch.setenv("TAX_DEBUG", "1")
    assert debug_log.debug_enabled() is True


def test_status_line(monkeypatch):
    monkeypatch.delenv("TAX_DEBUG", raising=False)
    assert "[TAX-DEBUG] ปิดอยู่" in debug_log.debug_status_line()
    monkeypatch.setenv("TAX_DEBUG", "1")
    assert "[TAX-DEBUG] เปิดอยู่" in debug_log.debug_status_line()


def test_status_printed_at_startup():
    # บล็อก __main__ ไม่ถูกรันในเทส — เช็กว่าเรียกพิมพ์ไว้ก่อน app.run แบบ substring เหมือน test_liff_page.py
    source = open(app_module.__file__, encoding="utf-8").read()
    main_block = source.split("if __name__ == '__main__':", 1)[1]
    assert "print(debug_log.debug_status_line())" in main_block.split("app.run(", 1)[0]


def test_imports_only_os():
    # app.py และ knowledge_search.py import ไฟล์นี้ — ถ้าไฟล์นี้ import อย่างอื่นของโปรเจกต์ อาจเกิด import วน
    tree = ast.parse(Path(debug_log.__file__).read_text(encoding="utf-8"))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module)
    assert imported == ["os"]
