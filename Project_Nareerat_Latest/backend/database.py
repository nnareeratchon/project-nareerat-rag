import sqlite3
import os
import datetime
from typing import List, Dict, Any, Optional

DB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "storage")
# IBSL_DB_PATH lets tests point at a throwaway database instead of the real one
DB_PATH = os.getenv("IBSL_DB_PATH") or os.path.join(DB_DIR, "database.sqlite")

def get_db_connection():
    db_dir = os.path.dirname(DB_PATH)
    os.makedirs(db_dir, exist_ok=True)
    try:
        mode = os.stat(db_dir).st_mode
        if not (mode & 0o200):
            os.chmod(db_dir, mode | 0o700)
    except OSError:
        pass
    conn = sqlite3.connect(DB_PATH, timeout=15.0)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        # 1. Table for queries
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS queries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query_text TEXT NOT NULL,
            search_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            displayed_count INTEGER NOT NULL,
            feedback_comment TEXT,
            feedback_saved_at TIMESTAMP
        )
        """)
        
        # 2. Table for retrieved query items
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS query_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query_id INTEGER NOT NULL,
            item_id TEXT NOT NULL,
            rank_no INTEGER NOT NULL,
            similarity_score REAL NOT NULL,
            item_name TEXT,
            item_content TEXT,
            item_source TEXT,
            user_relevance INTEGER DEFAULT NULL, -- 1 = เกี่ยวข้อง, 0 = ไม่เกี่ยวข้อง, NULL = ยังไม่ประเมิน
            evaluated_at TIMESTAMP,
            FOREIGN KEY (query_id) REFERENCES queries(id) ON DELETE CASCADE,
            UNIQUE (query_id, item_id)
        )
        """)
        
        conn.commit()
    finally:
        conn.close()

def save_query_search(query_text: str, items: List[Dict[str, Any]]) -> int:
    """Save search query and the top returned items into database."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        displayed_count = len(items)
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        cursor.execute("""
            INSERT INTO queries (query_text, search_time, displayed_count)
            VALUES (?, ?, ?)
        """, (query_text, now, displayed_count))
        
        query_id = cursor.lastrowid
        
        for rank, it in enumerate(items, 1):
            cursor.execute("""
                INSERT INTO query_items 
                (query_id, item_id, rank_no, similarity_score, item_name, item_content, item_source)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                query_id,
                it.get("item_id", ""),
                rank,
                float(it.get("similarity_score", 0.0)),
                it.get("name", ""),
                it.get("content", ""),
                it.get("source", "")
            ))
            
        conn.commit()
        return query_id
    finally:
        conn.close()

def save_evaluation(query_id: int, evaluations: List[Dict[str, Any]], comment: Optional[str] = None):
    """
    Save or update human-in-the-loop evaluation without duplicate entries.
    evaluations: [{'item_id': str, 'relevance': 1 or 0}]
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Update comments in queries table
        if comment is not None:
            cursor.execute("""
                UPDATE queries 
                SET feedback_comment = ?, feedback_saved_at = ?
                WHERE id = ?
            """, (comment.strip(), now, query_id))
        else:
            cursor.execute("""
                UPDATE queries 
                SET feedback_saved_at = ?
                WHERE id = ?
            """, (now, query_id))
            
        # Update each item's relevance
        for ev in evaluations:
            item_id = ev.get("item_id")
            relevance = ev.get("relevance")
            if relevance in (0, 1, "0", "1"):
                rel_int = int(relevance)
                cursor.execute("""
                    UPDATE query_items
                    SET user_relevance = ?, evaluated_at = ?
                    WHERE query_id = ? AND item_id = ?
                """, (rel_int, now, query_id, item_id))
                
        conn.commit()
    finally:
        conn.close()

def delete_evaluation(query_id: int) -> bool:
    """Delete a specific query and its evaluation items from database."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM query_items WHERE query_id = ?", (query_id,))
        cursor.execute("DELETE FROM queries WHERE id = ?", (query_id,))
        conn.commit()
        return True
    finally:
        conn.close()

def clear_all_evaluations() -> bool:
    """Clear all query evaluations and reset user evaluation history."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM query_items")
        cursor.execute("DELETE FROM queries")
        conn.commit()
        return True
    finally:
        conn.close()

def get_user_precision_metrics() -> Dict[str, Any]:
    """
    Calculate User Precision@3 from actual user evaluations (Human-in-the-Loop):
    - Only the top 3 displayed items (rank_no <= 3) are considered.
    - Only queries whose top items are ALL evaluated are counted; unevaluated
      items are never treated as irrelevant.
    - Precision@3 = relevant in top 3 / items in top 3 (max 3), per query, then Macro-averaged.
    - Precision (all displayed) = relevant / displayed_count, reported per query only
      when every displayed item has been evaluated (None otherwise).
    - Recall and F1 are NOT computed from user clicks (no full relevant set known);
      use the Ground Truth benchmark for those.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT q.id, q.query_text, q.search_time, q.feedback_comment,
                   COUNT(qi.id) as total_count,
                   SUM(CASE WHEN qi.user_relevance IS NOT NULL THEN 1 ELSE 0 END) as evaluated_count,
                   SUM(CASE WHEN qi.user_relevance = 1 THEN 1 ELSE 0 END) as relevant_count,
                   SUM(CASE WHEN qi.rank_no <= 3 THEN 1 ELSE 0 END) as top_count,
                   SUM(CASE WHEN qi.rank_no <= 3 AND qi.user_relevance IS NOT NULL THEN 1 ELSE 0 END) as top_evaluated_count,
                   SUM(CASE WHEN qi.rank_no <= 3 AND qi.user_relevance = 1 THEN 1 ELSE 0 END) as top_relevant_count
            FROM queries q
            JOIN query_items qi ON q.id = qi.query_id
            GROUP BY q.id
            HAVING top_count > 0 AND top_evaluated_count = top_count
            ORDER BY q.id
        """)
        rows = cursor.fetchall()
    finally:
        conn.close()

    evaluated_queries = []
    precision_scores = []

    for r in rows:
        top_count = r["top_count"]
        top_relevant = r["top_relevant_count"] or 0
        prec = top_relevant / float(top_count)
        precision_scores.append(prec)

        total_count = r["total_count"]
        all_evaluated = r["evaluated_count"] == total_count
        relevant_all = r["relevant_count"] or 0
        prec_all = round(relevant_all / float(total_count), 4) if all_evaluated else None

        evaluated_queries.append({
            "query_id": r["id"],
            "query_text": r["query_text"],
            "search_time": r["search_time"],
            "displayed_count": total_count,
            "evaluated_count": r["evaluated_count"],
            "relevant_count": relevant_all,
            "top_count": top_count,
            "top_relevant_count": top_relevant,
            "precision": round(prec, 4),
            "precision_at_3": round(prec, 4),
            "precision_all": prec_all,
            "feedback_comment": r["feedback_comment"] or ""
        })

    n = len(evaluated_queries)
    avg_precision = round(sum(precision_scores) / n, 4) if n > 0 else None

    return {
        "evaluated_queries_count": n,
        "macro_precision": avg_precision,
        "macro_precision_at_3": avg_precision,
        "precision_at_3": avg_precision,
        "evaluated_queries_list": evaluated_queries
    }

def get_all_recent_evaluations(limit: int = 20) -> List[Dict[str, Any]]:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT q.id, q.query_text, q.search_time, q.displayed_count, q.feedback_comment,
                   GROUP_CONCAT(qi.item_id || ':' || COALESCE(qi.user_relevance, -1)) as items_rel
            FROM queries q
            JOIN query_items qi ON q.id = qi.query_id
            GROUP BY q.id
            ORDER BY q.id DESC
            LIMIT ?
        """, (limit,))
        rows = cursor.fetchall()
    finally:
        conn.close()
    
    results = []
    for r in rows:
        items = []
        if r["items_rel"]:
            for part in r["items_rel"].split(","):
                if ":" in part:
                    it_id, rel = part.split(":", 1)
                    rel_val = int(rel) if rel != "-1" else None
                    items.append({"item_id": it_id, "relevance": rel_val})
        results.append({
            "id": r["id"],
            "query_text": r["query_text"],
            "search_time": r["search_time"],
            "displayed_count": r["displayed_count"],
            "feedback_comment": r["feedback_comment"],
            "items": items
        })
    return results

def normalize_query(query_text: str) -> str:
    """Queries that differ only by case/whitespace share feedback."""
    return " ".join((query_text or "").lower().split())

def get_feedback_by_query() -> Dict[str, Dict[str, Any]]:
    """
    Aggregate user evaluations per normalized query text.
    Each evaluation counts +1 (เกี่ยวข้อง) or -1 (ไม่เกี่ยวข้อง); net > 0 = relevant, net < 0 = not relevant.
    Returns {normalized_query: {"query": latest original text, "items": {item_id: net},
                                "fully_evaluated": True if some search had every displayed item evaluated}}
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT q.id, q.query_text, qi.item_id, qi.user_relevance
            FROM queries q
            JOIN query_items qi ON q.id = qi.query_id
            ORDER BY q.id
        """)
        rows = cursor.fetchall()
    finally:
        conn.close()

    feedback: Dict[str, Dict[str, Any]] = {}
    per_search: Dict[int, List[Optional[int]]] = {}
    search_query: Dict[int, str] = {}
    for r in rows:
        key = normalize_query(r["query_text"])
        entry = feedback.setdefault(key, {"query": r["query_text"], "items": {}, "fully_evaluated": False})
        entry["query"] = r["query_text"]
        per_search.setdefault(r["id"], []).append(r["user_relevance"])
        search_query[r["id"]] = key
        if r["user_relevance"] in (0, 1):
            delta = 1 if r["user_relevance"] == 1 else -1
            entry["items"][r["item_id"]] = entry["items"].get(r["item_id"], 0) + delta

    for search_id, rels in per_search.items():
        if rels and all(v is not None for v in rels):
            feedback[search_query[search_id]]["fully_evaluated"] = True

    return {k: v for k, v in feedback.items() if v["items"]}

def get_query_feedback(query_text: str) -> Dict[str, int]:
    """Net feedback {item_id: net} for one query (empty dict if never evaluated)."""
    entry = get_feedback_by_query().get(normalize_query(query_text))
    return dict(entry["items"]) if entry else {}

init_db()
