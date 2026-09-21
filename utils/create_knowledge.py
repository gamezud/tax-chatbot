import os
import re
from langchain_core.documents import Document
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS

def build_vector_store():
    candidate_paths = [
        "tax_knowledge.txt",
        os.path.join("utils", "tax_knowledge.txt"),
        "Tax Knowledge.txt",
        os.path.join("utils", "Tax Knowledge.txt")
    ]
    
    file_path = None
    for p in candidate_paths:
        if os.path.exists(p):
            file_path = p
            break

    if not file_path:
        raise FileNotFoundError(
            "❌ ไม่พบไฟล์ความรู้ภาษี! กรุณาวางไฟล์ 'tax_knowledge.txt' ไว้ที่รูทโปรเจกต์หรือในโฟลเดอร์ utils"
        )

    print(f"📖 กำลังโหลดฐานข้อมูลความรู้จาก: {file_path}")
    with open(file_path, "r", encoding="utf-8") as f:
        raw_content = f.read()

    # ล้างแท็กกำกับและลดช่องว่างบรรทัดเกิน
    clean_content = re.sub(r'\s*\[cite:[^\]]*\]', '', raw_content)
    clean_content = re.sub(r'\n{3,}', '\n\n', clean_content).strip()

    # แยกก้อนตามระดับ Header โดยใช้ strip_headers=True เพื่อไม่ให้ชื่อหัวข้อติดซ้ำซ้อนในเนื้อหา
    headers_to_split_on = [
        ("#", "Header_1"),
        ("##", "Header_2"),
        ("###", "Header_3"),
    ]
    markdown_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=headers_to_split_on,
        strip_headers=True
    )
    section_docs = markdown_splitter.split_text(clean_content)

    # แบ่งย่อยเนื้อหา (chunk_size=500 กำหนดเฉพาะขนาดของเนื้อความ)
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,
        chunk_overlap=80,
        separators=["\n\n", "\n* ", "\n- ", "\n", " "]
    )

    final_chunks = []
    for doc in section_docs:
        header_context = " > ".join([v for v in doc.metadata.values() if v])
        sub_texts = text_splitter.split_text(doc.page_content)
        for t in sub_texts:
            full_text = f"[{header_context}]\n{t}".strip() if header_context else t.strip()
            if full_text:
                final_chunks.append(Document(page_content=full_text, metadata=doc.metadata))

    print(f"✂️ แบ่งชิ้นส่วนเอกสารเสร็จสิ้น: {len(final_chunks)} Chunks")

    # พิมพ์ตัวอย่างโดยไม่ให้ Index ซ้ำแม้มี chunk น้อยกว่า 3 ก้อน
    print(f"\n{'='*60}")
    if final_chunks:
        total = len(final_chunks)
        sample_indices = sorted(set([0, total // 2, total - 1]))
        for idx in sample_indices:
            print(f"--- Sample Chunk [{idx}] ({len(final_chunks[idx].page_content)} ตัวอักษร) ---")
            print(final_chunks[idx].page_content)
            print("-" * 60)

    print("\n🧠 กำลังแปลงเวกเตอร์ (sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2)...")
    embeddings = HuggingFaceEmbeddings(
        model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    )

    db = FAISS.from_documents(final_chunks, embeddings)
    save_path = "faiss_tax_index"
    db.save_local(save_path)
    print(f"✅ สร้างและบันทึก Index สำเร็จที่: '{save_path}'")

if __name__ == "__main__":
    build_vector_store()