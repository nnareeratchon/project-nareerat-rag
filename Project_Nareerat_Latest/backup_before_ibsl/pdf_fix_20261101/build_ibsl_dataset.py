"""
Build the IBSL knowledge base used by the RAG engine from the PDF
`data/IBSL-PR-INC_001_V1.0_25690813(1).pdf` (Incident & Problem Analysis and Resolution Procedure).

Output (data_rag/, generated - do not hand-edit):
  ibsl_pages.json   every page of the PDF (all 133) with its cleaned text and the topic(s) it belongs to
  ibsl_items.json   one record per topic (1.1.1 ... 1.11.3), multi-page text linked together, split into
                    problem / conditions / invalid / valid / guidance / validation table / payload
  ibsl_chunks.jsonl retrieval chunks (one per topic per page) used to build the embedding index

Run:  python scripts/build_ibsl_dataset.py
Requires: pypdf
"""
import json
import os
import re
import sys
from collections import OrderedDict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PDF_NAME = "IBSL-PR-INC_001_V1.0_25690813(1).pdf"
PDF_PATH = os.path.join(ROOT, "data", PDF_NAME)
OUT_DIR = os.path.join(ROOT, "data_rag")

DOC_TITLE = ("เอกสารวิธีการปฏิบัติในการวิเคราะห์และแก้ไขปัญหา (Incident & Problem Analysis and Resolution Procedure) "
             "โครงการพัฒนาระบบฐานข้อมูลการประกันภัย-ชีวิต (Insurance Bureau System – Life)")

# Category (second-level heading) -> English key + Thai label, as printed in the document.
CATEGORIES = {
    "1.1": ("Policy", "กรมธรรม์ประกันชีวิต"),
    "1.2": ("Insured", "ผู้เอาประกัน"),
    "1.3": ("Sub Policy", "ความคุ้มครอง สัญญาหลัก สัญญาเพิ่มเติม หรือ บันทึกสลักหลัง"),
    "1.4": ("Policy Coverage Plan Sequence", "ลำดับแผน"),
    "1.5": ("Coverage Plan Coverage", "ความคุ้มครองภายใต้ลำดับแผน"),
    "1.6": ("Coverage", "ความคุ้มครอง"),
    "1.7": ("Payment", "การชำระเบี้ย"),
    "1.8": ("Payment Period Seq", "งวดการชำระเบี้ย"),
    "1.9": ("Claim", "การเคลม"),
    "1.10": ("Estimate", "การประมาณการเคลม"),
    "1.11": ("Claim Payment", "การจ่ายเคลม"),
}

HEADER_LINES = (
    "เอกสารวิธีการปฏิบัติในการวิเคราะห์และแก้ไขปัญหา",
    "(Incident & Problem Analysis and Resolution Procedure)",
    "โครงการพัฒนาระบบฐานข้อมูลการประกันภัย-ชีวิต (Insurance Bureau System – Life)",
)
RE_PAGE_NO = re.compile(r"^หน้าที่\s*\|\s*\d+$")
RE_TOPIC = re.compile(r"^(1\.\d{1,2}\.\d{1,2})\s+(\S.*)$")
RE_CATEGORY = re.compile(r"^(1\.\d{1,2})\s+ข้อกำหนดทางธุรกิจ")
RE_QCODE_LINE = re.compile(r"^รหัสคำถาม\s*:\s*(IBSLFAQ_[A-Z]+_\d+)")
RE_FIELD = re.compile(r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b")
RE_ERROR = re.compile(r"Error\s*code\s*[\"“”']?\s*(\d{5,})[\"“”']?\s*(?:message\s*[\"“”']([^\"“”']+)[\"“”'])?", re.I)
FIRST_CONTENT_PAGE = 6  # pages 1-5 are cover, version control and table of contents

# Section markers inside a topic (checked on stripped lines, in this order)
MARKERS = [
    ("payload", re.compile(r"^ตัวอย่างการส่งข้อมูลการนำส่ง\s*Trans", re.I)),
    ("validation_table", re.compile(r"^ตัวอย่างข้อมูลสำหรับการนำส่ง")),
    ("invalid_examples", re.compile(r"^ตัวอย่าง(การส่ง)?ข้อมูล\s*ที่ไม่ถูกต้อง")),
    ("valid_examples", re.compile(r"^ตัวอย่าง(การส่ง)?ข้อมูล\s*ที่ถูกต้อง")),
    ("conditions", re.compile(r"^(โดยมี)?เงื่อนไข|^รูปแบบการจัดส่งข้อมูล")),
    ("guidance", re.compile(r"^(คำแนะนำ|หมายเหตุ|ข้อเสนอแนะ|แนวทาง)")),
]
SECTION_LABELS = OrderedDict([
    ("problem", "คำอธิบายปัญหา"),
    ("conditions", "เงื่อนไขการส่งข้อมูล / Business Rule"),
    ("invalid_examples", "ตัวอย่างข้อมูลที่ไม่ถูกต้อง"),
    ("valid_examples", "ตัวอย่างข้อมูลที่ถูกต้อง"),
    ("guidance", "แนวทางแก้ไข / คำชี้แจงสำหรับการปฏิบัติ"),
    ("validation_table", "ตัวอย่างข้อมูลสำหรับการนำส่งและผลการตรวจสอบ"),
    ("payload", "ตัวอย่าง Payload (Transaction M)"),
])


def clean_line(s: str) -> str:
    s = s.replace("\u00a0", " ").replace("\t", " ")
    s = re.sub(r"(?:^|(?<=\s))à(?=\s)", "→", s)  # arrow glyph extracted as 'à' on some pages
    s = re.sub(r"[ ]{2,}", " ", s)
    return s.strip()


def read_pages():
    from pypdf import PdfReader
    reader = PdfReader(PDF_PATH)
    pages = []
    for i, p in enumerate(reader.pages, start=1):
        raw = p.extract_text() or ""
        lines = []
        for ln in raw.splitlines():
            c = clean_line(ln)
            if not c or c in HEADER_LINES or RE_PAGE_NO.match(c):
                continue
            lines.append(c)
        pages.append({"page": i, "lines": lines, "char_count": len(raw)})
    return pages


def split_topics(pages):
    """Walk pages 6+ line by line; a numbered heading (1.x.y) opens a new topic. Topics spanning pages are linked."""
    topics = []
    current = None
    category = None
    for pg in pages:
        if pg["page"] < FIRST_CONTENT_PAGE:
            continue
        lines = pg["lines"]
        i = 0
        while i < len(lines):
            ln = lines[i]
            mcat = RE_CATEGORY.match(ln)
            if mcat:
                category = mcat.group(1)
                i += 1
                continue
            m = RE_TOPIC.match(ln)
            if m and m.group(1).rsplit(".", 1)[0] in CATEGORIES:
                title = m.group(2)
                j = i + 1
                # Headings can wrap: join following lines until the question-code line (max 3 lines)
                while j < len(lines) and j - i <= 3 and not RE_QCODE_LINE.match(lines[j]):
                    title += " " + lines[j]
                    j += 1
                # Layout artefact: the tail of the previous topic is sometimes printed after this heading,
                # under the previous question-code line. Give those lines back to the previous topic.
                qidx = [k for k in range(j, len(lines)) if RE_QCODE_LINE.match(lines[k])]
                if current is not None and len(qidx) >= 2 and qidx[0] == j:
                    tail = lines[qidx[0] + 1:qidx[1]]
                    if len([t for t in tail if t]) <= 6 and RE_QCODE_LINE.match(lines[qidx[0]]).group(1) == current["question_code"]:
                        current["segments"].append((pg["page"], tail))
                        j = qidx[1]
                current = {
                    "section_no": m.group(1),
                    "category_no": m.group(1).rsplit(".", 1)[0],
                    "title": title.strip(),
                    "question_code": "",
                    "segments": [],
                }
                topics.append(current)
                i = j
                continue
            if current is not None:
                mq = RE_QCODE_LINE.match(ln)
                if mq and not current["question_code"]:
                    current["question_code"] = mq.group(1)
                if not current["segments"] or current["segments"][-1][0] != pg["page"]:
                    current["segments"].append((pg["page"], []))
                current["segments"][-1][1].append(ln)
            i += 1
    for t in topics:
        for _, seg in t["segments"]:
            for ln in seg:
                mq = RE_QCODE_LINE.match(ln)
                if mq:
                    t["question_code"] = mq.group(1)
                    break
            if t["question_code"]:
                break
    return topics


def split_sections(lines):
    """Assign every line of a topic to one section. Nothing is dropped: unlabeled lines go to problem/guidance."""
    sections = OrderedDict((k, []) for k in SECTION_LABELS)
    state = "problem"
    problem_closed = False
    for ln in lines:
        mq = RE_QCODE_LINE.match(ln)
        if mq:
            rest = ln[mq.end():].replace("ความคิดเห็น / คำชี้แจงสำหรับการปฏิบัติ", "").strip()
            if not rest:
                continue
            ln = rest
        new_state = None
        for name, rx in MARKERS:
            if rx.search(ln):
                new_state = name
                break
        if new_state:
            state = new_state
            if new_state != "problem":
                problem_closed = True
            # keep text that follows the marker on the same line (e.g. "ตัวอย่างข้อมูลที่ถูกต้อง กรณี ...")
            for name, rx in MARKERS:
                if name == new_state:
                    m = rx.search(ln)
                    rest = ln[m.end():].strip(" :")
                    if new_state in ("conditions", "guidance"):
                        sections[state].append(ln)
                    elif new_state in ("invalid_examples", "valid_examples") and rest.startswith("กรณี"):
                        sections[state].append(rest)
            continue
        if state == "problem" and problem_closed:
            state = "guidance"
        if state in ("invalid_examples", "valid_examples") and not ln.startswith("•") and ln.startswith("กรณี") and '"' not in ln:
            state = "guidance"
        if state == "payload" and ln.strip() == "}":
            sections["payload"].append(ln)
            state = "validation_table"
            continue
        sections[state].append(ln)
        if state == "problem" and re.search(r"(อย่างไร|หรือไม่|\?)\s*$", ln):
            problem_closed = True
    # Some topics print the question under the "invalid example" label (e.g. 1.9.7):
    # move the leading question sentence to problem, and the answer that follows to guidance.
    if not sections["problem"] and sections["invalid_examples"]:
        inv = sections["invalid_examples"]
        for k, ln in enumerate(inv[:5]):
            if ln.startswith("•") or '"' in ln:
                break
            if re.search(r"(อย่างไร|หรือไม่|\?)\s*$", ln):
                sections["problem"] = inv[:k + 1]
                sections["guidance"] = inv[k + 1:] + sections["guidance"]
                sections["invalid_examples"] = []
                break
    return OrderedDict((k, "\n".join(v).strip()) for k, v in sections.items())


def build():
    if not os.path.exists(PDF_PATH):
        sys.exit(f"PDF not found: {PDF_PATH}")
    pages = read_pages()
    topics = split_topics(pages)

    page_topics = {p["page"]: [] for p in pages}
    items, chunks = [], []
    for t in topics:
        item_id = f"IBSL-{t['section_no']}"
        cat_en, cat_th = CATEGORIES[t["category_no"]]
        title_fields = list(dict.fromkeys(RE_FIELD.findall(t["title"])))
        all_lines = [ln for _, seg in t["segments"] for ln in seg]
        body = "\n".join(ln for ln in all_lines
                         if not (RE_QCODE_LINE.match(ln) and ln.endswith("คำชี้แจงสำหรับการปฏิบัติ")))
        page_list = sorted({p for p, seg in t["segments"] if seg})
        for p in page_list:
            page_topics[p].append(item_id)
        mentioned = [f for f in dict.fromkeys(RE_FIELD.findall(body)) if f not in title_fields]
        errors = []
        for m in RE_ERROR.finditer(body.replace("\n", " ")):
            e = {"code": m.group(1), "message": (m.group(2) or "").strip()}
            if e not in errors:
                errors.append(e)
        sections = split_sections(all_lines)
        page_range = f"{page_list[0]}" if len(page_list) == 1 else f"{page_list[0]}-{page_list[-1]}"
        source = (f"{PDF_NAME} [หมวด: {cat_en}, หัวข้อ: {t['section_no']}, "
                  f"รหัสคำถาม: {t['question_code'] or '-'}, หน้า: {page_range}]")
        item = {
            "item_id": item_id,
            "doc_name": PDF_NAME,
            "section_no": t["section_no"],
            "category_no": t["category_no"],
            "category": cat_en,
            "category_th": cat_th,
            "question_code": t["question_code"],
            "topic_title": t["title"],
            "field_names": title_fields,
            "mentioned_fields": mentioned,
            "error_codes": errors,
            "pages": page_list,
            "page_range": page_range,
            "source": source,
            "full_text": body,
        }
        item.update(sections)
        items.append(item)

        header = f"{t['section_no']} {t['title']} ({cat_en} / {cat_th}) รหัสคำถาม {t['question_code']}"
        for p, seg in t["segments"]:
            text = "\n".join(seg).strip()
            if not text:
                continue
            chunks.append({
                "chunk_id": f"{item_id}#p{p}",
                "item_id": item_id,
                "page": p,
                "text": f"{header}\n{text}",
            })

    page_records = [{
        "page": p["page"],
        "text": "\n".join(p["lines"]),
        "kind": ("front_matter" if p["page"] < FIRST_CONTENT_PAGE else "content"),
        "item_ids": page_topics[p["page"]],
    } for p in pages]

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "ibsl_items.json"), "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    with open(os.path.join(OUT_DIR, "ibsl_pages.json"), "w", encoding="utf-8") as f:
        json.dump({"doc_name": PDF_NAME, "doc_title": DOC_TITLE, "page_count": len(pages), "pages": page_records},
                  f, ensure_ascii=False, indent=2)
    with open(os.path.join(OUT_DIR, "ibsl_chunks.jsonl"), "w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    content_pages = [p for p in page_records if p["kind"] == "content"]
    uncovered = [p["page"] for p in content_pages if not p["item_ids"] and p["text"].strip()]
    print(f"PDF pages read: {len(pages)}")
    print(f"Topics: {len(items)} | chunks: {len(chunks)}")
    print(f"Content pages {FIRST_CONTENT_PAGE}-{len(pages)} without a topic: {uncovered or 'none'}")
    for it in items:
        empty = [k for k in SECTION_LABELS if not it.get(k)]
        print(f"  {it['section_no']:7} {it['question_code']:22} p.{it['page_range']:8} fields={it['field_names']} empty={empty}")


if __name__ == "__main__":
    build()
