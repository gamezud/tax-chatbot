import os
import time
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_ollama import ChatOllama

# 1. โหลด Embedding และ FAISS Index ระดับโมดูล (โหลดครั้งเดียวตอนเริ่มระบบ)
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

# 2. โหลด LLM ระดับโมดูล (ล็อก temperature=0 เพื่อให้คำตอบเหมือนเดิมทุกครั้ง)
_llm = ChatOllama(
    model="llama3.2:3b",
    temperature=0,
    num_predict=90,          # ลดจาก 250 เหลือ 90 เพื่อความรวดเร็ว
    keep_alive="1h"          # สั่งให้โหลดโมเดลค้างไว้ใน VRAM/RAM 1 ชั่วโมง ไม่ต้องสลับโหลดใหม่
)

# ค่า Threshold จากการทดสอบความแม่นยำ
TAX_DISTANCE_THRESHOLD = 14.0

def query_tax_knowledge(user_question):
    if _db is None:
        return "ขออภัยครับ ระบบฐานข้อมูลความรู้ภาษียังไม่พร้อมใช้งานในขณะนี้"

    t_start = time.time()
    try:
        results = _db.similarity_search_with_score(user_question, k=2)
        if not results:
            return "ขออภัยครับ ไม่พบข้อมูลที่เกี่ยวข้องในฐานข้อมูลภาษีเงินได้บุคคลธรรมดาครับ"

        valid_chunks = [doc.page_content for doc, score in results if score <= TAX_DISTANCE_THRESHOLD]
        best_doc, best_score = results[0]
        print(f"🔍 คำถาม: '{user_question}' | L2 Distance ดีที่สุด: {best_score:.4f}")

        if not valid_chunks:
            return "ขออภัยครับ คำถามนี้อยู่นอกเหนือขอบเขตฐานข้อมูลภาษีเงินได้บุคคลธรรมดาครับ"

        # เตรียมข้อความสำรองดั้งเดิม
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

        try:
            result = _llm.invoke(prompt)
            answer = (result.content or "").strip()
            elapsed = time.time() - t_start
            print(f"⏱️ [LLM TIME]: {elapsed:.2f} วินาที")
            print(f"🤖 [LLM RAW OUTPUT]: {answer}")

            # ถ้าโมเดลตอบกลับมาและยังไม่เกินเวลา 4 วินาที ให้ใช้คำตอบของ LLM
            if answer and elapsed < 4.0:
                return f"📖 {answer}"
            else:
                print("⚠️ [TIMEOUT/EMPTY] สลับไปใช้ข้อความสำรองเนื่องจากใช้เวลานานเกินไป")
                return fallback

        except Exception as e:
            print(f"⚠️ [LLM-FAIL] {e} — สลับไปใช้ข้อความสำรอง")
            return fallback

    except Exception as e:
        print(f"❌ Error in query_tax_knowledge: {e}")
        return "เกิดข้อผิดพลาดในการค้นหาข้อมูลภาษี กรุณาลองใหม่อีกครั้งครับ"