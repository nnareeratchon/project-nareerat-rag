"""
Build a RAG-ready copy of the IBSL Policy 4.0 dataset.

Reads (never modifies):  data/Fields_4.0.csv, data/FAQ.xlsx, data/ground_truth.json
Writes:                  data_rag/  (fields.json, tables.json, faqs.json, glossary.json,
                                     corpus.jsonl, ground_truth.json, DATA_REPORT.md)

Run:  python scripts/build_rag_dataset.py
"""
import csv
import json
import os
import re
from collections import defaultdict

import openpyxl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(ROOT, "data")
OUT_DIR = os.path.join(ROOT, "data_rag")

# ตัด Payment และ Claim ออก — เหลือเฉพาะข้อมูล Policy
# (LoanPayment คือข้อมูล Policy จึงไม่ถูกตัด)
EXCLUDED_TABLE_PREFIXES = ("Payment", "Claim", "Estimate", "SIMB2")

FAQ_SHEETS = {  # sheet -> id prefix (same scheme as backend/rag_engine.py)
    "Policy": "FAQ-POL",
    "คู่มือการส่งกรมธรรม์": "FAQ-MAN",
    "อื่น ๆ": "FAQ-GEN",
}

# ---------------------------------------------------------------------------
# Hand-written domain knowledge (review with an IBSL expert before relying on it)
# ---------------------------------------------------------------------------

# Keyed by the English table name found in parentheses in Fields_4.0.csv
TABLE_META = {
    "Policy": {
        "key": "Policy", "entity": "กรมธรรม์", "container_dtype": None,
        "aliases": ["กรมธรรม์", "กรมธรรม์ประกันชีวิต", "policy", "สัญญาประกันภัย"],
    },
    "Customer": {
        "key": "Customer", "entity": "ผู้ชำระเบี้ยประกันภัย", "container_dtype": "Customer",
        "aliases": ["ผู้ซื้อประกัน", "ผู้ชำระเบี้ย", "ผู้จ่ายเบี้ย", "ลูกค้า", "customer", "payer"],
    },
    "Insured": {
        "key": "Insured", "entity": "ผู้เอาประกันภัย", "container_dtype": "[insureds]",
        "aliases": ["ผู้เอาประกัน", "ผู้เอาประกันภัย", "ผู้ทำประกัน", "สมาชิกประกันกลุ่ม", "insured"],
    },
    "InsuredBeneficiary": {
        "key": "InsuredBeneficiary", "entity": "ผู้รับผลประโยชน์ภายใต้ผู้เอาประกันภัย",
        "container_dtype": "[InsuredBeneficiary]",
        "aliases": ["ผู้รับผลประโยชน์ของผู้เอาประกัน", "insured beneficiary"],
    },
    "Sub Policy": {
        "key": "SubPolicy", "entity": "สัญญาหลัก/สัญญาเพิ่มเติม (Sub Policy)", "container_dtype": "[SubPolicy]",
        "aliases": ["สัญญาเพิ่มเติม", "สัญญาหลัก", "rider", "sub policy", "อนุสัญญา"],
    },
    "Policy Coverage Plan Sequence": {
        "key": "PolicyCovPlanSeq", "entity": "ลำดับแผนความคุ้มครอง", "container_dtype": "[PolicyCovPlanSeq]",
        "aliases": ["แผนประกัน", "ลำดับแผน", "แผนประกันกลุ่ม", "plan"],
    },
    "Coverage Plan Coverage": {
        "key": "CoveragePlanCoverage", "entity": "ความคุ้มครองภายใต้ลำดับแผน", "container_dtype": "[PlanCoverage]",
        "aliases": ["ความคุ้มครองตามแผน", "plan coverage"],
    },
    "Coverage": {
        "key": "Coverage", "entity": "ความคุ้มครอง", "container_dtype": "[SubPolicyCoverage]",
        "aliases": ["ความคุ้มครอง", "ทุนประกัน", "coverage"],
    },
    "Beneficiary": {
        "key": "Beneficiary", "entity": "ผู้รับผลประโยชน์", "container_dtype": "[Beneficiary]",
        "aliases": ["ผู้รับผลประโยชน์", "ผู้รับประโยชน์", "beneficiary"],
    },
    "Endorsement": {
        "key": "Endorsement", "entity": "สลักหลัง", "container_dtype": "[Endorsement]",
        "aliases": ["สลักหลัง", "บันทึกสลักหลัง", "endorsement"],
    },
    "Loan": {
        "key": "Loan", "entity": "การกู้ยืม", "container_dtype": "[Loan]",
        "aliases": ["กู้", "เงินกู้", "กู้ยืม", "กู้เงินกรมธรรม์", "loan"],
    },
    "Loan Payment": {
        "key": "LoanPayment", "entity": "การชำระคืนเงินกู้", "container_dtype": "[LoanPayment]",
        "aliases": ["ชำระคืนเงินกู้", "คืนเงินกู้", "จ่ายคืนเงินกู้", "loan payment"],
    },
    "Investment": {
        "key": "Investment", "entity": "การลงทุน", "container_dtype": "[Investment]",
        "aliases": ["การลงทุน", "ยูนิตลิงค์", "unit linked", "investment"],
    },
    "Policy Fund Code": {
        "key": "PolicyFundCode", "entity": "กองทุน", "container_dtype": "[PolicyFunCode]",
        "aliases": ["กองทุน", "หน่วยลงทุน", "NAV", "fund"],
    },
    "Payment": {
        "key": "Payment", "entity": "การชำระเบี้ย", "container_dtype": "[Payment]",
        "aliases": ["การชำระเบี้ย", "ชำระเบี้ย", "จ่ายเบี้ย", "payment", "เบี้ยประกัน"],
    },
    "Payment Period Seq": {
        "key": "PaymentPeriodSeq", "entity": "งวดการชำระเบี้ย", "container_dtype": "[PaymentPeriodSeq]",
        "aliases": ["งวดชำระเบี้ย", "งวดเบี้ย", "payment period"],
    },
    "Payment Premium Type Rider": {
        "key": "PaymentPremiumTypeRider", "entity": "การจ่ายเบี้ยสำหรับสัญญาเพิ่มเติม", "container_dtype": "[PaymentRider]",
        "aliases": ["จ่ายเบี้ยสัญญาเพิ่มเติม", "เบี้ยไรเดอร์", "payment rider"],
    },
    "Payment Premium Type Endorsement": {
        "key": "PaymentPremiumTypeEndorsement", "entity": "การจ่ายเบี้ยสำหรับบันทึกสลักหลัง", "container_dtype": "[PaymentEndorsement]",
        "aliases": ["จ่ายเบี้ยสลักหลัง", "เบี้ยสลักหลัง", "payment endorsement"],
    },
    "SIMB2": {
        "key": "SIMB2", "entity": "SIMB2", "container_dtype": None,
        "aliases": ["SIMB2", "simb"],
    },
    "Claim": {
        "key": "Claim", "entity": "การเคลม", "container_dtype": "[Claim]",
        "aliases": ["การเคลม", "เคลม", "สินไหม", "เรียกร้องสินไหม", "claim"],
    },
    "Claim Coverage": {
        "key": "ClaimCoverage", "entity": "การเคลมตามความคุ้มครอง", "container_dtype": "[ClaimCoverage]",
        "aliases": ["เคลมตามความคุ้มครอง", "สินไหมความคุ้มครอง", "claim coverage"],
    },
    "Estimate": {
        "key": "Estimate", "entity": "การประมาณการเคลม", "container_dtype": "[Estimate]",
        "aliases": ["ประมาณการเคลม", "สำรองสินไหม", "estimate"],
    },
    "Claim Payment": {
        "key": "ClaimPayment", "entity": "การจ่ายเคลม", "container_dtype": "[ClaimPayment]",
        "aliases": ["การจ่ายเคลม", "จ่ายสินไหม", "จ่ายเคลม", "claim payment"],
    },
}

# Field-name token -> (English expansion, Thai keywords)
GLOSSARY = {
    "pol": ("policy", ["กรมธรรม์"]),
    "policy": ("policy", ["กรมธรรม์"]),
    "policies": ("policies", ["กรมธรรม์"]),
    "sub": ("sub", ["ย่อย", "สัญญาเพิ่มเติม"]),
    "cust": ("customer", ["ผู้ชำระเบี้ย", "ผู้ซื้อ", "ลูกค้า"]),
    "customer": ("customer", ["ผู้ชำระเบี้ย", "ผู้ซื้อ", "ลูกค้า"]),
    "insrd": ("insured", ["ผู้เอาประกัน"]),
    "insured": ("insured", ["ผู้เอาประกัน"]),
    "insureds": ("insureds", ["ผู้เอาประกัน"]),
    "cov": ("coverage", ["ความคุ้มครอง"]),
    "coverage": ("coverage", ["ความคุ้มครอง"]),
    "coverages": ("coverages", ["ความคุ้มครอง"]),
    "inv": ("investment", ["การลงทุน"]),
    "investments": ("investments", ["การลงทุน"]),
    "endorse": ("endorsement", ["สลักหลัง"]),
    "endorsements": ("endorsements", ["สลักหลัง"]),
    "loan": ("loan", ["กู้", "เงินกู้"]),
    "loans": ("loans", ["กู้", "เงินกู้"]),
    "beneficiary": ("beneficiary", ["ผู้รับผลประโยชน์"]),
    "beneficiaries": ("beneficiaries", ["ผู้รับผลประโยชน์"]),
    "benefit": ("benefit", ["ผลประโยชน์"]),
    "renewal": ("renewal", ["ต่ออายุ"]),
    "amt": ("amount", ["จำนวนเงิน"]),
    "amount": ("amount", ["จำนวนเงิน"]),
    "date": ("date", ["วันที่"]),
    "start": ("start", ["เริ่มต้น"]),
    "end": ("end", ["สิ้นสุด"]),
    "id": ("id", ["เลขที่", "รหัส"]),
    "ids": ("ids", ["เลขที่", "รหัส"]),
    "seq": ("sequence", ["ลำดับ"]),
    "seqs": ("sequences", ["ลำดับ"]),
    "no": ("number", ["ลำดับ"]),
    "type": ("type", ["ประเภท"]),
    "status": ("status", ["สถานะ"]),
    "code": ("code", ["รหัส"]),
    "detail": ("detail", ["รายละเอียด"]),
    "name": ("name", ["ชื่อ"]),
    "first": ("first", ["ชื่อ"]),
    "last": ("last", ["นามสกุล"]),
    "birthday": ("birthday", ["วันเกิด"]),
    "age": ("age", ["อายุ"]),
    "gender": ("gender", ["เพศ"]),
    "address": ("address", ["ที่อยู่"]),
    "province": ("province", ["จังหวัด"]),
    "district": ("district", ["อำเภอ"]),
    "zip": ("zip", ["รหัสไปรษณีย์"]),
    "country": ("country", ["ประเทศ"]),
    "phone": ("phone", ["โทรศัพท์"]),
    "mobile": ("mobile", ["มือถือ"]),
    "email": ("email", ["อีเมล"]),
    "occupation": ("occupation", ["อาชีพ"]),
    "income": ("income", ["รายได้"]),
    "nationality": ("nationality", ["สัญชาติ"]),
    "religion": ("religion", ["ศาสนา"]),
    "race": ("race", ["เชื้อชาติ"]),
    "payment": ("payment", ["ชำระ"]),
    "payments": ("payments", ["ชำระ"]),
    "pmt": ("payment", ["ชำระเบี้ย", "การชำระ", "ชำระ", "จ่ายเบี้ย"]),
    "prd": ("period", ["งวด", "งวดการชำระ"]),
    "rid": ("rider", ["สัญญาเพิ่มเติม", "ไรเดอร์"]),
    "edm": ("endorsement", ["สลักหลัง"]),
    "claim": ("claim", ["เคลม", "สินไหม", "การเคลม"]),
    "hospital": ("hospital", ["โรงพยาบาล", "สถานพยาบาล"]),
    "estimate": ("estimate", ["ประมาณการ", "สำรอง"]),
    "reject": ("reject", ["ปฏิเสธ"]),
    "reason": ("reason", ["เหตุผล", "สาเหตุ"]),
    "term": ("term", ["งวด", "ระยะเวลา"]),
    "paid": ("paid", ["จ่าย", "ชำระแล้ว"]),
    "premium": ("premium", ["เบี้ยประกัน"]),
    "interest": ("interest", ["ดอกเบี้ย"]),
    "rate": ("rate", ["อัตรา"]),
    "principal": ("principal", ["เงินต้น"]),
    "sum": ("sum insured", ["ทุนประกัน", "จำนวนเงินเอาประกัน"]),
    "plan": ("plan", ["แผน"]),
    "plans": ("plans", ["แผน"]),
    "fund": ("fund", ["กองทุน"]),
    "nav": ("NAV", ["มูลค่าหน่วยลงทุน"]),
    "unit": ("unit", ["หน่วย"]),
    "license": ("license", ["ใบอนุญาต"]),
    "distribution": ("distribution channel", ["ช่องทางการขาย"]),
    "transaction": ("transaction", ["รายการ"]),
    "issue": ("issue", ["ออก"]),
    "issued": ("issued", ["ออก"]),
    "approved": ("approved", ["อนุมัติ"]),
    "sale": ("sale", ["ขาย"]),
    "year": ("year", ["ปี"]),
    "claim": ("claim", ["เคลม"]),
    "deductible": ("deductible", ["ความรับผิดส่วนแรก"]),
    "maturity": ("maturity", ["ครบกำหนด"]),
    "survival": ("survival", ["เงินคืน"]),
    "expropriation": ("surrender", ["เวนคืน"]),
    "refund": ("refund", ["คืนเงิน"]),
    "url": ("URL", ["ลิงก์"]),
    "urls": ("URLs", ["ลิงก์"]),
    "document": ("document", ["เอกสาร"]),
    "medical": ("medical", ["ตรวจสุขภาพ"]),
    "risk": ("risk", ["ภัย"]),
    "relation": ("relation", ["ความสัมพันธ์"]),
    "ratio": ("ratio", ["สัดส่วน"]),
    "permanent": ("permanent", ["ตามบัตรประชาชน"]),
    "current": ("current", ["ปัจจุบัน"]),
    "work": ("work", ["ที่ทำงาน"]),
    "gli": ("group life insurance", ["ประกันกลุ่ม"]),
    "oic": ("OIC", ["คปภ"]),
}

# High-value aliases for frequently asked fields (wording users actually type)
FIELD_ALIASES = {
    "pol_id": ["เลขกรมธรรม์", "หมายเลขกรมธรรม์", "policy number"],
    "pol_start_date": ["วันเริ่มคุ้มครอง", "วันที่มีผลคุ้มครอง", "วันเริ่มกรมธรรม์", "effective date"],
    "pol_end_date": ["วันหมดอายุกรมธรรม์", "วันสิ้นสุดสัญญา", "expiry date"],
    "pol_status": ["สถานะกรมธรรม์", "policy status"],
    "pol_type": ["ประเภทกรมธรรม์", "ประกันเดี่ยว", "ประกันกลุ่ม"],
    "pol_payment_amount": ["เบี้ยประกัน", "เบี้ยประกันภัยรายปี", "premium", "เบี้ย", "จำนวนเบี้ย"],
    "pol_payment_period": ["งวดชำระเบี้ย", "รายเดือน", "รายปี"],
    "pol_expropriation_value": ["มูลค่าเวนคืน", "surrender value"],
    "pol_distribution": ["ช่องทางขาย", "sales channel"],
    "pol_license": ["เลขใบอนุญาต", "ใบอนุญาตตัวแทน", "ใบอนุญาตนายหน้า"],
    "pol_issue_date": ["วันออกกรมธรรม์"],
    "renewal": ["ต่ออายุ", "renew"],
    "renewal_pol_id": ["เลขกรมธรรม์เดิม", "กรมธรรม์ก่อนต่ออายุ"],
    "cust_id": ["เลขบัตรประชาชนผู้ชำระเบี้ย", "เลขทะเบียนนิติบุคคล"],
    "insrd_id": ["เลขบัตรประชาชนผู้เอาประกัน"],
    "insrd_birthday": ["วันเกิดผู้เอาประกัน", "วันเกิดสมาชิก"],
    "insureds": ["ผู้เอาประกัน", "รายชื่อผู้เอาประกัน"],
    "cov_amt": ["ทุนประกัน", "จำนวนเงินเอาประกันภัย", "sum insured"],
    "loans": ["เงินกู้", "การกู้ยืม"],
    "loan_amount": ["ยอดเงินกู้", "จำนวนเงินกู้"],
    "loan_payments": ["การชำระคืนเงินกู้"],
}

# Known data problems in the source, fixed only in the RAG copy (originals untouched)
DESC_FIXES = {
    "cov_plan_cov_sum_insured_per_coverage_per_time": (
        "จำนวนเงินเอาประกันภัย/จำนวนเงินผลประโยชน์ สูงสุดในแต่ละความคุ้มครองต่อครั้ง (บาท)",
        "คำอธิบายต้นฉบับเป็น 'C' (ข้อมูลหาย) ใช้ความหมายจาก cov_sum_insured_per_coverage_per_time แทน",
    ),
    "cust_sub_district_work": (
        "รหัสตำบล ที่อยู่บริษัท (ที่ทำงาน) ของผู้ชำระเบี้ยประกันภัย",
        "ต้นฉบับเขียน 'ที่อยู่หลักตามกรมธรรม์' ซ้ำกับ cust_sub_district น่าจะคัดลอกผิด",
    ),
    "cust_district_work": (
        "รหัสอำเภอ ที่อยู่บริษัท (ที่ทำงาน) ของผู้ชำระเบี้ยประกันภัย",
        "ต้นฉบับเขียน 'ที่อยู่หลักตามกรมธรรม์' ซ้ำกับ cust_district น่าจะคัดลอกผิด",
    ),
    "cust_province_work": (
        "รหัสจังหวัด ที่อยู่บริษัท (ที่ทำงาน) ของผู้ชำระเบี้ยประกันภัย",
        "ต้นฉบับเขียน 'ที่อยู่หลักตามกรมธรรม์' ซ้ำกับ cust_province น่าจะคัดลอกผิด",
    ),
}

# FAQs that are out of the Policy scope even though they sit in an allowed sheet
FAQ_OUT_OF_SCOPE = {
    "FAQ-POL-13": "เนื้อหาเป็นสถานะของข้อมูล Claim (ClaimStatus / ClaimRejectType / ClaimPmtType) ซึ่งอยู่นอกขอบเขต Policy",
}

REQUIRED_LABELS = {
    "Yes": "จำเป็นต้องส่ง",
    "No": "ไม่บังคับส่ง",
    "Yes(C)": "จำเป็นต้องส่งตามเงื่อนไข",
}

FAQ_CHUNK_MAX_CHARS = 700

# Too generic to infer an FAQ -> table link from wording alone
INFERENCE_SKIP_TABLES = {"Policy", "Coverage"}

# Only snake_case words starting with these are treated as possible field names
FIELD_NAME_PREFIXES = {"pol", "sub", "cust", "insrd", "cov", "inv", "endorse",
                       "loan", "beneficiary", "renewal", "claim", "policy", "pmt"}


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------
def clean_text(text):
    """Normalize Thai text: unify sara am, drop doubled tone/vowel marks, collapse whitespace."""
    if text is None:
        return ""
    t = str(text)
    t = t.replace("ํา", "ำ")           # ํา (nikhahit + sara aa) -> ำ
    t = re.sub(r"([ัิ-ฺ็-๎])\1+", r"\1", t)  # ข้้อมูล -> ข้อมูล, นำส่่ง -> นำส่ง
    t = t.replace("เชิ้อชาติ", "เชื้อชาติ").replace("อีเมล์", "อีเมล")
    t = t.replace("\r", "")
    t = re.sub(r"[ \t ]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def one_line(text):
    return re.sub(r"\s+", " ", clean_text(text)).strip()


def split_table_name(raw):
    """'ผู้เอาประกัน (Insured)' -> ('ผู้เอาประกัน', 'Insured'). Uses the LAST parenthetical."""
    raw = one_line(raw)
    m = re.match(r"^(.*)\(([^()]*)\)\s*$", raw)
    if not m:
        return raw, raw
    return m.group(1).strip(), m.group(2).strip()


def mentions_field(text_lower, field_name):
    return re.search(r"(?<![a-z0-9_])" + re.escape(field_name.lower()) + r"(?![a-z0-9_])", text_lower) is not None


def chunk_text(text, max_chars):
    """Split on blank lines / line breaks, then pack paragraphs into chunks of <= max_chars."""
    paras = [p.strip() for p in re.split(r"\n\s*\n|\n(?=\s*(?:\d+[.)]|[-•*]))", text) if p.strip()]
    chunks, cur = [], ""
    for p in paras:
        if cur and len(cur) + 1 + len(p) > max_chars:
            chunks.append(cur)
            cur = p
        else:
            cur = f"{cur}\n{p}" if cur else p
    if cur:
        chunks.append(cur)
    return chunks


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------
def load_fields(issues):
    path = os.path.join(SRC_DIR, "Fields_4.0.csv")
    fields, tables = [], {}
    current, excluded = None, False
    with open(path, "r", encoding="utf-8-sig") as f:
        for r_idx, row in enumerate(csv.reader(f), start=1):
            if not row:
                continue
            col0 = row[0].strip()
            if col0 and (len(row) < 5 or not row[4].strip()):
                if col0.startswith("ประเภทข้อมูล"):
                    continue
                excluded = col0.startswith(EXCLUDED_TABLE_PREFIXES)
                if excluded:
                    current = None
                    continue
                th, en = split_table_name(col0)
                meta = TABLE_META.get(en)
                if meta is None:
                    issues.append(f"ตาราง '{col0}' ไม่มีใน TABLE_META (ไม่ได้เพิ่มคำพ้อง)")
                    meta = {"key": en.replace(" ", ""), "entity": th, "container_dtype": None, "aliases": []}
                current = {"key": meta["key"], "name_th": th, "name_en": en, "raw_name": col0,
                           "entity": meta["entity"], "aliases": meta["aliases"],
                           "container_dtype": meta["container_dtype"], "source_row": r_idx}
                tables[current["key"]] = current
                continue
            if excluded or current is None or len(row) <= 4:
                continue
            name = row[4].strip()
            if not name or name == "ชื่อข้อมูล":
                continue
            cell = lambda i: row[i].strip() if len(row) > i else ""
            fields.append({
                "field_name": name,
                "table_key": current["key"],
                "seq": cell(3),
                "required": cell(5),
                "dtype": cell(6),
                "length": cell(7),
                "desc_original": cell(10),
                "example": cell(11),
                "master_data": cell(12),
                "sensitive": cell(13),
                "condition": cell(14),
                "source_row": r_idx,
            })
    return fields, tables


def load_faqs():
    wb = openpyxl.load_workbook(os.path.join(SRC_DIR, "FAQ.xlsx"), data_only=True)
    faqs = []
    for sheet, prefix in FAQ_SHEETS.items():
        if sheet not in wb.sheetnames:
            continue
        ws = wb[sheet]
        headers = [one_line(ws.cell(1, c).value) for c in range(1, ws.max_column + 1)]
        note_col = next((i + 1 for i, h in enumerate(headers) if h == "หมายเหตุ"), None)
        for r in range(2, ws.max_row + 1):
            q, a = ws.cell(r, 2).value, ws.cell(r, 3).value
            if not q or not a or not str(q).strip() or not str(a).strip():
                continue
            faqs.append({
                "item_id": f"{prefix}-{r - 1:02d}",   # same id scheme as the engine / ground truth
                "sheet": sheet,
                "source_row": r,
                "question": clean_text(q),
                "answer": clean_text(a),
                "note": clean_text(ws.cell(r, note_col).value) if note_col else "",
            })
    return faqs


# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------
def enrich(fields, tables, faqs, issues):
    by_name = {f["field_name"]: f for f in fields}
    dtype_to_table = {t["container_dtype"]: k for k, t in tables.items() if t["container_dtype"]}

    # Table tree: a container field (dtype like [insureds]) links parent table -> child table
    for f in fields:
        child = dtype_to_table.get(f["dtype"])
        if child:
            f["child_table_key"] = child
            tables[child]["parent_table_key"] = f["table_key"]
            tables[child]["container_field"] = f["field_name"]

    fixes = []
    for f in fields:
        t = tables[f["table_key"]]
        desc = one_line(f["desc_original"])
        if f["field_name"] in DESC_FIXES:
            new_desc, reason = DESC_FIXES[f["field_name"]]
            fixes.append({"field_name": f["field_name"], "from": desc, "to": new_desc, "reason": reason})
            desc = new_desc
        f["desc"] = desc

        # Generic descriptions ("วันเกิด", "เพศ") are ambiguous across tables: add the entity
        entity = t["entity"]
        entity_head = re.split(r"[ (/]", entity)[0]
        f["desc_contextual"] = desc if entity_head and entity_head in desc else f"{desc} ของ{entity}"

        tokens = [tok for tok in f["field_name"].lower().split("_") if tok]
        f["name_tokens"] = tokens
        f["name_en"] = " ".join(GLOSSARY[tok][0] if tok in GLOSSARY else tok for tok in tokens)
        kw = []
        for tok in tokens:
            kw.extend(GLOSSARY.get(tok, ("", []))[1])
        f["keywords_th"] = list(dict.fromkeys(kw))
        f["aliases"] = FIELD_ALIASES.get(f["field_name"], [])
        f["required_label"] = REQUIRED_LABELS.get(f["required"], f["required"])
        f["condition"] = clean_text(f["condition"])
        f["example"] = one_line(f["example"])
        f["master_data"] = one_line(f["master_data"])
        f["related_faq_ids"] = []

    unknown_aliases = [n for n in FIELD_ALIASES if n not in by_name]
    if unknown_aliases:
        issues.append(f"FIELD_ALIASES อ้างถึงฟิลด์ที่ไม่มีอยู่: {unknown_aliases}")

    # FAQ <-> field links
    name_pattern = re.compile(r"(?<![a-z0-9_])[a-z][a-z0-9]*(?:_[a-z0-9]+)+(?![a-z0-9_])")
    for q in faqs:
        text = (q["question"] + "\n" + q["answer"] + "\n" + q["note"]).lower()
        explicit = sorted(n for n in by_name if mentions_field(text, n))
        q["linked_fields"] = explicit
        q["linked_tables"] = sorted({by_name[n]["table_key"] for n in explicit})
        # Table mentioned by wording only (e.g. "สลักหลัง") -> weaker, inferred link.
        # Policy/Coverage words ("กรมธรรม์", "ความคุ้มครอง") appear in almost every FAQ, so they don't count.
        inferred = sorted(k for k, t in tables.items()
                          if k not in q["linked_tables"] and k not in INFERENCE_SKIP_TABLES
                          and any(a.lower() in text for a in t["aliases"] if len(a) >= 4))
        q["inferred_tables"] = inferred
        q["unknown_field_mentions"] = sorted(
            m for m in set(name_pattern.findall(text)) - set(by_name)
            if m.split("_")[0] in FIELD_NAME_PREFIXES
        )
        for n in explicit:
            by_name[n]["related_faq_ids"].append(q["item_id"])

        if q["item_id"] in FAQ_OUT_OF_SCOPE:
            q["category"], q["in_scope"] = "out_of_scope", False
            q["out_of_scope_reason"] = FAQ_OUT_OF_SCOPE[q["item_id"]]
        elif explicit or q["sheet"] == "Policy":
            q["category"], q["in_scope"] = "policy_data", True
        elif q["sheet"] == "คู่มือการส่งกรมธรรม์":
            q["category"], q["in_scope"] = "manual", True
        elif inferred:
            q["category"], q["in_scope"] = "policy_data", True
        else:
            q["category"], q["in_scope"] = "system_usage", True

        q["chunks"] = chunk_text(q["answer"], FAQ_CHUNK_MAX_CHARS)

    for k, t in tables.items():
        t["field_names"] = [f["field_name"] for f in fields if f["table_key"] == k]
        t["key_fields"] = [n for n in t["field_names"] if re.search(r"(_id|_seq|_seq_no)$", n)]
    return fixes


# ---------------------------------------------------------------------------
# Corpus (one retrievable unit per line, ready for TF-IDF or embeddings)
# ---------------------------------------------------------------------------
def field_text(f, t):
    lines = [
        f"ชื่อฟิลด์: {f['field_name']} ({f['name_en']})",
        f"ตาราง: {t['name_th']} ({t['name_en']})",
        f"ความหมาย: {f['desc_contextual']}",
        f"คำค้นที่เกี่ยวข้อง: {', '.join(dict.fromkeys(f['aliases'] + f['keywords_th'] + t['aliases']))}",
        f"ความจำเป็น: {f['required_label']}" if f["required"] else "",
        f"ประเภทข้อมูล: {f['dtype']}" + (f" ความยาว {f['length']}" if f["length"] else "") if f["dtype"] else "",
        f"ตัวอย่างข้อมูล: {f['example']}" if f["example"] else "",
        f"Master Data: {f['master_data']}" if f["master_data"] else "",
        f"ข้อมูลอ่อนไหว (Sensitive): {f['sensitive']}" if f["sensitive"] else "",
        f"เงื่อนไขการส่งข้อมูล: {f['condition']}" if f["condition"] else "",
        f"FAQ ที่เกี่ยวข้อง: {', '.join(f['related_faq_ids'])}" if f["related_faq_ids"] else "",
    ]
    return "\n".join(l for l in lines if l)


def build_corpus(fields, tables, faqs):
    corpus = []
    for k, t in tables.items():
        corpus.append({
            "doc_id": f"TABLE-{k}", "type": "Table", "item_id": f"TABLE-{k}",
            "title": f"{t['name_th']} ({t['name_en']})",
            "text": "\n".join(filter(None, [
                f"ตาราง: {t['name_th']} ({t['name_en']})",
                f"คำค้นที่เกี่ยวข้อง: {', '.join(t['aliases'])}",
                f"อยู่ภายใต้ตาราง: {t['parent_table_key']} (ฟิลด์ {t['container_field']})" if t.get("parent_table_key") else "",
                f"ฟิลด์หลัก: {', '.join(t['key_fields'])}" if t["key_fields"] else "",
                f"ฟิลด์ทั้งหมด ({len(t['field_names'])}): {', '.join(t['field_names'])}",
            ])),
            "source": f"Fields_4.0.csv [ตาราง: {t['raw_name']}, แถวที่: {t['source_row']}]",
        })
    for f in fields:
        t = tables[f["table_key"]]
        corpus.append({
            "doc_id": f"FIELD-{f['field_name']}", "type": "Field", "item_id": f"FIELD-{f['field_name']}",
            "title": f"{f['field_name']} ({f['desc']})",
            "text": field_text(f, t),
            "table_key": f["table_key"],
            "source": f"Fields_4.0.csv [ตาราง: {t['raw_name']}, แถวที่: {f['source_row']}]",
        })
    for q in faqs:
        if not q["in_scope"]:
            continue
        header = f"คำถาม (FAQ): {q['question']}"
        if q["linked_fields"]:
            header += f"\nฟิลด์ที่เกี่ยวข้อง: {', '.join(q['linked_fields'])}"
        n = len(q["chunks"])
        for i, chunk in enumerate(q["chunks"], start=1):
            body = f"{header}\nคำตอบ{f' (ส่วนที่ {i}/{n})' if n > 1 else ''}: {chunk}"
            if i == n and q["note"]:
                body += f"\nหมายเหตุ: {q['note']}"
            corpus.append({
                "doc_id": f"{q['item_id']}#{i}" if n > 1 else q["item_id"],
                "type": "FAQ", "item_id": q["item_id"],   # item_id = parent FAQ, for ground-truth scoring
                "title": q["question"][:80],
                "text": body,
                "category": q["category"],
                "source": f"FAQ.xlsx [ชีต: {q['sheet']}, แถวที่: {q['source_row']}]",
            })
    return corpus


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def write_report(fields, tables, faqs, corpus, fixes, gt_missing, issues):
    cat = defaultdict(list)
    for q in faqs:
        cat[q["category"]].append(q["item_id"])
    unlinked = [q["item_id"] for q in faqs if q["in_scope"] and not q["linked_fields"]]
    multi_chunk = [(q["item_id"], len(q["chunks"])) for q in faqs if len(q["chunks"]) > 1]
    unknown = {q["item_id"]: q["unknown_field_mentions"] for q in faqs if q["unknown_field_mentions"]}
    generic = sorted({f["desc"] for f in fields if f["desc_contextual"] != f["desc"]
                      and sum(1 for g in fields if g["desc"] == f["desc"]) > 1})

    L = ["# รายงานชุดข้อมูลสำหรับ RAG (data_rag/)", "",
         "สร้างโดย `scripts/build_rag_dataset.py` จากไฟล์ใน `data/` (ไฟล์ต้นฉบับไม่ถูกแก้ไข)", "",
         "## สรุปจำนวน", "",
         f"- ตาราง: {len(tables)}",
         f"- ฟิลด์: {len(fields)}",
         f"- FAQ ทั้งหมด: {len(faqs)} (อยู่ในขอบเขต {sum(q['in_scope'] for q in faqs)})",
         f"- เอกสารใน corpus.jsonl: {len(corpus)} (ตาราง {sum(d['type']=='Table' for d in corpus)}, "
         f"ฟิลด์ {sum(d['type']=='Field' for d in corpus)}, FAQ chunk {sum(d['type']=='FAQ' for d in corpus)})", "",
         "## ตารางที่ได้", "", "| key | ชื่อ | จำนวนฟิลด์ | อยู่ภายใต้ |", "|---|---|---|---|"]
    for k, t in tables.items():
        L.append(f"| {k} | {t['name_th']} | {len(t['field_names'])} | {t.get('parent_table_key', '-')} |")
    L += ["", "## ปัญหาที่พบในข้อมูลต้นฉบับ และสิ่งที่ปรับในสำเนานี้", "",
          "### 1. ตาราง Loan Payment ถูกตัดทิ้งในระบบปัจจุบัน",
          "`backend/rag_engine.py` ตัดทุกตารางที่ชื่อมีคำว่า \"Payment\" ทำให้ `การจ่ายกู้ยืมของกรมธรรม์ (Loan Payment)` "
          "หายไป ทั้งที่เป็นข้อมูลกรมธรรม์ สำเนานี้ตัดเฉพาะตาราง Payment/Claim จริงด้วยการเทียบคำขึ้นต้นชื่อตาราง", "",
          "### 2. คำอธิบายฟิลด์ที่ผิด/หาย (แก้ในสำเนาเท่านั้น ควรให้ผู้เชี่ยวชาญยืนยัน)", "",
          "| ฟิลด์ | ต้นฉบับ | ในสำเนา | เหตุผล |", "|---|---|---|---|"]
    for x in fixes:
        L.append(f"| `{x['field_name']}` | {x['from']} | {x['to']} | {x['reason']} |")
    L += ["", "### 3. คำอธิบายกว้างเกินไป ซ้ำกันข้ามตาราง",
          f"คำอธิบายเช่น {', '.join(f'\"{g}\"' for g in generic[:12])} ใช้ซ้ำในหลายตาราง ค้นแล้วแยกไม่ออกว่าเป็นของใคร "
          "สำเนานี้เพิ่ม `desc_contextual` ที่ต่อท้ายด้วยเจ้าของข้อมูล เช่น \"วันเกิด ของผู้เอาประกันภัย\"", "",
          "### 4. การสะกดและรูปแบบข้อความ",
          "ปรับ ํา→ำ, ลบวรรณยุกต์/สระซ้ำ (ข้้อมูล, นำส่่ง), เชิ้อชาติ→เชื้อชาติ, อีเมล์→อีเมล, ลบการขึ้นบรรทัดใหม่กลางคำอธิบาย", "",
          "### 5. คอลัมน์ที่ระบบปัจจุบันไม่ได้อ่าน",
          "- `Sensitive Data` ใน Fields_4.0.csv → เก็บไว้ในฟิลด์ `sensitive`",
          "- `หมายเหตุ` ในชีต Policy ของ FAQ.xlsx → เก็บไว้ใน `note` และต่อท้าย chunk สุดท้ายของ FAQ", "",
          "### 6. ดัชนีที่ระบบใช้อยู่ล้าสมัย",
          f"FAQ.xlsx มี {len(faqs)} ข้อ แต่ `storage/index_cache.pkl` มี 33 ข้อ "
          "(ขาด FAQ-POL-17, FAQ-POL-18, FAQ-MAN-06, FAQ-GEN-13) ต้องกด \"รีเฟรชดัชนี\" หรือเรียก `POST /api/reindex`", "",
          "### 7. FAQ ที่อยู่นอกขอบเขต"]
    for k, v in FAQ_OUT_OF_SCOPE.items():
        L.append(f"- `{k}`: {v} (ไม่ใส่ใน corpus.jsonl)")
    L += ["", "### 8. FAQ ที่ไม่อ้างชื่อฟิลด์ใดเลย (เชื่อมกับฟิลด์ไม่ได้อัตโนมัติ)",
          "ควรให้ผู้เชี่ยวชาญระบุฟิลด์ที่เกี่ยวข้องเพิ่ม: " + ", ".join(f"`{i}`" for i in unlinked), "",
          "### 9. FAQ ที่อ้างถึงชื่อฟิลด์ที่ไม่มีในเวอร์ชัน 4.0",
          "(อาจเป็นฟิลด์ของเวอร์ชัน 4.01+ หรือพิมพ์ผิด)", ""]
    for k, v in unknown.items():
        L.append(f"- `{k}`: {', '.join(v)}")
    L += ["", "### 10. FAQ ยาวที่ถูกแบ่งเป็นหลายส่วน (chunk ≤ "
          f"{FAQ_CHUNK_MAX_CHARS} ตัวอักษร)", ", ".join(f"`{i}` ({n} ส่วน)" for i, n in multi_chunk), "",
          "## หมวดหมู่ FAQ", ""]
    for c, ids in cat.items():
        L.append(f"- **{c}** ({len(ids)}): {', '.join(ids)}")
    L += ["", "## ชุดเฉลย (ground_truth.json)",
          "คัดลอกมาจาก `data/ground_truth.json` โดยไม่แก้ไข"]
    L.append(f"- item_id ที่ไม่มีอยู่ในชุดข้อมูล: {gt_missing}" if gt_missing else "- item_id ทุกตัวมีอยู่ในชุดข้อมูล")
    if issues:
        L += ["", "## คำเตือนจากสคริปต์", ""] + [f"- {i}" for i in issues]
    L += ["", "## ต้องให้ผู้เชี่ยวชาญตรวจ", "",
          "- คำพ้อง (`TABLE_META.aliases`, `FIELD_ALIASES`, `GLOSSARY`) ในสคริปต์ เขียนจากความหมายในคำอธิบายฟิลด์ ยังไม่ผ่านการยืนยัน",
          "- การแก้คำอธิบายในหัวข้อ 2",
          "- การจัด FAQ-POL-13 เป็นนอกขอบเขต และหมวดหมู่ FAQ", ""]
    with open(os.path.join(OUT_DIR, "DATA_REPORT.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    issues = []
    fields, tables = load_fields(issues)
    faqs = load_faqs()
    fixes = enrich(fields, tables, faqs, issues)
    corpus = build_corpus(fields, tables, faqs)

    with open(os.path.join(SRC_DIR, "ground_truth.json"), encoding="utf-8") as f:
        gt = json.load(f)
    known_ids = {d["item_id"] for d in corpus}
    gt_missing = sorted({i for c in gt for i in c.get("expected_item_ids", []) if i not in known_ids})

    def dump(name, obj):
        with open(os.path.join(OUT_DIR, name), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)

    dump("tables.json", list(tables.values()))
    dump("fields.json", fields)
    dump("faqs.json", faqs)
    dump("glossary.json", {k: {"en": v[0], "th": v[1]} for k, v in GLOSSARY.items()})
    dump("ground_truth.json", gt)
    with open(os.path.join(OUT_DIR, "corpus.jsonl"), "w", encoding="utf-8") as f:
        for d in corpus:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    write_report(fields, tables, faqs, corpus, fixes, gt_missing, issues)

    print(f"tables={len(tables)} fields={len(fields)} faqs={len(faqs)} corpus={len(corpus)} -> {OUT_DIR}")
    if gt_missing:
        print("ground truth ids not found:", gt_missing)
    for i in issues:
        print("WARN:", i)


if __name__ == "__main__":
    main()
