import os
import csv
import json
import pickle
import openpyxl
import re
from typing import List, Dict, Any, Tuple, Optional
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import pythainlp
from pythainlp.tokenize import word_tokenize
from dotenv import load_dotenv

ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
STORAGE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "storage")
CACHE_FILE = os.path.join(STORAGE_DIR, "index_cache.pkl")
KB_JSON_FILE = os.path.join(STORAGE_DIR, "knowledge_base.json")
GROUND_TRUTH_FILE = os.path.join(DATA_DIR, "ground_truth.json")

# RAG-ready copy built by scripts/build_rag_dataset.py. Used when present unless
# IBSL_DATA_SOURCE=original is set (falls back to parsing data/ directly).
RAG_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data_rag")
RAG_SOURCE_FILES = ("fields.json", "faqs.json", "tables.json")
ORIGINAL_SOURCE_FILES = ("Fields_4.0.csv", "FAQ.xlsx")

load_dotenv(dotenv_path=ENV_FILE)

# FAQ ranking tunables
FAQ_FIELD_RELATIVE_MIN = 0.5   # a field must score >= 50% of the top field to influence FAQ matching
FAQ_DESC_HITS_CAP = 3          # max description-term hits counted per FAQ
FAQ_MIN_SCORE = 12.0           # minimum score for an FAQ with no field link (desc-term hits alone max out at 10.5)

# Result-count tunables: drop the weak tail instead of returning every partial match
FIELD_RELATIVE_MIN = 0.5       # keep fields scoring >= 50% of the top field
FIELD_MAX_RESULTS = 10
FAQ_RELATIVE_MIN = 0.5         # keep FAQs scoring >= 50% of the top FAQ
FAQ_MAX_RESULTS = 5

# Alias tunables (aliases only exist in the data_rag/ dataset)
ALIAS_EXACT_BONUS = 30.0       # query equals a field alias (same weight as an exact description match)
ALIAS_PARTIAL_BONUS = 12.0     # alias contained in the query, or vice versa
WEAK_ALIAS_EXACT_BONUS = 20.0  # query equals the table's alias, on that table's key fields


def use_rag_dataset() -> bool:
    if os.getenv("IBSL_DATA_SOURCE", "").strip().lower() == "original":
        return False
    return all(os.path.exists(os.path.join(RAG_DATA_DIR, n)) for n in RAG_SOURCE_FILES)


def data_signature() -> str:
    """Identifies which source files (and versions) an index was built from, so a stale cache is rebuilt."""
    if use_rag_dataset():
        base, names, kind = RAG_DATA_DIR, RAG_SOURCE_FILES, "rag"
    else:
        base, names, kind = DATA_DIR, ORIGINAL_SOURCE_FILES, "original"
    parts = [kind]
    for n in names:
        p = os.path.join(base, n)
        parts.append(f"{n}:{os.path.getmtime(p):.0f}:{os.path.getsize(p)}" if os.path.exists(p) else f"{n}:missing")
    return "|".join(parts)


def cut_weak_tail(scored: List[Dict[str, Any]], relative_min: float, max_results: int) -> List[Dict[str, Any]]:
    """Keep items (sorted by 'score' desc) that score >= relative_min * top score, up to max_results."""
    if not scored:
        return scored
    threshold = scored[0]["score"] * relative_min
    return [it for it in scored if it["score"] >= threshold][:max_results]

def is_valid_key_format(key: str, min_len: int = 15) -> bool:
    """Check if key looks like an actual API key rather than an empty string or dummy placeholder."""
    if not key or len(key) < min_len:
        return False
    placeholders = ["finder", "fieldfinder", "your_", "api_key", "xxx", "test", "dummy", "placeholder"]
    if any(p in key.lower() for p in placeholders):
        return False
    return True

def get_api_keys() -> Tuple[str, str, str]:
    """Reload .env dynamically so user key additions take effect immediately without restarting."""
    load_dotenv(dotenv_path=ENV_FILE, override=True)
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip() or os.getenv("GOOGLE_API_KEY", "").strip()
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    tavily_key = os.getenv("TAVILY_API_KEY", "").strip()
    return gemini_key, openai_key, tavily_key

def clean_plain_thai_text(text: str) -> str:
    """
    Remove Markdown syntax (asterisks, hashtags, backticks, bullet dashes) and decorative emojis,
    while strictly preserving actual field names (e.g., pol_id), brackets [Loan], [Customer], and codes.
    """
    if not text:
        return ""
    
    # 1. Remove emojis (common emoji ranges)
    emoji_pattern = re.compile(
        "[\U0001F000-\U0001FAFF\U00002700-\U000027BF\U0001F300-\U0001F5FF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF\U00002600-\U000026FF\u2705\u274c\u2728\u26a0\ufe0f]",
        flags=re.UNICODE
    )
    t = emoji_pattern.sub("", text)
    
    # 2. Remove markdown headers (#, ##, ###) at start of lines
    t = re.sub(r"(?m)^[ \t]*#{1,6}[ \t]*", "", t)
    
    # 3. Remove bold/italic asterisks (**, *)
    t = t.replace("**", "").replace("*", "")
    
    # 4. Remove backticks
    t = t.replace("`", "")
    
    # 5. Remove bullet markers (- , + ) at start of lines
    t = re.sub(r"(?m)^[ \t]*[-+]\s+", "", t)
    
    # 6. Clean dangling colons with trailing empty whitespace like 'ความยาว: \n' or 'ความยาว: '
    t = re.sub(r"(?m)(ความยาว|เงื่อนไข|ตัวอย่างข้อมูล|Master Data):\s*$", "", t)
    
    # 7. Collapse multiple empty lines
    t = re.sub(r"\n{3,}", "\n\n", t)
    
    return t.strip()

def thai_tokenize(text: str) -> List[str]:
    """Tokenize Thai and English text combining word tokens and 3-grams for Thai words."""
    if not text:
        return []
    words = word_tokenize(text, engine="newmm", keep_whitespace=False)
    clean_text = re.sub(r"\s+", "", text)
    char_ngrams = [clean_text[i:i+3] for i in range(len(clean_text) - 2)]
    return words + char_ngrams[:40]

class RAGEngine:
    def __init__(self):
        self.documents: List[Dict[str, Any]] = []
        self.fields: List[Dict[str, Any]] = []
        self.faqs: List[Dict[str, Any]] = []
        self.vectorizer: Optional[TfidfVectorizer] = None
        self.tfidf_matrix = None
        self.field_index: Dict[str, int] = {}
        self.is_ready = False
        os.makedirs(STORAGE_DIR, exist_ok=True)
        self.initialize()

    def initialize(self, force_reload: bool = False):
        """Load index from cache if valid, otherwise build index from data files."""
        if not force_reload and os.path.exists(CACHE_FILE) and os.path.exists(KB_JSON_FILE):
            try:
                with open(KB_JSON_FILE, "r", encoding="utf-8") as f:
                    self.documents = json.load(f)
                with open(CACHE_FILE, "rb") as f:
                    data = pickle.load(f)
                    if data.get("data_signature") != data_signature():
                        raise ValueError("data source changed since the index was built")
                    self.vectorizer = data["vectorizer"]
                    self.tfidf_matrix = data["tfidf_matrix"]
                self.fields = [d for d in self.documents if d.get("type") == "Field"]
                self.faqs = [d for d in self.documents if d.get("type") == "FAQ"]
                self._build_field_map()
                self.is_ready = True
                print(f"[RAGEngine] Loaded {len(self.documents)} documents ({len(self.fields)} fields, {len(self.faqs)} FAQs) from persistent cache.")
                return
            except Exception as e:
                print(f"[RAGEngine] Cache load failed ({e}), rebuilding index...")

        self.rebuild_index()

    def _build_field_map(self):
        self.field_index = {}
        for idx, doc in enumerate(self.documents):
            fn = doc.get("field_name")
            if fn:
                self.field_index[fn.lower().strip()] = idx

    def load_raw_data(self) -> List[Dict[str, Any]]:
        """
        Load Policy 4.0 data:
        1. 204 Policy Fields across 14 sub-tables (Fields_4.0.csv / .xlsx). Excludes Payment and Claim.
        2. 33 Policy FAQs from FAQ.xlsx (sheets 'Policy', 'คู่มือการส่งกรมธรรม์', 'อื่น ๆ'). Excludes Payment and Claim.
        """
        if use_rag_dataset():
            return self.load_rag_data()

        fields = []
        faqs = []

        # 1. Parse Policy 4.0 Fields
        csv_path = os.path.join(DATA_DIR, "Fields_4.0.csv")
        if os.path.exists(csv_path):
            with open(csv_path, "r", encoding="utf-8-sig", errors="ignore") as f:
                reader = csv.reader(f)
                current_table = ""
                in_excluded = False
                for r_idx, row in enumerate(reader, start=1):
                    if not row:
                        continue
                    col0 = row[0].strip() if len(row) > 0 else ""
                    if col0 and (len(row) < 5 or not row[4].strip()):
                        table_name = col0
                        if any(ex in table_name for ex in ["Payment", "Claim", "การชำระเบี้ย", "การเคลม", "SIMB2", "Estimate"]):
                            in_excluded = True
                        else:
                            in_excluded = False
                            current_table = table_name
                        continue
                    if in_excluded:
                        continue
                    if len(row) > 4:
                        field_name = row[4].strip()
                        if field_name and field_name not in ["ชื่อข้อมูล", "pol_refer_code_of_company_header"]:
                            req = row[5].strip() if len(row) > 5 else ""
                            dtype = row[6].strip() if len(row) > 6 else ""
                            length = row[7].strip() if len(row) > 7 else ""
                            desc = row[10].strip() if len(row) > 10 else ""
                            example = row[11].strip() if len(row) > 11 else ""
                            master_data = row[12].strip() if len(row) > 12 else ""
                            condition = row[14].strip() if len(row) > 14 else ""

                            item_id = f"FIELD-{field_name}"
                            content_lines = [
                                f"ชื่อฟิลด์: {field_name}",
                                f"ตาราง/โครงสร้าง: {current_table}",
                                f"คำอธิบาย/ความหมาย: {desc}" if desc else "",
                                f"ความจำเป็น: {req}" if req else "",
                                f"ประเภทข้อมูล: {dtype} (ความยาว: {length})" if dtype else "",
                                f"ตัวอย่างข้อมูล: {example}" if example else "",
                                f"Master Data: {master_data}" if master_data else "",
                                f"เงื่อนไขการส่งข้อมูล: {condition}" if condition else ""
                            ]
                            content = "\n".join([line for line in content_lines if line])
                            
                            fields.append({
                                "id": item_id,
                                "item_id": item_id,
                                "name": f"{field_name}" + (f" ({desc})" if desc else ""),
                                "type": "Field",
                                "file": "Fields_4.0.xlsx",
                                "sheet": "Sheet1",
                                "row": r_idx,
                                "table": current_table,
                                "field_name": field_name,
                                "desc": desc,
                                "required": req,
                                "dtype": dtype,
                                "length": length,
                                "example": example,
                                "master_data": master_data,
                                "condition": condition,
                                "content": content,
                                "source": f"Fields_4.0.xlsx [ตาราง: {current_table}, แถวที่: {r_idx}]"
                            })

        # 2. Parse Policy FAQs from FAQ.xlsx (sheets: 'Policy', 'คู่มือการส่งกรมธรรม์', 'อื่น ๆ')
        faq_path = os.path.join(DATA_DIR, "FAQ.xlsx")
        if os.path.exists(faq_path):
            wb = openpyxl.load_workbook(faq_path, data_only=True)
            allowed_sheets = ["Policy", "คู่มือการส่งกรมธรรม์", "อื่น ๆ"]
            for sheet_name in allowed_sheets:
                if sheet_name not in wb.sheetnames:
                    continue
                ws = wb[sheet_name]
                prefix = "FAQ-POL" if sheet_name == "Policy" else ("FAQ-MAN" if "คู่มือ" in sheet_name else "FAQ-GEN")
                for r_idx in range(2, ws.max_row + 1):
                    q = ws.cell(r_idx, 2).value
                    a = ws.cell(r_idx, 3).value
                    if not q or not a:
                        continue
                    q = str(q).strip()
                    a = str(a).strip()
                    if not q or not a:
                        continue

                    item_id = f"{prefix}-{r_idx-1:02d}"
                    content = f"คำถาม (FAQ): {q}\nคำตอบ/แนวทางปฏิบัติ: {a}"

                    faqs.append({
                        "id": item_id,
                        "item_id": item_id,
                        "name": q[:80] + ("..." if len(q) > 80 else ""),
                        "type": "FAQ",
                        "file": "FAQ.xlsx",
                        "sheet": sheet_name,
                        "row": r_idx,
                        "table": f"FAQ ({sheet_name})",
                        "field_name": "",
                        "desc": q,
                        "question": q,
                        "answer": a,
                        "content": content,
                        "source": f"FAQ.xlsx [ชีต: {sheet_name}, แถวที่: {r_idx}]"
                    })

        self.fields = fields
        self.faqs = faqs
        return fields + faqs

    def load_rag_data(self) -> List[Dict[str, Any]]:
        """
        Load the RAG-ready copy in data_rag/ (built by scripts/build_rag_dataset.py) into the
        same document schema as load_raw_data, plus:
        - aliases:      field aliases; a container field (e.g. insureds) also gets its child table's aliases
        - weak_aliases: table aliases on the table's key fields (_id/_seq), for broad entity queries
        Out-of-scope FAQs are skipped; FAQ notes (หมายเหตุ) are appended to the answer.
        """
        def read(name):
            with open(os.path.join(RAG_DATA_DIR, name), "r", encoding="utf-8") as f:
                return json.load(f)

        tables = {t["key"]: t for t in read("tables.json")}
        corpus_text = {}
        corpus_path = os.path.join(RAG_DATA_DIR, "corpus.jsonl")
        if os.path.exists(corpus_path):
            with open(corpus_path, "r", encoding="utf-8") as f:
                for line in f:
                    d = json.loads(line)
                    if d["type"] == "Field":
                        corpus_text[d["item_id"]] = d["text"]

        fields = []
        for rf in read("fields.json"):
            t = tables[rf["table_key"]]
            table_label = t.get("raw_name") or f"{t['name_th']} ({t['name_en']})"
            item_id = f"FIELD-{rf['field_name']}"
            aliases = list(rf.get("aliases", []))
            if rf.get("child_table_key"):
                aliases += tables[rf["child_table_key"]].get("aliases", [])
            weak_aliases = t.get("aliases", []) if rf["field_name"] in t.get("key_fields", []) else []
            fields.append({
                "id": item_id,
                "item_id": item_id,
                "name": rf["field_name"] + (f" ({rf['desc']})" if rf["desc"] else ""),
                "type": "Field",
                "file": "Fields_4.0.csv",
                "sheet": "Sheet1",
                "row": rf["source_row"],
                "table": table_label,
                "field_name": rf["field_name"],
                "desc": rf["desc"],
                "desc_contextual": rf.get("desc_contextual", rf["desc"]),
                "required": rf["required"],
                "dtype": rf["dtype"],
                "length": rf["length"],
                "example": rf["example"],
                "master_data": rf["master_data"],
                "sensitive": rf.get("sensitive", ""),
                "condition": rf["condition"],
                "aliases": list(dict.fromkeys(aliases)),
                "weak_aliases": weak_aliases,
                "keywords_th": rf.get("keywords_th", []),
                "content": corpus_text.get(item_id, rf["desc"]),
                "source": f"Fields_4.0.xlsx [ตาราง: {table_label}, แถวที่: {rf['source_row']}]"
            })

        faqs = []
        for rq in read("faqs.json"):
            if not rq.get("in_scope", True):
                continue
            answer = rq["answer"] + (f"\nหมายเหตุ: {rq['note']}" if rq.get("note") else "")
            faqs.append({
                "id": rq["item_id"],
                "item_id": rq["item_id"],
                "name": rq["question"][:80] + ("..." if len(rq["question"]) > 80 else ""),
                "type": "FAQ",
                "file": "FAQ.xlsx",
                "sheet": rq["sheet"],
                "row": rq["source_row"],
                "table": f"FAQ ({rq['sheet']})",
                "field_name": "",
                "desc": rq["question"],
                "question": rq["question"],
                "answer": answer,
                "category": rq.get("category", ""),
                "linked_fields": rq.get("linked_fields", []),
                "content": f"คำถาม (FAQ): {rq['question']}\nคำตอบ/แนวทางปฏิบัติ: {answer}",
                "source": f"FAQ.xlsx [ชีต: {rq['sheet']}, แถวที่: {rq['source_row']}]"
            })

        self.fields = fields
        self.faqs = faqs
        return fields + faqs

    def rebuild_index(self):
        """Re-index Policy fields and FAQs into persistent TF-IDF index."""
        print("[RAGEngine] Parsing Policy field and FAQ data files...")
        self.documents = self.load_raw_data()
        
        corpus = []
        for d in self.documents:
            fn = d.get("field_name", "")
            desc = d.get("desc", "")
            tbl = d.get("table", "")
            cond = d.get("condition", "")
            q = d.get("question", "")
            a = d.get("answer", "")
            content = d.get("content", "")
            corpus_text = f"{fn} {desc} {tbl} {cond} {q} {a} {content}"
            corpus.append(corpus_text)

        print("[RAGEngine] Creating TF-IDF and character n-gram matrix...")
        self.vectorizer = TfidfVectorizer(
            tokenizer=thai_tokenize,
            token_pattern=None,
            min_df=1,
            max_df=0.95,
            sublinear_tf=True
        )
        self.tfidf_matrix = self.vectorizer.fit_transform(corpus)
        self._build_field_map()

        with open(KB_JSON_FILE, "w", encoding="utf-8") as f:
            json.dump(self.documents, f, ensure_ascii=False, indent=2)

        with open(CACHE_FILE, "wb") as f:
            pickle.dump({
                "vectorizer": self.vectorizer,
                "tfidf_matrix": self.tfidf_matrix,
                "data_signature": data_signature()
            }, f)

        self.is_ready = True
        print(f"[RAGEngine] Indexing complete! Indexed {len(self.documents)} items ({len(self.fields)} fields, {len(self.faqs)} FAQs).")

    def search_fields(self, query: str, top_k: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Search Policy 4.0 fields (204 fields) by 'ชื่อข้อมูล' (Field Name, Table)
        or 'รายละเอียดข้อมูล' (Description / Meaning, Conditions).
        - If top_k is None, returns ALL relevant matching fields (ไม่จำกัดแค่ 3 คำตอบ).
        """
        if not self.is_ready or not self.fields:
            return []

        query = query.strip()
        if not query:
            return []

        query_lower = query.lower()
        norm_query = re.sub(r'[\s_]+', '', query_lower)
        clean_th_query = re.sub(r'[\s_ของที่ในเริ่มมีข้อมูลรหัสอะไรส่ง]+', '', query_lower)
        
        q_tokens = [t.lower().strip() for t in word_tokenize(query_lower, engine="newmm", keep_whitespace=False) 
                    if len(t.strip()) > 1 and t not in ["_", "-", "ที่", "ของ", "ใน", "การ", "มี", "ให้", "เป็น", "และ", "หรือ", "จะ", "ได้"]]

        q_vec = self.vectorizer.transform([query])
        sims = cosine_similarity(q_vec, self.tfidf_matrix)[0]

        scored_items = []
        seen_ids = set()

        for idx, doc in enumerate(self.documents):
            if doc.get("type") != "Field":
                continue

            raw_sim = float(sims[idx])
            score = raw_sim * 4.0
            is_name_match = False
            is_detail_match = False

            # 1. MATCH ON 'ชื่อข้อมูล' (Field Name, Table / Structure Name)
            fn = doc.get("field_name", "").lower().strip()
            norm_fn = re.sub(r'[\s_]+', '', fn)
            if fn:
                if fn == query_lower or norm_fn == norm_query:
                    score += 35.0
                    is_name_match = True
                elif fn.startswith(query_lower) or norm_fn.startswith(norm_query):
                    score += 18.0
                    is_name_match = True
                elif query_lower in fn or norm_query in norm_fn:
                    score += 12.0
                    is_name_match = True
                elif len(norm_fn) >= 4 and norm_fn in norm_query:
                    score += 10.0
                    is_name_match = True
                else:
                    for tok in q_tokens:
                        if len(tok) >= 3 and (tok == fn or tok in fn):
                            score += 4.0
                            is_name_match = True

            tbl = doc.get("table", "").lower().strip()
            norm_tbl = re.sub(r'[\s_]+', '', tbl)
            if tbl:
                if norm_query in norm_tbl or query_lower in tbl:
                    score += 10.0
                    is_name_match = True
                else:
                    for tok in q_tokens:
                        if len(tok) >= 3 and tok in tbl:
                            score += 2.5
                            is_name_match = True

            # 2. MATCH ON 'รายละเอียดข้อมูล' (Field Description / Meaning, Conditions)
            desc = doc.get("desc", "").lower().strip()
            norm_desc = re.sub(r'[\s_]+', '', desc)
            clean_th_desc = re.sub(r'[\s_ของที่ในเริ่มมีข้อมูลรหัสอะไรส่ง]+', '', desc)
            if desc:
                if norm_desc == norm_query or desc == query_lower:
                    score += 30.0
                    is_detail_match = True
                elif clean_th_query and clean_th_desc == clean_th_query:
                    score += 25.0
                    is_detail_match = True
                elif norm_query in norm_desc or query_lower in desc:
                    score += 14.0
                    is_detail_match = True
                elif clean_th_query and len(clean_th_query) >= 3 and clean_th_query in clean_th_desc:
                    score += 10.0
                    is_detail_match = True
                elif len(norm_desc) >= 3 and norm_desc in norm_query:
                    score += 6.0
                    is_detail_match = True
                else:
                    for tok in q_tokens:
                        if len(tok) >= 2 and tok in desc:
                            score += 2.5
                            is_detail_match = True

            cond = doc.get("condition", "").lower().strip()
            if cond:
                norm_cond = re.sub(r'[\s_]+', '', cond)
                if norm_query in norm_cond or query_lower in cond:
                    score += 6.0
                    is_detail_match = True
                else:
                    for tok in q_tokens:
                        if len(tok) >= 3 and tok in cond:
                            score += 1.5
                            is_detail_match = True

            # 3. MATCH ON aliases from data_rag/ (wording users actually type, e.g. "ผู้เอาประกัน" -> insureds)
            alias_bonus = 0.0
            for alias in doc.get("aliases", []):
                norm_alias = re.sub(r'[\s_]+', '', alias.lower())
                if norm_alias == norm_query:
                    alias_bonus = max(alias_bonus, ALIAS_EXACT_BONUS)
                elif len(norm_alias) >= 3 and (norm_alias in norm_query or norm_query in norm_alias):
                    alias_bonus = max(alias_bonus, ALIAS_PARTIAL_BONUS)
            for alias in doc.get("weak_aliases", []):
                if re.sub(r'[\s_]+', '', alias.lower()) == norm_query:
                    alias_bonus = max(alias_bonus, WEAK_ALIAS_EXACT_BONUS)
            if alias_bonus:
                score += alias_bonus
                is_detail_match = True

            if is_name_match or is_detail_match or raw_sim >= 0.20:
                pct = min(100.0, max(25.0, round(min(100.0, score * 3.5), 1)))
                scored_items.append({
                    "score": score,
                    "raw_sim": raw_sim,
                    "similarity_score": pct,
                    "is_name_match": is_name_match,
                    "is_detail_match": is_detail_match,
                    "doc": doc
                })

        scored_items.sort(key=lambda x: x["score"], reverse=True)
        scored_items = cut_weak_tail(scored_items, FIELD_RELATIVE_MIN, FIELD_MAX_RESULTS)
        results = []
        for item in scored_items:
            doc_id = item["doc"]["id"]
            if doc_id in seen_ids:
                continue
            seen_ids.add(doc_id)
            result_entry = dict(item["doc"])
            result_entry["similarity_score"] = item["similarity_score"]
            result_entry["raw_score"] = round(item["score"], 4)
            result_entry["is_name_match"] = item["is_name_match"]
            result_entry["is_detail_match"] = item["is_detail_match"]
            results.append(result_entry)
            if top_k is not None and len(results) >= top_k:
                break
        return results

    def search_faqs_for_fields(self, query: str, matched_fields: List[Dict[str, Any]], top_k: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Find Policy FAQs that correspond to the matched fields or search query.
        - Checks if FAQ question or answer mentions the matched field_name (e.g. pol_status, pol_license, sub_policies).
        - Checks if FAQ question or answer mentions the field description or key query terms.
        - If top_k is None, returns ALL corresponding FAQs (ไม่จำกัดแค่ 3 คำตอบ).
        """
        if not self.is_ready or not self.faqs:
            return []

        query = query.strip()
        query_lower = query.lower()
        norm_query = re.sub(r'[\s_]+', '', query_lower)
        clean_th_query = re.sub(r'[\s_ของที่ในเริ่มมีข้อมูลรหัสอะไรส่ง]+', '', query_lower)

        # Collect field names and meaningful terms from STRONG matched fields only.
        # Weak tail matches (e.g. pol_status for "pol_start_date") used to pull in
        # unrelated FAQs, so each field's bonus is weighted by its score relative to the top field.
        top_field_score = matched_fields[0].get("raw_score", 0.0) if matched_fields else 0.0
        strong_fields = [
            f for f in matched_fields[:8]
            if top_field_score > 0 and f.get("raw_score", 0.0) >= FAQ_FIELD_RELATIVE_MIN * top_field_score
        ]
        field_weights = {}
        field_terms = []
        desc_terms = []
        for f in strong_fields:
            fn = f.get("field_name", "").strip()
            if fn:
                field_terms.append(fn)
                field_weights.setdefault(fn, f.get("raw_score", 0.0) / top_field_score)
            desc = f.get("desc", "").strip()
            if desc:
                for w in word_tokenize(desc, engine="newmm", keep_whitespace=False):
                    if len(w) >= 3 and w not in ["การ", "ของ", "ที่", "และ", "หรือ", "เพื่อ", "ข้อมูล", "รหัส"]:
                        desc_terms.append(w)

        field_terms = list(dict.fromkeys(field_terms))
        desc_terms = list(dict.fromkeys(desc_terms))

        q_vec = self.vectorizer.transform([query])
        sims = cosine_similarity(q_vec, self.tfidf_matrix)[0]

        scored_faqs = []
        seen_ids = set()

        for idx, doc in enumerate(self.documents):
            if doc.get("type") != "FAQ":
                continue

            raw_sim = float(sims[idx])
            q_text = doc.get("question", "")
            a_text = doc.get("answer", "")
            qa_combined = (q_text + " " + a_text).lower()

            matched_field_names = []
            score = raw_sim * 4.0

            # 1. Check if FAQ explicitly mentions matched field names
            for fn in field_terms:
                fn_lower = fn.lower()
                if re.search(r'\b' + re.escape(fn_lower) + r'\b', qa_combined) or fn_lower in qa_combined:
                    score += 26.0 * field_weights.get(fn, 1.0)
                    matched_field_names.append(fn)

            # 2. Check direct query match in FAQ question/answer
            if norm_query in re.sub(r'[\s_]+', '', qa_combined):
                score += 20.0
            elif clean_th_query and len(clean_th_query) >= 3 and clean_th_query in re.sub(r'[\s_]+', '', qa_combined):
                score += 15.0

            # 3. Check description terms overlap (capped so long FAQs don't win on length alone)
            desc_hits = sum(1 for dt in desc_terms if dt.lower() in qa_combined)
            score += 3.5 * min(desc_hits, FAQ_DESC_HITS_CAP)

            if matched_field_names or score >= FAQ_MIN_SCORE or raw_sim >= 0.22:
                pct = min(100.0, max(25.0, round(min(100.0, score * 3.5), 1)))
                scored_faqs.append({
                    "score": score,
                    "raw_sim": raw_sim,
                    "similarity_score": pct,
                    "matched_field_names": list(dict.fromkeys(matched_field_names)),
                    "doc": doc
                })

        scored_faqs.sort(key=lambda x: x["score"], reverse=True)
        scored_faqs = cut_weak_tail(scored_faqs, FAQ_RELATIVE_MIN, FAQ_MAX_RESULTS)
        results = []
        for item in scored_faqs:
            doc_id = item["doc"]["id"]
            if doc_id in seen_ids:
                continue
            seen_ids.add(doc_id)
            result_entry = dict(item["doc"])
            result_entry["similarity_score"] = item["similarity_score"]
            result_entry["raw_score"] = round(item["score"], 4)
            result_entry["matched_field_names"] = item["matched_field_names"]
            if item["matched_field_names"]:
                result_entry["matched_field_str"] = ", ".join(item["matched_field_names"])
            else:
                result_entry["matched_field_str"] = query
            results.append(result_entry)
            if top_k is not None and len(results) >= top_k:
                break
        return results

    def search_structured(self, query: str, top_k: Optional[int] = None,
                          feedback: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
        """
        2-stage search pipeline:
        Stage 1: Match Policy fields by field name or description.
        Stage 2: From matched field names, find corresponding Policy FAQ answers.
        Directly attaches corresponding FAQ answers into each field object.
        Returns fields first, followed by FAQs.
        feedback: {item_id: net user evaluation} for this query (see apply_user_feedback).
        """
        fields = self.search_fields(query, top_k=top_k)
        if feedback:
            fields = self.apply_user_feedback(fields, feedback)
        faqs = self.search_faqs_for_fields(query, fields, top_k=top_k)

        # Attach corresponding FAQ answers directly into each matched field object
        for f in fields:
            fn = f.get("field_name", "").strip().lower()
            desc = f.get("desc", "").strip().lower()
            field_faqs = []

            for faq in self.faqs:
                q_text = faq.get("question", "")
                a_text = faq.get("answer", "")
                qa_text = (q_text + " " + a_text).lower()

                # Match by field name (e.g. pol_status) or strong description match
                is_match = False
                if fn and (re.search(r'\b' + re.escape(fn) + r'\b', qa_text) or fn in qa_text):
                    is_match = True
                elif desc and len(desc) >= 5 and desc in qa_text:
                    is_match = True

                if is_match:
                    field_faqs.append({
                        "item_id": faq.get("item_id"),
                        "question": q_text,
                        "answer": a_text,
                        "sheet": faq.get("sheet"),
                        "source": faq.get("source")
                    })

            f["faq_answers"] = field_faqs
            f["faq_count"] = len(field_faqs)

        return {
            "fields": fields,
            "faqs": faqs,
            "items": fields + faqs
        }

    def search(self, query: str, top_k: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Search returning combined items with Policy Fields first, then corresponding FAQs.
        """
        structured = self.search_structured(query, top_k=top_k)
        return structured["items"]

    def list_fields_catalog(self) -> Dict[str, Any]:
        """
        Every indexed field (the knowledge base itself), grouped by table, for the field-list page.
        Related FAQs = FAQs whose question/answer mention the field name as a whole word.
        """
        required_labels = {"Yes": "จำเป็น", "No": "ไม่บังคับ", "Yes(C)": "ตามเงื่อนไข"}
        faq_texts = [(q, (q.get("question", "") + "\n" + q.get("answer", "")).lower()) for q in self.faqs]

        tables: Dict[str, Dict[str, Any]] = {}
        fields = []
        for f in self.fields:
            table = f.get("table", "")
            tables.setdefault(table, {"table": table, "field_count": 0})["field_count"] += 1
            fn = f.get("field_name", "")
            pattern = re.compile(r"(?<![a-z0-9_])" + re.escape(fn.lower()) + r"(?![a-z0-9_])")
            related = [{"item_id": q["item_id"], "question": q.get("question", "")}
                       for q, text in faq_texts if fn and pattern.search(text)]
            fields.append({
                "item_id": f["item_id"],
                "field_name": fn,
                "table": table,
                "desc": f.get("desc", ""),
                "required": f.get("required", ""),
                "required_label": required_labels.get(f.get("required", ""), f.get("required", "")),
                "dtype": f.get("dtype", ""),
                "length": f.get("length", ""),
                "example": f.get("example", ""),
                "master_data": f.get("master_data", ""),
                "sensitive": f.get("sensitive", ""),
                "condition": f.get("condition", ""),
                "aliases": f.get("aliases", []),
                "related_faqs": related,
                "source": f.get("source", ""),
            })
        return {"tables": list(tables.values()), "fields": fields, "total": len(fields)}

    def list_faqs_catalog(self) -> Dict[str, Any]:
        """Every in-scope Policy FAQ in the knowledge base, with id, question, answer, sheet and source."""
        faqs = [
            {
                "item_id": q.get("item_id", ""),
                "question": q.get("question", ""),
                "answer": q.get("answer", ""),
                "sheet": q.get("sheet", ""),
                "source": q.get("source", ""),
            }
            for q in self.faqs
        ]
        return {"faqs": faqs, "total": len(faqs)}

    def apply_user_feedback(self, fields: List[Dict[str, Any]], feedback: Dict[str, int]) -> List[Dict[str, Any]]:
        """
        Human-in-the-Loop re-ranking for a query that users have evaluated before:
        - net feedback < 0 (ไม่เกี่ยวข้อง): removed from the results
        - net feedback > 0 (เกี่ยวข้อง): moved to the top (more votes first); re-added if the search had cut it off
        - net 0 / never evaluated: keeps its original order below the confirmed items
        Each returned field carries "user_feedback" (net votes) or 0.
        """
        doc_by_id = {d["item_id"]: d for d in self.fields}
        result_ids = {f["item_id"] for f in fields}

        kept = []
        for f in fields:
            net = feedback.get(f["item_id"], 0)
            if net < 0:
                continue
            kept.append(dict(f, user_feedback=net))

        for item_id, net in feedback.items():
            if net > 0 and item_id not in result_ids and item_id in doc_by_id:
                kept.append(dict(doc_by_id[item_id], similarity_score=0.0, raw_score=0.0,
                                 is_name_match=False, is_detail_match=False, user_feedback=net))

        confirmed = sorted((f for f in kept if f["user_feedback"] > 0), key=lambda f: -f["user_feedback"])
        rest = [f for f in kept if f["user_feedback"] <= 0]
        return confirmed + rest

    def search_ranked(self, query: str, k: int = 3) -> List[Dict[str, Any]]:
        """
        Return the true top-k across Fields AND FAQs, ranked together by raw_score.
        Used for Precision@k / Recall@k so k really means k items (not k fields + k FAQs).
        """
        fields = self.search_fields(query)
        faqs = self.search_faqs_for_fields(query, fields)

        # FAQ and Field scores are on different scales (FAQs stack bonuses), so an FAQ
        # is capped just below the field it was matched through: a field ranks ahead of its own FAQs.
        field_scores = {f.get("field_name", ""): f.get("raw_score", 0.0) for f in fields}
        top_field_score = fields[0].get("raw_score", 0.0) if fields else None

        def rank_score(it: Dict[str, Any]) -> float:
            s = it.get("raw_score", 0.0)
            if it.get("type") != "FAQ":
                return s
            via = [field_scores[fn] for fn in it.get("matched_field_names", []) if fn in field_scores]
            cap = max(via) if via else top_field_score
            return min(s, cap * 0.99) if cap is not None else s

        merged = sorted(fields + faqs, key=rank_score, reverse=True)
        return merged[:k]

    def synthesize_item_explanation(self, query: str, item: Dict[str, Any]) -> str:
        """
        Generate targeted, natural Thai prose AI explanation for a single retrieved item:
        - If Field: explains field meaning, data type, requirement, condition, and includes FAQ answers.
        - If FAQ: explains the FAQ answer in relation to the query/field, summarizing mapping codes and guidelines.
        - No Markdown (no **, ###, backticks)
        - No emojis
        - Preserves technical identifiers (pol_status, 0100, 0300)
        - Cites source
        """
        item_type = item.get("type", "Field")
        source = item.get("source", "").strip()

        if item_type == "Field":
            fn = item.get("field_name", "")
            desc = item.get("desc", "").strip()
            tbl = item.get("table", "").strip()
            dtype = item.get("dtype", "").strip()
            req = item.get("required", "").strip()
            length = item.get("length", "").strip()
            example = item.get("example", "").strip()
            cond = item.get("condition", "").strip()
            master = item.get("master_data", "").strip()
            faq_answers = item.get("faq_answers", [])

            prose_parts = []
            if desc:
                prose_parts.append(f"ฟิลด์ {fn} ในโครงสร้างตาราง {tbl} มีความหมายคือ {desc}")
            else:
                prose_parts.append(f"ฟิลด์ {fn} อยู่ในโครงสร้างตาราง {tbl}")

            type_info = []
            if dtype:
                if length:
                    type_info.append(f"กำหนดประเภทข้อมูลเป็น {dtype} ความยาว {length}")
                else:
                    type_info.append(f"กำหนดประเภทข้อมูลเป็น {dtype}")
            if req:
                type_info.append(f"มีสถานะความจำเป็นในการนำส่งคือ {req}")
            if type_info:
                prose_parts.append(" และ ".join(type_info))

            if cond:
                prose_parts.append(f"มีเงื่อนไขและข้อกำหนดคือ {cond}")
            if example:
                clean_ex = example.replace('"""', '').replace('"', '')
                prose_parts.append(f"ตัวอย่างค่าข้อมูลคือ {clean_ex}")
            if master:
                prose_parts.append(f"อ้างอิงรหัส Master Data: {master}")

            # Include answer from corresponding FAQ directly if available
            if faq_answers:
                top_faq = faq_answers[0]
                clean_faq_a = re.sub(r'\s*\n+\s*', ' ', top_faq.get("answer", "")).strip()
                prose_parts.append(f"คำตอบและแนวทางปฏิบัติเพิ่มเติมตามข้อมูล FAQ (คำถาม: {top_faq.get('question')}): {clean_faq_a}")

            main_text = " ".join(prose_parts)
            if source:
                main_text += f" แหล่งอ้างอิง: {source}"
            return clean_plain_thai_text(main_text)

        elif item_type == "FAQ":
            q = item.get("question", "").strip()
            a = item.get("answer", "").strip()
            matched_fn = item.get("matched_field_str", "")

            clean_a = re.sub(r'\s*\n+\s*', ' ', a).strip()
            prose_parts = []
            if matched_fn:
                prose_parts.append(f"สำหรับข้อกำหนดและแนวทางปฏิบัติที่เกี่ยวข้องกับฟิลด์ {matched_fn} ตามคำถาม {q}")
            else:
                prose_parts.append(f"สำหรับแนวทางปฏิบัติตามคำถาม {q}")

            prose_parts.append(f"สำนักงาน คปภ. ได้กำหนดแนวทางคือ {clean_a}")
            main_text = " ".join(prose_parts)
            if source:
                main_text += f" แหล่งอ้างอิง: {source}"
            return clean_plain_thai_text(main_text)

        return ""

    def synthesize_answer(self, query: str, retrieved_items: List[Dict[str, Any]]) -> Tuple[str, str]:
        """
        Synthesize Thai answer based strictly on retrieved internal knowledge (Fields + FAQs).
        Adheres to:
        - ข้อความภาษาไทยที่อ่านง่าย ไม่แสดง Markdown อีโมจิ หรือสัญลักษณ์ตกแต่ง (ไม่มี **, ###, backtick, ขีดหัวข้อ)
        - ต้องคงอักขระที่เป็นส่วนหนึ่งของชื่อฟิลด์และข้อมูลจริง เช่น pol_id, pol_status, [Loan]
        - สรุปความหมายของฟิลด์ก่อน แล้วอธิบายประเภทข้อมูล ความจำเป็นในการนำส่ง และเงื่อนไขที่เกี่ยวข้อง จากนั้นอธิบายแนวทางปฏิบัติจาก FAQ ที่สอดคล้องกัน
        - หากข้อมูลส่วนใดว่างให้ละส่วนนั้น ไม่แสดงข้อความค้าง เช่น ความยาว:
        - แสดงแหล่งอ้างอิงไว้ท้ายคำตอบ
        - เชื่อมต่อ LLM จริง พร้อมจัดการข้อผิดพลาด หากไม่สำเร็จให้ fallback ข้อมูลต้นฉบับอย่างสมบูรณ์โดยไม่แสดงรหัส error เชิงเทคนิค
        """
        if not retrieved_items:
            return (
                "ไม่พบข้อมูลที่เกี่ยวข้องในฐานความรู้การนำส่งข้อมูลกรมธรรม์ (IBSL Policy) กรุณาตรวจสอบคำค้นหาหรือชื่อฟิลด์อีกครั้ง",
                "not_found"
            )

        gemini_api_key, openai_api_key, _ = get_api_keys()

        # Separate fields and FAQs for structured context prompt
        field_items = [it for it in retrieved_items if it.get("type") == "Field"]
        faq_items = [it for it in retrieved_items if it.get("type") == "FAQ"]

        evidence_blocks = []
        for it in field_items[:5]:
            evidence_blocks.append(f"[{it['item_id']}] แหล่งข้อมูล: {it['source']}\n{it['content']}")
        for it in faq_items[:4]:
            evidence_blocks.append(f"[{it['item_id']}] แหล่งข้อมูล: {it['source']}\n{it['content']}")

        context_str = "\n\n---\n\n".join(evidence_blocks)

        system_instruction = (
            "คุณคือผู้เชี่ยวชาญระบบนำส่งข้อมูลกรมธรรม์ประกันภัย (IBSL Policy) ของสำนักงาน คปภ.\n"
            "หน้าที่ของคุณคือเรียบเรียงคำตอบจากหลักฐานข้อมูลภายในที่ค้นคืนได้จริง (ข้อมูลฟิลด์และแนวทางปฏิบัติจาก FAQ) เพื่อตอบคำถามของผู้ใช้\n\n"
            "ข้อบังคับสำคัญที่สุดด้านรูปแบบข้อความ:\n"
            "1. ห้ามใช้สัญลักษณ์ Markdown ทุกชนิดเด็ดขาด เช่น ห้ามใช้เครื่องหมายดอกจัน **, *, ห้ามใช้เครื่องหมายหัวข้อ ###, ##, #, ห้ามใช้ bullet list เช่น ขีด - หรือเลขนำ, และห้ามใช้เครื่องหมาย backtick `\n"
            "2. ห้ามใช้อีโมจิหรือสัญลักษณ์ตกแต่งใดๆ ทั้งสิ้น\n"
            "3. ต้องคงอักขระที่เป็นส่วนหนึ่งของชื่อฟิลด์ รหัส และชื่อโครงสร้างจริง เช่น pol_id, pol_status, [Loan], [Customer], 0100, 0300\n"
            "4. นำเสนอเป็นข้อความภาษาไทยในรูปแบบย่อหน้าที่ต่อเนื่องกัน อ่านง่าย สละสลวย โดยสรุปความหมายของฟิลด์ก่อน อธิบายประเภทข้อมูล ความจำเป็นในการนำส่ง และเงื่อนไขที่เกี่ยวข้อง จากนั้นอธิบายแนวทางปฏิบัติและข้อยกเว้นจากคำถามคำตอบ FAQ ที่สอดคล้องกันเป็นลำดับถัดไป\n"
            "5. ห้ามแต่งเงื่อนไขเพิ่มเติมหรือเปลี่ยนความหมายจากต้นฉบับเด็ดขาด\n"
            "6. หากข้อมูลส่วนใดว่างหรือไม่มีข้อมูล ให้ละส่วนนั้นไปทันที ไม่แสดงข้อความค้าง เช่น ห้ามแสดงคำว่า ความยาว: ค้างไว้\n"
            "7. แสดงแหล่งอ้างอิงไว้ท้ายสุดของคำตอบในรูปแบบ: แหล่งอ้างอิง: [รหัสเอกสาร] ชื่อไฟล์ ชีต/ตาราง และแถวที่เกี่ยวข้อง"
        )

        user_prompt = f"คำถาม: {query}\n\nหลักฐานข้อมูลจากระบบ IBSL (Policy Fields & FAQs):\n{context_str}\n\nกรุณาเรียบเรียงคำตอบเป็นภาษาไทยแบบย่อหน้าต่อเนื่องตามข้อบังคับ:"

        # 1. Try Gemini API
        if gemini_api_key and is_valid_key_format(gemini_api_key):
            candidate_models = []
            env_model = os.getenv("GEMINI_MODEL", "").strip()
            if env_model:
                candidate_models.append(env_model)
            for m in ["gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-flash-latest"]:
                if m not in candidate_models:
                    candidate_models.append(m)

            try:
                from google import genai
                client = genai.Client(api_key=gemini_api_key, http_options={"timeout": 15000})

                for model_name in candidate_models:
                    try:
                        response = client.models.generate_content(
                            model=model_name,
                            contents=f"{system_instruction}\n\n{user_prompt}"
                        )
                        if response and response.text:
                            clean_res = clean_plain_thai_text(response.text)
                            if clean_res:
                                print(f"[RAGEngine] Successfully synthesized answer with Gemini ({model_name})")
                                return (clean_res, "llm_gemini")
                    except Exception as e:
                        print(f"[RAGEngine] Gemini call failed with {model_name}: {e}")
            except Exception as e:
                print(f"[RAGEngine] Gemini client init error: {e}")

        # 2. Try OpenAI API
        if openai_api_key and is_valid_key_format(openai_api_key):
            try:
                import httpx
                headers = {"Authorization": f"Bearer {openai_api_key}", "Content-Type": "application/json"}
                model_name = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
                payload = {
                    "model": model_name,
                    "messages": [
                        {"role": "system", "content": system_instruction},
                        {"role": "user", "content": user_prompt}
                    ],
                    "temperature": 0.2
                }
                res = httpx.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload, timeout=10.0)
                if res.status_code == 200:
                    ans = res.json()["choices"][0]["message"]["content"]
                    clean_res = clean_plain_thai_text(ans)
                    if clean_res:
                        print("[RAGEngine] Successfully synthesized answer with OpenAI")
                        return (clean_res, "llm_openai")
            except Exception as e:
                print(f"[RAGEngine] OpenAI call failed: {e}")

        # 3. Clean fallback directly from Knowledge Base (without technical error messages)
        print("[RAGEngine] Using structured knowledge base fallback for synthesized answer")
        fallback_text = self._build_natural_prose_fallback(retrieved_items)
        return (fallback_text, "fallback_structured")

    def _build_natural_prose_fallback(self, retrieved_items: List[Dict[str, Any]], status_line: str = "") -> str:
        """
        Build continuous, natural Thai prose from retrieved data:
        - Explains primary field specification
        - Explains corresponding FAQ guidance if available
        - No Markdown (no **, ###, backticks, bullet dashes)
        - No emojis
        - Preserves field names and actual data (e.g. pol_id, [Loan])
        - Omits empty fields completely (no dangling 'ความยาว: ')
        - Sources at the end
        """
        paragraphs = []
        field_items = [it for it in retrieved_items if it.get("type") == "Field"]
        faq_items = [it for it in retrieved_items if it.get("type") == "FAQ"]
        sources = []

        if field_items:
            top_field = field_items[0]
            sources.append(f"[{top_field['item_id']}] {top_field['source']}")
            fn = top_field.get("field_name", "")
            desc = top_field.get("desc", "").strip()
            tbl = top_field.get("table", "").strip()
            req = top_field.get("required", "").strip()
            dtype = top_field.get("dtype", "").strip()
            length = top_field.get("length", "").strip()
            cond = top_field.get("condition", "").strip()
            ex = top_field.get("example", "").strip()
            m_data = top_field.get("master_data", "").strip()

            # Paragraph 1: Meaning, table, data type, submission requirement
            p1_parts = []
            if desc:
                p1_parts.append(f"ฟิลด์ {fn} หมายถึง {desc} อยู่ในชุดข้อมูลตาราง {tbl}")
            else:
                p1_parts.append(f"ฟิลด์ {fn} อยู่ในชุดข้อมูลตาราง {tbl}")

            type_info = []
            if dtype:
                if length:
                    type_info.append(f"จัดเก็บข้อมูลประเภท {dtype} ความยาว {length}")
                else:
                    type_info.append(f"จัดเก็บข้อมูลประเภท {dtype}")
            if req:
                type_info.append(f"มีสถานะความจำเป็นในการนำส่งข้อมูลคือ {req}")

            if type_info:
                p1_parts.append(" และ ".join(type_info))
            p1_parts.append("ตามข้อกำหนดโครงสร้างแบบรายงานข้อมูล")
            paragraphs.append(" ".join(p1_parts))

            # Paragraph 2: Conditions, Master Data, Example
            p2_parts = []
            if cond:
                p2_parts.append(f"เงื่อนไขการนำส่งข้อมูล: {cond}")
            if m_data:
                p2_parts.append(f"การอ้างอิง Master Data: {m_data}")
            if ex:
                clean_ex = ex.replace('"""', '').replace('"', '')
                p2_parts.append(f"ตัวอย่างข้อมูลที่นำส่ง: {clean_ex}")

            if p2_parts:
                paragraphs.append(" ".join(p2_parts))

            # Paragraph 3: FAQ guidelines corresponding to this field
            if faq_items:
                faq_parts = []
                for f in faq_items[:3]:
                    sources.append(f"[{f['item_id']}] {f['source']}")
                    q = f.get("question", "").strip()
                    a = f.get("answer", "").strip()
                    clean_a = re.sub(r'\s*\n+\s*', ' ', a).strip()
                    faq_parts.append(f"แนวทางปฏิบัติเพิ่มเติมตามคำถาม {q}: {clean_a}")

                if faq_parts:
                    paragraphs.append("สำหรับแนวทางปฏิบัติและข้อกำหนดเพิ่มเติมจาก FAQ ที่สอดคล้องกัน ได้แก่ " + " ".join(faq_parts))

            # Paragraph 4: Other related fields
            if len(field_items) > 1:
                other_parts = []
                for other in field_items[1:5]:
                    sources.append(f"[{other['item_id']}] {other['source']}")
                    o_fn = other.get("field_name", "")
                    o_desc = other.get("desc", "").strip()
                    o_tbl = other.get("table", "").strip()
                    o_req = other.get("required", "").strip()
                    if o_desc:
                        other_parts.append(f"ฟิลด์ {o_fn} ({o_desc}) ในตาราง {o_tbl} [ความจำเป็น: {o_req}]")
                    else:
                        other_parts.append(f"ฟิลด์ {o_fn} ในตาราง {o_tbl}")

                if other_parts:
                    paragraphs.append("สำหรับรายการฟิลด์อื่นที่เกี่ยวข้องในโครงสร้างข้อมูล ได้แก่ " + " และ ".join(other_parts))

        elif faq_items:
            # Only FAQs retrieved
            top_faq = faq_items[0]
            sources.append(f"[{top_faq['item_id']}] {top_faq['source']}")
            q = top_faq.get("question", "").strip()
            a = top_faq.get("answer", "").strip()
            clean_a = re.sub(r'\s*\n+\s*', ' ', a).strip()
            paragraphs.append(f"แนวทางปฏิบัติตามคำถาม {q} คือ {clean_a}")

            if len(faq_items) > 1:
                for other_faq in faq_items[1:3]:
                    sources.append(f"[{other_faq['item_id']}] {other_faq['source']}")
                    o_q = other_faq.get("question", "").strip()
                    o_a = other_faq.get("answer", "").strip()
                    paragraphs.append(f"นอกจากนี้ยังมีแนวทางในคำถาม {o_q} คือ {re.sub(r'\\s*\\n+\\s*', ' ', o_a).strip()}")

        # End: Clean Sources
        unique_sources = list(dict.fromkeys(sources))
        if unique_sources:
            paragraphs.append(f"แหล่งอ้างอิง: {', '.join(unique_sources[:5])}")

        return clean_plain_thai_text("\n\n".join(paragraphs))

    def external_ai_search(self, query: str) -> Dict[str, Any]:
        """
        AI Agent searching external credible websites for complementary recommendations.
        - Plain Thai text, no markdown or emojis.
        - Provides real clickable links or reports real status if search unavailable.
        """
        _, _, tavily_api_key = get_api_keys()

        if not tavily_api_key or not is_valid_key_format(tavily_api_key):
            return {
                "status": "unconfigured",
                "message": (
                    "สถานะคำแนะนำภายนอก: ยังไม่ได้ตั้งค่า Web Search API Key ในไฟล์ .env (เช่น TAVILY_API_KEY) "
                    "ระบบจึงไม่ได้ดึงคำแนะนำเพิ่มเติมจากภายนอก โดยคำตอบหลักจากฐานความรู้ภายในยังคงทำงานได้อย่างสมบูรณ์"
                ),
                "sources": []
            }

        try:
            import httpx
            search_query = f"คปภ กรมธรรม์ ประกันชีวิต {query}"
            resp = httpx.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": tavily_api_key,
                    "query": search_query,
                    "search_depth": "basic",
                    "include_domains": ["oic.or.th", "tlaa.org", "sec.or.th", "rd.go.th"],
                    "max_results": 3
                },
                timeout=12.0
            )
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("results", [])
                if not results:
                    return {
                        "status": "no_results",
                        "message": "ไม่พบข้อมูลภายนอกที่ตรงกับคำถามในเว็บไซต์ทางการที่เชื่อถือได้",
                        "sources": []
                    }

                sources = []
                snippets = []
                for r in results:
                    sources.append({"title": r.get("title", "แหล่งข้อมูลภายนอก"), "url": r.get("url", "")})
                    clean_content = clean_plain_thai_text(r.get("content", ""))
                    snippets.append(f"{r.get('title')}: {clean_content}")

                advice_text = (
                    "คำแนะนำเพิ่มเติมจากแหล่งข้อมูลภายนอก:\n\n"
                    + "\n\n".join(snippets[:2])
                    + "\n\nข้อควรระวัง: ข้อมูลภายนอกนี้เป็นเพียงคำแนะนำประกอบจากเว็บไซต์ทางการทั่วไป "
                    "ไม่ถือเป็นข้อกำหนดของระบบ IBSL และห้ามนำไปแทนที่ข้อกำหนดของ คปภ. หากพบข้อขัดแย้งให้ยึดข้อกำหนด IBSL ในฐานความรู้เป็นหลัก"
                )
                return {
                    "status": "success",
                    "message": advice_text,
                    "sources": sources
                }
            elif resp.status_code == 429:
                return {
                    "status": "quota_exceeded",
                    "message": "บริการค้นหาภายนอกเกินโควตาการใช้งาน (429 Rate Limit) โดยคำตอบภายในยังคงแสดงได้ตามปกติ",
                    "sources": []
                }
            else:
                return {
                    "status": "error",
                    "message": f"บริการค้นหาภายนอกส่งกลับรหัสสถานะ {resp.status_code}",
                    "sources": []
                }
        except Exception as e:
            return {
                "status": "error",
                "message": f"การค้นหาข้อมูลภายนอกขัดข้องหรือหมดเวลา ({str(e)[:60]}) โดยระบบยังคงแสดงคำตอบจากฐานความรู้ภายในได้ตามปกติ",
                "sources": []
            }

    def evaluate_ground_truth(self) -> Dict[str, Any]:
        """
        Evaluate Ground Truth strictly according to specification:
        - TP = รายการที่ค้นคืนและอยู่ในเฉลย
        - FP = รายการที่ค้นคืนแต่ไม่อยู่ในเฉลย
        - FN = รายการในเฉลยที่ระบบไม่ได้ค้นคืน
        - Precision = TP / (TP + FP)
        - Recall = TP / (TP + FN)
        - F1 = 2 × Precision × Recall / (Precision + Recall) (คำนวณต่อคำค้นก่อนเฉลี่ย)
        - Macro-averaged over queries.
        - If no ground truth file, returns message 'ยังไม่มีข้อมูลสำหรับคำนวณ'
        """
        if not os.path.exists(GROUND_TRUTH_FILE):
            return {
                "available": False,
                "message": "ยังไม่มีข้อมูลสำหรับคำนวณ",
                "test_count": 0
            }

        try:
            with open(GROUND_TRUTH_FILE, "r", encoding="utf-8") as f:
                cases = json.load(f)
        except Exception as e:
            return {
                "available": False,
                "message": "ยังไม่มีข้อมูลสำหรับคำนวณ",
                "test_count": 0
            }

        if not cases or not isinstance(cases, list):
            return {
                "available": False,
                "message": "ยังไม่มีข้อมูลสำหรับคำนวณ",
                "test_count": 0
            }

        test_results = []
        precisions = []
        recalls = []
        f1_scores = []

        for c in cases:
            q = c.get("query", "").strip()
            expected_ids = set(c.get("expected_item_ids", []))
            if not q or not expected_ids:
                continue

            retrieved = self.search_ranked(q, k=3)
            retrieved_ids = set([it["item_id"] for it in retrieved])

            tp = len(retrieved_ids.intersection(expected_ids))
            fp = len(retrieved_ids - expected_ids)
            fn = len(expected_ids - retrieved_ids)

            p = tp / float(tp + fp) if (tp + fp) > 0 else 0.0
            r = tp / float(tp + fn) if (tp + fn) > 0 else 0.0
            f1 = (2.0 * p * r) / (p + r) if (p + r) > 0 else 0.0

            precisions.append(p)
            recalls.append(r)
            f1_scores.append(f1)

            test_results.append({
                "query": q,
                "description": c.get("description", ""),
                "expected_ids": list(expected_ids),
                "retrieved_ids": list(retrieved_ids),
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "precision": round(p, 4),
                "recall": round(r, 4),
                "f1": round(f1, 4)
            })

        if not test_results:
            return {
                "available": False,
                "message": "ยังไม่มีข้อมูลสำหรับคำนวณ",
                "test_count": 0
            }

        macro_p = sum(precisions) / len(precisions)
        macro_r = sum(recalls) / len(recalls)
        macro_f1 = sum(f1_scores) / len(f1_scores)

        return {
            "available": True,
            "test_count": len(test_results),
            "macro_precision_at_3": round(macro_p, 4),
            "macro_recall_at_3": round(macro_r, 4),
            "macro_f1_at_3": round(macro_f1, 4),
            "test_cases": test_results,
            "explanation": "ประเมินคุณภาพการค้นคืนข้อมูล 3 รายการแรกเทียบกับข้อมูลอ้างอิง (Ground Truth) โดยเฉลี่ยแบบ Macro-average (F1 คำนวณต่อคำค้นก่อนเฉลี่ย)"
        }

rag_engine = RAGEngine()
