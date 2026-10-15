# CLAUDE.md — คู่มือสำหรับ AI ที่เข้ามาช่วยแก้โปรเจกต์นี้

ไฟล์นี้ถูกอ่านอัตโนมัติทุกครั้งที่เปิด session ใหม่
เนื้อหาคือ "สิ่งที่ถ้าไม่บอกไว้แล้วจะทำผิด" ไม่ใช่เอกสารอธิบายโปรเจกต์ทั้งหมด
ทุกข้อมีเหตุผลกำกับไว้ เพื่อให้คนที่ไม่ได้เขียนโค้ดอ่านแล้วเข้าใจด้วย

---

## โปรเจกต์นี้คืออะไร

แชตบอตให้ข้อมูลและคำนวณภาษีเงินได้บุคคลธรรมดา (ปริญญานิพนธ์ ป.ตรี วิทยาการคอมพิวเตอร์)
ผู้ใช้คุยผ่าน LINE ระบบถามข้อมูลรายได้ทีละขั้น คำนวณภาษี ออก PDF สรุปผล
และตอบคำถามความรู้ภาษีจากฐานความรู้

**เส้นทางของข้อความหนึ่งข้อความ**

```
ผู้ใช้พิมพ์ใน LINE
      ↓
Dialogflow ES  — ทายว่าผู้ใช้ต้องการอะไร (intent) และดึงตัวเลข/คำสำคัญออกมา (parameter)
      ↓
ngrok          — ท่อที่ทำให้ Dialogflow บนอินเทอร์เน็ตเรียกเข้ามาถึงเครื่องเราได้
      ↓
Flask (app.py) — ตัดสินใจว่าจะทำอะไร คำนวณ แล้วส่งข้อความตอบกลับ
      ↓
Dialogflow ES  — ส่งข้อความต่อให้ LINE
```

**โปรเจกต์นี้ไม่ได้เรียก LINE Messaging API เอง** การตอบกลับทั้งหมดส่งกลับไปที่ Dialogflow
แล้ว Dialogflow ส่งต่อให้ LINE จึงไม่มี token ของบอท (channel access token) อยู่ในโค้ด — อย่าเสนอให้เพิ่ม
> เหตุผล: แชตบอต LINE ส่วนใหญ่เรียก API เอง ถ้าไม่บอกไว้ AI จะเสนอให้ติดตั้ง LINE SDK
> ซึ่งไม่จำเป็นและทำให้โครงสร้างซับซ้อนขึ้นโดยไม่ได้อะไร

**ข้อยกเว้น: หน้า LIFF อ่านใบเสร็จบริจาค** (`GET /liff/donation`, `POST /liff/ocr`) ไม่ผ่าน Dialogflow
หน้าเว็บส่งรูปพร้อม LIFF access token **ของผู้ใช้** มาที่ `/liff/ocr` แล้ว `utils/liff_auth.py` ตรวจ token นั้นกับ
`https://api.line.me/oauth2/v2.1/verify` ทุกครั้งก่อนอ่านรูป — ไม่ใช่ Messaging API และไม่ใช้ token ของบอท **ห้ามลบ**
> เหตุผล: ถ้าตีความกฎข้างบนว่า "ห้ามมี token ใดในโค้ด" แล้วลบการตรวจนี้ ใครก็ส่งรูปเข้า `/liff/ocr` ได้โดยไม่ต้องเปิดจาก LINE

**ข้อยกเว้น: หน้า Admin แก้อัตราภาษี** (`/admin*` ใน `admin_routes.py`) ไม่ผ่าน Dialogflow และเข้าได้จากเครื่องที่รันเซิร์ฟเวอร์เท่านั้น
request ที่มี header `X-Forwarded-*` / `Forwarded` หรือ Host ไม่ใช่ 127.0.0.1 / localhost / ::1 หรือยังไม่ได้ตั้ง `ADMIN_PASSWORD_HASH`
ได้ 404 แบบเดียวกับหน้าที่ไม่มีอยู่ **ห้ามลบหรือผ่อนตัวกันนี้** และห้ามเช็กแค่ `remote_addr`
> เหตุผล: ผ่าน ngrok `remote_addr` เป็น 127.0.0.1 เหมือน request ในเครื่อง (ทดลองจริงแล้ว) สิ่งที่แยกได้คือ header กับ Host
> ตัวกันใช้ `before_app_request` และเช็ก `request.path` — `before_request` ของ blueprint ไม่ถูกเรียกเมื่อจับคู่ route ไม่ได้ (405/308 รั่ว)

---

## กฎเหล็ก (ละเมิดไม่ได้)

### 1. งบเวลา 5 วินาที

Dialogflow ES รอคำตอบจาก webhook ได้ประมาณ **5 วินาที** ถ้าเกิน มันจะทิ้งคำตอบของเรา
แล้วตอบข้อความสำรองของมันเองแทน ผู้ใช้จะเห็นว่าบอทตอบไม่ตรงคำถาม ทั้งที่โค้ดเราทำงานถูก

> เหตุผลที่ต้องเขียนไว้: ข้อจำกัดนี้ไม่ปรากฏที่ไหนในโค้ด เป็นกติกาของ Dialogflow
> ถ้าไม่รู้ จะเผลอเสนอ "ลองใหม่อัตโนมัติเมื่อ LLM ล้มเหลว" หรือ "เพิ่มเวลารอ"
> ซึ่งในระบบทั่วไปเป็นคำแนะนำที่ดี แต่ที่นี่ทำให้พังหนักกว่าเดิม

**ข้อห้าม**
- ห้ามเพิ่ม retry ในเส้นทางที่ตอบ Dialogflow
- ห้ามเพิ่มเวลา timeout ของการเรียก Ollama
- ห้ามเรียก LLM หลายรอบต่อหนึ่งข้อความ
- ห้ามเพิ่มการอ่าน/เขียน Firestore ในเส้นทางหลักโดยไม่จำเป็น (แต่ละครั้งกินหลายร้อยมิลลิวินาที)

**วิธีที่ใช้อยู่:** `webhook()` ตั้ง `req_deadline = start_time + 4.0` ทันทีที่รับ request
แล้วส่งต่อให้ `query_tax_knowledge(..., deadline=req_deadline)` ใครใช้เวลาไปก่อน
คนถัดไปก็ได้เวลาน้อยลงเอง **ให้ส่ง deadline ต่อเสมอ ห้ามตั้งเวลาใหม่กลางทาง**

ตอนนี้ deadline ถูกใช้แค่ในสาขาตอบคำถามความรู้ (RAG) เท่านั้น สาขาสรุปผลภาษี
(`04_Tax_Interview_Deductions`) ทั้งสร้าง PDF และเขียน Firestore โดยไม่มีการคุมเวลา
— รู้ไว้ แต่ยังไม่ต้องแก้

`POST /liff/ocr` ไม่อยู่ในงบนี้ เพราะหน้า LIFF เรียกตรง ไม่ผ่าน Dialogflow — OCR มีเพดานของตัวเอง (`TIMEOUT_SECONDS` ใน `utils/ocr.py`)
**ห้ามย้าย OCR เข้า `webhook()`** และอ่านได้ทีละรูป (`_ocr_slot` ใน `app.py`) รูปที่มาตอนไม่ว่างตอบ `busy` ทันที
> เหตุผล: OCR ใช้เวลาหลายร้อยมิลลิวินาทีถึงหลายวินาทีและใช้ CPU เครื่องเดียวกับ webhook
> ถ้าอยู่ในเส้นทางของ Dialogflow หรืออ่านพร้อมกันหลายรูป คำตอบของ webhook จะเกินงบ 5 วินาที

หน้า `/admin*` ก็ไม่อยู่ในงบนี้ แต่ **ห้ามย้ายการอ่าน Firestore ของ admin หรือ `load_effective_rules` เข้า `webhook()`**
ค่าที่ admin แก้ถูกอ่านครั้งเดียวตอนเปิดเซิร์ฟเวอร์ (`apply_effective_rules` ในบล็อก `__main__`)
การอ่านและบันทึกครอบด้วย `_call_with_timeout` (`READ_TIMEOUT_SECONDS`, `SAVE_TIMEOUT_SECONDS` ใน `utils/rules_store.py`)

### 2. ตัวเลขภาษีต้องถูกต้อง

คำตอบที่ช้าหรือไม่สวยยังพอรับได้ แต่ตัวเลขผิดคือระบบใช้ไม่ได้

- **LLM ห้ามคำนวณเลข** LLM มีหน้าที่เรียบเรียงถ้อยคำคำตอบความรู้เท่านั้น
  การคำนวณทั้งหมดอยู่ใน `utils/tax_calculator.py` ซึ่งเป็นโค้ดธรรมดาที่ตรวจสอบได้
- ถ้า LLM ตอบไม่ทันหรือล้ม ต้องส่ง **ข้อความดิบจากฐานความรู้** แทน ไม่ใช่คำตอบที่ขาดกลางประโยค
- **ห้ามแก้สูตร อัตรา หรือเพดานภาษีโดยไม่ได้รับคำสั่งชัดเจน** ถ้าเห็นว่าผิด ให้บอกพร้อมอ้างที่มาทางกฎหมาย
- **ตัวเลขตามกฎหมายทุกตัวอยู่ใน `utils/tax_rules.py` ที่เดียว** (เพดาน อัตรา ขั้นบันได เกณฑ์วิธีที่ 2)
  ตัวเลขกฎหมายใหม่ต้องเพิ่มเป็น key ในไฟล์นั้น ห้ามเขียนลงในโค้ดตรง ๆ
  คอมเมนต์ในโค้ดให้อ้างชื่อ key (เช่น `social_security_cap`) ไม่ใช่ตัวเลข
  > เหตุผล: ถ้ามีตัวเลขซ้ำอยู่ในโค้ดหรือคอมเมนต์ ตอนเปลี่ยนปีภาษีจะมีจุดที่ผิดค้างอยู่โดยไม่มีอะไรเตือน
- **วิธีอ่าน `tax_rules`:** `from utils import tax_rules` แล้วอ่าน `tax_rules.TAX_RULES[...]` (และ `tax_rules.RULES_INFO`) ในตัวฟังก์ชันตอนถูกเรียก
  - ห้าม `from utils.tax_rules import TAX_RULES` — ปัญหาเดียวกับกับดักข้อ 2: ได้ dict ที่ผูกไว้ตอน import
    ถ้าระบบ Admin แทนที่ `tax_rules.TAX_RULES` ทีหลัง ฟังก์ชันนั้นจะยังใช้ค่าเก่าโดยไม่มี error
  - ห้ามใช้เป็นค่า default ของ parameter (กับดักข้อ 9)
  - `handle_history` แสดงปีจาก field `tax_year` ที่บันทึกไว้ใน Firestore **ห้ามอ่าน `tax_rules`**
    > เหตุผล: รายการเก่าคำนวณด้วยกติกาปีอื่น รายการที่ไม่มี field นี้ไม่แสดงปีและไม่เดา
  - `pdf_generator.py` และ `firebase_db.py` ไม่ import `tax_rules` — ได้ปีจาก `tax_report_data` ที่ `handle_deductions` ส่งมา
    > เหตุผล: LINE, PDF และ Firestore ได้ปีจาก dict เดียวกัน ปีในสามที่จึงตรงกันเสมอ
- **ตัวเลขใน `tax_rules.py` ต้องตรงกับ `docs/tax-rules-2569.md`** ถ้ากฎหมายเปลี่ยน ให้แก้ docs ก่อน
  พร้อมแหล่งอ้างอิงจากกรมสรรพากรหรือประมวลรัษฎากร แล้วค่อยแก้ `tax_rules.py`
  > เหตุผล: อาจารย์ที่ปรึกษากำหนดให้ทุกตัวเลขต้องมีแหล่งอ้างอิงชัดเจน docs คือที่เก็บแหล่งอ้างอิงนั้น
  > ถ้าแก้โค้ดก่อน จะมีตัวเลขที่ไม่มีที่มาอยู่ในระบบ
- **ค่าที่ใช้คำนวณจริงอาจไม่ใช่ค่าใน `tax_rules.py`** — ตอนเปิดเซิร์ฟเวอร์ `apply_effective_rules` แทนที่ `tax_rules.TAX_RULES`
  และ `RULES_INFO` ด้วยค่าที่ admin บันทึกใน Firestore (ถ้ามีและผ่านการตรวจ) ดูว่าใช้ค่าไหนจาก log `[TAX-RULES]` ตอนเปิด
  - **ห้ามแทนหรือสลับ `tax_rules.TAX_RULES` ที่อื่น** (เช่น เพื่อคำนวณภาษีตัวอย่าง) ค่าที่บันทึกมีผลหลังรีสตาร์ตเท่านั้น
    > เหตุผล: Flask รับหลาย request พร้อมกัน webhook ที่เข้ามาระหว่างนั้นจะคิดภาษีด้วยค่าที่ยังไม่ยืนยัน หรือได้ค่าครึ่งเก่าครึ่งใหม่
  - กฎตรวจค่าที่แก้อยู่ใน `rules_store.build_rules` ที่เดียว (ตัวโหลด ฟอร์ม และการย้อนเวอร์ชันใช้ร่วมกัน) — กฎใหม่เพิ่มที่นั่น
  - การเขียนมีทางเดียว: หน้ายืนยัน → `POST /admin/save` → `rules_store.save_config` (transaction เดียว เขียน active + ประวัติ)
    GET ของหน้า admin ห้ามเขียน Firestore — ข้อตัดสินใจทั้งหมดพร้อมเหตุผลอยู่ใน `docs/history-admin-tax-rules.md`
- **กฎหมายเปลี่ยนแล้วแก้ทางไหน**
  - เปลี่ยนถาวรหรือขึ้นปีภาษีใหม่ → แก้ docs ก่อน แล้วแก้ `tax_rules.py` ตามข้อข้างบน
  - ระบบ Admin ใช้กับการปรับระหว่างปีที่มีประกาศรองรับ ต้องกรอกแหล่งอ้างอิง (เก็บในประวัติทุกเวอร์ชัน)
    หลังแก้ค่าตั้งต้นในโค้ดให้ตรงกับค่าที่ปรับไว้แล้ว → กด "ใช้ค่าตั้งต้นในโค้ด" ในหน้าประวัติ
  - **ขึ้นปีภาษีใหม่:** doc active ที่ `based_on_tax_year` เป็นปีเก่าจะถูกตัวโหลดปฏิเสธ (log `❌ [TAX-RULES]` + แถบแดงในหน้า admin)
    **ตั้งใจ ไม่ใช่บั๊ก ห้ามแก้ตัวโหลด** → กด "ใช้ค่าตั้งต้นในโค้ด" เพื่อบันทึก doc ของปีใหม่
    > เหตุผล: ค่าที่แก้ไว้สำหรับปีเก่าต้องไม่ติดไปกับกติกาของปีใหม่โดยไม่มีใครตรวจ
- **`tests/test_line_verified_cases.py` ล็อกภาษีจาก LINE จริง 21 กรณี** ถ้าล้มแปลว่าโค้ดเปลี่ยนตัวเลขภาษี
  **ห้ามแก้ค่าที่คาดให้ผ่าน** ต้องหาสาเหตุก่อน ถ้าตั้งใจเปลี่ยน (เช่น กฎหมายเปลี่ยน) ต้องแก้ `docs/tax-rules-2569.md` ก่อน
  แล้วทดสอบใน LINE ใหม่ จึงค่อยแก้ค่าในเทส
  > เหตุผล: เทสนี้คำนวณด้วยค่าตั้งต้นเสมอ (`tax_rules_not_leaked` ใน conftest ล้มเทสที่ทิ้งค่าที่ถูกแทนไว้) จึงจับภาษีที่เปลี่ยนโดยไม่ตั้งใจ
  > ส่วนใน LINE ถ้า admin แก้ค่าอยู่ ภาษีจะต่างจากเทสได้ — ดู `[TAX-RULES]` ก่อนเทียบ
  > เทสนี้กันถอยหลังเท่านั้น ไม่ใช่หลักฐานว่าสูตรถูกตามกฎหมาย (นั่นคือ `test_tax_calculator.py` + docs)
- **อ่านยอดบริจาคจากรูปต้อง "ไม่เดา"** (`utils/receipt_parser.py`) ไม่แน่ใจ → ไม่ส่งยอด
  (`not_found` / `ambiguous` / `thai_digits` / `not_donation`) ให้ผู้ใช้พิมพ์เอง
  > เหตุผล: หน้า LIFF เติมยอดของสถานะ `ok` ลงช่องให้ทันที ยอดผิดที่มากับ `ok` (confident wrong) อันตรายที่สุด
  - แก้ `receipt_parser.py` หรือ `ocr.py` แล้วต้องรัน `python scripts\make_sample_receipts.py` และ `python scripts\measure_ocr.py`
    confident wrong ต้องเป็น 0 (สคริปต์จบด้วย exit 1 ถ้าไม่ใช่) ผลล่าสุดอยู่ใน `docs/ocr-eval-2569-10.md`
  - status ใหม่ของ `/liff/ocr` ต้องเพิ่มทั้งใน `STATUS_MESSAGES` และเงื่อนไขรีเซ็ตช่องของ `templates/liff_donation.html`
    > เหตุผล: status ที่หน้า LIFF ไม่รู้จักแสดงข้อความทั่วไป และไม่ล้างยอดที่ค้างจากรูปก่อนหน้า (พบตอนเพิ่ม `thai_digits`)
  - `/liff/ocr` ไม่บันทึกรูปลงดิสก์ และไม่ print ข้อความที่อ่านได้หรือยอด (`test_log_has_no_text_amount_or_token` ฯลฯ)
    > เหตุผล: รูปและข้อความในใบเสร็จมีชื่อ ที่อยู่ และยอดเงินของผู้ใช้
  - หน้า LIFF ไม่ชวนให้พิมพ์ค่าลดหย่อนอื่นต่อท้ายยอดบริจาค
    > เหตุผล: หน้า LIFF ส่งข้อความเดียวแล้วสาขา 04 คำนวณทันที ค่าลดหย่อนที่ไม่อยู่ในข้อความนั้นจะไม่ถูกนับ

### 3. ชื่อที่ผูกกับ Dialogflow ห้ามเปลี่ยน

ชื่อ intent, context และ parameter ถูกตั้งไว้บนเว็บ Dialogflow Console ซึ่งอยู่คนละที่กับโค้ด
**ถ้าเปลี่ยนชื่อในโค้ด ระบบจะพังโดยไม่มี error ใด ๆ** — สาขานั้นแค่จะไม่ทำงานอีกเลย

> เหตุผล: โค้ดเทียบชื่อด้วยข้อความตรงตัว เช่น `intent_name == '02_Tax_Interview_Salary'`
> ถ้าสะกดต่างจากบน Console แม้ตัวเดียว เงื่อนไขก็เป็นเท็จเงียบ ๆ แล้วไหลไปสาขาอื่น

**Intent** (ชื่อตามที่ใช้ใน `app.py`)

| ชื่อ | หน้าที่ |
|---|---|
| `Welcome`, `Default Welcome Intent` | ทักทาย แสดงเมนู (`Default Welcome Intent` ไม่มีใน export — ดูกับดักข้อ 7) |
| `99_Reset_Chat` | ล้างข้อมูลที่กรอกไว้ทั้งหมด |
| `01_Tax_Interview_Start` | เริ่มคำนวณ ถามประเภทเงินได้ |
| `02_Tax_Interview_Salary` | เก็บข้อมูลเงินเดือน |
| `03_Tax_Interview_Rental` | เก็บข้อมูลค่าเช่า |
| `05_Tax_Interview_Online` | เก็บข้อมูลขายของออนไลน์ (เลข 05 มาก่อน 04 ในการทำงานจริง) |
| `04_Tax_Interview_Deductions` | เก็บค่าลดหย่อน คำนวณ ออก PDF บันทึกประวัติ |
| `06_Tax_Knowledge` | คำถามความรู้ภาษี — ไม่มีสาขาของตัวเองในโค้ด จึงตกไปสาขาความรู้ (ขั้นที่ 8 ของกับดักข้อ 1) โดยไม่ต้องแก้โค้ด เปิด webhook และไม่มี input context |
| อื่น ๆ ทั้งหมด | ตกไปสาขาตอบคำถามความรู้ (RAG) |

**Context**

| ชื่อ | lifespanCount | หน้าที่ |
|---|---|---|
| `tax_session` | 20 | ถุงเก็บ parameter ที่ผู้ใช้กรอกสะสมมาตลอดการสัมภาษณ์ |
| `awaiting_salary` | 2 | กำลังรอคำตอบเรื่องเงินเดือน |
| `awaiting_rental` | 2 | กำลังรอคำตอบเรื่องค่าเช่า |
| `awaiting_online` | 2 | กำลังรอคำตอบเรื่องขายออนไลน์ |
| `awaiting_deduction` | 2 | กำลังรอคำตอบเรื่องค่าลดหย่อน |

> lifespanCount คือจำนวน "รอบสนทนา" ที่ context อยู่ได้ ไม่ใช่นาที
> นอกจากนี้ Dialogflow ยังลบ context ทิ้งเองเมื่อผู้ใช้เงียบไปเกิน 20 นาที ไม่ว่า lifespanCount จะเหลือเท่าไหร่

**พฤติกรรมของบอทขึ้นกับการตั้งค่าบน Console ซึ่งไม่อยู่ใน git**
ตัวอย่าง: `99_Reset_Chat` เคยไม่ได้เปิด webhook — Console ตั้ง `tax_session` เป็น lifespan 0 เอง
แต่ `awaiting_*` ยังค้าง รีเซ็ตจึงทำงานได้ครึ่งเดียว และโค้ดรีเซ็ตใน `app.py` ไม่เคยทำงานเลย
เปิด webhook ของ intent นี้เมื่อ 2026-10-03 แล้วทดสอบผ่านทั้งกรณีรีเซ็ตกลางคันและรีเซ็ตแล้วเริ่มใหม่

> ถ้าบอททำงานไม่ตรงกับโค้ด ให้สงสัยการตั้งค่าบน Console ก่อน (เปิด webhook หรือไม่, Is list, training phrase)
> เพราะ AI มองไม่เห็นส่วนนี้ ต้องให้เจ้าของโปรเจกต์ตรวจ

**ทุกครั้งที่แก้บน Console ต้อง export agent ใหม่ลง `dialogflow/` แล้ว commit**

> เหตุผล: `tests/test_dialogflow_export.py` ตรวจการตั้งค่า (เช่น `isList` ของ `spouse` และ parameter ตัวเลขใน 04) จากไฟล์ใน `dialogflow/`
> ตัวอย่างจริง: export ขั้น E ครั้งแรก `life_insurance` ถูกเปลี่ยนเป็น `isList: true` บน Console โดยไม่ได้ตั้งใจ
> `test_deduction_numbers_are_not_lists` จับได้ก่อน commit
> ถ้าแก้บน Console แล้วไม่ export เทสจะยังผ่านทั้งที่การตั้งค่าจริงเปลี่ยนไปแล้ว

**แก้ training phrase ให้ทำตาม `docs/holdout-sentences.md`** (เทส xfail → แก้บน Console → export พร้อมถอด xfail ใน commit เดียว → ทดสอบ holdout ใน LINE)
**ห้ามใช้ประโยคในไฟล์นั้นเป็น training phrase** ทั้งแบบตรงตัวและแบบที่อยู่ข้างในประโยคอื่น
> เหตุผล: ถ้าสอนด้วยประโยคที่ใช้เจอบั๊ก แล้วทดสอบด้วยประโยคเดิม ผลผ่านไม่ได้พิสูจน์ว่า Dialogflow แยกได้จริง (#23 รอบแรกถูกตีกลับเพราะเรื่องนี้)

**Parameter** (อ่านจาก `queryResult.parameters` รวมกับที่สะสมใน `tax_session`)

`income_types`, `salary_per_month`, `bonus`, `social_security`, `withholding_tax`,
`property_type`, `rental_income`, `online_income`,
`spouse` (ค่าเป็น `yes`/`no` จาก custom entity), `num_children`, `num_parents`,
`life_insurance`, `health_insurance`, `home_loan_interest`, `ssf_rmf`, `thai_esg`, `donation`

`ssf_rmf` เก็บยอด RMF อย่างเดียว (ชื่อเดิมคงไว้เพราะผูกกับ Console) ยอด SSF ไม่ถูก annotate เข้า parameter ไหน
`thai_esg` เป็น `@sys.number` แบบเดียวกับ `ssf_rmf` (สร้างในขั้น E)
training phrase ของ 04 ที่มี SSF หรือ "กองทุน ESG" คงไว้แต่ไม่ annotate — ห้ามลบและห้าม annotate
> เหตุผล: ให้ข้อความยัง match 04 แล้วได้หมายเหตุว่า SSF ลดหย่อนไม่ได้แล้ว ถ้าลบ ข้อความจะตกเป็น Fallback
> ถ้า annotate Dialogflow จะเรียนให้จับยอด SSF เป็น `ssf_rmf` — ตัวกันปฏิเสธ (SSF เป็นคำห้ามนับ) ผู้ใช้ได้ข้อความ ก แทนหมายเหตุ SSF
> และหลัง export เทสล้ม 3 ตัว: `test_training_phrases_pass`, `test_ssf_rmf_annotation_follows_rmf_not_ssf`,
> `test_annotation_follows_term_since_previous_number` (ลองด้วยประโยคจำลอง "ซื้อ SSF [20000:ssf_rmf]" แล้ว)

**ตัวกันค่าลดหย่อน (#25)** — `utils/deduction_guard.py` ตรวจว่าค่าที่ Dialogflow ส่งมาใน 04 มีหลักฐานในข้อความ
ไม่มีหลักฐาน → ปฏิเสธทั้งข้อความ ไม่แก้ค่าให้เอง (เดาผิดคือตัวเลขภาษีผิด)
- รายการคำของแต่ละ parameter อยู่ใน `utils/deduction_terms.py` **ที่เดียว** ใช้ทั้งตัวกันตอนรันและเทส export
  training phrase ทุกประโยคใน 04 ต้องผ่านตัวกัน (`test_training_phrases_pass`)
  > เหตุผล: ถ้าเพิ่มคำใหม่บน Console แต่ไม่เพิ่มใน `deduction_terms.py` ตัวกันจะปฏิเสธข้อความที่ Dialogflow จับถูก
- ค่า `num_children` / `num_parents` ต้องมาจากตัวเลข (หรือคำ หนึ่ง–สิบ) ที่มี "คน" ตามหลัง และตัวเลขที่มี "คน" ตามหลังไม่นับเป็นเงิน
  > เหตุผล: "ประกันสุขภาพพ่อแม่ 10000" ถ้า Dialogflow ให้ `num_parents` 10000 ตัวคำนวณจะตัดที่ `parent_max_count` แล้วได้ลดหย่อนเกิน
- parameter ใหม่บน Console ต้องเพิ่มใน `COUNT_PARAMETERS` หรือ `MONEY_PARAMETERS` ของตัวกันด้วย
  > เหตุผล: parameter ที่ตัวกันไม่รู้จักถูกปฏิเสธทุกข้อความที่ parameter นั้นมีค่า (ตั้งใจ — ปฏิเสธดีกว่าปล่อยผ่านโดยไม่ตรวจ)

### 4. ห้ามแก้พฤติกรรมไปพร้อมกับย้ายโค้ด

โปรเจกต์กำลังอยู่ระหว่างจัดระเบียบโครงสร้าง (refactor)

> เหตุผล: ถ้าย้ายโค้ดไปด้วยแก้บั๊กไปด้วย เวลาผลลัพธ์เพี้ยนจะแยกไม่ออกว่าเพี้ยนเพราะย้ายผิด
> หรือเพราะแก้ผิด งานที่ตัวเลขต้องถูกแบบนี้รับความเสี่ยงนั้นไม่ไหว

**ถ้าเจอบั๊กระหว่างย้ายโค้ด ให้บอก ไม่ต้องแก้** แล้วเพิ่มลงหัวข้อ "ปัญหาที่รู้แล้ว" ท้ายไฟล์นี้
เจ้าของโปรเจกต์จะตัดสินใจเองว่าจะแก้เมื่อไหร่ โดยแก้เป็นคนละ commit กับการย้าย

**เมื่อแก้บั๊กที่มี xfail คุมอยู่ ต้องถอดป้าย `xfail` ออกใน commit เดียวกัน**

> เหตุผล: ป้ายใช้ `strict=True` ถ้าแก้บั๊กแล้วไม่ถอดป้าย เทสจะ XPASS และชุด test ฟ้องล้ม
> ถ้าแยกถอดป้ายไปอีก commit จะมี commit ที่ชุด test แดงค้างอยู่ใน history
> การแก้บั๊กกับการถอดป้ายอยู่ commit เดียวกัน ทำให้ diff ของ commit นั้นยืนยันการแก้ได้ในตัว

---

## กับดักเฉพาะของการแยก app.py (อ่านก่อนเริ่ม refactor)

`app.py` มีฟังก์ชัน `webhook()` ตัวเดียวยาวราว 400 บรรทัด จุดต่อไปนี้จะพังถ้าย้ายแบบไม่ระวัง

**1. การเลือกสาขาไม่ได้ดูแค่ชื่อ intent** ลำดับการตรวจจริงคือ

1. ข้อความเป็นคำทักทาย **หรือ** intent เป็น Welcome → ทักทาย
2. ข้อความเป็นคำว่ารีเซ็ต **หรือ** intent เป็น `99_Reset_Chat` → ล้างข้อมูล
3. ข้อความตรงกับ "ดูประวัติ" ฯลฯ → แสดงประวัติ (**ดูจากข้อความล้วน ไม่มี intent**)
4. intent `01` → `02` → `03`
5. intent `05` **หรือมี context `awaiting_online` ค้างอยู่** → สาขาออนไลน์
   (ตัวอย่างจริง: `tests/fixtures/009_Default_Fallback_Intent.json` ผู้ใช้พิมพ์ "400000"
   Dialogflow จัดเป็น `Default Fallback Intent` แต่เข้าสาขาออนไลน์เพราะ `awaiting_online` ค้างอยู่)
6. intent `04` → ผ่านตัวกัน (`check_deduction_parameters`) ก่อน ถ้าถูกปฏิเสธ → `handle_unclear_deductions` ไม่คำนวณ
7. intent `Default Fallback Intent` **และ** มี context `awaiting_deduction` **และ** ข้อความมีตัวเลข → `handle_unclear_deductions`
   (เช่น "บุตร 1 คน" ที่ Dialogflow เคยจับไม่ได้ เดิมตกไปสาขาความรู้ ผู้ใช้ไม่รู้ว่ายอดไม่ถูกบันทึก)
8. ที่เหลือ → ตอบคำถามความรู้

> เหตุผลที่สำคัญ: ถ้าแปลงเป็นตาราง "ชื่อ intent → ฟังก์ชัน" ตรง ๆ ข้อ 1, 2, 3, 5 และ 7
> จะหายไปเงียบ ๆ เพราะไม่ได้ตัดสินจากชื่อ intent และลำดับมีผล เช่น ข้อ 5 อยู่ก่อนข้อ 6
> ถ้าสลับกัน ผลลัพธ์จะเปลี่ยน ต้องรักษาลำดับนี้ไว้ทุกครั้ง

**2. ตัวแปร `PUBLIC_URL` เป็น global ที่ถูกเขียนทับตอนรัน** มันเริ่มเป็น `""`
แล้วถูกกำหนดค่าใน `if __name__ == '__main__':` หลังเปิด ngrok

> ถ้าย้ายโค้ดสาขาสรุปผลไปไฟล์อื่นแล้วเขียน `from app import PUBLIC_URL`
> จะได้ค่า `""` ที่ถูกคัดลอกไปตั้งแต่ตอน import ลิงก์ดาวน์โหลด PDF จะหายไปเงียบ ๆ
> ต้องส่งค่านี้เป็น parameter หรืออ่านผ่านฟังก์ชัน/ตัวแปรของ config แทน

**3. path หลายจุดอ้างอิงจากโฟลเดอร์ที่สั่งรัน (cwd) ไม่ใช่ตำแหน่งไฟล์**
`firebase_key.json`, `faiss_tax_index`, `static/reports`, `tax_knowledge.txt`
ถ้าสั่งรันจากโฟลเดอร์อื่นที่ไม่ใช่ root ของโปรเจกต์ จะหาไฟล์ไม่เจอ
**ต้องรันจาก `C:\Projects\tax-chatbot` เสมอ**
ระวัง: `/download` ส่งไฟล์ด้วย `send_from_directory('static/reports', ...)` ซึ่ง Flask อ้างจาก `app.root_path`
แต่ `handle_deductions` เขียน PDF ลง `static/reports` โดยอ้างจาก cwd — สองที่ตรงกันเฉพาะเมื่อรันจาก root

**4. `utils/knowledge_search.py` โหลดโมเดลและ FAISS ใน `get_db()` เมื่อถูกเรียกครั้งแรก** ใช้เวลาหลายวินาที
`app.py` เรียก `get_db()` ล่วงหน้าในบล็อก `if __name__ == '__main__':` ก่อน `app.run`

> ถ้าย้ายบล็อกนั้น ลบบรรทัดนั้น หรือรันด้วย `flask run` ผู้ใช้คนแรกที่ถามคำถามความรู้
> จะต้องรอโหลดโมเดลจนเกินงบ 5 วินาที แล้ว Dialogflow จะตอบข้อความสำรองของมันเองแทน
> การ import ไฟล์นี้เฉย ๆ ยังใช้ราว 1 วินาที (import ไลบรารี langchain) แต่ไม่โหลดโมเดลแล้ว

**5. สาขา `01` เช็กประเภทเงินได้จากข้อความดิบควบคู่กับ `income_types`** ([app.py](app.py) `has_salary` ฯลฯ)
Dialogflow อาจตัดบางประเภทออกจาก `income_types` — ใน `tests/fixtures/006_01_Tax_Interview_Start.json`
ผู้ใช้พิมพ์ "เงินเดือน ค่าเช่า และขายของออนไลน์" แต่ได้ `income_types` แค่ `["ค่าเช่า", "ขายของออนไลน์"]`
โค้ดยังทำงานถูกเพราะเจอคำว่า "เงินเดือน" ในข้อความดิบ

> **ห้ามลบการเช็กข้อความดิบตอน refactor** แม้จะดูซ้ำซ้อนกับ `income_types`
> ถ้าลบ เงินเดือนจะหายจากการคำนวณโดยไม่มี error ใด ๆ

**6. intent `05_Tax_Interview_Online` ยังไม่พบว่าถูกจับในการทดสอบ (fixture 001–021 ณ 2026-10-03)**
สาขาออนไลน์ทำงานผ่าน context `awaiting_online` ร่วมกับโค้ดสำรอง `clean_number(user_query)` ในสาขาออนไลน์
(`app.py` บรรทัด 248-249 ณ commit 8ff1d44)
ใน `tests/fixtures/009_Default_Fallback_Intent.json` Dialogflow จัด "400000" เป็น `Default Fallback Intent`
parameter `online_income` จึงว่าง ยอดขายได้มาจากข้อความดิบผ่านบรรทัดนั้น

ยืนยันสาเหตุจาก export แล้ว (2026-10-06): training phrase ของ `05` ที่มีตัวเลขมีคำนำหน้าทุกประโยค
(เช่น "ขายของออนไลน์ 250000", "ยอดขาย 1.5 ล้าน") ไม่มีประโยคที่เป็นตัวเลขโดด
Dialogflow จึงไม่จับคำตอบอย่าง "400000" เป็น `05` — ตั้งใจไม่แก้ เพราะเงื่อนไข `awaiting_online`
กับ `clean_number(user_query)` ทำงานแทนอยู่แล้ว

> **ห้ามลบเงื่อนไข `awaiting_online` และโค้ดสำรองนี้ตอน refactor** แม้จะดูเหมือนกันไว้เผื่อ
> ถ้าลบ สาขาออนไลน์จะไม่ทำงานเลย หรือได้ยอดขาย 0 โดยไม่มี error

**7. `'Default Welcome Intent'` ในเงื่อนไขสาขาทักทายไม่มีอยู่ใน export** (มีแค่ `Welcome`) เงื่อนไขนี้จึงไม่มีวันเป็นจริง
(`app.py` บรรทัด 62 ณ commit 8ff1d44)
**ห้ามลบระหว่าง refactor** ถ้าจะลบ ให้ทำเป็น commit แยก

> เหตุผล: ตามกฎเหล็กข้อ 4 การลบเงื่อนไขคือการเปลี่ยนพฤติกรรม แม้ตอนนี้จะไม่มีผล
> แต่ถ้าวันหนึ่งมีคนสร้าง intent ชื่อนี้บน Console มันจะกลับมาทำงาน

**8. สาขา `01` อ่าน `income_types` จาก `parameters` ดิบ ไม่ใช่ `merged_params`**
(`income_types = parameters.get('income_types', [])` — `app.py` บรรทัด 130 ณ commit 0a32c45)
ส่วนสาขา `02` และ `03` อ่านจาก `merged_params.get('income_types', [])`

> **ห้ามเปลี่ยนให้เหมือนกันตอน refactor** `merged_params` มี `income_types` ของรอบก่อนจาก `tax_session` ปนอยู่
> ถ้าสาขา `01` ไปอ่านจาก `merged_params` แล้วข้อความรอบนี้ไม่มี key `income_types` (`merged_params.update(parameters)` จึงไม่ทับ)
> ผู้ใช้ที่เริ่มคำนวณใหม่จะได้ประเภทเงินได้ของรอบเก่าติดมาโดยไม่มี error
> ถ้าเห็นว่าควรเหมือนกัน ให้บอกแล้วทำเป็น commit แยก (กฎเหล็กข้อ 4)

**9. handler ต้องอ่าน global ตอนถูกเรียก ห้ามรับเป็นค่า default ของ parameter**
global ที่สาขาใช้: `PUBLIC_URL`, `firebase_client`, `generate_tax_pdf`, `query_tax_knowledge`
และ `tax_rules.TAX_RULES` / `tax_rules.RULES_INFO` (อ่านผ่านชื่อโมดูล เพราะถูกแทนที่ตอนเปิดเซิร์ฟเวอร์ — กฎเหล็กข้อ 2)

> ค่า default ของ parameter ถูกคำนวณครั้งเดียวตอนประกาศฟังก์ชัน
> `def handle_deductions(..., public_url=PUBLIC_URL)` จะได้ `""` ตลอดไป เพราะตอนนั้น ngrok ยังไม่เปิด
> (ปัญหาเดียวกับกับดักข้อ 2 แต่เกิดในไฟล์เดียวกันก็ได้)
> นอกจากนี้ `tests/test_webhook.py` patch ชื่อเหล่านี้ที่ `app_module.*` ตอนรันเทส
> ถ้าค่าถูกล็อกไว้ก่อน mock จะไม่มีผล เทสอาจเรียก Firestore หรือ Ollama จริง

### รูปแบบ handler (ใช้กับทุกสาขา)

ตัวอย่างแรก: `handle_greeting()` ใน `app.py`

- เป็นฟังก์ชันใน `app.py` เหนือ `webhook()` ชื่อ `handle_<สาขา>` (ไม่ย้ายไฟล์ — ดู "การตัดสินใจ: หยุด refactor ที่ตรงนี้")
- **รับตัวแปรของ `webhook()` ทีละตัว เฉพาะค่าที่สาขานั้นใช้** ห้ามสร้าง dict ใหม่มาห่อหลายตัวรวมกัน
  ส่วนตัวแปรที่เป็น dict หรือ list อยู่แล้ว (`merged_params`, `parameters`, `output_contexts`, `active_contexts`) ส่งทั้งก้อนได้
  > เหตุผล: signature บอกได้ทันทีว่าสาขาพึ่งอะไร เช่น สาขาความรู้รับ `deadline` (กฎเหล็กข้อ 1)
  > ส่วนสาขาสรุปผลไม่รับ ทำให้เห็นปัญหา #6 จาก signature
  > ถ้าส่งเป็น dict แล้วพิมพ์ key ผิด จะได้ `KeyError` ที่ถูก `except` กว้างกลืนเป็นข้อความ error ทั่วไป หาสาเหตุยาก
  > ข้อดีของการรวมเป็นก้อนคือได้ signature เดียวกันสำหรับทำตาราง "intent → ฟังก์ชัน" ซึ่งกับดักข้อ 1 ห้ามอยู่แล้ว
- คืน `dict` เสมอ `webhook()` เป็นคนเรียก `jsonify`
- เงื่อนไขเลือกสาขา ลำดับการตรวจ และ `try/except` อยู่ใน `webhook()` ย้ายแค่เนื้อหาของสาขา
- ห้ามเปลี่ยนข้อความ ชื่อ context หรือ print ใด ๆ
- ไม่มีสาขาไหนเขียนตัวแปรที่โค้ดหลังจากนั้นใช้ต่อ (ทุกสาขาจบด้วย `return`) handler จึงไม่ต้องคืนค่าอื่นนอกจาก dict
  ยกเว้นสาขาออนไลน์แก้ `merged_params['online_income']` ในตัว ซึ่งยังทำงานเหมือนเดิมเพราะ dict ส่งเข้าฟังก์ชันเป็นออบเจกต์เดียวกัน
- ทีละสาขา ทีละ commit และชุด test ต้องผ่านโดยไม่แก้ test
- คำถามค่าลดหย่อนเขียนซ้ำ 3 จุด (`handle_salary`, `handle_rental`, `handle_online` — ต่อท้ายด้วย `liff_donation_link()`)
  > เหตุผล: ยังไม่ได้รวมเป็นที่เดียว แก้ข้อความจุดเดียวแล้วอีกสองสาขาจะถามไม่เหมือนกัน ต้องแก้ครบทั้งสาม

### การตัดสินใจ: หยุด refactor ที่ตรงนี้ (2026-10-04)

- **ไม่ย้าย handler ไปไฟล์อื่น** เพราะทุกสาขาอ่านแยกได้แล้ว ซึ่งแก้ปัญหาตั้งต้น ("โค้ดยาวและไม่เป็นระเบียบ") แล้ว
  และถ้าย้ายไฟล์ กับดักข้อ 2 (`PUBLIC_URL`) จะกลับมามีผล
- **ไม่ทำ dispatcher แบบตาราง intent → ฟังก์ชัน** เพราะเงื่อนไขหลายข้อไม่ได้ตัดสินจากชื่อ intent (กับดักข้อ 1)
  โครง `if/elif` ใน `webhook()` เห็นลำดับชัดกว่า
- กฎเหล็กข้อ 4 ยังใช้อยู่ ถ้าวันหน้าจะย้ายโค้ดอีก
- งานถัดไปตอนนั้นคือเปลี่ยนเป็นปีภาษี 2569 (เสร็จแล้วในขั้น C–G — ดู `docs/history-tax-year-2569.md`)

---

## วิธีทำงานที่ต้องการ

1. **อธิบายเหตุผลก่อนลงมือ** — เจ้าของโปรเจกต์ต้องอธิบายโค้ดทุกบรรทัดได้ในวันสอบ
   จึงต้องการรู้ว่าทำไมเขียนแบบนั้น ไม่ใช่ได้โค้ดสำเร็จรูปมาวางอย่างเดียว
2. **ทำทีละก้อนเล็ก ๆ** — แตก `webhook()` ทีละสาขา ทีละ commit
   ถ้าแก้ก้อนใหญ่ จะตรวจไม่ไหวแล้วต้องยอมรับทั้งที่ยังไม่เข้าใจ
3. **ถามก่อนถ้าไม่แน่ใจ** — โดยเฉพาะเรื่องกฎหมายภาษีหรือการตั้งค่าใน Dialogflow Console
   ซึ่ง AI มองไม่เห็นจากโค้ด
4. **ตอบเป็นภาษาไทย** ชื่อฟังก์ชันและตัวแปรเป็นภาษาอังกฤษตามเดิม
   ข้อความที่บอทส่งให้ผู้ใช้เป็นภาษาไทยลงท้าย "ครับ"
5. **แสดง diff แล้วหยุดรอคำตอบก่อน commit ทุกครั้ง** — การอนุมัติแผนไม่ใช่การอนุมัติ diff
   เพราะเนื้อหาจริงอาจต่างจากแผน ยกเว้นเจ้าของโปรเจกต์พิมพ์ชัดว่า "commit ได้เลยไม่ต้องให้ดู"
   > เหตุผล: เคย commit ก่อนให้ดูมาแล้วสองครั้ง (55d9210, 1d56146)
6. **พิสูจน์ว่าเทสที่เพิ่มจับได้จริง** — ทำโค้ดเสียชั่วคราวแล้วรันให้เห็นว่าเทสล้ม
   ขั้นตอน: คัดลอกไฟล์สำรอง → ทำเสีย → รันเทส → คืนจากสำเนา → เช็ก hash ให้ตรงกับสำเนา · **ห้ามคืนด้วย `git checkout -- <file>`**
   > เหตุผล: `git checkout --` คืนไฟล์เป็นเวอร์ชันใน commit ล่าสุด ตอนพิสูจน์เทส โค้ดใหม่มักยังไม่ได้ commit จึงหายไปด้วย

---

## ขั้นตอนการรันและทดสอบ

ทำตามลำดับ เพราะแต่ละขั้นพึ่งขั้นก่อนหน้า

**1. เตรียม Ollama (ตัวรัน LLM ในเครื่อง)**

```powershell
ollama ps                    # ดูว่ามีโมเดลโหลดอยู่หรือยัง
ollama run llama3.2:3b       # ถ้ายังไม่มี ให้โหลดเข้าหน่วยความจำรอไว้ (ออกด้วย /bye)
```

> ถ้าไม่เปิด ระบบยังทำงานได้ แต่คำถามความรู้จะตกไปใช้ข้อความดิบจากฐานความรู้ทุกครั้ง
> ซึ่งถูกต้องแต่ไม่ได้เรียบเรียง ส่วนการคำนวณภาษีไม่เกี่ยวกับ Ollama เลย

**2. เปิด virtual environment**

```powershell
cd C:\Projects\tax-chatbot
.\.venv\Scripts\Activate.ps1
```

ต้องเห็น `(.venv)` หน้า prompt ก่อนทำอะไรต่อ

**3. ตั้งค่าที่หน้า LIFF ใช้ (ครั้งแรกต่อเครื่อง)**

`LIFF_ID` — ไม่มี `load_dotenv` ใน `app.py` ต้องตั้งเป็นตัวแปรสภาพแวดล้อม

```powershell
setx LIFF_ID "<LIFF ID>"     # วิธีหลัก: ครั้งเดียว ถาวรในเครื่อง แล้วปิด-เปิด PowerShell ใหม่ (setx ไม่มีผลกับหน้าต่างที่เปิดอยู่)
echo $env:LIFF_ID            # ตรวจในหน้าต่างใหม่
$env:LIFF_ID = "<LIFF ID>"   # ทางเลือก (เครื่องอื่น/ชั่วคราว): ใช้ได้เฉพาะหน้าต่างเดียวกับที่รัน python app.py
```

- LIFF ID ไม่ใช่ความลับ (อยู่ในลิงก์ที่ส่งให้ผู้ใช้) แต่เขียนเป็น `<LIFF ID>` ที่นี่ และห้ามใส่ค่าลงในโค้ด
  > เหตุผล: โค้ดอ่านค่าจาก env ตอนถูกเรียก (กับดักข้อ 9) ถ้ามีค่าจริงในไฟล์นี้ AI อาจคัดลอกไป hardcode
  > และถ้าสร้าง LIFF app ใหม่จะต้องตามแก้สองที่
- Channel ID ที่ใช้ตรวจ token คือตัวเลขหน้า "-" ของ LIFF ID ไม่ต้องตั้งแยก · **LINE Login Channel secret ห้ามเก็บในเครื่องหรือ repo** — โค้ดไม่ได้ใช้
- ไม่ได้ตั้ง → log ตอนเริ่มเซิร์ฟเวอร์ขึ้น `⚠️ [LIFF] ยังไม่ได้ตั้ง LIFF_ID` · คำถามค่าลดหย่อนไม่มีลิงก์ · `/liff/ocr` ตอบ 503 `not_configured`
- log บอกแค่ว่าตั้งแล้ว ไม่ได้ตรวจว่าค่าถูก — ถ้า log บอกตั้งแล้วแต่หน้า LIFF เปิดไม่ขึ้น ให้ตรวจค่าด้วย `echo $env:LIFF_ID`

Tesseract (อ่านรูปใบเสร็จ) — ติดตั้งตัว UB Mannheim พร้อมภาษา `tha` และ `eng`
`utils/ocr.py` หาเองจาก `TESSERACT_CMD` → PATH → `C:\Program Files\Tesseract-OCR\tesseract.exe`
ถ้าตั้ง `TESSERACT_CMD` แล้วไฟล์ไม่มีอยู่ จะไม่หาที่อื่นต่อ (ตั้งใจ ให้รู้ว่าตั้งผิด) — ไม่มี Tesseract ระบบอื่นยังทำงาน `/liff/ocr` ตอบ 503

`ADMIN_PASSWORD_HASH` — เปิดหน้า `/admin` (ไม่ได้ตั้ง → `/admin*` ตอบ 404 ระบบอื่นทำงานปกติ)

```powershell
python scripts\make_admin_hash.py           # ถามรหัสสองครั้ง พิมพ์แค่ hash
setx ADMIN_PASSWORD_HASH '<hash>'            # quote เดี่ยวเท่านั้น: hash มี $ ซึ่ง quote คู่จะถูกแปลงเป็นตัวแปร
```

- **ห้ามเก็บรหัสหรือ hash ใน repo หรือไฟล์นี้** · หลัง `setx` ตรวจประวัติคำสั่ง PowerShell ว่าไม่มี hash ค้าง

**4. รันเซิร์ฟเวอร์**

```powershell
python app.py
```

ต้องรันด้วยคำสั่งนี้เท่านั้น (ไม่ใช่ `flask run`) เพราะ ngrok, `PUBLIC_URL` และการโหลด FAISS ล่วงหน้า
ถูกตั้งค่าในบล็อก `if __name__ == '__main__':` — ถ้ารันวิธีอื่น ลิงก์ PDF จะว่างเปล่า
และคำถามความรู้ข้อแรกจะช้าจนเกินงบ 5 วินาที

ควรเห็นข้อความตามลำดับ: เชื่อม Firebase สำเร็จ → ลงทะเบียนฟอนต์ไทย → โหลด FAISS สำเร็จ →
`[TAX-RULES]` (ใช้ค่าตั้งต้นหรือค่าที่แก้ version ไหน พร้อมเวลาที่ใช้อ่าน) → URL ของ ngrok →
บรรทัด `[LIFF]` (ตั้ง `LIFF_ID` แล้วหรือไม่) → `[ADMIN]` (เปิดหรือปิด) → `Running on http://127.0.0.1:5000`

โหลดฐานความรู้ (`get_db`) ไม่ได้ → เซิร์ฟเวอร์**ตั้งใจไม่เปิด** (`test_main_block_stops_when_knowledge_base_fails_to_load`)
> เหตุผล: ถ้าเปิดได้ คำถามความรู้ทุกข้อจะเกินงบ 5 วินาทีโดยไม่มีใครรู้ ล้มตอนเปิดเห็นได้ทันที

**5. ทดสอบผ่าน LINE**

URL ของ ngrok เป็น static domain ที่ผูกกับบัญชี จึงเหมือนเดิมทุกครั้ง
ช่อง Fulfillment ใน Dialogflow Console ตั้งไว้แล้ว ไม่ต้องแก้ แค่พิมพ์ใน LINE ทดสอบได้เลย

### ก่อน demo / วันสอบ (ยังไม่ได้แก้โค้ด — เป็นรายการตรวจ)

- ซ่อนกล่อง debug ในหน้า LIFF (`<details id="debug">` ใน `templates/liff_donation.html` แสดง OS, เวอร์ชัน LINE, ขนาดรูป)
- log ที่พิมพ์ LINE user ID ข้อความ หรือยอดเงิน: `[INCOMING]`, `[QUERY]`, `[RAW PARAMS]`, `[DEBUG-STATE]`, `[DEBUG-HISTORY]`,
  `[DEBUG-STEP2]`, `[DEBUG-STEP3]`, `[DEBUG-STEP3.5]`, `[DEBUG-CALC-SUMMARY]`, `[DEDUCTION-GUARD]`, `[DEBUG-RAG]`
  > เหตุผล: เป็นข้อมูลรายได้ของผู้ทดสอบ (เหตุผลเดียวกับที่ `static/reports/` ถูก ignore) ห้ามให้คนอื่นเห็นหน้าจอเซิร์ฟเวอร์ตอนที่ log เหล่านี้ยังเปิด
- log `[TAX-RULES]` ตอนเปิดต้องขึ้น "ใช้ค่าตั้งต้นในโค้ด" หรือ "ไม่ต่างจากค่าตั้งต้น"
  > เหตุผล: ถ้าใช้ค่าที่แก้อยู่ ภาษีใน demo จะต่างจาก 21 กรณีที่ตรวจไว้
- ภาพหน้าจอต้องเบลอโดเมน ngrok, รหัสผู้ใช้ในหัว PDF และชื่อโปรเจกต์ Firebase
- ป้ายในคอลัมน์เวอร์ชันของหน้าประวัติ (`/admin/history`) ขึ้นบรรทัดใหม่กลางคำ — แก้ CSS

### รันชุด test

แยกจากขั้นที่ 1–5 เพราะไม่ต้องเปิด Ollama, เซิร์ฟเวอร์ Flask หรือ ngrok แค่เปิด `.venv` (ขั้นที่ 2) แล้วรันที่ root

```powershell
pytest -v
```

**เกณฑ์ผ่าน: ต้องไม่มี `failed` และไม่มี `XPASS`**

ตัวเลข ณ ตอนนี้คือ `1183 passed` (เครื่องที่มี Tesseract) ตัวเลขนี้จะเปลี่ยนทุกครั้งที่แก้บั๊กแล้วถอด xfail ออก
(passed เพิ่ม xfailed ลด) และเครื่องที่ไม่มี Tesseract จะมี `skipped` จึงใช้ตัดสินผ่าน/ไม่ผ่านไม่ได้ ให้ดูเกณฑ์ข้างบนแทน

- `pytest.ini` ที่ root บอก pytest สองอย่าง: `pythonpath = .` ให้ใส่ root ของโปรเจกต์ลง path
  เพื่อให้ `from utils.tax_calculator import ...` หาเจอ (ถ้าไม่มีจะได้ `No module named 'utils'`)
  และ `testpaths = tests` ให้หา test เฉพาะในโฟลเดอร์ `tests/`
- **เทสห้ามต่อของจริง:** ไม่เขียน Firestore ไม่สร้าง PDF ลง `static/reports/` ไม่เรียก Ollama ไม่ต่อ LINE
  - `tests/conftest.py` ใส่โมดูลปลอมแทน `utils.firebase_db` ก่อนไฟล์ test ใดจะ `import app`
    > เหตุผล: ไฟล์จริงสร้าง client ของ Firestore ทันทีที่ถูก import (อ่าน `firebase_key.json`) โมดูลปลอมอยู่ใน `sys.modules` ตลอดการรัน
  - `app.py` import `from utils import ocr, liff_auth` แบบโมดูล แล้วเรียก `ocr.read_text(...)` ตอนใช้ — เทส patch ที่ `utils.ocr` / `utils.liff_auth`
    > เหตุผล: ถ้าเปลี่ยนเป็น `from utils.ocr import read_text` การ patch ในเทสจะไม่มีผล แล้วเทสเรียก Tesseract หรือ LINE จริง
  - เทสที่อ่านรูปจริงใน `test_ocr.py` (`requires_tesseract`) skip เองถ้าเครื่องไม่มี Tesseract ภาษา tha+eng หรือ OpenCV
  - เทสระบบ Admin ที่**เขียน** Firestore (บันทึก ย้อนเวอร์ชัน) และหน้าประวัติ ใช้ `tests/fake_firestore.py` (เก็บข้อมูลจริงและบังคับกฎของ transaction)
    **ห้ามใช้ `MagicMock` แทน** — เทสที่แค่อ่าน doc เดียว (ตัวโหลด ฟอร์ม) ยังใช้ `fake_db` แบบ `MagicMock` ใน `test_rules_loading.py` ได้
    > เหตุผล: `MagicMock` ยอมรับทุกการเรียก พิสูจน์ไม่ได้ว่าเขียนสอง doc ใน transaction เดียว หรือว่าสิ่งที่ฟอร์มเขียน ตัวโหลดอ่านได้จริง
    ตัวปลอมใช้ method private ของ google-cloud-firestore 2.29.0 — อัปเกรดแล้ว `test_fake_firestore.py` ล้ม ต้องตรวจตัวปลอมใหม่
  - `tax_rules_not_leaked` (autouse ใน conftest) ล้มเทสที่ทิ้ง `TAX_RULES` / `RULES_INFO` ที่ถูกแก้ไว้ — แทนค่าในเทสด้วย `monkeypatch` เท่านั้น
- เทสแต่ละไฟล์
  - `test_tax_calculator.py` — สูตรใน `utils/tax_calculator.py` (import แค่ `re` กับ `utils.tax_rules`)
  - `test_dialogflow_export.py` — การตั้งค่าในไฟล์ export ใน `dialogflow/` และ annotation ของ training phrase (ใช้รายการคำจาก `deduction_terms`)
  - `test_deduction_terms.py` / `test_deduction_guard.py` — รายการคำ และตัวกัน รวมถึง training phrase ทุกประโยคของ 04 ต้องผ่านตัวกัน
  - `test_receipt_parser.py` — หายอดจากข้อความ OCR (ข้อความล้วน ไม่ต้องมีรูป) · `test_ocr.py` — `utils/ocr.py`
  - `test_liff_auth.py` — ตรวจ token (mock `requests.get`) · `test_liff_ocr.py` — route `/liff/ocr` (mock ตรวจ token และ OCR)
  - `test_liff_page.py` — หน้า LIFF (เช็กแบบ substring ไม่มี JS test) และ log `LIFF_ID` ตอนเริ่มเซิร์ฟเวอร์
  - `test_firebase_db.py` — `utils/firebase_db.py` ตัวจริง (ไฟล์อื่นใช้โมดูลปลอม จึงไม่ครอบไฟล์นี้)
    โหลดด้วย `importlib.util.spec_from_file_location("real_firebase_db", ...)` ชื่อโมดูลต่างจาก `utils.firebase_db`
    จึงไม่ชนกับโมดูลปลอมใน `sys.modules` และก่อนโหลด patch `firebase_admin.credentials.Certificate` ให้โยน error
    ซึ่งเป็นจุดเดียวที่อ่าน `firebase_key.json` → `FirebaseManager()` ตอน import ตกไป `except` ได้ `db = None`
    ไม่อ่านไฟล์ key และไม่ต่อ Firestore จากนั้นเทสใส่ `db = MagicMock()` เองเพื่อตรวจ doc ที่จะเขียน
  - `test_pdf_generator.py` — smoke test ของ `generate_tax_pdf` ตัวจริง เขียน PDF ลง `tmp_path` (ฟังก์ชันเขียนเฉพาะ path ที่ส่งเข้าไป)
    ตรวจแค่ว่าได้ไฟล์ `%PDF` และ `static/reports/` ไม่เปลี่ยน ไม่ตรวจข้อความใน PDF
  - `test_knowledge_index.py` — `faiss_tax_index/` สร้างจาก `tax_knowledge.txt` ฉบับปัจจุบันหรือไม่ โดยเทียบ hash
    ไม่โหลด FAISS หรือ embedding (กับดักข้อ 4) · hash ไม่ขึ้นกับ CRLF/LF · import `utils.create_knowledge` ไม่ลาก langchain
    (เทสหลังรันใน process แยกด้วย `sys.executable` เพราะ `test_webhook.py` โหลด langchain เข้า `sys.modules` ไปแล้ว)
  - `test_webhook.py` — ยิง fixture ใน `tests/fixtures/` เข้า `/webhook` ผ่าน Flask test client
    เพื่อล็อกการเลือกสาขา, outputContexts และข้อความตอบ ก่อน refactor `webhook()`
    mock PDF / RAG ทุกเทส จึงไม่สร้าง PDF และไม่เรียก Ollama
  - `test_line_verified_cases.py` — ภาษีของ 21 กรณีที่ทดสอบใน LINE แล้ว (เงินเดือน 40,000/เดือน ประกันสังคม 10,500)
    ยิงผ่าน `/webhook` แบบเดียวกับ `test_webhook.py` (มี `mocks` ของตัวเอง) — ถ้าล้มดูกฎเหล็กข้อ 2
  - ระบบ Admin: `test_rules_store` / `test_rules_loading` / `test_rules_version` (ตรวจค่า โหลด และหมายเหตุใน LINE/PDF)
    · `test_admin_routes` / `test_admin_login` / `test_admin_edit` / `test_admin_save` / `test_admin_history` / `test_admin_restore`
    · `test_fake_firestore` (ตัวปลอมเอง) — สิ่งที่แต่ละไฟล์ทดสอบอยู่ใน docstring หัวไฟล์ เหตุผลของข้อตัดสินใจที่เทสล็อกอยู่ใน `docs/history-admin-tax-rules.md`
- รันทั้งชุดราว 20–25 วินาที ส่วนใหญ่คือเทสบล็อก `__main__` ใน `test_rules_loading.py` และ import langchain ตอน `import app`
- `1 warning` ท้ายผล pytest: `langchain-community` แจ้ง `DeprecationWarning` ตอน import FAISS
  ใน `utils/knowledge_search.py` (มีมาก่อน refactor) ยังใช้งานได้ปกติเพราะ `requirements.txt` ล็อกเวอร์ชันไว้
  **ห้ามซ่อนคำเตือนด้วย `filterwarnings`** ย้ายไป package ใหม่หลัง refactor เสร็จ
  และต้องตรวจว่า FAISS index เดิม (`faiss_tax_index/`) ยังโหลดได้ **และค้นได้ chunk กับ score เดิม**
  > เหตุผล: เทส hash ตรวจแค่ว่า index สร้างจาก `tax_knowledge.txt` ฉบับปัจจุบัน ไม่ได้ตรวจผลค้น
  > วิธีเทียบ (คำถาม 15 ข้อ k=2) ดูหัวข้อ "กัน index" ใน `docs/history-tax-year-2569.md`
- ชื่อ test บอกประเภท:
  - test ธรรมดา: ค่าที่คาดหวังมาจากกฎหมายหรือสูตร และโค้ดให้ผลตรงแล้ว
  - `xfail(strict=True)`: บั๊กที่ยืนยันแล้ว ค่าที่คาดหวังในเทสคือ "ค่าที่ถูก" เทสจึงยังไม่ผ่านจนกว่าจะแก้บั๊ก
  - `test_current_behavior_*`: เรื่องที่ยังไม่ได้ตัดสินใจ ล็อกค่าที่ได้จากการรันจริงไว้ เพื่อจับว่าพฤติกรรมเปลี่ยนระหว่าง refactor หรือไม่

(`test_speed.py` เป็นสคริปต์วัดความเร็วที่ถูก ignore ไว้ ไม่ใช่ส่วนหนึ่งของชุด test)

---

## สภาพแวดล้อม

| เรื่อง | รายละเอียด |
|---|---|
| ระบบปฏิบัติการ | Windows + PowerShell — ไม่มี `grep`, `xargs`, `wc` แบบ Linux ให้ใช้คำสั่ง PowerShell |
| `requirements.txt` | **ห้าม `pip freeze > requirements.txt` ใน PowerShell 5.1** — `>` เขียนเป็น UTF-16 (เคยเกิดแล้ว ต้องแปลงกลับใน b33d3d5) แก้ด้วย editor หรือให้ Python เขียนไฟล์ UTF-8 เอง |
| ข้อความไทยใน output | ตั้ง `[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; $env:PYTHONIOENCODING = "utf-8"` ก่อนรัน python ถ้ายังเพี้ยนให้สคริปต์เขียนไฟล์ UTF-8 เองแล้วเปิดอ่าน — ห้ามใช้ `>` (แปลง encoding ซ้ำ) และห้ามเขียนสคริปต์ที่มีภาษาไทยด้วย heredoc ของ Git Bash (บันทึกเป็น cp874 ประโยคทดสอบเพี้ยน) |
| DEBUG logging | ถ้าเปิด `logging.DEBUG` urllib3 จะ log URL ตอนตรวจ LIFF token ซึ่งมี token อยู่ใน query string — อย่าเปิดตอนใช้งานจริง |
| ตำแหน่งโปรเจกต์ | `C:\Projects\tax-chatbot` (ย้ายออกจาก OneDrive แล้ว ห้ามแนะนำให้ย้ายกลับ) |
| Python | 3.13 ใน `.venv` / รายการ package อยู่ใน `requirements.txt` |
| ngrok | เปิดผ่าน `pyngrok` ใน `app.py` / authtoken อยู่ในไฟล์ตั้งค่าของ ngrok ไม่ได้อยู่ในโค้ด |
| ไฟล์ลับ | `firebase_key.json` ที่ root ถูกกันไว้ใน `.gitignore` — **ห้ามเปิดอ่าน ห้าม commit** |
| LLM | `llama3.2:3b` ผ่าน Ollama ที่ `http://127.0.0.1:11434` — **ห้ามเปลี่ยนกลับเป็น `localhost`** บน Windows จะลอง IPv6 ก่อน เสียเวลาราว 2 วินาที เกิน connect timeout ทำให้ LLM timeout ทุกครั้ง |
| Embedding | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` |
| google-cloud-firestore | ล็อก 2.29.0 ใน `requirements.txt` — **อัปเกรดแล้วต้องตรวจ `tests/fake_firestore.py`** (ใช้ method private ของ Transaction) |
| เวลาไทย | หน้า admin ใช้ +7 คงที่ — ห้ามเปลี่ยนเป็น `zoneinfo("Asia/Bangkok")` บน Windows ต้องลง `tzdata` เพิ่ม (ไทยไม่มีเวลาออมแสง) |

---

## แผนที่ไฟล์

| ไฟล์ | หน้าที่ | หมายเหตุ |
|---|---|---|
| `app.py` | route `/webhook` (รับจาก Dialogflow), `/download/<filename>` (ส่ง PDF), `/liff/donation` (หน้า LIFF) และ `POST /liff/ocr` (อ่านยอดจากรูป) · ลงทะเบียน blueprint `/admin` | ~700 บรรทัด `webhook()` เตรียมข้อมูลและเลือกสาขา เนื้อหาแต่ละสาขาอยู่ใน `handle_*` |
| `utils/rules_store.py` | ตรวจ รวม โหลด และบันทึกค่าที่ admin แก้ (`build_rules`, `load_effective_rules`, `save_config`, ลายนิ้วมือ `base_token`) | ไม่ import firebase ตอน import — รับ `db` เป็น argument · ห้ามเรียกใน `webhook()` (กฎเหล็กข้อ 1) |
| `admin_routes.py`, `admin_form.py`, `admin_history.py` | หน้า `/admin`: ตัวกันในเครื่อง + login, ฟอร์มแก้ค่าและหน้ายืนยัน, ประวัติและย้อนเวอร์ชัน | `admin_form` แปลงข้อความด้วย `Decimal` แล้วให้ `build_rules` ตรวจ · `admin_history` ไม่อ่าน Firestore เอง |
| `templates/admin_*.html`, `_admin_banners.html`, `_change_table.html` | หน้าของระบบ Admin | แหล่งอ้างอิงและหมายเหตุเป็นข้อความที่ผู้ใช้พิมพ์ — ห้ามใส่ `\|safe` |
| `scripts/make_admin_hash.py` | สร้างค่า `ADMIN_PASSWORD_HASH` | พิมพ์แค่ hash ไม่เขียนไฟล์ |
| `tests/fake_firestore.py` | Firestore ปลอมที่เก็บข้อมูลจริง ใช้เทสระบบ Admin | ไม่ใช่ไฟล์เทส · ผูกกับ google-cloud-firestore 2.29.0 |
| `utils/tax_calculator.py` | สูตรภาษีทั้งหมด: แปลงข้อความเป็นตัวเลข, ขั้นบันได, วิธีที่ 2, อัตราค่าใช้จ่ายค่าเช่า, ค่าลดหย่อน, คำแนะนำวางแผนภาษี | **import แค่ `re` กับ `utils.tax_rules`** ไม่พึ่ง Flask หรือ Firebase จึงเขียน test ได้ทันที — รักษาคุณสมบัตินี้ไว้ |
| `utils/tax_rules.py` | ตัวเลขตามกฎหมายของปีภาษีเดียว (`TAX_RULES`, ปีอยู่ใน key `tax_year` ตอนนี้ 2569) | ค่าคงที่ล้วน ไม่ import อะไร ไม่มี I/O — วิธีอ่านดูกฎเหล็กข้อ 2 |
| `docs/tax-rules-2569.md` | สเปกตัวเลขภาษีปี 2569 ที่โค้ดใช้อยู่ เทียบกับค่าเดิม (2567) พร้อมแหล่งอ้างอิง | ⚠️ = แหล่งรอง ต้องตรวจตัวบทก่อนอ้างในเล่ม |
| `utils/knowledge_search.py` | ค้นฐานความรู้ด้วย FAISS + ให้ Ollama เรียบเรียงคำตอบภายใน deadline | มีข้อความสำรองเมื่อ LLM ใช้ไม่ได้ |
| `utils/pdf_generator.py` | สร้าง PDF สรุปผลด้วย ReportLab | ใช้ฟอนต์ไทย `static/fonts/THSarabunNew.ttf` |
| `utils/firebase_db.py` | `firebase_client` สำหรับ `save_tax_report()` และ `get_user_history()` | อ่าน key จาก `firebase_key.json` |
| `utils/create_knowledge.py` | สร้าง FAISS index ใหม่จาก `tax_knowledge.txt` แล้วเขียน hash ของไฟล์นั้นลง `faiss_tax_index/tax_knowledge.sha256` | รันแยกเมื่อแก้ฐานความรู้ ต้องรันจาก root (`python utils\create_knowledge.py`) · import langchain อยู่ใน `build_vector_store()` ให้เทส import `knowledge_sha256` ได้เบา — ห้ามย้ายขึ้นหัวไฟล์ (`test_create_knowledge_import_is_light` จับ) |
| `tax_knowledge.txt` | ฐานความรู้ภาษี (Markdown) | แก้ไฟล์นี้แล้วต้องรัน `create_knowledge.py` ใหม่ (ถ้าลืม `test_index_hash_matches_knowledge_file` ล้ม) · หัวข้อ 5.1 แยกเป็น `###` ละรายการ (แก้โครงสร้าง ไม่ใช่เนื้อหา) เพื่อให้แต่ละรายการอยู่คนละ chunk — ห้ามรวมกลับ |
| `faiss_tax_index/` | ฐานข้อมูลเวกเตอร์ที่สร้างแล้ว (100 chunk ~260 KB) | เก็บใน git เพราะเล็ก · **ต้องสร้างจาก `tax_knowledge.txt` เสมอ และ commit คู่กับไฟล์นั้นใน commit เดียวกัน** · `tax_knowledge.sha256` คือ hash ของไฟล์ความรู้ที่ใช้สร้าง · สร้างใหม่ทุกครั้ง `index.pkl` จะเปลี่ยนเสมอ (docstore id สุ่มด้วย uuid4) แม้เนื้อหาเหมือนเดิม |
| `static/reports/` | PDF ที่ระบบสร้าง | ถูก ignore เพราะมีข้อมูลรายได้ของผู้ทดสอบ |
| `utils/deduction_terms.py` | รายการคำของแต่ละค่าลดหย่อน และคำที่ห้ามนับ (SSF, ประกันสังคม, ประกันรถ ฯลฯ) | ที่เดียวที่เก็บคำ ใช้ทั้งตัวกันและเทส export — ดูกฎเหล็กข้อ 3 |
| `utils/deduction_guard.py` | ตัวกันค่าลดหย่อนก่อนสาขา 04 คำนวณ (#25) | import แค่ `re`, `decimal`, `deduction_terms` ไม่มี I/O |
| `utils/thai_text.py` | แปลงเลขไทยเป็นอารบิก และรวม ํ + า ที่ Tesseract แยกกลับเป็น ำ | ใช้ร่วมกันใน parser และรายการคำ |
| `utils/ocr.py` | อ่านข้อความจากรูปด้วย OpenCV + Tesseract (ส่งรูปทาง stdin ไม่เขียนรูปลงดิสก์) | import `cv2` ในตัวฟังก์ชัน เครื่องที่ไม่มี OpenCV/Tesseract ยังรันระบบเดิมได้ |
| `utils/receipt_parser.py` | ตรวจคำบริจาค และหายอดจากข้อความ OCR แบบไม่เดา | import แค่ `re`, `decimal`, `thai_text` — กฎ "ไม่เดา" ดูกฎเหล็กข้อ 2 |
| `utils/liff_auth.py` | ตรวจ LIFF access token ของผู้ใช้กับ LINE | ไม่ใช่ Messaging API — ดู "โปรเจกต์นี้คืออะไร" |
| `templates/liff_donation.html` | หน้า LIFF: เลือกรูป → ย่อเป็น JPEG ในเบราว์เซอร์ → ส่ง `/liff/ocr` → ส่ง "บริจาค N" เข้าแชต | ข้อความทุกสถานะลงท้าย "ครับ" · status ใหม่ต้องเพิ่มที่นี่ (กฎเหล็กข้อ 2) |
| `scripts/make_sample_receipts.py`, `scripts/measure_ocr.py` | สร้างใบเสร็จจำลอง (seed คงที่) และวัดความแม่นของ OCR | ผลอยู่ใน `ocr_eval/` ซึ่งถูก ignore — ห้าม commit รูป |
| `scripts/scrub_fixtures.py` | แทน LINE user ID ใน `tests/fixtures/` ด้วยค่าปลอมก่อน commit | |
| `docs/known-limitations.md` | ข้อจำกัดที่ยอมรับไว้ แต่ละข้อมีผลกระทบและสิ่งที่ระบบเลือกทำ | ข้อที่มีเลขปัญหาอยู่ในหัวข้อ "ปัญหาที่รู้แล้ว" ของไฟล์นี้ |
| `docs/holdout-sentences.md` | ประโยคทดสอบที่ห้ามใช้เป็น training phrase + ขั้นตอนแก้ training phrase | |
| `docs/ocr-eval-2569-10.md` | ผลวัดความแม่น OCR ก่อน/หลังแก้ parser | |
| `docs/history-tax-year-2569.md` | ประวัติงานเปลี่ยนเป็นปีภาษี 2569 (ขั้น C–G) | |
| `docs/history-admin-tax-rules.md` | ประวัติงานระบบ Admin (ก้อน 1–7): ข้อตัดสินใจพร้อมเหตุผล รูปร่าง doc ใน Firestore และผลทดลองจริง | |

---

## ขอบเขตงาน (อัปเดตเมื่อสถานะเปลี่ยน)

**ทำเสร็จแล้ว**
เงินได้ 4 ประเภท (เงินเดือน, โบนัส, ค่าเช่า, ขายออนไลน์) ·
ค่าลดหย่อน 9 รายการ (ส่วนตัว, คู่สมรส, บุตร, บิดามารดา, ประกันสังคม, ประกันชีวิต+สุขภาพ,
ดอกเบี้ยบ้าน, RMF, เงินบริจาค) + ThaiESG (เงินได้ที่ได้รับยกเว้น) · แจ้งผู้ใช้เมื่อกรอก SSF ว่าลดหย่อนไม่ได้แล้ว ·
ตัวเลขตามปีภาษี 2569 · ภาษีวิธีที่ 2 (0.5%) · สิทธิทางภาษีที่ยังใช้ได้ (RMF, ThaiESG, ประกันชีวิต) ·
ตอบคำถามความรู้ด้วย RAG · PDF สรุปผล · ประวัติการคำนวณ ·
แสดงปีภาษีใน LINE, PDF, Firestore และประวัติ ·
อ่านยอดเงินบริจาคจากรูปใบเสร็จผ่านหน้า LIFF (OCR) โดยผู้ใช้ตรวจตัวเลขและกดส่งเอง ·
ตัวกันค่าลดหย่อนที่ Dialogflow ใส่ผิดช่อง (#25) ·
แจ้งผู้ใช้เมื่อเงินบริจาคถูกตัดที่ `donation_rate` ("ใช้ได้ X จาก Y บาท" ต่อท้าย `tax_advice` ไปถึง LINE และ PDF) ·
ระบบ Admin แก้อัตราภาษีและเพดานลดหย่อนผ่านเว็บ (แหล่งอ้างอิงบังคับ, ประวัติทุกเวอร์ชัน, ย้อนเวอร์ชัน, มีผลหลังรีสตาร์ต,
`rules_version` ใน `tax_history` และหมายเหตุใน LINE/PDF เมื่อค่าต่างจากค่าตั้งต้น — ทดสอบกับ Firestore จริงแล้ว)

**ข้อจำกัดที่ควรเขียนในเล่ม** อยู่ใน `docs/known-limitations.md` (ผลวัด OCR ใน `docs/ocr-eval-2569-10.md`)

**กำลังทำ** refactor ขั้นแรกเสร็จแล้ว `webhook()` เหลือแค่เตรียมข้อมูลกับเลือกสาขา
ทุกสาขาเป็น `handle_*` ใน `app.py` ทดสอบผ่าน LINE แล้ว
— เสร็จแล้ว: ทักทาย, รีเซ็ต, ดูประวัติ, เริ่มคำนวณ (01), เงินเดือน (02), ค่าเช่า (03), ขายออนไลน์ (05), สรุปผล (04), ความรู้
(ดู "การตัดสินใจ: หยุด refactor ที่ตรงนี้")

**ค้างอยู่ — ห้ามทำจนกว่าจะสั่ง**
- แจ้งผู้ใช้เมื่อ context หมดอายุ (ผู้ใช้เงียบเกิน 20 นาทีแล้วกลับมาพิมพ์ตัวเลข)
- หาเหตุผลรองรับค่า `TAX_DISTANCE_THRESHOLD = 14.0`
  — หลักฐานจากขั้น F (L2 distance ของ chunk ใกล้สุด, index เดิม 16 chunk → index ใหม่ 100 chunk ที่ 6d25a06):
  "ภาษีที่ดินและสิ่งปลูกสร้างจ่ายเท่าไหร่" (นอกขอบเขต) 13.11 → 10.62 **ผ่าน threshold ทั้งคู่**
  · "ภาษีรถยนต์ต่อทะเบียนเท่าไหร่" 16.55 → 14.11 ถูกปัดแต่**เกือบผ่าน**
  · "สูตรต้มยำกุ้ง" 22.02 → 18.98 · "วันนี้อากาศเป็นยังไง" 28.69 → 28.52 · "VAT คืออะไร" 32.03 → 27.89
  · ในขอบเขต "SSF ยังลดหย่อนได้ไหม" 15.37 (ถูกปัด) → 12.77 · "Thai ESG ต้องถือกี่ปี" 13.11 → 12.93
  — chunk มากขึ้นทำให้ระยะใกล้สุดลดลงทั้งคำถามในและนอกขอบเขต ยังไม่แก้ค่า
- ✅ **เปลี่ยนเป็นปีภาษี 2569** (เงินได้ปี 2569 ยื่นต้นปี 2570) — **เสร็จแล้ว (ขั้น C–G)** รายละเอียดแต่ละขั้นอยู่ใน `docs/history-tax-year-2569.md`
  **ที่ยังเหลือ (ยังไม่ต้องทำจนกว่าจะสั่ง)**
  - ⚠️ แหล่งอ้างอิงที่ยังเป็นแหล่งรอง: อัตราเงินสมทบประกันสังคม 5% (docs 2.2) · กอช. 30,000 จากหน้า [4] ที่เป็นข้อมูลปี 2567 (docs หัวข้อ 4)
  - วันขยายเวลายื่นแบบออนไลน์ของปี 2570 ยังไม่มีประกาศ — มีแล้วให้เพิ่มใน docs 2.4 ก่อน แล้วค่อยแก้ `tax_knowledge.txt`
  - ThaiESG: ตัวบทกำหนดช่วงซื้อถึง 31 ธ.ค. 2569 ปี 2569 จึงเป็นปีภาษีสุดท้ายที่ `thai_esg_cap` ใช้ได้ (docs หัวข้อ 3)
    ปีภาษีถัดไปต้องตรวจกฎกระทรวงใหม่
  - ปัญหาที่เกี่ยวข้องและยังไม่แก้: #20 (สิทธิที่เหลือไม่ดูภาษีวิธีที่ 2), #22 (LLM อ้าง ม.47 กับ ThaiESG)
  - ข้อความใน `tax_knowledge.txt` ที่ไม่มีใน docs ไม่ได้ตรวจกับปี 2569 (ดูขั้น F ในไฟล์ประวัติ) และรายการใน docs หัวข้อ 4
    ที่บอทยังไม่รองรับ (เช่น โซลาร์เซลล์ใหม่ปี 2569) ต้องตัดสินใจว่าจะเพิ่มหรือเขียนเป็นข้อจำกัด
  - เล่มข้อ 1.3.2: ตัวอย่างในวงเล็บควรแก้เป็น "ปี 2569 ที่ต้องยื่นต้นปี 2570" (docs หัวข้อ 1) — เจ้าของโปรเจกต์แก้ในเล่ม

**จะทำต่อไป**
- งานก่อน demo (หลัง merge ระบบ Admin): ซ่อน debug หน้า LIFF, ปิด log ที่มีข้อมูลอ่อนไหว, CSS ป้ายหน้าประวัติ — ดู "ก่อน demo / วันสอบ"
- ย้าย FAISS ออกจาก `langchain-community` ไป package ใหม่ (ดูหมายเหตุ `1 warning` ในหัวข้อ "รันชุด test")
- แจ้งค่าลดหย่อนรายการอื่นที่ถูกตัดที่เพดาน (รายการอยู่ใน `docs/known-limitations.md` หัวข้อ 1) ด้วย `deduction_breakdown(total_income, params)`
  ดึงเนื้อในของ `calculate_detailed_deductions` ออกมาคืน `{รายการ: (ยอดที่กรอก, ยอดที่ใช้ได้)}`
  แล้ว `calculate_detailed_deductions` เรียกมันและคืน tuple 4 ค่าเหมือนเดิม
  — ย้ายโค้ดเป็น commit แยกก่อนตามกฎเหล็กข้อ 4 (`test_tax_calculator.py` และ `test_line_verified_cases.py` ต้องผ่านโดยไม่แก้)
  > เหตุผล: สูตรเพดานอยู่ที่เดียว ข้อความจึงบอกยอดตรงกับที่หักจริงเสมอ
  > ถ้าเปลี่ยนสิ่งที่ฟังก์ชันคืนต้องแก้เทสเดิม ถ้าเขียนฟังก์ชันใหม่คำนวณแยก สูตรจะอยู่สองที่แล้วคลาดกันได้

---

## ปัญหาที่รู้แล้ว (ยังไม่แก้ — ห้ามแก้ระหว่าง refactor)

เลขข้อเป็นรหัสประจำปัญหา ไม่เรียงใหม่เมื่อแก้ข้อใดเสร็จ (เทสและเล่มอ้างเลขเหล่านี้)
เลขที่หายไปคือข้อที่ปิดแล้ว · ข้อจำกัดที่ตั้งใจยอมรับ (ไม่มีเลขข้อ) อยู่ใน `docs/known-limitations.md`
- #1 ยานพาหนะ และ #7 โรงเรือน — แก้โค้ดแล้ว (ตอนนี้ได้ 30% ทั้งคู่)
- #9 และ #10 `clean_number` ตัดปี พ.ศ. ผิด — แก้แล้ว (ทาง ก) ตัดปีเฉพาะเมื่อมี "ปี"/"พ.ศ." นำหน้า
  ตัดก่อนสาขา "ล้าน" และค่าที่เป็น int, float หรือ dict ที่มี `amount` คืนตัวเลขเลยโดยไม่ผ่าน regex
  — เทส: `test_clean_number_amount_looks_like_buddhist_year`, `test_clean_number_million_with_year`
- #12 `spouse` ที่เป็น list ไม่ถูกนับ — ไม่ใช่บั๊ก เพราะ Console ตั้ง `isList: false`
  (export `dialogflow/intents/04_Tax_Interview_Deductions.json`) และ `tests/fixtures/010_04_Tax_Interview_Deductions.json`
  ได้ `spouse` เป็น string `"yes"` สำหรับข้อความ "มีคู่สมรสไม่มีเงินได้"
  — กันการตั้งค่าเปลี่ยนด้วย `test_spouse_is_not_list` ส่วน `test_current_behavior_spouse_as_list_not_counted` เก็บไว้บันทึกว่าโค้ดไม่รองรับ list
- #14 "ยื่นภาษีได้ถึงวันไหน" ถูกจับเป็น `01` — แก้บน Console แล้ว: สร้าง intent `06_Tax_Knowledge`
  ที่มีประโยคนี้เป็น training phrase จึงตกไปสาขาความรู้ ไม่ต้องแก้โค้ด — ทดสอบผ่าน LINE แล้ว
- #15 ข้อความถามประเภทเงินได้ยกตัวอย่างประเภทที่ไม่รองรับ — แก้บน Console แล้ว: prompt ของ `income_types`
  ใน `01` เหลือ "คุณมีรายได้ประเภทไหนบ้างครับ (เงินเดือน, ค่าเช่า, ขายของออนไลน์)" — ทดสอบผ่าน LINE แล้ว
- #18 prompt ของ `social_security` ถามยอดรายเดือน ("โดนหักประกันสังคมเดือนละเท่าไหร่ครับ (เช่น 750)")
  แต่โค้ดนับเป็นยอดทั้งปีโดยไม่คูณ 12 (`min(clean_number(params.get('social_security', 0)), ...)` ใน `calculate_detailed_deductions`)
  ผู้ใช้ที่ตอบ 750 ตามตัวอย่างได้ลดหย่อนแค่ 750 แทน 9,000 — แก้แค่ข้อความ ไม่แตะสูตร (ปิดใน commit เดียวกับที่บันทึกข้อนี้):
  prompt บน Console ถามยอดทั้งปี บอกให้ดูจากใบ 50 ทวิ และให้คนที่จำได้แค่ยอดรายเดือนคูณเองแล้วพิมพ์เฉพาะยอดรวม
  ข้อความแสดงกลับ `"- หักประกันสังคม: "` ใน `handle_salary` เปลี่ยนเป็น `"- หักประกันสังคมทั้งปี: "` (`app.py` บรรทัด 135 ณ commit 0347426)
  — เทส: `test_social_security_prompt_asks_yearly` — ทดสอบผ่าน LINE แล้ว (ตอบ 9000 และ 0 ได้ภาษีตรงกับที่คิดมือ)
  — หมายเหตุ: `clean_number("750 × 12")` และ `clean_number("750x12")` ได้ 750 (เลือกตัวเลขที่ยาวที่สุด) ตัวอย่างใน prompt จึงให้พิมพ์ยอดรวม ไม่ใช่สูตร
- #19 คำถามค่าลดหย่อนไม่บอกหน่วย แต่โค้ดนับทุกยอดเป็นยอดทั้งปี (เช่น ดอกเบี้ยบ้านที่คนมักจำเป็นยอดรายเดือน)
  — แก้แค่ข้อความ (ปิดใน commit เดียวกับที่บันทึกข้อนี้): คำถาม "คุณมี 'ค่าลดหย่อน' เพิ่มเติมไหมครับ?" ทั้ง 3 จุด
  (`handle_salary`, `handle_rental`, `handle_online` — `app.py` บรรทัด 145, 178, 209 ณ commit 0347426)
  เติม "จำนวนเงินให้ตอบเป็นยอดรวมทั้งปี" — เทส: `test_deduction_question_asks_yearly`
- #3 SSF และ RMF ใช้ parameter เดียวกัน (`ssf_rmf`) — ปิดแล้วในขั้น C และ E
  เพดาน: `ssf_rmf` ถือเป็น RMF อย่างเดียว ใช้ `rmf_cap` (85df929)
  คำถามค่าลดหย่อนเลิกพูดถึง SSF (4180200) · Console ไม่ annotate ยอด SSF เป็น `ssf_rmf` แล้ว (e7d8d14)
  · ผู้ใช้ที่พิมพ์ SSF ได้หมายเหตุว่า SSF ลดหย่อนไม่ได้แล้ว เช็กจากข้อความดิบ
  `if any(k in user_query.lower() for k in ['ssf', 'เอสเอสเอฟ']):` ใน `handle_deductions` (`app.py` บรรทัด 295 ณ commit e7d8d14)
  ข้อความว่า "ไม่มี SSF" ก็ได้หมายเหตุด้วย ยอมรับไว้เพราะเป็นแค่ข้อมูล ไม่กระทบตัวเลข
  — เทส: `test_ssf_rmf_annotation_follows_rmf_not_ssf`, `test_ssf_note_and_ssf_not_counted`,
  `test_ssf_note_with_rmf_in_same_message`, `test_deduction_question_lists_rmf_and_thai_esg`
- #23 ข้อความที่มีบริจาคคู่กับค่าลดหย่อนอื่น Dialogflow จับได้แค่ `donation` ค่าอื่นหายเงียบ ภาษีสูงเกินจริง
  — แก้บน Console (training phrase คู่ทั้งสองลำดับ) 8bea01a, 38e708f — เทส: `test_donation_paired_with_each_deduction_both_orders`
- #24 ประโยคเดี่ยวของประกันสุขภาพ บิดามารดา และดอกเบี้ยเงินกู้บ้าน ได้ Fallback — แก้บน Console 7d1e1ef, db985ae
  — เทส: `test_each_deduction_term_has_solo_phrase`, `test_home_loan_phrases_say_home`
- #25 Dialogflow ใส่ตัวเลขผิดช่อง ("ดอกเบี้ยเงินกู้รถ 20000" ได้ `donation`) — ตัวกัน `utils/deduction_guard.py`
  (bf9f89e, dfd0dc3, 9b8a7d8) ดูกฎเหล็กข้อ 3 และขั้น 6–7 ของกับดักข้อ 1 — เทส: `test_deduction_guard.py`
- #26 "บุตร", "อาร์เอ็มเอฟ", "กองทุนรวมเพื่อการเลี้ยงชีพ", "ดอกเบี้ยกู้บ้าน" ได้ Fallback หรือผิดช่อง — แก้บน Console 58c3b6c, 516596f
  — เทส: `test_each_deduction_term_has_solo_phrase`
  ประโยคทดสอบของ #23–#26 อยู่ใน `docs/holdout-sentences.md`

ข้อใหม่ให้ใช้เลขถัดจากเลขสูงสุด

- **#2** **ตรวจคำว่า "บ้าน" ก่อนคำว่า "ที่ดิน"** ข้อความอย่าง "ที่ดินพร้อมบ้าน" จะได้ 30% เสมอ
  — อาจถูกหรือผิดขึ้นกับเจตนา ต้องตัดสินใจ
  — 📌 ล็อกพฤติกรรม: `test_current_behavior_rental_expense_rate`
- **#4** **การจัดการ context ไม่สม่ำเสมอ** สาขาเงินเดือนและออนไลน์ปิด `awaiting_*` ของตัวเอง
  แต่สาขาค่าเช่าไม่ปิด `awaiting_rental` (ปล่อยให้หมดอายุเอง)
  — หลักฐาน: `tests/fixtures/009_Default_Fallback_Intent.json` มี `awaiting_rental` ค้างอยู่คู่กับ `awaiting_online` ตอนถึงสาขาออนไลน์
- **#5** **สาขาออนไลน์อาจแย่งงานสาขาลดหย่อน** ถ้า `awaiting_online` ยังค้างอยู่
  แล้ว Dialogflow จับได้ intent `04` ข้อความนั้นจะเข้าสาขาออนไลน์ก่อน เพราะเงื่อนไขอยู่ก่อนกัน
- **#6** **สาขาสรุปผลไม่คุมเวลา** สร้าง PDF + เขียน Firestore โดยไม่เช็ก `req_deadline`
- **#8** **ทรัพย์สินที่ไม่รู้จักได้ 30%** สาขา `else` คืน 30% แต่กฎหมายให้ "ทรัพย์สินอื่น" 10%
  (เช่น "เครื่องจักร") — ต้องตัดสินใจ เพราะข้อความที่ไม่รู้จักอาจเป็นบ้านที่สะกดต่างไปก็ได้
  — 📌 ล็อกพฤติกรรม: `test_current_behavior_rental_expense_rate`
- **#11** **เครื่องหมายลบไม่สม่ำเสมอ** regex มี `[-+]?` เฉพาะรูปแบบที่มีจุดทศนิยม "-5000" จึงได้ 5000
  แต่ "-1.5" ได้ -1.5 ถ้าเลขติดลบหลุดเข้า `min()` ของเพดานลดหย่อน ยอดลดหย่อนจะติดลบได้
  — ค่าที่เป็น int/float (ไม่ใช่ข้อความ) คืนตามจริงรวมเครื่องหมายลบตั้งแต่แก้ #9 (`-5000` ได้ -5000 ไม่ใช่ 5000)
  — 📌 ล็อกพฤติกรรม: `test_current_behavior_clean_number`, `test_current_behavior_negative_social_security_becomes_positive`
- **#13** **ตรวจคำว่า "เกษตร" ก่อนคำว่า "เรือ"/"รถ"** "โรงเรือนเกษตร" และ "รถไถเกษตร" ได้ 20%
  แต่ตาม พ.ร.ฎ. ฉบับที่ 11 ม.5 ควรได้ 30% เพราะเป็นโรงเรือนกับยานพาหนะ ไม่ใช่ที่ดินเกษตรกรรม
  — ปัญหาเดียวกับ #2 (ลำดับการเทียบคำตัดสินผล) ต้องตัดสินใจ
- **#16** **`income_types` ใน `01` เป็น `@sys.any`** (`isList: true`, required) ระหว่าง slot filling
  ทุกข้อความจะถูกเก็บเป็นประเภทเงินได้ เช่น "สวัสดี" (ทดสอบใน Try it now แล้ว)
  — ผลฝั่งโค้ด (ยังไม่ได้ทดสอบผ่าน LINE): คำทักทาย คำรีเซ็ต และคำว่าดูประวัติ โค้ดจับได้ก่อนตามขั้น 1–3 ของกับดักข้อ 1
  จึงตอบตามคำนั้น แต่ Dialogflow ยังถือว่าเป็นคำตอบของ `income_types` อยู่ ส่วนข้อความอื่นที่ไม่ใช่ประเภทที่รองรับ
  เช่น "งานอิสระ" จะตกไป `else` ของสาขา `01` แล้วถามซ้ำ
  — สาเหตุอยู่ที่การตั้งค่าบน Console (`dialogflow/intents/01_Tax_Interview_Start.json`) ไม่ใช่โค้ด
  — แนวทาง: เปลี่ยนเป็น custom entity ที่มีเฉพาะประเภทที่รองรับ — ยังไม่แก้
- **#17** **`/download/<filename>` ไม่ตรวจสิทธิ์** ใครรู้ชื่อไฟล์ก็โหลด PDF ที่มีข้อมูลรายได้ได้
  `download_file` ส่งไฟล์ด้วย `send_from_directory('static/reports', filename, as_attachment=True)` ทันที
  (`app.py` บรรทัด 27 ณ commit d4219ef) ชื่อไฟล์สุ่มแค่ 8 hex จาก `uuid.uuid4().hex[:8]` ใน `handle_deductions`
  (`app.py` บรรทัด 292 ณ commit d4219ef) และ LINE เรียกลิงก์เหล่านี้เองตอนสร้าง link preview
  — เขียนเป็นข้อจำกัดด้านความปลอดภัยในเล่ม — ยังไม่แก้
- **#20** **สิทธิที่ยังใช้ได้ไม่ได้บอกว่าภาษีจะลดจริงเท่าไหร่**
  — `generate_tax_planning_advice` ดูแค่ภาษีวิธีที่ 1 จาก `if compute_tax_from_net(net_income) == 0.0:`
  (`utils/tax_calculator.py` บรรทัด 159 ณ commit ab1f40b) ไม่รู้ `tax_method_2` ใน `handle_deductions`
  ถ้าผู้ใช้เสียตามวิธีที่ 2 ข้อความอาจบอกว่า "อยู่ในขั้นที่ได้รับยกเว้นภาษี" ทั้งที่ต้องเสีย
  หรือแสดงสิทธิที่เหลือทั้งที่ใช้เพิ่มแล้วภาษีไม่ลด — แก้ต้องส่ง `tax_method_2` เข้าฟังก์ชัน (commit แยก)
  — สิทธิที่เหลือแต่ละรายการรวมกันอาจเกินส่วนที่ลดภาษีได้จริง (ใช้ครบแล้วเงินได้สุทธิต่ำกว่าขั้นยกเว้น
  ส่วนที่ต่ำกว่านั้นไม่ลดภาษีเพิ่ม) ระบบแสดงสิทธิตามกฎหมาย ไม่ได้คำนวณแผน — เขียนเป็นข้อจำกัดในเล่ม
- **#21** **ตัวกรองอักษรใน PDF ปล่อยสัญลักษณ์ที่ Unicode จัดเป็นตัวอักษรผ่าน** (ความสำคัญต่ำ)
  `advice_clean = re.sub(...)` ที่เก็บแค่อักษรไทย `\w` ช่องว่าง และเครื่องหมายบางตัว (`utils/pdf_generator.py` บรรทัด 151 ณ commit e7d8d14)
  `\w` ของ Python รับทุกอักขระที่เป็นตัวอักษรหรือตัวเลขใน Unicode เช่น ℹ (U+2139 หมวด `Ll`) จึงไม่ถูกตัด
  ฟอนต์ TH Sarabun อาจไม่มีอักขระเหล่านี้ PDF จะแสดงเป็นสี่เหลี่ยม (emoji หมวด `So` อย่าง 💡 ถูกตัดตามปกติ)
  — ตอนนี้ไม่มีผล เพราะ `tax_advice` มีแต่ข้อความที่เราเขียนเอง (หมายเหตุ SSF จึงไม่ใส่ ℹ️)
  ถ้าวันหน้า `tax_advice` มีข้อความจากภายนอก (เช่น LLM) ต้องแก้
- **#22** **LLM อ้างผิดว่า ThaiESG ลดหย่อนตาม "มาตรา 47"** (พบตอนทดสอบขั้น F ผ่าน LINE)
  ThaiESG เป็นเงินได้ที่ได้รับยกเว้นตามกฎกระทรวง ฉบับที่ 126 ข้อ 2 (105) ไม่ใช่ค่าลดหย่อนตาม ม.47 (docs หัวข้อ 3) ตัวเลขในคำตอบถูก
  — สาเหตุที่น่าจะเป็น: chunk (ฐ) ถูกแปะหัวข้อ `[5. การหักลดหย่อน (มาตรา 47) ปีภาษี 2569 > 5.1 ... > (ฐ) ...]` จากหัวข้อ `# 5. การหักลดหย่อน (มาตรา 47) ปีภาษี 2569`
  ใน `tax_knowledge.txt` แม้เนื้อหา (ฐ) จะเขียนว่า "ไม่ใช่ค่าลดหย่อนตามมาตรา 47" — RMF (ฏ) ก็อยู่ใต้หัวข้อเดียวกันและอาจโดนแบบเดียวกัน
  — ยังไม่แก้

🔒 = มี xfail คุมอยู่ (บั๊กชัดเจน) · 📌 = ล็อกพฤติกรรมปัจจุบันไว้ (ยังไม่ตัดสินใจ) · ไม่มีป้าย = ยังไม่มีเทส
