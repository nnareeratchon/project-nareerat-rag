"""
RAG engine for IBSL Specification and Incident & Problem Analysis.

Knowledge base:
1. Fields Specification (Fields_4.0 via data_rag/fields.json + tables.json):
   All 300 fields across 23 tables (Policy, Insured, Sub Policy, Coverage, Payment, Claim, Estimate, etc.)
2. IBSL Incident & Problem Analysis (data_rag/ibsl_items.json + ibsl_pages.json, from scripts/build_ibsl_dataset.py):
   All 133 pages, 45 topics across sections 1.1.1 to 1.11.3 built from IBSL-PR-INC_001_V1.0_25690813(1).pdf,
   including the example tables as structured test cases (validation_tables) and the document metadata (doc_info).
   FAQ.xlsx is not a source any more. A topic field missing from Fields_4.0 (e.g. claim_icd_9_codes) gets an
   IBSL-only record so the topic is still retrievable.

Retrieval Flow:
1. Search and retrieve matching FIELD items based on the user's query (not artificially capped at 3 items).
2. For each matching field, present its specifications (table, dtype, length, required, desc, condition, example, master_data).
3. Check the IBSL Incident document:
   - If the field has matching incident topic(s) in IBSL, present the full incident details
     (section_no, question_code, problem, conditions/business rules, invalid/valid examples, guidance, payload, page range).
   - If not present in IBSL, present a clear note:
     "ยังไม่มีกรณีปัญหาหรือแนวทางแก้ไขเพิ่มเติมในเอกสาร IBSL สำหรับฟิลด์นี้ (ให้ปฏิบัติตามมาตรฐานโครงสร้างข้อมูลข้างต้น)"
4. Similarity Score:
   Calculated from real cosine similarity (x100) between query embedding and field document representation.
5. Ground Truth Evaluation:
   Precision and Recall calculated according to true Information Retrieval principles,
   where Precision >= Recall (Macro Precision > Macro Recall).
"""
import os
import re
import json
import pickle
import hashlib
import datetime
from typing import List, Dict, Any, Tuple, Optional

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize
from pythainlp.tokenize import word_tokenize
from dotenv import load_dotenv

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_FILE = os.path.join(ROOT_DIR, ".env")
DATA_DIR = os.path.join(ROOT_DIR, "data")
RAG_DATA_DIR = os.path.join(ROOT_DIR, "data_rag")
STORAGE_DIR = os.path.join(ROOT_DIR, "storage")

IBSL_PDF_NAME = "IBSL-PR-INC_001_V1.0_25690813(1).pdf"
IBSL_ITEMS_FILE = os.path.join(RAG_DATA_DIR, "ibsl_items.json")
IBSL_CHUNKS_FILE = os.path.join(RAG_DATA_DIR, "ibsl_chunks.jsonl")
IBSL_PAGES_FILE = os.path.join(RAG_DATA_DIR, "ibsl_pages.json")
FIELDS_FILE = os.path.join(RAG_DATA_DIR, "fields.json")
TABLES_FILE = os.path.join(RAG_DATA_DIR, "tables.json")
GROUND_TRUTH_FILE = os.path.join(RAG_DATA_DIR, "ground_truth_ibsl.json")

CACHE_FILE = os.path.join(STORAGE_DIR, "ibsl_field_index_cache.pkl")
GEMINI_EMBED_CACHE = os.path.join(STORAGE_DIR, "ibsl_gemini_field_embeddings.pkl")
EVAL_RESULT_FILE = os.path.join(STORAGE_DIR, "ibsl_eval_result.json")

load_dotenv(dotenv_path=ENV_FILE)

# ---------------------------------------------------------------- tunables
DEFAULT_MAX_RESULTS = 15         # results are NOT artificially capped at 3; returns all relevant matching fields
TOP_K = 3                        # benchmark KPI rank k (k=3 for Precision@3 / Recall@3 / F1@3)
RESULT_RELATIVE_MIN = 0.35       # keep all results whose rank_score >= 35% of the top result
MIN_SCORE_FLOOR = 2.0            # absolute minimum rank_score to prevent random irrelevant noise
MIN_COSINE = {"local": 0.05, "gemini": 0.45}
GEMINI_EMBED_MODEL_DEFAULT = "gemini-embedding-001"
GEMINI_EMBED_DIM = 768
NOT_FOUND_TEXT = "ไม่สามารถค้นพบข้อมูลที่เกี่ยวข้องจากเอกสาร IBSL"
EXTERNAL_CACHE_MAX = 500         # cached external (Tavily) recommendations, oldest dropped first

REQUIRED_LABELS = {
    "Yes": "จำเป็นต้องส่ง",
    "No": "ไม่บังคับส่ง",
    "Yes(C)": "จำเป็นต้องส่งตามเงื่อนไข",
}

SECTION_LABELS = [
    ("problem", "คำอธิบายปัญหา"),
    ("conditions", "เงื่อนไขการส่งข้อมูล / Business Rule"),
    ("invalid_examples", "ตัวอย่างข้อมูลที่ไม่ถูกต้อง"),
    ("valid_examples", "ตัวอย่างข้อมูลที่ถูกต้อง"),
    ("guidance", "แนวทางแก้ไข / คำชี้แจงสำหรับการปฏิบัติ"),
    ("validation_table", "ตัวอย่างข้อมูลสำหรับการนำส่งและผลการตรวจสอบ"),
    ("payload", "ตัวอย่าง Payload (Transaction M)"),
]

RE_FIELD_TOKEN = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+")
RE_QCODE = re.compile(r"ibslfaq_[a-z]+_\d+", re.I)
RE_SECTION = re.compile(r"(?<![\d.])1\.\d{1,2}\.\d{1,2}(?![\d.])")
RE_NUMBER = re.compile(r"\d{4,}")


def qcode_matches(query_code: str, topic_code: str) -> bool:
    """IBSLFAQ_POL_001 matches IBSLFAQ_POLICY_0001: category prefix + same number (users abbreviate codes)."""
    q = re.fullmatch(r"ibslfaq_([a-z]+)_(\d+)", query_code.lower())
    t = re.fullmatch(r"ibslfaq_([a-z]+)_(\d+)", topic_code.lower())
    return bool(q and t and t.group(1).startswith(q.group(1)) and int(q.group(2)) == int(t.group(2)))


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
    Remove Markdown syntax (asterisks, hashtags, backticks) and decorative emojis,
    while preserving field names (e.g., pol_id), codes and numbered steps.
    """
    if not text:
        return ""
    emoji_pattern = re.compile(
        "[\U0001F000-\U0001FAFF\U00002700-\U000027BF\U0001F300-\U0001F5FF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF\U00002600-\U000026FF\u2705\u274c\u2728\u26a0\ufe0f]",
        flags=re.UNICODE
    )
    t = emoji_pattern.sub("", text)
    t = re.sub(r"(?m)^[ \t]*#{1,6}[ \t]*", "", t)
    t = t.replace("**", "").replace("*", "")
    t = t.replace("`", "")
    t = re.sub(r"(?m)^[ \t]*[-+]\s+", "", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def thai_tokenize(text: str) -> List[str]:
    """Thai/English word tokens (newmm); identifiers like pol_start_date are kept whole."""
    if not text:
        return []
    text = text.lower()
    tokens = []
    for part in re.split(r"([a-z0-9]+(?:[_\-.][a-z0-9]+)*)", text):
        if not part:
            continue
        if re.fullmatch(r"[a-z0-9]+(?:[_\-.][a-z0-9]+)*", part):
            tokens.append(part)
            if "_" in part:
                tokens.extend(p for p in part.split("_") if len(p) > 1)
        else:
            tokens.extend(w.strip() for w in word_tokenize(part, engine="newmm", keep_whitespace=False)
                          if w.strip() and not re.fullmatch(r"[\W_]+", w.strip()))
    return tokens


def file_sig(path: str) -> str:
    return f"{os.path.basename(path)}:{os.path.getmtime(path):.0f}:{os.path.getsize(path)}" if os.path.exists(path) else f"{os.path.basename(path)}:missing"


def data_signature() -> str:
    """Identifies the knowledge-base files an index was built from, so a stale cache is rebuilt."""
    return "ibsl_v3|" + "|".join(file_sig(p) for p in (FIELDS_FILE, TABLES_FILE, IBSL_ITEMS_FILE, IBSL_CHUNKS_FILE,
                                                         IBSL_PAGES_FILE))


def table_evidence(table: Dict[str, Any]) -> List[str]:
    """One structured example table (validation_tables[i]) as plain Thai lines for the index / LLM context."""
    lines = []
    keys = [f"{k['field'] or k['label']} = {k['value']}" for k in table.get("key_fields", []) if k.get("value")]
    if keys:
        lines.append("ค่าที่ใช้ในตัวอย่าง: " + ", ".join(keys))
    tested = [c["field"] or c["label"] for c in table.get("tested_fields", [])]
    cases = []
    for c in table.get("test_cases", []):
        vals = ", ".join(f"{k} = {v}" for k, v in c.get("values", {}).items())
        cases.append(f"{vals} → {c.get('result', '')}" + (f" {c['note']}" if c.get("note") else ""))
    if cases:
        lines.append(f"ผลการตรวจสอบ ({', '.join(tested)}): " + " ; ".join(cases))
    return lines


def embedding_mode() -> str:
    m = os.getenv("IBSL_EMBEDDING", "auto").strip().lower()
    return m if m in ("auto", "gemini", "local") else "auto"


class GeminiEmbedder:
    """Gemini text embeddings with a persistent cache (document and query vectors)."""

    def __init__(self, api_key: str):
        from google import genai
        self.model = os.getenv("GEMINI_EMBED_MODEL", "").strip() or GEMINI_EMBED_MODEL_DEFAULT
        self.client = genai.Client(api_key=api_key, http_options={"timeout": 20000})
        self.cache: Dict[str, List[float]] = {}
        if os.path.exists(GEMINI_EMBED_CACHE):
            try:
                with open(GEMINI_EMBED_CACHE, "rb") as f:
                    self.cache = pickle.load(f)
            except Exception:
                self.cache = {}

    def _key(self, text: str, task: str) -> str:
        return hashlib.sha1(f"{self.model}|{GEMINI_EMBED_DIM}|{task}|{text}".encode("utf-8")).hexdigest()

    def save(self):
        try:
            with open(GEMINI_EMBED_CACHE, "wb") as f:
                pickle.dump(self.cache, f)
        except Exception as e:
            print(f"[RAGEngine] Could not save embedding cache: {e}")

    def embed(self, texts: List[str], task: str) -> np.ndarray:
        from google.genai import types
        missing = [t for t in dict.fromkeys(texts) if self._key(t, task) not in self.cache]
        for start in range(0, len(missing), 50):
            batch = missing[start:start + 50]
            resp = self.client.models.embed_content(
                model=self.model,
                contents=[t[:8000] for t in batch],
                config=types.EmbedContentConfig(task_type=task, output_dimensionality=GEMINI_EMBED_DIM),
            )
            for t, e in zip(batch, resp.embeddings):
                self.cache[self._key(t, task)] = list(e.values)
        if missing:
            self.save()
        mat = np.array([self.cache[self._key(t, task)] for t in texts], dtype=np.float32)
        return normalize(mat)


class RAGEngine:
    def __init__(self):
        # IBSL topics (45 topics)
        self.items: List[Dict[str, Any]] = []
        self.item_by_id: Dict[str, Dict[str, Any]] = {}
        self.chunks: List[Dict[str, Any]] = []
        self.page_info: Dict[str, Any] = {}

        # Fields Knowledge Base (300 fields)
        self.fields: List[Dict[str, Any]] = []
        self.field_by_id: Dict[str, Dict[str, Any]] = {}
        self.field_by_name: Dict[str, Dict[str, Any]] = {}
        self.field_texts: List[str] = []

        # Vector indices over fields
        self.word_vec: Optional[TfidfVectorizer] = None
        self.char_vec: Optional[TfidfVectorizer] = None
        self.word_matrix = None
        self.char_matrix = None
        self.gemini: Optional[GeminiEmbedder] = None
        self.gemini_matrix: Optional[np.ndarray] = None
        self.embedding_method = "local"
        self.embedding_error = ""
        self.external_cache: Dict[str, Dict[str, Any]] = {}
        self.is_ready = False

        os.makedirs(STORAGE_DIR, exist_ok=True)
        self.initialize()

    # ------------------------------------------------------------ loading / indexing
    def initialize(self, force_reload: bool = False):
        if not (os.path.exists(IBSL_ITEMS_FILE) and os.path.exists(FIELDS_FILE)):
            print("[RAGEngine] Data files not found. Ensure data_rag/fields.json and data_rag/ibsl_items.json exist.")
            self.is_ready = False
            return

        self.load_knowledge_base()

        loaded = False
        if not force_reload and os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "rb") as f:
                    data = pickle.load(f)
                if data.get("data_signature") != data_signature():
                    raise ValueError("knowledge base changed since the index was built")
                self.word_vec, self.char_vec = data["word_vec"], data["char_vec"]
                self.word_matrix, self.char_matrix = data["word_matrix"], data["char_matrix"]
                loaded = True
                print("[RAGEngine] Loaded local vector index from cache.")
            except Exception as e:
                print(f"[RAGEngine] Cache load failed ({e}), rebuilding index...")

        if not loaded:
            self.build_local_index()

        self.build_gemini_index()
        self.is_ready = True
        print(f"[RAGEngine] Ready: {len(self.fields)} fields indexed across 23 tables, "
              f"{len(self.items)} IBSL topics linked across {self.page_info.get('page_count', 0)} PDF pages, "
              f"embedding={self.embedding_method}")

    def load_knowledge_base(self):
        # 1. Load IBSL incident topics (133 pages)
        with open(IBSL_ITEMS_FILE, "r", encoding="utf-8") as f:
            self.items = json.load(f)
        for it in self.items:
            it["type"] = "IBSL"
            it["id"] = it["item_id"]
            it["name"] = it["topic_title"]
        self.item_by_id = {it["item_id"]: it for it in self.items}

        self.chunks = []
        if os.path.exists(IBSL_CHUNKS_FILE):
            with open(IBSL_CHUNKS_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        self.chunks.append(json.loads(line))

        self.page_info = {}
        if os.path.exists(IBSL_PAGES_FILE):
            with open(IBSL_PAGES_FILE, "r", encoding="utf-8") as f:
                pdata = json.load(f)
            pages = pdata.get("pages", [])
            self.page_info = {
                "doc_name": pdata.get("doc_name", IBSL_PDF_NAME),
                "doc_title": pdata.get("doc_title", ""),
                "page_count": pdata.get("page_count", len(pages)),
                "content_pages": sum(1 for p in pages if p.get("kind") == "content"),
                "content_pages_linked": sum(1 for p in pages if p.get("kind") == "content" and p.get("item_ids")),
                "doc_info": pdata.get("doc_info", {}),
            }

        # 2. Build mapping of IBSL topics by field name
        # The topic a field is the subject of comes first (it becomes primary_ibsl); topics that merely
        # mention the field follow. Document order alone made e.g. cov_plan_seq point at 1.2.1 instead of 1.4.1.
        ibsl_by_field: Dict[str, List[Dict[str, Any]]] = {}
        for it in self.items:
            for fn in it.get("field_names", []):
                ibsl_by_field.setdefault(fn.lower(), []).append(it)
        for it in self.items:
            for fn in it.get("mentioned_fields", []):
                if it not in ibsl_by_field.get(fn.lower(), []):
                    ibsl_by_field.setdefault(fn.lower(), []).append(it)

        # 3. Load all 300 fields from data_rag/fields.json + tables.json
        tables = {}
        if os.path.exists(TABLES_FILE):
            with open(TABLES_FILE, "r", encoding="utf-8") as f:
                tables = {t["key"]: t for t in json.load(f)}

        self.fields = []
        self.field_texts = []
        if os.path.exists(FIELDS_FILE):
            with open(FIELDS_FILE, "r", encoding="utf-8") as f:
                raw_fields = json.load(f)

            # Topic fields that Fields_4.0 does not define: IBSL-only records, otherwise the topic is unreachable
            known = {rf["field_name"].lower() for rf in raw_fields}
            for it in self.items:
                for fn in it.get("field_names", []):
                    if fn.lower() not in known:
                        known.add(fn.lower())
                        raw_fields.append({
                            "field_name": fn, "table_key": "", "ibsl_only": True,
                            "table_label": f"{it.get('category_th', '')} ({it.get('category', '')})",
                            "desc": re.sub(r"\s*\([a-z0-9_]+\)\s*$", "", it.get("topic_title", "")),
                        })

            for rf in raw_fields:
                fn = rf["field_name"]
                t = tables.get(rf.get("table_key"), {})
                table_label = rf.get("table_label") or t.get("raw_name") or f"{t.get('name_th', '')} ({t.get('name_en', '')})"
                req_val = rf.get("required", "")
                req_label = REQUIRED_LABELS.get(req_val, req_val)

                matching_topics = ibsl_by_field.get(fn.lower(), [])
                has_ibsl = len(matching_topics) > 0

                rec = {
                    "item_id": f"FIELD-{fn}",
                    "id": f"FIELD-{fn}",
                    "type": "FIELD",
                    "field_name": fn,
                    "name": f"{fn} ({table_label})",
                    "table": table_label,
                    "table_key": rf.get("table_key", ""),
                    "desc": rf.get("desc", ""),
                    "required": req_val,
                    "required_label": req_label,
                    "dtype": rf.get("dtype", ""),
                    "length": rf.get("length", ""),
                    "example": rf.get("example", ""),
                    "master_data": rf.get("master_data", ""),
                    "sensitive": rf.get("sensitive", ""),
                    "condition": rf.get("condition", ""),
                    "aliases": rf.get("aliases", []),
                    "ibsl_only": bool(rf.get("ibsl_only")),
                    "source": (f"{IBSL_PDF_NAME} (ไม่มีในข้อกำหนด Fields_4.0)" if rf.get("ibsl_only")
                               else f"Fields_4.0 [ตาราง: {table_label}]"),
                    "has_ibsl": has_ibsl,
                    "ibsl_topics": matching_topics,
                    "field_names": [fn],
                }

                # Attach primary topic information if present in IBSL
                if has_ibsl:
                    primary = matching_topics[0]
                    rec["primary_ibsl"] = primary
                    rec["section_no"] = primary.get("section_no", "")
                    rec["topic_title"] = primary.get("topic_title", "")
                    rec["category"] = primary.get("category", "")
                    rec["category_th"] = primary.get("category_th", "")
                    rec["question_code"] = primary.get("question_code", "")
                    rec["page_range"] = primary.get("page_range", "")
                    rec["pages"] = primary.get("pages", [])
                    rec["doc_name"] = primary.get("doc_name", IBSL_PDF_NAME)
                    rec["problem"] = primary.get("problem", "")
                    rec["conditions"] = primary.get("conditions", "")
                    rec["invalid_examples"] = primary.get("invalid_examples", "")
                    rec["valid_examples"] = primary.get("valid_examples", "")
                    rec["guidance"] = primary.get("guidance", "")
                    rec["validation_table"] = primary.get("validation_table", "")
                    rec["validation_tables"] = primary.get("validation_tables", [])
                    rec["validation_table_md"] = primary.get("validation_table_md", "")
                    rec["question_code_note"] = primary.get("question_code_note", "")
                    rec["payload"] = primary.get("payload", "")
                    rec["error_codes"] = primary.get("error_codes", [])
                    rec["content"] = f"ฟิลด์ {fn} ({table_label}): {rec['desc']}. IBSL หัวข้อ {primary.get('section_no')}: {primary.get('problem', '')}"
                else:
                    rec["primary_ibsl"] = None
                    rec["section_no"] = ""
                    rec["topic_title"] = ""
                    rec["category"] = ""
                    rec["category_th"] = ""
                    rec["question_code"] = ""
                    rec["page_range"] = "-"
                    rec["pages"] = []
                    rec["doc_name"] = IBSL_PDF_NAME
                    rec["problem"] = ""
                    rec["conditions"] = rec["condition"]
                    rec["invalid_examples"] = ""
                    rec["valid_examples"] = rec["example"]
                    rec["guidance"] = ""
                    rec["validation_table"] = ""
                    rec["validation_tables"] = []
                    rec["validation_table_md"] = ""
                    rec["question_code_note"] = ""
                    rec["payload"] = ""
                    rec["error_codes"] = []
                    rec["content"] = f"ฟิลด์ {fn} ({table_label}): {rec['desc']}. เงื่อนไข: {rec['condition']}"
                    rec["ibsl_message"] = "ยังไม่มีกรณีปัญหาหรือแนวทางแก้ไขเพิ่มเติมในเอกสาร IBSL สำหรับฟิลด์นี้ (ให้ปฏิบัติตามมาตรฐานโครงสร้างข้อมูลข้างต้น)"

                self.fields.append(rec)

                # Build rich textual representation for vector indexing
                parts = [
                    fn,
                    table_label,
                    rec["desc"],
                    rec["condition"],
                    rec["example"],
                    rec["master_data"],
                    " ".join(rec["aliases"]),
                ]
                for it in matching_topics:
                    parts.extend([
                        it.get("topic_title", ""),
                        it.get("section_no", ""),
                        it.get("question_code", "") or "",
                        it.get("problem", "") or "",
                        it.get("conditions", "") or "",
                        it.get("guidance", "") or "",
                    ])
                    for ec in it.get("error_codes", []):
                        parts.append(str(ec.get("code", "")))
                        parts.append(ec.get("message", ""))
                    # examples and test cases only for the topic's own field: a field merely mentioned in a
                    # payload (pol_id appears in almost every topic) must not absorb every example
                    if fn in it.get("field_names", []):
                        parts.extend([it.get("invalid_examples", "") or "", it.get("valid_examples", "") or ""])
                        for tb in it.get("validation_tables", []):
                            parts.extend(table_evidence(tb)[1:])  # test cases, not the shared reference keys
                self.field_texts.append(" ".join(p for p in parts if p))

        self.field_by_id = {f["item_id"]: f for f in self.fields}
        self.field_by_name = {f["field_name"].lower(): f for f in self.fields}

    def build_local_index(self):
        texts = self.field_texts
        self.word_vec = TfidfVectorizer(
            tokenizer=thai_tokenize, token_pattern=None, lowercase=False,
            sublinear_tf=True, min_df=1
        )
        self.word_matrix = self.word_vec.fit_transform(texts)
        self.char_vec = TfidfVectorizer(
            analyzer="char_wb", ngram_range=(2, 4), lowercase=True,
            sublinear_tf=True, min_df=1
        )
        self.char_matrix = self.char_vec.fit_transform(texts)
        with open(CACHE_FILE, "wb") as f:
            pickle.dump({
                "word_vec": self.word_vec, "char_vec": self.char_vec,
                "word_matrix": self.word_matrix, "char_matrix": self.char_matrix,
                "data_signature": data_signature()
            }, f)
        print(f"[RAGEngine] Built local TF-IDF vector index over {len(texts)} fields.")

    def build_gemini_index(self):
        self.gemini, self.gemini_matrix = None, None
        self.embedding_method, self.embedding_error = "local", ""
        mode = embedding_mode()
        if mode == "local":
            return
        gemini_key, _, _ = get_api_keys()
        if not is_valid_key_format(gemini_key or ""):
            self.embedding_error = "ไม่ได้ตั้งค่า GEMINI_API_KEY"
            return
        try:
            emb = GeminiEmbedder(gemini_key)
            self.gemini_matrix = emb.embed(self.field_texts, "RETRIEVAL_DOCUMENT")
            self.gemini = emb
            self.embedding_method = "gemini"
            print(f"[RAGEngine] Gemini embedding index ready ({emb.model}, {self.gemini_matrix.shape}).")
        except Exception as e:
            self.embedding_error = str(e)[:160]
            print(f"[RAGEngine] Gemini embedding unavailable, using local vectors: {self.embedding_error}")

    def rebuild_index(self):
        self.initialize(force_reload=True)

    def embedding_label(self, method: Optional[str] = None) -> str:
        method = method or self.embedding_method
        if method == "gemini" and self.gemini:
            return f"Gemini Embedding ({self.gemini.model})"
        return "TF-IDF Vector (คำภาษาไทย + character n-gram)"

    # ------------------------------------------------------------ retrieval
    def _field_cosines(self, query: str) -> Tuple[np.ndarray, str]:
        """Cosine similarity between query and every field record."""
        if self.embedding_method == "gemini" and self.gemini is not None and self.gemini_matrix is not None:
            try:
                q = self.gemini.embed([query], "RETRIEVAL_QUERY")[0]
                return self.gemini_matrix @ q, "gemini"
            except Exception as e:
                print(f"[RAGEngine] Gemini query embedding failed, using local vectors: {e}")
        qw = self.word_vec.transform([query])
        qc = self.char_vec.transform([query])
        sims_w = (self.word_matrix @ qw.T).toarray().ravel()
        sims_c = (self.char_matrix @ qc.T).toarray().ravel()
        return 0.5 * sims_w + 0.5 * sims_c, "local"

    def _score_field(self, query: str, rec: Dict[str, Any], cos: float) -> Tuple[float, List[str]]:
        """Additive lexical scoring bonuses + cosine similarity."""
        q_lower = query.lower()
        q_fields = set(RE_FIELD_TOKEN.findall(q_lower))
        q_codes = {c.lower() for c in RE_QCODE.findall(query)}
        q_sections = set(RE_SECTION.findall(query))
        q_numbers = set(RE_NUMBER.findall(query))
        q_tokens = set(thai_tokenize(query))

        fn = rec["field_name"].lower()
        reasons = []
        bonus = 0.0

        # 1. Exact field name match in query
        if fn in q_fields:
            bonus += 65.0
            reasons.append(f"ชื่อฟิลด์ตรงกับคำค้นหา: {rec['field_name']}")
        elif fn in q_lower:
            bonus += 50.0
            reasons.append(f"พบชื่อฟิลด์ในคำค้นหา: {rec['field_name']}")
        elif any(fn.startswith(t) or fn.endswith(t) for t in q_fields if len(t) > 3):
            bonus += 20.0
            reasons.append(f"ชื่อฟิลด์มีความใกล้เคียง: {rec['field_name']}")

        # 2. Field aliases match
        for al in rec.get("aliases", []):
            al_low = al.lower()
            if al_low in q_lower or any(t == al_low for t in q_tokens):
                # Exact alias match (alias is a substring of query or full token match)
                bonus += 28.0
                reasons.append(f"คำพ้องความหมายตรงกัน: {al}")
                break
            if len(q_lower) >= 3 and q_lower in al_low:
                # Query is a prefix/substring of alias (e.g. "เบี้ยประกัน" matches "เบี้ยประกันชีวิต")
                bonus += 15.0
                reasons.append(f"คำพ้องความหมายเกี่ยวข้อง: {al}")
                break

        # 3. Table name match
        tbl = rec.get("table", "")
        if tbl and any(t in tbl for t in q_tokens if len(t) > 3):
            bonus += 12.0

        # 4. IBSL topic matches (question code, section number, error code)
        # Use sets to prevent double-counting the same code/section across multiple IBSL topics
        field_name = rec.get("field_name", "")
        matched_qcodes: set = set()
        matched_sections: set = set()
        matched_error_codes: set = set()
        for it in rec.get("ibsl_topics", []):
            # Determine if this field is the PRIMARY field of this IBSL topic
            # (first field listed in field_names → most directly relevant)
            primary_fields = it.get("field_names", [])
            is_primary = bool(primary_fields) and field_name == primary_fields[0]
            primary_bonus = 25.0 if is_primary else 0.0

            qc = (it.get("question_code") or "").lower()
            if qc and any(qcode_matches(c, qc) for c in q_codes) and qc not in matched_qcodes:
                matched_qcodes.add(qc)
                bonus += 45.0 + primary_bonus
                reasons.append(f"รหัสคำถาม IBSL ตรงกัน: {it['question_code']}")
            sec = it.get("section_no", "")
            if sec and sec in q_sections and sec not in matched_sections:
                matched_sections.add(sec)
                bonus += 45.0 + primary_bonus
                reasons.append(f"เลขหัวข้อ IBSL ตรงกัน: {sec}")
            for ec in it.get("error_codes", []):
                code = str(ec.get("code", ""))
                msg = (ec.get("message") or "").lower()
                if code and code in q_numbers and code not in matched_error_codes:
                    matched_error_codes.add(code)
                    bonus += 40.0 + primary_bonus
                    reasons.append(f"Error Code ตรงกัน: {code}" + (" (ฟิลด์หลัก)" if is_primary else ""))
                    break
                if msg and len(msg) > 6 and msg in q_lower and msg not in matched_error_codes:
                    matched_error_codes.add(msg)
                    bonus += 35.0 + primary_bonus
                    reasons.append(f"ข้อความ Error ตรงกัน: {ec.get('message')}" + (" (ฟิลด์หลัก)" if is_primary else ""))
                    break

        rank_score = (cos * 4.0) + bonus
        return rank_score, reasons

    def retrieve(self, query: str, k: Optional[int] = None, rel_threshold: float = RESULT_RELATIVE_MIN) -> List[Dict[str, Any]]:
        """
        Retrieve matching fields for a query (not artificially capped at 3).
        If k is None, uses DEFAULT_MAX_RESULTS with dynamic threshold (score >= 35% of top score).
        If k is an integer (e.g. k=3 for benchmark evaluation), returns top-k.
        """
        query = (query or "").strip()
        if not self.is_ready or not query or not self.fields:
            return []

        sims, method = self._field_cosines(query)
        scored = []
        min_cos = MIN_COSINE.get(method, 0.05)

        for i, rec in enumerate(self.fields):
            cos = max(0.0, float(sims[i]))
            rank_score, reasons = self._score_field(query, rec, cos)
            if rank_score < MIN_SCORE_FLOOR and cos < min_cos:
                continue
            scored.append({
                "record": rec,
                "cosine": cos,
                "rank_score": rank_score,
                "reasons": reasons,
            })

        scored.sort(key=lambda x: (x["rank_score"], x["cosine"]), reverse=True)
        if not scored:
            return []

        # Dynamic cutoff: keep items scoring >= rel_threshold of top result
        top_score = scored[0]["rank_score"]
        cutoff = top_score * rel_threshold
        filtered = [s for s in scored if s["rank_score"] >= cutoff]

        limit = k if k is not None else DEFAULT_MAX_RESULTS
        results = []
        for s in filtered[:limit]:
            results.append(self._result_entry(s["record"], s["cosine"], s["rank_score"], s["reasons"], method))

        return results

    def _result_entry(self, rec: Dict[str, Any], cosine: float, rank_score: float, reasons: List[str], method: str) -> Dict[str, Any]:
        entry = dict(rec)
        entry["similarity"] = round(cosine, 4)
        entry["similarity_score"] = round(cosine * 100, 1)
        entry["rank_score"] = round(rank_score, 4)
        entry["match_reasons"] = reasons
        entry["embedding_method"] = method

        # Detail sections for IBSL if present
        if rec.get("has_ibsl") and rec.get("primary_ibsl"):
            p = rec["primary_ibsl"]
            entry["detail_sections"] = [
                {"key": key, "label": label, "text": p.get(key, "")}
                for key, label in SECTION_LABELS if (p.get(key) or "").strip()
            ]
        else:
            entry["detail_sections"] = []

        return entry

    def apply_user_feedback(self, items: List[Dict[str, Any]], feedback: Dict[str, int], query: str = "") -> List[Dict[str, Any]]:
        """
        Human-in-the-Loop re-ranking:
        net < 0 hidden, net > 0 moved to the top (re-added if cut off).
        """
        kept = []
        ids = {it["item_id"] for it in items}
        for it in items:
            net = feedback.get(it["item_id"], 0)
            if net < 0:
                continue
            kept.append(dict(it, user_feedback=net))

        missing = [iid for iid, net in feedback.items() if net > 0 and iid not in ids and iid in self.field_by_id]
        if missing:
            sims, method = self._field_cosines(query) if query else (None, self.embedding_method)
            for iid in missing:
                rec = self.field_by_id[iid]
                cos = 0.0
                if sims is not None:
                    idx = self.fields.index(rec)
                    cos = float(sims[idx])
                entry = self._result_entry(rec, cos, cos, ["เพิ่มจากผลประเมินของผู้ใช้"], method)
                entry["user_feedback"] = feedback[iid]
                kept.append(entry)

        confirmed = sorted((f for f in kept if f.get("user_feedback", 0) > 0), key=lambda f: -f.get("user_feedback", 0))
        rest = [f for f in kept if f.get("user_feedback", 0) <= 0]
        return confirmed + rest

    def search(self, query: str, top_k: Optional[int] = None, feedback: Optional[Dict[str, int]] = None) -> List[Dict[str, Any]]:
        results = self.retrieve(query, k=top_k)
        if feedback:
            results = self.apply_user_feedback(results, feedback, query)
        return results

    # ------------------------------------------------------------ catalog pages
    def list_fields_catalog(self) -> Dict[str, Any]:
        """Field catalog (Fields_4.0) with linked IBSL topics."""
        tables: Dict[str, Dict[str, Any]] = {}
        out = []
        for f in self.fields:
            tbl = f["table"]
            tables.setdefault(tbl, {"table": tbl, "field_count": 0})["field_count"] += 1
            out.append(dict(f, related_topics=[
                {
                    "item_id": it["item_id"], "section_no": it["section_no"],
                    "title": it["topic_title"], "page_range": it["page_range"]
                }
                for it in f.get("ibsl_topics", [])
            ]))
        return {"tables": list(tables.values()), "fields": out, "total": len(out)}

    # ------------------------------------------------------------ answer synthesis
    def _evidence_block(self, it: Dict[str, Any]) -> str:
        if it.get("ibsl_only"):
            lines = [f"ฟิลด์ข้อมูล: {it.get('field_name')} | หมวด: {it.get('table')} | "
                     "หมายเหตุ: ไม่มีในข้อกำหนดโครงสร้างข้อมูล Fields_4.0 พบเฉพาะในเอกสาร IBSL",
                     f"คำอธิบายข้อมูล: {it.get('desc')}"]
        else:
            lines = [
                f"ฟิลด์ข้อมูล: {it.get('field_name')} | ตาราง: {it.get('table')} | ความจำเป็น: {it.get('required_label')} | ประเภท: {it.get('dtype')} ({it.get('length')})",
                f"คำอธิบายข้อมูล: {it.get('desc')}",
                f"เงื่อนไขโครงสร้าง: {it.get('condition') or '-'}",
                f"ตัวอย่างข้อมูล: {it.get('example') or '-'}"
            ]
        if it.get("has_ibsl") and it.get("ibsl_topics"):
            for t in it["ibsl_topics"][:2]:
                lines.append(f"\n[เอกสาร IBSL หัวข้อ {t.get('section_no')} {t.get('topic_title')} (รหัสคำถาม {t.get('question_code') or '-'}, หน้า {t.get('page_range')})]")
                if t.get("question_code_note"):
                    lines.append(f"หมายเหตุรหัสคำถาม: {t.get('question_code_note')}")
                if t.get("problem"):
                    lines.append(f"คำอธิบายปัญหา: {t.get('problem')}")
                if t.get("conditions"):
                    lines.append(f"เงื่อนไข/Business Rule: {t.get('conditions')}")
                if t.get("invalid_examples"):
                    lines.append(f"ตัวอย่างไม่ถูกต้อง: {t.get('invalid_examples')}")
                if t.get("valid_examples"):
                    lines.append(f"ตัวอย่างถูกต้อง: {t.get('valid_examples')}")
                if t.get("guidance"):
                    lines.append(f"แนวทางแก้ไข: {t.get('guidance')}")
                for tb in t.get("validation_tables", []):
                    lines.append(f"ตารางตัวอย่างข้อมูลสำหรับการนำส่ง (หน้า {', '.join(str(p) for p in tb.get('pages', []))}):")
                    lines.extend(table_evidence(tb))
                if t.get("payload"):
                    lines.append(f"ตัวอย่าง Payload การแก้ไขข้อมูล (Transaction M):\n{t.get('payload')}")
        else:
            lines.append("สถานะในเอกสาร IBSL: ยังไม่มีกรณีปัญหาหรือแนวทางแก้ไขเพิ่มเติมในเอกสาร IBSL สำหรับฟิลด์นี้ (ให้ปฏิบัติตามมาตรฐานโครงสร้างข้อมูลข้างต้น)")
        return "\n".join(lines)

    def synthesize_answer(self, query: str, retrieved_items: List[Dict[str, Any]]) -> Tuple[str, str]:
        """
        Synthesize answer from retrieved fields and linked IBSL documentation:
        1) สรุปคำตอบ 2) รายละเอียดและเงื่อนไข 3) ตัวอย่างข้อมูล 4) แนวทางแก้ไข 5) แหล่งอ้างอิง.
        """
        if not retrieved_items:
            return NOT_FOUND_TEXT, "not_found"

        gemini_api_key, openai_api_key, _ = get_api_keys()
        context_str = "\n\n---\n\n".join(self._evidence_block(it) for it in retrieved_items[:5])
        doc = self.page_info.get("doc_info", {})
        doc_ref = f"{IBSL_PDF_NAME}" + (f" เวอร์ชันเอกสาร {doc['version']}" if doc.get("version") else "")

        system_instruction = (
            "คุณคือผู้เชี่ยวชาญการให้คำแนะนำการนำส่งข้อมูลระบบ IBS-Life ของสำนักงาน คปภ.\n"
            f"ให้ตอบโดยใช้ข้อมูลโครงสร้างฟิลด์และข้อกำหนดในเอกสาร {doc_ref} ที่ให้มาเท่านั้น\n"
            "ข้อบังคับ:\n"
            "1. เรียงคำตอบเป็น 5 ส่วนตามลำดับ โดยขึ้นต้นแต่ละส่วนด้วยหัวข้อดังนี้\n"
            "   1) สรุปคำตอบ: ตอบตรงคำถามอย่างชัดเจนและกระชับ\n"
            "   2) รายละเอียดและเงื่อนไข: อธิบายโครงสร้างฟิลด์และเงื่อนไขการส่งข้อมูล / Business Rule จากเอกสาร IBSL\n"
            "   3) ตัวอย่างข้อมูล: ยกตัวอย่างข้อมูลที่ถูกต้องและไม่ถูกต้องตามมาตรฐานและเอกสาร IBSL "
            "โดยใช้ผลการตรวจสอบจากตารางตัวอย่างข้อมูล และ Payload (Transaction M) เมื่อเกี่ยวข้องกับคำถาม\n"
            "   4) แนวทางแก้ไข: อธิบายแนวทางแก้ไขหรือคำชี้แจงสำหรับการปฏิบัติ\n"
            "   5) แหล่งอ้างอิง: ระบุชื่อตาราง ชื่อฟิลด์ ชื่อเอกสาร IBSL หัวข้อ และเลขหน้าอ้างอิง\n"
            "2. ห้ามสร้างเงื่อนไขหรือข้อเท็จจริงที่ไม่มีในข้อมูลที่ให้มา\n"
            f"3. หากข้อมูลที่ให้มาไม่ตอบคำถามเลย ให้ตอบเพียงข้อความ: {NOT_FOUND_TEXT}\n"
            "4. ห้ามใช้ Markdown (**, #, backtick) และห้ามใช้อีโมจิ คงชื่อฟิลด์และรหัสตามต้นฉบับ เช่น pol_status, 0100, 1000005"
        )
        user_prompt = f"คำถาม: {query}\n\nข้อมูลฟิลด์และเอกสาร IBSL ที่ค้นคืนได้:\n{context_str}\n\nกรุณาเรียบเรียงคำตอบภาษาไทยตามข้อบังคับ:"

        if gemini_api_key and is_valid_key_format(gemini_api_key):
            candidate_models = []
            env_model = os.getenv("GEMINI_MODEL", "").strip()
            if env_model:
                candidate_models.append(env_model)
            for m in ["gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-flash-latest"]:
                if m not in candidate_models:
                    candidate_models.append(m)

            for model_name in candidate_models:
                try:
                    from google import genai
                    from google.genai import types
                    client = genai.Client(api_key=gemini_api_key, http_options={"timeout": 30000})
                    resp = client.models.generate_content(
                        model=model_name,
                        contents=user_prompt,
                        config=types.GenerateContentConfig(
                            system_instruction=system_instruction,
                            temperature=0.1,
                            max_output_tokens=1500,
                        )
                    )
                    text = clean_plain_thai_text(resp.text or "")
                    if text:
                        print(f"[RAGEngine] Synthesized answer with Gemini ({model_name})")
                        return text, f"Gemini ({model_name})"
                except Exception as e:
                    print(f"[RAGEngine] Gemini ({model_name}) failed: {e}")

        if openai_api_key and is_valid_key_format(openai_api_key):
            try:
                import httpx
                model_name = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
                resp = httpx.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {openai_api_key}", "Content-Type": "application/json"},
                    json={
                        "model": model_name,
                        "messages": [
                            {"role": "system", "content": system_instruction},
                            {"role": "user", "content": user_prompt}
                        ],
                        "temperature": 0.1,
                        "max_tokens": 1500
                    },
                    timeout=30.0
                )
                if resp.status_code == 200:
                    text = clean_plain_thai_text(resp.json()["choices"][0]["message"]["content"])
                    print(f"[RAGEngine] Synthesized answer with OpenAI ({model_name})")
                    return text, f"OpenAI ({model_name})"
            except Exception as e:
                print(f"[RAGEngine] OpenAI synthesis failed: {e}")

        # Deterministic fallback
        return self._rule_based_answer(query, retrieved_items), "Rule-based fallback"

    def _rule_based_answer(self, query: str, retrieved_items: List[Dict[str, Any]]) -> str:
        top = retrieved_items[0]
        fn = top.get("field_name", "")
        tbl = top.get("table", "")
        desc = top.get("desc", "")
        cond = top.get("condition") or "ไม่มีเงื่อนไขเพิ่มเติม"
        req = top.get("required_label", "")

        has_ibsl = top.get("has_ibsl")
        topics = top.get("ibsl_topics", [])
        primary = topics[0] if topics else None

        part1 = f"ฟิลด์ {fn} อยู่ในตาราง {tbl} ({req}) หมายถึง {desc}"
        if primary and primary.get("problem"):
            part1 += f" โดยในเอกสาร IBSL มีข้อกำหนดเฉพาะคือ {re.sub(r'\s+', ' ', primary['problem'])}"

        part2 = f"ฟิลด์ {fn} ประเภท {top.get('dtype')} ความยาว {top.get('length')} เงื่อนไขโครงสร้าง: {cond}"
        if top.get("ibsl_only"):
            part1 = part1.replace(f"อยู่ในตาราง {tbl} ({req})", f"({tbl}) ไม่มีในข้อกำหนด Fields_4.0 พบเฉพาะในเอกสาร IBSL")
            part2 = f"ฟิลด์ {fn} ไม่มีข้อกำหนดโครงสร้าง (ประเภท/ความยาว) ใน Fields_4.0"
        if primary and primary.get("conditions"):
            part2 += f" และตาม Business Rule ในเอกสาร IBSL: {re.sub(r'\s+', ' ', primary['conditions'])}"

        part3 = ""
        if primary and (primary.get("valid_examples") or primary.get("invalid_examples")):
            part3 = f"ตัวอย่างที่ถูกต้อง: {primary.get('valid_examples') or '-'} | ตัวอย่างที่ไม่ถูกต้อง: {primary.get('invalid_examples') or '-'}"
        else:
            part3 = f"ตัวอย่างข้อมูลที่ถูกต้องตามมาตรฐาน: {top.get('example') or '-'}"
        if primary:
            checks = [ln for tb in primary.get("validation_tables", []) for ln in table_evidence(tb)
                      if ln.startswith("ผลการตรวจสอบ")]
            if checks:
                part3 += "\n" + "\n".join(checks)

        part4 = ""
        if primary and primary.get("guidance"):
            part4 = re.sub(r"[\s\n]+", " ", primary["guidance"])
        elif top.get("ibsl_only"):
            part4 = f"ตรวจสอบให้ค่าของฟิลด์ {fn} เป็นไปตามเงื่อนไขและตัวอย่างในเอกสาร IBSL ข้างต้น"
        else:
            part4 = f"ตรวจสอบให้ค่าของฟิลด์ {fn} เป็นไปตามรูปแบบ {top.get('dtype')} ({top.get('length')}) และเงื่อนไขที่กำหนด"

        part5 = f"ตาราง {tbl}, ฟิลด์ {fn}" if not top.get("ibsl_only") else f"ฟิลด์ {fn} (ไม่มีในข้อกำหนด Fields_4.0)"
        if primary:
            version = self.page_info.get("doc_info", {}).get("version")
            part5 += (f", เอกสาร {primary.get('doc_name', IBSL_PDF_NAME)}" + (f" เวอร์ชัน {version}" if version else "")
                      + f" หัวข้อ {primary.get('section_no')} (รหัสคำถาม {primary.get('question_code') or '-'}) หน้า {primary.get('page_range')}")

        return (
            f"1) สรุปคำตอบ:\n{part1}\n\n"
            f"2) รายละเอียดและเงื่อนไข:\n{part2}\n\n"
            f"3) ตัวอย่างข้อมูล:\n{part3}\n\n"
            f"4) แนวทางแก้ไข:\n{part4}\n\n"
            f"5) แหล่งอ้างอิง:\n{part5}"
        )

    # ------------------------------------------------------------ external AI agent
    def external_ai_search(self, query: str, context_items: Optional[List[Dict[str, Any]]] = None,
                           field: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        AI Agent searching external credible websites (Tavily) for complementary recommendations.
        With `field`, the search is about that one field (name + description), for its result card;
        otherwise the top retrieved field names are the context. Clearly separated from IBSL rules.
        Successful results are cached per search text, so repeated searches do not spend Tavily quota.
        """
        _, _, tavily_api_key = get_api_keys()
        if not tavily_api_key or not is_valid_key_format(tavily_api_key):
            return {
                "status": "unconfigured",
                "message": (
                    "สถานะคำแนะนำภายนอก: ยังไม่ได้ตั้งค่า Web Search API Key ในไฟล์ .env (เช่น TAVILY_API_KEY) "
                    "ระบบจึงไม่ได้ดึงคำแนะนำเพิ่มเติมจากภายนอก โดยคำตอบหลักจากฐานข้อมูลเอกสาร IBSL ยังคงทำงานได้ตามปกติ"
                ),
                "sources": []
            }
        if field:
            context_terms = [field.get("field_name", ""), field.get("desc", "")]
        else:
            context_terms = [it.get("field_name", "") for it in (context_items or [])[:3]]
        search_query = f"คปภ IBS-Life ประกันชีวิต นำส่งข้อมูล {query} {' '.join(dict.fromkeys(t for t in context_terms if t))}".strip()
        if search_query in self.external_cache:
            return dict(self.external_cache[search_query], cached=True)
        result = self._tavily_search(search_query, tavily_api_key)
        if result["status"] in ("success", "no_results"):
            if len(self.external_cache) >= EXTERNAL_CACHE_MAX:
                self.external_cache.pop(next(iter(self.external_cache)))
            self.external_cache[search_query] = result
        return result

    def _tavily_search(self, search_query: str, tavily_api_key: str) -> Dict[str, Any]:
        try:
            import httpx
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
                results = resp.json().get("results", [])
                if not results:
                    return {"status": "no_results",
                            "message": "ไม่พบข้อมูลภายนอกที่ตรงกับคำถามในเว็บไซต์ทางการที่เชื่อถือได้",
                            "sources": []}
                sources, snippets = [], []
                for r in results:
                    sources.append({"title": r.get("title", "แหล่งข้อมูลภายนอก"), "url": r.get("url", "")})
                    snippets.append(f"{r.get('title')}: {clean_plain_thai_text(r.get('content', ''))}")
                advice_text = (
                    "คำแนะนำเพิ่มเติมจากแหล่งข้อมูลภายนอก (ไม่ใช่ข้อกำหนดในเอกสาร IBSL):\n\n"
                    + "\n\n".join(snippets[:2])
                    + "\n\nข้อควรระวัง: ข้อมูลภายนอกนี้เป็นเพียงคำแนะนำประกอบ ไม่ถือเป็นข้อกำหนดของเอกสาร IBSL "
                    "หากพบข้อขัดแย้งให้ยึดเอกสาร IBSL เป็นหลัก"
                )
                return {"status": "success", "message": advice_text, "sources": sources}
            elif resp.status_code == 429:
                return {"status": "quota_exceeded",
                        "message": "บริการค้นหาภายนอกเกินโควตาการใช้งาน (429 Rate Limit) โดยคำตอบจากเอกสาร IBSL ยังคงแสดงได้ตามปกติ",
                        "sources": []}
            return {"status": "error", "message": f"บริการค้นหาภายนอกส่งกลับรหัสสถานะ {resp.status_code}", "sources": []}
        except Exception as e:
            return {"status": "error",
                    "message": f"การค้นหาข้อมูลภายนอกขัดข้องหรือหมดเวลา ({str(e)[:60]}) โดยระบบยังคงแสดงคำตอบจากเอกสาร IBSL ได้ตามปกติ",
                    "sources": []}

    # ------------------------------------------------------------ Ground Truth evaluation
    def _evaluation_signature(self) -> str:
        return "|".join([
            data_signature(),
            file_sig(GROUND_TRUTH_FILE),
            self.embedding_method,
            self.gemini.model if self.gemini else "",
            f"k={TOP_K}|rel=0.60|fields={len(self.fields)}"
        ])

    def load_ground_truth(self) -> List[Dict[str, Any]]:
        if not os.path.exists(GROUND_TRUTH_FILE):
            return []
        try:
            with open(GROUND_TRUTH_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return []
        cases = data.get("cases", []) if isinstance(data, dict) else data
        return [c for c in cases if isinstance(c, dict)]

    def evaluate_ground_truth(self, force: bool = False) -> Dict[str, Any]:
        """
        Academic Information Retrieval Benchmark Evaluation:
        Precision@3, Recall@3, F1@3 calculated against data_rag/ground_truth_ibsl.json.
        Per IR theory:
          Precision = TP / (TP + FP) measures the fraction of retrieved items that are relevant (exactness).
          Recall    = TP / (TP + FN) measures the fraction of benchmark relevant items that were retrieved (completeness).
          When retrieval returns confident, highly-specific items, Precision is naturally higher than Recall (Precision > Recall).
        """
        empty = {"available": False, "message": "ยังไม่มีผลการประเมิน", "test_count": 0}
        if not self.is_ready:
            return empty
        cases = self.load_ground_truth()
        if not cases:
            return empty

        sig = self._evaluation_signature()
        if not force and os.path.exists(EVAL_RESULT_FILE):
            try:
                with open(EVAL_RESULT_FILE, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                if cached.get("signature") == sig:
                    return cached["result"]
            except Exception:
                pass

        results, ps, rs, fs = [], [], [], []
        for c in cases:
            q = (c.get("query") or "").strip()
            relevant = list(dict.fromkeys(c.get("expected_item_ids") or c.get("relevant_doc_ids") or []))
            if not q or not relevant:
                continue

            # Confident benchmark retrieval (k=3, rel_threshold=0.60)
            retrieved_items = self.retrieve(q, k=TOP_K, rel_threshold=0.60)
            retrieved = [it["item_id"] for it in retrieved_items]

            rel_set, ret_set = set(relevant), set(retrieved)
            tp = len(ret_set & rel_set)
            fp = len(ret_set - rel_set)
            fn = len(rel_set - ret_set)

            p = tp / (tp + fp) if (tp + fp) > 0 else 0.0
            r = tp / (tp + fn) if (tp + fn) > 0 else 0.0
            f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0.0

            ps.append(p)
            rs.append(r)
            fs.append(f1)

            results.append({
                "test_id": c.get("test_id", ""),
                "query": q,
                "category": c.get("category", ""),
                "expected_ids": relevant[:1],
                "relevant_doc_ids": relevant,
                "retrieved_ids": retrieved,
                "tp": tp, "fp": fp, "fn": fn,
                "precision": round(p, 4),
                "recall": round(r, 4),
                "f1": round(f1, 4),
                "verified": bool(c.get("verified")),
                "note": f"TP={tp}, FP={fp}, FN={fn}",
            })

        if not results:
            return empty

        macro_p = sum(ps) / len(ps)
        macro_r = sum(rs) / len(rs)
        macro_f1 = sum(fs) / len(fs)

        result = {
            "available": True,
            "test_count": len(results),
            "verified_count": sum(1 for x in results if x["verified"]),
            "macro_precision_at_3": round(macro_p, 4),
            "macro_recall_at_3": round(macro_r, 4),
            "macro_f1_at_3": round(macro_f1, 4),
            "evaluated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "embedding_method": self.embedding_label(),
            "ground_truth_file": os.path.relpath(GROUND_TRUTH_FILE, ROOT_DIR),
            "test_cases": results,
            "explanation": (
                "Precision@3, Recall@3 และ F1@3 คำนวณจากผลการค้นคืนเปรียบเทียบกับชุดทดสอบมาตรฐาน Ground Truth "
                "ตามหลักการ Information Retrieval โดย Precision คำนวณจากความถูกต้องของรายการที่ค้นคืน (TP / (TP+FP)) "
                "และ Recall คำนวณจากความครบถ้วนของคำตอบที่ครอบคลุม (TP / (TP+FN))"
            ),
        }

        try:
            with open(EVAL_RESULT_FILE, "w", encoding="utf-8") as f:
                json.dump({"signature": sig, "result": result}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[RAGEngine] Could not save evaluation result: {e}")

        return result


rag_engine = RAGEngine()
