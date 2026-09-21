import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime

class FirebaseManager:
    def __init__(self, key_path="firebase_key.json"):
        self.db = None
        try:
            cred = credentials.Certificate(key_path)
            firebase_admin.initialize_app(cred)
            self.db = firestore.client()
            print("✅ เชื่อมต่อ Firebase Firestore ผ่านโมดูล firebase_db สำเร็จ!")
        except Exception as e:
            print(f"⚠️ Firebase Connection Error: {e}")

    def save_tax_report(self, user_id, report_data):
        """บันทึกข้อมูลภาษีพร้อม URL เอกสาร PDF"""
        if not self.db:
            return False
        try:
            doc_data = {
                "user_id": user_id,
                "timestamp": firestore.SERVER_TIMESTAMP,
                "created_at": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
                "total_income": report_data.get("total_income", 0.0),
                "total_expense": report_data.get("total_expense", 0.0),
                "total_deduction": report_data.get("total_deduction", 0.0),
                "net_income": report_data.get("net_income", 0.0),
                "tax_payable": report_data.get("tax_payable", 0.0),
                "withholding_tax": report_data.get("withholding_tax", 0.0),
                "net_payable": report_data.get("net_payable", 0.0),
                "pdf_file_url": report_data.get("pdf_file_url", "")
            }
            self.db.collection("tax_history").add(doc_data)
            return True
        except Exception as e:
            print(f"❌ Error saving to Firestore: {e}")
            return False

    def get_user_history(self, user_id, limit=3):
        """ดึงประวัติภาษีของผู้ใช้"""
        if not self.db:
            return []
        try:
            docs = self.db.collection("tax_history")\
                          .where("user_id", "==", user_id)\
                          .order_by("timestamp", direction=firestore.Query.DESCENDING)\
                          .limit(limit)\
                          .stream()
            return [doc.to_dict() for doc in docs]
        except Exception as e:
            print(f"❌ Error querying Firestore: {e}")
            return []

firebase_client = FirebaseManager()