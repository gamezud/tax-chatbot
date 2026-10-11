import subprocess
import sys
from pathlib import Path

from utils.create_knowledge import knowledge_sha256

ROOT = Path(__file__).resolve().parent.parent


def test_knowledge_hash_ignores_line_endings(tmp_path):
    # git บน Windows (core.autocrlf=true) แปลง tax_knowledge.txt เป็น CRLF ส่วนเครื่องที่ clone ไปอาจได้ LF
    # hash ต้องเท่ากัน ไม่อย่างนั้นเทสเทียบ hash จะผ่านในเครื่องหนึ่งแต่ล้มอีกเครื่อง
    lf = tmp_path / "lf.txt"
    crlf = tmp_path / "crlf.txt"
    lf.write_bytes("# ภาษี\nประกันสังคม 10,500\n".encode("utf-8"))
    crlf.write_bytes("# ภาษี\r\nประกันสังคม 10,500\r\n".encode("utf-8"))
    assert knowledge_sha256(lf) == knowledge_sha256(crlf)

    changed = tmp_path / "changed.txt"
    changed.write_bytes("# ภาษี\nประกันสังคม 10,501\n".encode("utf-8"))
    assert knowledge_sha256(lf) != knowledge_sha256(changed)


def test_create_knowledge_import_is_light():
    # ต้องรันใน process แยก เพราะ test_webhook.py import app ซึ่งโหลด langchain เข้า sys.modules ไปแล้ว
    code = (
        "import sys, utils.create_knowledge\n"
        "heavy = [m for m in sys.modules\n"
        "         if m.startswith(('langchain', 'faiss', 'sentence_transformers'))]\n"
        "print(heavy)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"
