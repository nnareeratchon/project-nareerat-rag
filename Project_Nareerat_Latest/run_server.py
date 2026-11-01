import os
import uvicorn
from dotenv import load_dotenv

load_dotenv()

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    host = os.getenv("HOST", "127.0.0.1")
    print(f"==================================================================")
    print(f"🚀 เริ่มต้นระบบ IBSL Policy Fields & FAQs RAG Server")
    print(f"📍 เว็บไซต์พร้อมใช้งานที่: http://{host}:{port}")
    print(f"📖 ขอบเขตข้อมูล: รายการฟิลด์ข้อมูล Policy และ คำถาม-คำตอบ FAQ ที่สอดคล้องกัน")
    print(f"==================================================================")
    uvicorn.run("backend.main:app", host=host, port=port, log_level="info")
