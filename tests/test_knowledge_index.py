import subprocess
import sys
from pathlib import Path

from utils.create_knowledge import HASH_FILENAME, knowledge_sha256

ROOT = Path(__file__).resolve().parent.parent

REBUILD_HINT = (
    "faiss_tax_index/ ไม่ตรงกับ tax_knowledge.txt — รัน python utils\\create_knowledge.py "
    "จาก root ของโปรเจกต์ แล้ว commit faiss_tax_index/ คู่กับ tax_knowledge.txt ใน commit เดียวกัน"
)


def test_index_hash_matches_knowledge_file():
    # ขั้น F พบ index ที่ไม่ได้สร้างจากไฟล์ใดใน git เทสนี้จับกรณีแก้ tax_knowledge.txt แล้วไม่สร้าง index ใหม่
    # เทียบแค่ hash ไม่โหลด FAISS หรือ embedding (กับดักข้อ 4)
    hash_path = ROOT / "faiss_tax_index" / HASH_FILENAME
    assert hash_path.exists(), f"ไม่พบ {hash_path.name} — {REBUILD_HINT}"
    recorded = hash_path.read_text(encoding="utf-8").strip()
    assert recorded == knowledge_sha256(ROOT / "tax_knowledge.txt"), REBUILD_HINT


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
