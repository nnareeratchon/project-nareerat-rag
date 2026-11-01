"""
Build the IBSL knowledge base used by the RAG engine from the PDF
`data/IBSL-PR-INC_001_V1.0_25690813(1).pdf` (Incident & Problem Analysis and Resolution Procedure).

Output (data_rag/, generated - do not hand-edit):
  ibsl_pages.json   every page of the PDF (all 133) with its cleaned text and the topic(s) it belongs to,
                    plus doc_info (version, author, approver, contract) read from the front matter
  ibsl_items.json   one record per topic (1.1.1 ... 1.11.3), multi-page text linked together, split into
                    problem / conditions / invalid / valid / guidance / validation table / payload,
                    plus validation_tables: the example tables as structured rows / test cases / Markdown
  ibsl_chunks.jsonl retrieval chunks (one per topic per page + one per table + document info + table of contents),
                    each carrying its own metadata (section, category, question code, fields, page, source)
  ibsl_document.md  the whole document as Markdown, one section per topic (for review / Markdown loaders)
  IBSL_REPORT.md    verification report: completeness checks against the PDF and source-document anomalies

Run:  python scripts/build_ibsl_dataset.py
Requires: pypdf, pdfplumber (table borders), pypdfium2 (table cell text + character cross-check)
          -> pip install pypdf pdfplumber pypdfium2   (build-time only; the web app does not need them)
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

CATEGORY_HEADINGS = {}  # "1.1" -> heading line exactly as printed; filled by split_topics()

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
    s = re.sub(r"\b([A-Z]{2,}) -(\d)", r"\1-\2", s)  # letter-spaced codes: "PT -03" is printed "PT-03"
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
        pages.append({"page": i, "lines": lines, "char_count": len(raw), "raw": raw})
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
                CATEGORY_HEADINGS[category] = ln
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
    depth = 0  # bracket depth inside the JSON payload; nested objects/arrays must not end it early
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
            depth = 0
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
        if state == "payload":
            sections["payload"].append(ln)
            depth += ln.count("{") + ln.count("[") - ln.count("}") - ln.count("]")
            if depth <= 0 and ln.strip() in ("}", "]"):
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


THAI_MARKS = re.compile(r"[ัิ-ฺ็-๎]")  # above/below marks: belong to the preceding letter
RESULT_HEADER = "ผลการตรวจสอบ"
RE_RESULT = re.compile(r"^(ไม่ถูกต้อง|ถูกต้อง)\s*(.*)$")


def _cluster(values, gap=3):
    out = []
    for v in sorted(values):
        if not out or v - out[-1] > gap:
            out.append(v)
    return out


def _page_tables(plumber_page, pdfium_page):
    """Grid of every validation table on one page: [(bbox, [(row_top, row_bottom, [x splits])])].
    The question/answer frame (header row "รหัสคำถาม") and the small label boxes are not data tables."""
    H = plumber_page.height
    tp = pdfium_page.get_textpage()
    grids = []
    for t in plumber_page.find_tables():
        x0, top, x1, bot = t.bbox
        if top < 100 or x1 - x0 < 300:
            continue
        head = tp.get_text_bounded(left=x0, bottom=H - min(bot, top + 25), right=x1, top=H - top)
        if "รหัสคำถาม" in head:
            continue
        edges = plumber_page.edges
        hs = [e for e in edges if e["orientation"] == "h" and top - 1 <= e["top"] <= bot + 1]
        # a row border must cross both the first and the last column; shading boxes behind single text
        # lines (left labels, the "ผลการตรวจสอบ" cell) only cross one of them
        def crosses(x):
            return {round(e["top"]) for e in hs if e["x0"] <= x <= e["x1"]}
        ys = _cluster(crosses(x0 + 8) & crosses(x1 - 8) | {round(top), round(bot)})
        vs = [e for e in edges if e["orientation"] == "v" and x0 + 20 < e["x0"] < x1 - 20
              and e["top"] >= top - 1 and e["bottom"] <= bot + 1]
        rows = []
        for a, b in zip(ys, ys[1:]):
            mid = (a + b) / 2
            # cell shading draws extra lines a few points from the real border: merge lines closer than 12pt
            splits = _cluster((e["x0"] for e in vs if e["top"] <= mid <= e["bottom"]), gap=12)
            rows.append((a, b, [x0] + splits + [x1]))
        grids.append((t.bbox, rows))
    return grids


def _fill_cells(pdfium_page, H, grids):
    """Assign every character to the cell containing its centre; Thai marks follow their base letter."""
    tp = pdfium_page.get_textpage()
    cells = [[[[] for _ in range(len(splits) - 1)] for _, _, splits in rows] for _, rows in grids]
    last = None
    for i in range(tp.count_chars()):
        ch = tp.get_text_range(i, 1).replace("￾", "-")
        if not ch or ch.isspace():
            if last:
                last.append(" ")
            continue
        if THAI_MARKS.match(ch) and last is not None:
            last.append(ch)
            continue
        l, b, r, t = tp.get_charbox(i)
        cx, cy = (l + r) / 2, H - (b + t) / 2
        last = None
        for g, (_, rows) in enumerate(grids):
            for k, (a, z, splits) in enumerate(rows):
                if a <= cy < z and splits[0] <= cx <= splits[-1]:
                    c = max(j for j in range(len(splits) - 1) if splits[j] <= cx)
                    last = cells[g][k][c]
        if last is not None:
            last.append(ch)
    def norm(chars):
        s = re.sub(r"\s+", " ", "".join(chars)).strip()
        return re.sub(r"(?<=[฀-๿])\(", " (", s)  # "กรมธรรม์(pol_type)" is printed with a space
    return [[[norm(c) for c in row] for row in table] for table in cells]


def _label_field(label, prefer=()):
    """Field name of a label: the topic's own field if the label names it (labels can also mention a
    condition field, e.g. "วันที่เข้ารับการรักษา (claim_notify_date) กรณีระบุ ... (claim_hospital_name)"), else the last one."""
    fields = RE_FIELD.findall(label)
    own = [f for f in fields if f in prefer]
    return {"label": label, "field": own[0] if own else (fields[-1] if fields else "")}


def structure_table(raw_rows, topic_fields=()):
    """Raw cell rows -> typed rows: key (label | value), group (full-width entity heading),
    test_header (field(s) under test | ผลการตรวจสอบ) and test_case (value(s) | result)."""
    rows, group, header = [], "", None
    for cells in raw_rows:
        if not any(cells):
            continue
        if rows and rows[-1]["type"] == "test_header" and len(cells) == len(rows[-1]["columns"]) + 1 \
                and not cells[-1]:  # next line of a wrapped test header: append cell by cell
            for col, extra in zip(rows[-1]["columns"], cells):
                if extra:
                    col.update(_label_field(f"{col['label']} {extra}", topic_fields))
            continue
        if len(cells) == 1:
            m = re.search(r"\(([^()]+)\)\s*$", cells[0])
            group = m.group(1) if m else cells[0]
            rows.append({"type": "group", "text": cells[0], "entity": group})
            continue
        if cells[-1] == RESULT_HEADER:
            header = [_label_field(c, topic_fields) for c in cells[:-1]]
            rows.append({"type": "test_header", "group": group, "columns": header})
            continue
        m = RE_RESULT.match(cells[-1])
        if header is not None and m and len(cells) - 1 == len(header):
            rows.append({"type": "test_case", "group": group,
                         "values": {h["field"] or h["label"]: v for h, v in zip(header, cells[:-1])},
                         "result": m.group(1), "valid": m.group(1) == "ถูกต้อง", "note": m.group(2)})
            continue
        row = _label_field(cells[0])
        row.update({"type": "key", "group": group, "value": " | ".join(cells[1:])})
        rows.append(row)
    return rows


def table_markdown(table):
    out = []
    def esc(s):
        return str(s).replace("|", "\\|")
    keys = [r for r in table["rows"] if r["type"] in ("key", "group")]
    tests = [r for r in table["rows"] if r["type"] in ("test_header", "test_case")]
    out += ["| กลุ่มข้อมูล | รายการ (ฟิลด์) | ค่า |", "|---|---|---|"]
    for r in table["rows"]:
        if r["type"] == "group":
            out.append(f"| **{esc(r['text'])}** | | |")
        elif r["type"] == "key":
            out.append(f"| {esc(r['group'])} | {esc(r['label'])} | {esc(r['value'])} |")
    if tests and keys:
        out.append("")
    for r in tests:
        if r["type"] == "test_header":
            cols = [c["label"] for c in r["columns"]]
            out += ["| " + " | ".join(esc(c) for c in cols) + " | ผลการตรวจสอบ |",
                    "|" + "---|" * (len(cols) + 1)]
        else:
            res = r["result"] + (f" {r['note']}" if r["note"] else "")
            out.append("| " + " | ".join(esc(v) for v in r["values"].values()) + f" | {esc(res)} |")
    return "\n".join(out)


def extract_tables(items, page_topics):
    """Structured validation tables per topic. Returns (tables by item_id, check results)."""
    try:
        import pdfplumber
        import pypdfium2 as pdfium
    except ImportError:
        return None, []
    from collections import Counter
    by_item, problems = {}, []
    doc = pdfium.PdfDocument(PDF_PATH)
    item_by_id = {it["item_id"]: it for it in items}
    with pdfplumber.open(PDF_PATH) as pl:
        for pno in range(FIRST_CONTENT_PAGE, len(pl.pages) + 1):
            ppage, fpage = pl.pages[pno - 1], doc[pno - 1]
            grids = _page_tables(ppage, fpage)
            if not grids:
                continue
            filled = _fill_cells(fpage, ppage.height, grids)
            # on a page shared by two topics, a table above the later topic's heading belongs to the earlier one
            owners = page_topics.get(pno, [])
            words = ppage.extract_words()
            starts = {iid: min((w["top"] for w in words if w["text"] == item_by_id[iid]["section_no"]), default=0)
                      for iid in owners}
            tp = fpage.get_textpage()
            H = ppage.height
            for (bbox, _), raw in zip(grids, filled):
                owner = max((iid for iid in owners if starts[iid] <= bbox[1]), key=lambda i: starts[i], default=None)
                if owner is None:
                    problems.append(f"หน้า {pno}: ไม่ทราบว่าตารางเป็นของหัวข้อใด")
                    continue
                # completeness: same characters as the whole table area read by pdfium
                whole = Counter(c for c in tp.get_text_bounded(left=bbox[0], bottom=H - bbox[3], right=bbox[2],
                                                               top=H - bbox[1]).replace("￾", "-")
                                if not c.isspace())
                got = Counter(c for row in raw for cell in row for c in cell if not c.isspace())
                if whole != got:
                    problems.append(f"หน้า {pno} ({owner}): อักขระในตารางต่างจากพื้นที่ตาราง "
                                    f"ขาด {sum((whole - got).values())} เกิน {sum((got - whole).values())}")
                tables = by_item.setdefault(owner, [])
                # a table cut by a page break continues at the top of the next page
                if tables and tables[-1]["pages"][-1] == pno - 1 and tables[-1]["_bottom"] > 470 and bbox[1] < 180:
                    tables[-1]["pages"].append(pno)
                    tables[-1]["_raw"] += raw
                else:
                    tables.append({"pages": [pno], "_raw": list(raw)})
                tables[-1]["_bottom"] = bbox[3]
    for iid, tables in by_item.items():
        for k, t in enumerate(tables, start=1):
            t["table_id"] = f"{iid}#table{k}"
            t["rows"] = structure_table(t.pop("_raw"), item_by_id[iid]["field_names"])
            t.pop("_bottom")
            t["key_fields"] = [{"group": r["group"], "field": r["field"], "label": r["label"], "value": r["value"]}
                               for r in t["rows"] if r["type"] == "key"]
            heads = [r for r in t["rows"] if r["type"] == "test_header"]
            t["tested_fields"] = [c for h in heads for c in h["columns"]]
            t["test_cases"] = [{"values": r["values"], "result": r["result"], "valid": r["valid"], "note": r["note"]}
                               for r in t["rows"] if r["type"] == "test_case"]
            t["markdown"] = table_markdown(t)
            if heads and not t["test_cases"]:
                problems.append(f"{t['table_id']}: มีหัวตารางผลการตรวจสอบแต่ไม่มีกรณีทดสอบ")
    return by_item, problems


def parse_doc_info(pages):
    """Version-control page (page 2) and cover (page 1) -> document metadata."""
    cover = "\n".join(pages[0]["lines"])
    vc = "\n".join(pages[1]["lines"])
    info = {"doc_name": PDF_NAME, "doc_title": DOC_TITLE, "doc_code": "IBSL-PR-INC_001"}
    m = re.search(r"เวอร์ชันเอกสาร\s*([\d.]+)", vc)
    info["version"] = m.group(1) if m else ""
    m = re.search(r"สัญญาเลขที่\s*(\S+)\s*ลงวันที่\s*(.+)", cover)
    info["contract"] = f"{m.group(1)} ลงวันที่ {m.group(2).strip()}" if m else ""
    m = re.search(r"โดย\s*:\s*(.+)", cover)
    info["prepared_by_company"] = m.group(1).strip() if m else ""
    m = re.search(r"^1\.\s+(\S+\s+\S+)\s+(Business Analyst)\s+(.+)$", vc, re.M)
    if m:
        info["author"], info["author_role"], info["author_date"] = m.group(1), m.group(2), m.group(3).strip()
    m = re.search(r"^1\.\s+([\d.]+)\s+(\d{2}/\d{2}/\d{2})\s+(\S+)\s+(\S+\s+\S+)\s+(\S+\s+\S+)$", vc, re.M)
    if m:
        info["revision_history"] = [{"no": 1, "version": m.group(1), "date": m.group(2), "detail": m.group(3),
                                     "editor": m.group(4), "approver": m.group(5)}]
    return info


def question_code_notes(items):
    """Flag codes the PDF prints more than once, or whose prefix differs from the topic category.
    The code itself is kept exactly as printed; only a note is added."""
    by_code = {}
    for it in items:
        by_code.setdefault(it["question_code"], []).append(it["section_no"])
    prefix_by_cat = {"Policy": "POLICY", "Insured": "POLICY", "Sub Policy": "POLICY",
                     "Policy Coverage Plan Sequence": "POLICY", "Coverage Plan Coverage": "POLICY",
                     "Coverage": "POLICY", "Payment": "PAYMENT", "Payment Period Seq": "PAYMENT",
                     "Claim": "CLAIM", "Estimate": "CLAIM", "Claim Payment": "CLAIM"}
    for it in items:
        notes = []
        others = [s for s in by_code[it["question_code"]] if s != it["section_no"]]
        if others:
            notes.append(f"รหัสคำถาม {it['question_code']} ในเอกสารต้นฉบับซ้ำกับหัวข้อ {', '.join(others)} "
                         f"(คงไว้ตามที่พิมพ์ในเอกสาร ให้อ้างอิงด้วยเลขหัวข้อ {it['section_no']})")
        expected = prefix_by_cat[it["category"]]
        if it["question_code"] and f"_{expected}_" not in it["question_code"]:
            notes.append(f"รหัสคำถามขึ้นต้นด้วย {it['question_code'].split('_')[1]} แต่หัวข้ออยู่ในหมวด {it['category']} "
                         f"(คงไว้ตามที่พิมพ์ในเอกสาร)")
        it["question_code_note"] = " ; ".join(notes)


def cross_check_pdfium(raw_texts):
    """Compare per-page character counts (whitespace ignored) with pdfium. Returns (ok, list of differences)."""
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return None, []
    from collections import Counter
    doc = pdfium.PdfDocument(PDF_PATH)
    diffs = []
    for i, page in enumerate(doc, start=1):
        other = page.get_textpage().get_text_range()
        a = Counter(c for c in raw_texts[i - 1] if not c.isspace())
        b = Counter("-" if c == "￾" else c for c in other if not c.isspace())  # pdfium: soft hyphen at line break
        if a != b:
            diffs.append({"page": i, "missing": sum((b - a).values()), "extra": sum((a - b).values())})
    return not diffs, diffs


def toc_entries(pages):
    toc = " ".join(" ".join(p["lines"]) for p in pages[2:FIRST_CONTENT_PAGE - 1])
    return [(no, t.strip(), int(pg)) for no, t, pg in re.findall(r"(1\.\d+\.\d+)\s+(.+?)\s*\.{2,}\s*(\d+)", toc)]


def to_markdown(doc_info, items):
    out = [f"# {DOC_TITLE}", "",
           f"- ไฟล์: `{PDF_NAME}`", f"- เวอร์ชันเอกสาร: {doc_info.get('version', '')}",
           f"- จัดทำโดย: {doc_info.get('author', '')} ({doc_info.get('author_role', '')}), "
           f"{doc_info.get('prepared_by_company', '')}",
           f"- สัญญาเลขที่: {doc_info.get('contract', '')}", ""]
    cat = None
    for it in items:
        if it["category_no"] != cat:
            cat = it["category_no"]
            out += [f"## {cat} {it['category_th']} ({it['category']})", ""]
        out += [f"### {it['section_no']} {it['topic_title']}", "",
                f"- รหัสคำถาม: {it['question_code']}", f"- หน้า: {it['page_range']}",
                f"- ฟิลด์: {', '.join(it['field_names'])}"]
        if it["question_code_note"]:
            out.append(f"- หมายเหตุ: {it['question_code_note']}")
        out.append("")
        for key, label in SECTION_LABELS.items():
            if key == "validation_table" and it.get("validation_table_md"):
                out += [f"**{label}**", "", it["validation_table_md"], ""]
            elif it.get(key):
                fence = key == "payload"
                out += [f"**{label}**", ""] + (["```"] if fence else []) + [it[key]] + (["```"] if fence else []) + [""]
    return "\n".join(out)


def build():
    if not os.path.exists(PDF_PATH):
        sys.exit(f"PDF not found: {PDF_PATH}")
    pages = read_pages()
    topics = split_topics(pages)
    doc_info = parse_doc_info(pages)

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

    question_code_notes(items)
    item_by_id = {it["item_id"]: it for it in items}
    tables_by_item, table_problems = extract_tables(items, page_topics)
    for it in items:
        tables = (tables_by_item or {}).get(it["item_id"], [])
        it["validation_tables"] = tables
        it["validation_table_md"] = "\n\n".join(t["markdown"] for t in tables)
    for t in topics:
        it = item_by_id[f"IBSL-{t['section_no']}"]
        header = (f"{CATEGORY_HEADINGS.get(it['category_no'], it['category_no'])}\n"
                  f"{t['section_no']} {t['title']} ({it['category']} / {it['category_th']}) "
                  f"รหัสคำถาม {t['question_code']}")
        segs = [(p, "\n".join(seg).strip()) for p, seg in t["segments"]]
        segs = [(p, s) for p, s in segs if s]
        for k, (p, text) in enumerate(segs, start=1):
            chunks.append({
                "chunk_id": f"{it['item_id']}#p{p}",
                "item_id": it["item_id"],
                "page": p,
                "part": f"{k}/{len(segs)}",
                "section_no": it["section_no"],
                "category": it["category"],
                "category_th": it["category_th"],
                "question_code": it["question_code"],
                "topic_title": it["topic_title"],
                "field_names": it["field_names"],
                "source": f"{PDF_NAME} [หัวข้อ {it['section_no']}, หน้า {p}]",
                "text": f"{header}\n{text}",
            })
        # the same validation table again, as a Markdown table (clean rows/columns for the LLM)
        for tb in it["validation_tables"]:
            pr = "-".join(str(p) for p in (tb["pages"][0], tb["pages"][-1])) if len(tb["pages"]) > 1 else str(tb["pages"][0])
            chunks.append({
                "chunk_id": tb["table_id"],
                "item_id": it["item_id"],
                "page": tb["pages"][0],
                "part": "table",
                "section_no": it["section_no"],
                "category": it["category"],
                "category_th": it["category_th"],
                "question_code": it["question_code"],
                "topic_title": it["topic_title"],
                "field_names": it["field_names"],
                "source": f"{PDF_NAME} [หัวข้อ {it['section_no']}, ตารางตัวอย่างข้อมูล, หน้า {pr}]",
                "text": f"{header}\nตัวอย่างข้อมูลสำหรับการนำส่งและผลการตรวจสอบ (ตาราง)\n{tb['markdown']}",
            })

    # Document-level chunks so questions about the document itself (version, author, contents) can be answered
    rev = (doc_info.get("revision_history") or [{}])[0]
    doc_text = (f"{DOC_TITLE}\nรหัสเอกสาร {doc_info['doc_code']} เวอร์ชันเอกสาร {doc_info['version']}\n"
                f"จัดทำโดย {doc_info.get('author', '')} ({doc_info.get('author_role', '')}) "
                f"วันที่ {doc_info.get('author_date', '')} {doc_info.get('prepared_by_company', '')}\n"
                f"ประวัติการแก้ไข: เวอร์ชัน {rev.get('version', '')} วันที่ {rev.get('date', '')} "
                f"{rev.get('detail', '')} ผู้แก้ไข {rev.get('editor', '')} ผู้อนุมัติ {rev.get('approver', '')}\n"
                f"สัญญาเลขที่ {doc_info['contract']}\n"
                f"นำเสนอ สำนักงานคณะกรรมการกำกับและส่งเสริมการประกอบธุรกิจประกันภัย (สำนักงาน คปภ.)\n"
                f"เนื้อหา: คุณภาพข้อมูล (Quality Data) - ข้อกำหนดทางธุรกิจ ปัญหาที่พบ และแนวทางแก้ไขการนำส่งข้อมูล "
                f"{len(items)} หัวข้อ ใน {len(CATEGORIES)} หมวด")
    toc_lines = []
    for cno, (cen, cth) in CATEGORIES.items():
        toc_lines.append(f"{cno} {cth} ({cen})")
        toc_lines += [f"  {it['section_no']} {it['topic_title']} - หน้า {it['page_range']}"
                      for it in items if it["category_no"] == cno]
    for cid, pg, text in (("IBSL-DOC#info", "1-2", doc_text), ("IBSL-DOC#toc", "3-5", "สารบัญ\n" + "\n".join(toc_lines))):
        chunks.insert(0 if cid.endswith("info") else 1, {
            "chunk_id": cid, "item_id": "IBSL-DOC", "page": int(pg.split("-")[0]), "part": "1/1", "section_no": "",
            "category": "Document", "category_th": "ข้อมูลเอกสาร", "question_code": "", "topic_title": "",
            "field_names": [], "source": f"{PDF_NAME} [หน้า {pg}]", "text": text,
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
        json.dump({"doc_name": PDF_NAME, "doc_title": DOC_TITLE, "page_count": len(pages), "doc_info": doc_info,
                   "pages": page_records}, f, ensure_ascii=False, indent=2)
    with open(os.path.join(OUT_DIR, "ibsl_chunks.jsonl"), "w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    with open(os.path.join(OUT_DIR, "ibsl_document.md"), "w", encoding="utf-8") as f:
        f.write(to_markdown(doc_info, items))

    content_pages = [p for p in page_records if p["kind"] == "content"]
    uncovered = [p["page"] for p in content_pages if not p["item_ids"] and p["text"].strip()]
    print(f"PDF pages read: {len(pages)}")
    print(f"Topics: {len(items)} | chunks: {len(chunks)}")
    print(f"Content pages {FIRST_CONTENT_PAGE}-{len(pages)} without a topic: {uncovered or 'none'}")
    for it in items:
        empty = [k for k in SECTION_LABELS if not it.get(k)]
        print(f"  {it['section_no']:7} {it['question_code']:22} p.{it['page_range']:8} fields={it['field_names']} empty={empty}")
    write_report(pages, page_records, items, chunks, doc_info, tables_by_item, table_problems)


def write_report(pages, page_records, items, chunks, doc_info, tables_by_item, table_problems):
    """Completeness checks against the PDF; the result is printed and saved to IBSL_REPORT.md."""
    checks = []
    if tables_by_item is None:
        checks.append(("แปลงตารางเป็นข้อมูลมีโครงสร้าง", False, "ข้าม: ต้องติดตั้ง pdfplumber และ pypdfium2"))
    else:
        n_tables = sum(len(t) for t in tables_by_item.values())
        no_table = [it["section_no"] for it in items
                    if re.search(r"ตัวอย่างข้อมูลสำหรับการนำส่ง", it["full_text"]) and not it["validation_tables"]]
        if no_table:
            table_problems = table_problems + [f"หัวข้อ {', '.join(no_table)} มีหัวข้อตารางตัวอย่างแต่หาตารางไม่พบ"]
        n_cases = sum(len(t["test_cases"]) for ts in tables_by_item.values() for t in ts)
        checks.append(("ตารางตัวอย่างข้อมูล: อักขระครบทุกช่อง และทุกหัวข้อที่มีตารางหาตารางพบ", not table_problems,
                       f"{n_tables} ตาราง, กรณีทดสอบ {n_cases} แถว" +
                       (f" ปัญหา: {table_problems[:5]}" if table_problems else "")))
    # 1. every content line of every page ends up in exactly the chunks of that page
    chunk_text = {}
    for c in chunks:
        chunk_text.setdefault(c["page"], []).append(c["text"])
    lost = []
    for p in page_records:
        if p["kind"] != "content":
            continue
        joined = "\n".join(chunk_text.get(p["page"], []))
        lost += [(p["page"], ln) for ln in p["text"].split("\n")
                 if ln.strip() and ln not in joined and not RE_QCODE_LINE.match(ln)
                 and ln != "1. คุณภาพข้อมูล (Quality Data)"]  # repeated table header / top heading (in doc chunk)
    checks.append(("ทุกบรรทัดของหน้าเนื้อหา (6-133) อยู่ใน chunk ของหน้านั้น", not lost,
                   f"ขาด {len(lost)} บรรทัด: {lost[:5]}" if lost else "ครบทุกบรรทัด"))
    # 2. every line of a topic is kept in one of its sections (section labels themselves excepted)
    lost = []
    for it in items:
        secs = "\n".join(it[k] for k in SECTION_LABELS)
        for ln in it["full_text"].split("\n"):
            if not ln.strip() or RE_QCODE_LINE.match(ln) or any(rx.search(ln) for _, rx in MARKERS):
                continue
            if ln not in secs:
                lost.append((it["section_no"], ln))
    checks.append(("ทุกบรรทัดของหัวข้อถูกจัดเข้าส่วนย่อย (problem/conditions/...)", not lost,
                   f"ขาด {len(lost)} บรรทัด: {lost[:5]}" if lost else "ครบทุกบรรทัด"))
    # 3. topics vs table of contents
    toc = toc_entries(pages)
    by_no = {it["section_no"]: it for it in items}
    bad = [no for no, title, pg in toc
           if no not in by_no or re.sub(r"\s+", "", title) != re.sub(r"\s+", "", by_no[no]["topic_title"])
           or pg != by_no[no]["pages"][0]]
    checks.append(("หัวข้อ ชื่อหัวข้อ และหน้าเริ่มต้น ตรงกับสารบัญ", not bad and len(toc) == len(items),
                   f"สารบัญ {len(toc)} หัวข้อ / ที่ได้ {len(items)} หัวข้อ" + (f" ไม่ตรง: {bad}" if bad else "")))
    # 4. second extractor
    ok, diffs = cross_check_pdfium([p["raw"] for p in pages])
    if ok is None:
        checks.append(("ตรวจตัวอักษรเทียบกับ pdfium", True, "ข้าม (ไม่ได้ติดตั้ง pypdfium2)"))
    else:
        checks.append(("ตัวอักษรทุกหน้าตรงกับตัวแยกข้อความตัวที่สอง (pdfium)", ok,
                       f"{len(pages)} หน้าตรงกันทั้งหมด" if ok else f"ต่างกัน {len(diffs)} หน้า: {diffs[:5]}"))
    # 5. no leftover extraction artefacts
    leftovers = [(p["page"], ln) for p in page_records for ln in p["text"].split("\n")
                 if re.search(r"(?:^|\s)à(?:\s|$)|�|[-]", ln)]
    checks.append(("ไม่มีอักขระเสีย (à, �, Private Use)", not leftovers, f"{leftovers[:5]}" if leftovers else "ไม่พบ"))

    notes = [it for it in items if it["question_code_note"]]
    out = ["# รายงานตรวจสอบชุดข้อมูล IBSL (สร้างโดย scripts/build_ibsl_dataset.py)", "",
           f"ไฟล์ต้นฉบับ: `data/{PDF_NAME}` เวอร์ชันเอกสาร {doc_info.get('version', '')} "
           f"({len(pages)} หน้า: ปก/ควบคุมเวอร์ชัน/สารบัญ หน้า 1-{FIRST_CONTENT_PAGE - 1}, "
           f"เนื้อหา หน้า {FIRST_CONTENT_PAGE}-{len(pages)})", "",
           "## สรุปจำนวน", "",
           f"- หัวข้อ (ibsl_items.json): {len(items)} ใน {len(CATEGORIES)} หมวด",
           f"- chunk (ibsl_chunks.jsonl): {len(chunks)} (หัวข้อละหน้า {len(chunks) - 2} + ข้อมูลเอกสาร/สารบัญ 2)",
           f"- หน้า (ibsl_pages.json): {len(page_records)}", "",
           "## ผลการตรวจความครบถ้วน", "", "| การตรวจ | ผล | รายละเอียด |", "|---|---|---|"]
    out += [f"| {name} | {'ผ่าน' if ok else 'ไม่ผ่าน'} | {detail} |" for name, ok, detail in checks]
    out += ["", "## จุดผิดปกติในเอกสารต้นฉบับ (คงไว้ตามต้นฉบับ ไม่ได้แก้)", "",
            "รหัสคำถามบางรายการในเอกสารซ้ำกัน หรือขึ้นต้นไม่ตรงหมวด จึงใช้เลขหัวข้อ (`item_id` เช่น `IBSL-1.2.1`) "
            "เป็นคีย์อ้างอิงแทนรหัสคำถาม", "", "| หัวข้อ | รหัสคำถาม | หมายเหตุ |", "|---|---|---|"]
    out += [f"| {it['section_no']} | {it['question_code']} | {it['question_code_note']} |" for it in notes]
    mismatch = [(it, [c["field"] or c["label"] for t in it["validation_tables"] for c in t["tested_fields"]])
                for it in items]
    mismatch = [(it, tf) for it, tf in mismatch if tf and not set(tf) & set(it["field_names"])]
    if mismatch:
        out += ["", "ตารางตัวอย่างที่พิมพ์ชื่อฟิลด์ที่ทดสอบไม่ตรงกับฟิลด์ของหัวข้อ (คงไว้ตามต้นฉบับ "
                "ให้ยึดฟิลด์ของหัวข้อและ Payload เป็นหลัก)", "",
                "| หัวข้อ | ฟิลด์ของหัวข้อ | ฟิลด์ที่พิมพ์ในตาราง |", "|---|---|---|"]
        out += [f"| {it['section_no']} | {', '.join(it['field_names'])} | {', '.join(tf)} |" for it, tf in mismatch]
    out += ["", "## สิ่งที่ปรับระหว่างแปลงข้อมูล", "",
            "- ตัดหัว/ท้ายกระดาษที่ซ้ำทุกหน้า (ชื่อเอกสาร, \"หน้าที่ | n\")",
            "- ลูกศร → ที่ถูกอ่านเป็นตัว `à` แปลงกลับเป็น →",
            "- รหัสที่ถูกแยกด้วยช่องว่างจากการจัดระยะอักษร (เช่น `PT -03`) รวมกลับเป็น `PT-03` ตามที่พิมพ์",
            "- เนื้อหาหัวข้อที่ต่อหลายหน้าถูกรวมเป็นหัวข้อเดียว และแยก chunk ตามหน้าเพื่ออ้างอิงเลขหน้าได้",
            "- ตารางตัวอย่างข้อมูลแปลงเป็นข้อมูลมีโครงสร้างใน `validation_tables` ของแต่ละหัวข้อ "
            "(อ่านเส้นตารางด้วย pdfplumber และข้อความในช่องด้วย pdfium; ตารางที่ขึ้นหน้าใหม่ถูกต่อกัน):",
            "  - `rows` ทุกแถวตามลำดับในเอกสาร: `key` (label/field/value), `group` (หัวกลุ่มข้อมูล เช่น Sub Policy), "
            "`test_header` (ฟิลด์ที่ทดสอบ), `test_case` (ค่า → ผลการตรวจสอบ)",
            "  - `key_fields`, `tested_fields`, `test_cases` (values, result, valid true/false, note) สำหรับใช้งานตรง ๆ",
            "  - `markdown` ตารางเดียวกันในรูป Markdown (รวมไว้ใน `validation_table_md` และเป็น chunk `...#tableN`)",
            "- ฟิลด์ `validation_table` (ข้อความเดิม) ยังคงไว้เพื่อให้หน้าเว็บเดิมแสดงผลได้เหมือนเดิม", ""]
    with open(os.path.join(OUT_DIR, "IBSL_REPORT.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    print("\nVerification:")
    for name, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    print(f"  source-document code anomalies noted: {len(notes)} topics (see IBSL_REPORT.md)")


if __name__ == "__main__":
    build()
