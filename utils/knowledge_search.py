import os
import json
import time
import requests
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS

# ใช้ 127.0.0.1 แทน localhost: บน Windows ชื่อ localhost จะลอง IPv6 (::1) ก่อน
# แต่ Ollama รับแค่ IPv4 จึงเสียเวลาราว 2 วินาทีก่อนถอยไปใช้ 127.0.0.1 ซึ่งเกิน connect timeout (1.0 วินาที)
# วัดจริง: GET /api/tags ผ่าน localhost = 2.05 วินาที, ผ่าน 127.0.0.1 = 0.005 วินาที
OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
TAX_DISTANCE_THRESHOLD = 14.0

# 1. โหลด FAISS Vector Store ระดับโมดูล
print("🧠 [Knowledge Search] กำลังโหลดโมเดล Embeddings และ FAISS Index...")
_embeddings = HuggingFaceEmbeddings(
    model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
)

_index_path = "faiss_tax_index" if os.path.exists("faiss_tax_index") else os.path.join("utils", "faiss_tax_index")
if os.path.exists(_index_path):
    _db = FAISS.load_local(_index_path, _embeddings, allow_dangerous_deserialization=True)
    print("✅ [Knowledge Search] โหลด FAISS Index สำเร็จ พร้อมใช้งาน")
else:
    _db = None
    print("⚠️ [Knowledge Search] ไม่พบโฟลเดอร์ faiss_tax_index")


def ask_llm(prompt, deadline):
    """
    เรียกใช้งาน Ollama แบบ stream=True เพื่อควบคุมงบเวลาไม่ให้เกิน deadline
    หากเกินเวลา จะหลุดจาก with statement ทันที ทำให้ connection ถูกตัด
    Ollama ฝั่ง server จะหยุดการ generate ทันที ไม่กินทรัพยากร CPU/VRAM ตกค้าง
    """
    chunks = []
    try:
        # timeout=(connect_timeout, read_timeout_per_chunk)
        with requests.post(
            OLLAMA_URL,
            json={
                "model": "llama3.2:3b",
                "prompt": prompt,
                "stream": True,
                "keep_alive": "30m",  # ป้องกันปัญหา Cold Start โมเดลหลุดจากหน่วยความจำ
                "options": {
                    "temperature": 0,
                    "num_predict": 120  # ควบคุมความยาวไม่ให้ตอบเยิ่นเย้อ
                }
            },
            stream=True,
            timeout=(1.0, 3.0)
        ) as r:
            if r.status_code != 200:
                print(f"⚠️ [LLM-FAIL] HTTP Error: {r.status_code}")
                return None

            for line in r.iter_lines():
                # ตรวจสอบ Hard Deadline ทุกครั้งที่ได้ token
                if time.monotonic() > deadline:
                    print(f"⏱️ [LLM-TIMEOUT] เวลาเกิน Deadline ที่กำหนด ตัดการเชื่อมต่อทันที")
                    return None  # ออกจาก with -> connection ถูก reset ทันที

                if line:
                    data = json.loads(line)
                    chunks.append(data.get("response", ""))
                    if data.get("done"):
                        break

    except requests.RequestException as e:
        print(f"⚠️ [LLM-FAIL] Request Exception: {e}")
        return None

    full_response = "".join(chunks).strip()
    return full_response if full_response else None


def query_tax_knowledge(user_question, deadline=None):
    if _db is None:
        return "ขออภัยครับ ระบบฐานข้อมูลความรู้ภาษียังไม่พร้อมใช้งานในขณะนี้"

    # หากไม่ได้ส่ง deadline มา ให้ตั้งเพดานไว้ 3.5 วินาที นับจากฟังก์ชันนี้เริ่ม
    if deadline is None:
        deadline = time.monotonic() + 3.5

    try:
        # ค้นหา Top-2 chunks จาก FAISS
        results = _db.similarity_search_with_score(user_question, k=2)
        if not results:
            return "ขออภัยครับ ไม่พบข้อมูลที่เกี่ยวข้องในฐานข้อมูลภาษีเงินได้บุคคลธรรมดาครับ"

        valid_chunks = [doc.page_content for doc, score in results if score <= TAX_DISTANCE_THRESHOLD]
        best_doc, best_score = results[0]
        print(f"🔍 คำถาม: '{user_question}' | L2 Distance ดีที่สุด: {best_score:.4f}")

        if not valid_chunks:
            return "ขออภัยครับ คำถามนี้อยู่นอกเหนือขอบเขตฐานข้อมูลภาษีเงินได้บุคคลธรรมดาครับ"

        # ข้อความสำรอง (Fallback) กรณี LLM ตอบไม่ทันหรือขัดข้อง ส่งข้อมูลดิบที่ตัวเลขถูกต้องเสมอ
        fallback = f"📖 ข้อมูลจากฐานข้อมูลภาษี:\n{valid_chunks[0]}"

        prompt = f"""คุณคือผู้ช่วยให้ข้อมูลภาษีเงินได้บุคคลธรรมดา ตอบคำถามโดยอ้างอิงจาก [ข้อมูลอ้างอิง] เท่านั้น

[กฎ]
1. ห้ามใช้ความรู้อื่น ห้ามคาดเดา
2. คัดลอกตัวเลขตรงตัว ห้ามคำนวณเพิ่ม
3. ตอบสั้น กระชับ เป็นภาษาไทย ไม่เกิน 2 บรรทัด ห้ามใช้ Markdown

[ข้อมูลอ้างอิง]
{chr(10).join(valid_chunks)}

คำถาม: {user_question}
คำตอบ:"""

        # ส่งเข้า Ollama ภายใต้งบเวลา deadline ที่เหลืออยู่
        answer = ask_llm(prompt, deadline)

        if answer:
            print("🤖 [LLM] เรียบเรียงคำตอบสำเร็จ")
            return f"📖 {answer}"
        else:
            print("🔄 [LLM-FALLBACK] ใช้ข้อความสำรองจากฐานข้อมูล FAISS")
            return fallback

    except Exception as e:
        print(f"❌ Error in query_tax_knowledge: {e}")
        return "เกิดข้อผิดพลาดในการค้นหาข้อมูลภาษี กรุณาลองใหม่อีกครั้งครับ"