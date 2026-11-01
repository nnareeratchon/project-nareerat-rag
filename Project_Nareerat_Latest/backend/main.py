import os
from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional

from backend.rag_engine import rag_engine, TOP_K
from backend.database import (
    save_query_search,
    save_evaluation,
    delete_evaluation,
    clear_all_evaluations,
    get_user_precision_metrics,
    get_all_recent_evaluations,
    get_query_feedback
)

app = FastAPI(
    title="IBSL Incident & Problem Analysis RAG System",
    description="ระบบสืบค้นข้อกำหนดและแนวทางแก้ไขปัญหาการนำส่งข้อมูล IBSL ด้วยเทคนิค RAG",
    version="2.0.0"
)

# Enable CORS for local development flexibility
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")

# Pydantic models
class SearchRequest(BaseModel):
    query: str = Field(..., description="คำค้นหาที่เป็นชื่อฟิลด์ รหัสฟิลด์ รหัสคำถาม ข้อความ Error หรือคำถามภาษาธรรมชาติ")
    top_k: Optional[int] = Field(None, description="จำนวนผลลัพธ์สูงสุด (ไม่ระบุ = แสดงทุกฟิลด์ที่เกี่ยวข้อง ไม่จำกัดจำนวน)")

class ExternalRequest(BaseModel):
    query: str = Field(..., description="คำค้นหาเดิมของผู้ใช้")
    item_id: str = Field(..., description="รายการฟิลด์ที่ต้องการคำแนะนำ เช่น FIELD-pol_status")

class ItemEvaluation(BaseModel):
    item_id: str
    relevance: int = Field(..., description="1 = เกี่ยวข้อง, 0 = ไม่เกี่ยวข้อง")

class EvaluationRequest(BaseModel):
    query_id: int
    evaluations: List[ItemEvaluation]
    comment: Optional[str] = None

@app.get("/api/status")
def get_system_status():
    """Get system health, knowledge base stats, and API configuration status."""
    has_gemini = bool(os.getenv("GEMINI_API_KEY", "").strip())
    has_openai = bool(os.getenv("OPENAI_API_KEY", "").strip())
    has_tavily = bool(os.getenv("TAVILY_API_KEY", "").strip())

    total_topics = len(rag_engine.items)
    total_fields = len(rag_engine.fields)
    page_count = rag_engine.page_info.get("page_count", 133)
    chunk_count = len(rag_engine.chunks)

    return {
        "status": "ready" if rag_engine.is_ready else "initializing",
        "doc_name": rag_engine.page_info.get("doc_name", "IBSL-PR-INC_001_V1.0_25690813(1).pdf"),
        "doc_title": rag_engine.page_info.get("doc_title", ""),
        "page_count": page_count,
        "topic_count": total_topics,
        "field_count": total_fields,
        "chunk_count": chunk_count,
        "embedding_method": rag_engine.embedding_label(),
        "llm_service": "Gemini API" if has_gemini else ("OpenAI API" if has_openai else "Internal Extractor (No API Key set)"),
        "llm_configured": has_gemini or has_openai,
        "web_search_service": "Tavily AI Search" if has_tavily else "Not Configured",
        "web_search_configured": has_tavily,
        "knowledge_scope": rag_engine.scope,
        "table_count": rag_engine.table_count,
        "scope": (
            f"เฉพาะข้อมูลกรมธรรม์: {rag_engine.table_count} ตาราง {total_fields} ฟิลด์ "
            f"และเอกสาร FAQ {total_topics} หัวข้อ ใน {rag_engine.page_info.get('category_count', 0)} หมวด "
            f"(หน้า {rag_engine.page_info.get('topic_page_range', '-')})"
            if rag_engine.scope == "policy" else
            f"เอกสาร FAQ ทั้ง {page_count} หน้า รวม {total_topics} หัวข้อ "
            f"ครอบคลุม {rag_engine.page_info.get('category_count', 0)} หมวดข้อมูล"
        ),
    }

@app.post("/api/search")
def search_knowledge(req: SearchRequest):
    """
    Search endpoint:
    1. Search IBSL document topics matching query (top 3 by default)
    2. Re-rank based on earlier user evaluations (Human-in-the-Loop)
    3. Synthesize overall Thai answer from retrieved IBSL topics (5-part format)
    4. Provide external AI agent recommendations (with clear disclaimer)
    5. Save query and displayed top 3 results to SQLite database
    """
    query = req.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="กรุณาระบุคำค้นหา เช่น ชื่อฟิลด์ รหัสคำถาม หรือข้อความ Error")

    # 1. Retrieve matching fields (not artificially capped at 3), re-ranked by user feedback if previously evaluated
    feedback = get_query_feedback(query)
    items = rag_engine.search(query, top_k=req.top_k, feedback=feedback)

    # 2. Overall answer synthesized from retrieved IBSL evidence
    if items:
        answer_text, answer_source = rag_engine.synthesize_answer(query, items)
    else:
        answer_text = "ไม่สามารถค้นพบข้อมูลที่เกี่ยวข้องจากเอกสาร FAQ"
        answer_source = "not_found"

    # 3. External recommendations are per field card: the frontend calls /api/external for each item

    # 4. Save to persistent database (only top displayed items)
    query_id = save_query_search(query, items)

    return {
        "query_id": query_id,
        "query": query,
        "answer": answer_text,
        "answer_source": answer_source,
        "items": items,
        "item_count": len(items),
        "feedback_applied": {
            "promoted": sum(1 for f in items if f.get("user_feedback", 0) > 0),
            "hidden": sum(1 for net in feedback.values() if net < 0)
        },
    }

@app.post("/api/external")
def external_recommendation(req: ExternalRequest):
    """AI Agent recommendation from external credible websites for ONE result field (kept apart from IBSL rules)."""
    field = rag_engine.field_by_id.get(req.item_id)
    if field is None:
        raise HTTPException(status_code=404, detail=f"ไม่พบรายการ {req.item_id}")
    data = rag_engine.external_ai_search(req.query.strip(), field=field)
    return {
        "item_id": req.item_id,
        "status": data.get("status", "unknown"),
        "message": data.get("message", ""),
        "sources": data.get("sources", []),
        "cached": bool(data.get("cached")),
    }

@app.post("/api/evaluate")
def submit_evaluation(req: EvaluationRequest):
    """
    Human-in-the-loop evaluation submission:
    Updates relevance for displayed items and saves optional comment.
    Prevents duplicate records by updating the existing query's items.
    """
    eval_list = [{"item_id": ev.item_id, "relevance": ev.relevance} for ev in req.evaluations]
    save_evaluation(req.query_id, eval_list, req.comment)
    return {
        "success": True,
        "message": "บันทึกผลการประเมินสำเร็จเรียบร้อยแล้ว",
        "query_id": req.query_id
    }

@app.delete("/api/evaluate/{query_id}")
def delete_single_evaluation(query_id: int):
    """Delete a specific user evaluation record."""
    delete_evaluation(query_id)
    return {
        "success": True,
        "message": f"ลบประวัติคำค้นรหัส {query_id} สำเร็จ"
    }

@app.delete("/api/evaluate")
def delete_all_evaluations():
    """Clear all user evaluation records."""
    clear_all_evaluations()
    return {
        "success": True,
        "message": "ลบประวัติการประเมินทั้งหมดสำเร็จ"
    }

@app.get("/api/metrics")
def get_system_metrics():
    """
    Evaluation metrics endpoint:
    1. User Precision@3: only queries fully evaluated by users
    2. Ground Truth Precision@3, Recall@3, F1@3 (macro-averaged against verified Ground Truth)
    """
    user_metrics = get_user_precision_metrics()
    gt_metrics = rag_engine.evaluate_ground_truth()

    return {
        "user_evaluation": user_metrics,
        "ground_truth": gt_metrics
    }

@app.get("/api/fields")
def list_fields():
    """Field catalog: field definitions from the spec for the field catalog view."""
    return rag_engine.list_fields_catalog()

@app.post("/api/reindex")
def reindex_data():
    """Backend re-indexing endpoint to re-build index from IBSL dataset."""
    rag_engine.initialize(force_reload=True)
    return {
        "success": True,
        "message": f"สร้างดัชนีเอกสาร FAQ ใหม่สำเร็จ รวมทั้งหมด {len(rag_engine.items)} หัวข้อ ({len(rag_engine.chunks)} Chunks)",
        "topic_count": len(rag_engine.items),
        "chunk_count": len(rag_engine.chunks)
    }

# Serve static frontend files
if os.path.exists(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @app.get("/")
    def serve_home():
        return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))

    @app.get("/{full_path:path}")
    def serve_frontend_catchall(full_path: str):
        file_path = os.path.join(FRONTEND_DIR, full_path)
        if os.path.exists(file_path) and os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))
