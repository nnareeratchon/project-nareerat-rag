import os
from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional

from backend.rag_engine import rag_engine
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
    title="IBSL Policy Fields RAG System",
    description="ระบบสืบค้นรายการฟิลด์ข้อมูลกรมธรรม์ประกันภัย ที่เกี่ยวข้องกัน ด้วยเทคนิค RAG",
    version="1.0.0"
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
    query: str = Field(..., description="คำค้นหาที่เป็นชื่อข้อมูล หรือรายละเอียดข้อมูล")
    top_k: Optional[int] = Field(None, description="จำนวนผลลัพธ์ที่ต้องการ (หากไม่ระบุจะแสดงผลลัพธ์ทั้งหมดที่ตรงเงื่อนไขโดยไม่จำกัด)")

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
    
    total_docs = len(rag_engine.documents)
    field_count = len([d for d in rag_engine.documents if d.get("type") == "Field"])
    faq_count = len([d for d in rag_engine.documents if d.get("type") == "FAQ"])

    return {
        "status": "ready" if rag_engine.is_ready else "initializing",
        "total_documents": total_docs,
        "field_count": field_count,
        "faq_count": faq_count,
        "llm_service": "Gemini API" if has_gemini else ("OpenAI API" if has_openai else "Internal Extractor (No API Key set)"),
        "llm_configured": has_gemini or has_openai,
        "web_search_service": "Tavily AI Search" if has_tavily else "Not Configured",
        "web_search_configured": has_tavily,
        "scope": f"Policy Fields ({field_count} ฟิลด์ ใน 14 โครงสร้างตารางย่อย) และ Policy FAQs ({faq_count} ข้อ)"
    }

@app.post("/api/search")
def search_knowledge(req: SearchRequest):
    """
    Search endpoint:
    1. Search Policy fields matching query by name or desc (ไม่จำกัดแค่ 3 คำตอบ)
    2. Search corresponding Policy FAQs matching those fields
    3. Synthesize individual AI explanations for each field
    4. Synthesize overall Thai answer from combined evidence (fields + FAQs)
    5. Save query & displayed results (fields only) to SQLite database
    FAQs are not shown as a separate list; their answers appear inside each field card (faq_answers).
    """
    query = req.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="กรุณาระบุชื่อข้อมูลหรือรายละเอียดข้อมูลที่ต้องการสืบค้น")

    # 1. 2-stage search, re-ranked by earlier user evaluations of the same query (Human-in-the-Loop).
    # Ground Truth evaluation calls search_ranked directly, so the benchmark is not affected by feedback.
    feedback = get_query_feedback(query)
    structured_res = rag_engine.search_structured(query, top_k=req.top_k, feedback=feedback)
    fields = structured_res.get("fields", [])
    faqs = structured_res.get("faqs", [])

    # 2. Individual AI explanation for each field
    for f in fields:
        f["ai_explanation"] = rag_engine.synthesize_item_explanation(query, f)

    # Only fields are displayed (and evaluated by users); FAQs still serve as evidence for the overall answer
    items = fields
    evidence = fields + faqs

    # 4. Overall answer from retrieved evidence
    if evidence:
        answer_text, answer_source = rag_engine.synthesize_answer(query, evidence)
    else:
        answer_text = (
            "ไม่พบข้อมูลที่ตรงกับคำค้นหาในระบบนำส่งข้อมูลกรมธรรม์ (IBSL Policy)\n"
            "คำค้นหาจะต้องเป็น 'ชื่อข้อมูล' (เช่น pol_id, pol_type, renewal, c_fname) "
            "หรือ 'รายละเอียดข้อมูล' (เช่น เลขที่กรมธรรม์, ประเภทกรมธรรม์, วันที่มีผลคุ้มครอง, เบี้ยประกันภัย) ตามโครงสร้างข้อมูล"
        )
        answer_source = "not_found"

    # 5. External recommendation (with disclaimer)
    external_data = rag_engine.external_ai_search(query)

    # 6. Determine Query Type (ชื่อรายการฟิลด์ vs คำอธิบายข้อมูล)
    import re
    query_type = "คำอธิบายข้อมูล"
    if fields:
        top_field = fields[0]
        fn = top_field.get("field_name", "").lower()
        q_clean = re.sub(r'[\s_]+', '', query.lower())
        fn_clean = re.sub(r'[\s_]+', '', fn)
        if top_field.get("is_name_match") or q_clean in fn_clean or fn_clean in q_clean:
            query_type = "ชื่อรายการฟิลด์"

    # 7. Save to persistent database
    query_id = save_query_search(query, items)

    return {
        "query_id": query_id,
        "query": query,
        "query_type": query_type,
        "answer": answer_text,
        "answer_source": answer_source,
        "fields": fields,
        "fields_count": len(fields),
        "faqs": faqs,
        "faqs_count": len(faqs),
        "items": items,
        "item_count": len(items),
        "feedback_applied": {
            "promoted": sum(1 for f in fields if f.get("user_feedback", 0) > 0),
            "hidden": sum(1 for net in feedback.values() if net < 0)
        },
        "external_recommendation": external_data.get("message", ""),
        "external_sources": external_data.get("sources", []),
        "external_status": external_data.get("status", "unknown")
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
    2. Ground Truth Precision@3, Recall@3, F1@3 (macro-averaged)
    """
    user_metrics = get_user_precision_metrics()
    gt_metrics = rag_engine.evaluate_ground_truth()

    return {
        "user_evaluation": user_metrics,
        "ground_truth": gt_metrics
    }

@app.get("/api/fields")
def list_fields():
    """Field catalog: every field the RAG engine indexes, with table, requirement and related FAQs."""
    return rag_engine.list_fields_catalog()

@app.get("/api/faqs")
def list_faqs():
    """FAQ catalog: every in-scope Policy FAQ the RAG engine indexes."""
    return rag_engine.list_faqs_catalog()

@app.post("/api/reindex")
def reindex_data():
    """Backend re-indexing endpoint to re-parse Excel files without adding UI data management."""
    rag_engine.initialize(force_reload=True)
    return {
        "success": True,
        "message": f"สร้างดัชนีฐานความรู้ใหม่สำเร็จ รวมทั้งหมด {len(rag_engine.documents)} รายการ",
        "total_documents": len(rag_engine.documents)
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
