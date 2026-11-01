"""
Build a RAG-ready copy of the IBSL Policy 4.0 dataset.

Reads (never modifies):  data/Fields_4.0.csv
Writes:                  data_rag/  (fields.json, tables.json, glossary.json, corpus.jsonl, DATA_REPORT.md)

FAQ.xlsx is no longer a source: the IBSL knowledge (problems, rules, examples) comes from the PDF via
scripts/build_ibsl_dataset.py. The benchmark is data_rag/ground_truth_ibsl.json.

Run:  python scripts/build_rag_dataset.py
"""
import csv
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(ROOT, "data")
OUT_DIR = os.path.join(ROOT, "data_rag")

# Every table of Fields_4.0 is kept (Policy, Payment, Claim, Estimate, SIMB2): the IBSL PDF covers them all.
# Add a table-name prefix here only to deliberately drop a table from the knowledge base.
EXCLUDED_TABLE_PREFIXES = ()

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
    "pmt_direct_premium": ["เบี้ยประกัน", "เบี้ยตรง", "direct premium", "ค่าเบี้ย"],
    "pmt_premium_payment_period_year": ["ปีที่ชำระเบี้ย", "งวดชำระ"],
    "pmt_premium_payment_year": ["ปีกรมธรรม์", "policy year"],
    "pmt_prd_premium_amount": ["เบี้ยประกัน", "จำนวนเงินเบี้ย", "premium amount", "เบี้ย"],
    "pmt_prd_premium_amount_tax": ["เบี้ยประกันภาษี", "เบี้ยภาษี", "tax premium"],
    "pmt_prd_premium_amount_life": ["เบี้ยประกันชีวิต", "เบี้ยชีวิต", "life premium"],
    "pmt_prd_premium_amt_saving": ["เบี้ยออมทรัพย์", "เบี้ยสะสมทรัพย์", "saving premium"],
    "pmt_prd_premium_amt_investment": ["เบี้ยลงทุน", "investment premium"],
    "pmt_prd_premium_amount_other": ["เบี้ยอื่น", "other premium"],
    "pmt_prd_premium_amount_com": ["เบี้ยรวม", "combined premium"],
    "pmt_prd_premium_amt_interest": ["ดอกเบี้ยเบี้ย", "interest premium"],
    "pmt_prd_premium_date": ["วันที่ชำระเบี้ย", "premium date", "วันชำระ"],
    "pmt_prd_premium_due_date": ["วันครบกำหนดเบี้ย", "premium due date", "วันครบชำระ"],
    "pmt_premium_rid_amount": ["เบี้ยสัญญาเพิ่มเติม", "rider premium", "สัญญาเพิ่มเติม"],
    "pmt_premium_edm_amount": ["เบี้ยเอ็นดอร์ส", "endorsement premium"],
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

REQUIRED_LABELS = {
    "Yes": "จำเป็นต้องส่ง",
    "No": "ไม่บังคับส่ง",
    "Yes(C)": "จำเป็นต้องส่งตามเงื่อนไข",
}


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
                "is_object": cell(1),        # column headed "8" in the source: Yes = object / sub-table field
                "is_array": cell(2),         # อาเรย์
                "sftp_limit_v1": cell(8),    # ข้อจำกัดของ sFTP V.1.0
                "sftp_limit_v4": cell(9),    # ข้อจำกัดของ sFTP V.4.0
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


# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------
def enrich(fields, tables, issues):
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

    unknown_aliases = [n for n in FIELD_ALIASES if n not in by_name]
    if unknown_aliases:
        issues.append(f"FIELD_ALIASES อ้างถึงฟิลด์ที่ไม่มีอยู่: {unknown_aliases}")

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
        f"อาเรย์: {f['is_array']}" if f["is_array"] == "Yes" else "",
        f"ข้อจำกัดของ sFTP V.1.0: {f['sftp_limit_v1']}" if f["sftp_limit_v1"] else "",
        f"ข้อจำกัดของ sFTP V.4.0: {f['sftp_limit_v4']}" if f["sftp_limit_v4"] else "",
        f"เงื่อนไขการส่งข้อมูล: {f['condition']}" if f["condition"] else "",
    ]
    return "\n".join(l for l in lines if l)


def build_corpus(fields, tables):
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
    return corpus


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def write_report(fields, tables, corpus, fixes, issues):
    generic = sorted({f["desc"] for f in fields if f["desc_contextual"] != f["desc"]
                      and sum(1 for g in fields if g["desc"] == f["desc"]) > 1})

    L = ["# รายงานชุดข้อมูลสำหรับ RAG (data_rag/)", "",
         "สร้างโดย `scripts/build_rag_dataset.py` จาก `data/Fields_4.0.csv` (ไฟล์ต้นฉบับไม่ถูกแก้ไข)", "",
         "FAQ.xlsx ไม่ได้ใช้เป็นแหล่งข้อมูลแล้ว: ปัญหา เงื่อนไข และตัวอย่างการส่งข้อมูลมาจากเอกสาร IBSL (PDF) "
         "ผ่าน `scripts/build_ibsl_dataset.py` (ดู `IBSL_REPORT.md`)", "",
         "## สรุปจำนวน", "",
         f"- ตาราง: {len(tables)}",
         f"- ฟิลด์: {len(fields)}",
         f"- เอกสารใน corpus.jsonl: {len(corpus)} (ตาราง {sum(d['type']=='Table' for d in corpus)}, "
         f"ฟิลด์ {sum(d['type']=='Field' for d in corpus)})", "",
         "## ตารางที่ได้", "", "| key | ชื่อ | จำนวนฟิลด์ | อยู่ภายใต้ |", "|---|---|---|---|"]
    for k, t in tables.items():
        L.append(f"| {k} | {t['name_th']} | {len(t['field_names'])} | {t.get('parent_table_key', '-')} |")
    L += ["", "## ปัญหาที่พบในข้อมูลต้นฉบับ และสิ่งที่ปรับในสำเนานี้", "",
          "### 1. คำอธิบายฟิลด์ที่ผิด/หาย (แก้ในสำเนาเท่านั้น ควรให้ผู้เชี่ยวชาญยืนยัน)", "",
          "| ฟิลด์ | ต้นฉบับ | ในสำเนา | เหตุผล |", "|---|---|---|---|"]
    for x in fixes:
        L.append(f"| `{x['field_name']}` | {x['from']} | {x['to']} | {x['reason']} |")
    L += ["", "### 2. คำอธิบายกว้างเกินไป ซ้ำกันข้ามตาราง",
          f"คำอธิบายเช่น {', '.join(f'\"{g}\"' for g in generic[:12])} ใช้ซ้ำในหลายตาราง ค้นแล้วแยกไม่ออกว่าเป็นของใคร "
          "สำเนานี้เพิ่ม `desc_contextual` ที่ต่อท้ายด้วยเจ้าของข้อมูล เช่น \"วันเกิด ของผู้เอาประกันภัย\"", "",
          "### 3. การสะกดและรูปแบบข้อความ",
          "ปรับ ํา→ำ, ลบวรรณยุกต์/สระซ้ำ (ข้้อมูล, นำส่่ง), เชิ้อชาติ→เชื้อชาติ, อีเมล์→อีเมล, ลบการขึ้นบรรทัดใหม่กลางคำอธิบาย", "",
          "### 4. คอลัมน์เพิ่มเติม",
          "- `Sensitive Data` ใน Fields_4.0.csv → เก็บไว้ในฟิลด์ `sensitive`"]
    if issues:
        L += ["", "## คำเตือนจากสคริปต์", ""] + [f"- {i}" for i in issues]
    L += ["", "## ต้องให้ผู้เชี่ยวชาญตรวจ", "",
          "- คำพ้อง (`TABLE_META.aliases`, `FIELD_ALIASES`, `GLOSSARY`) ในสคริปต์ เขียนจากความหมายในคำอธิบายฟิลด์ ยังไม่ผ่านการยืนยัน",
          "- การแก้คำอธิบายในหัวข้อ 1", ""]
    with open(os.path.join(OUT_DIR, "DATA_REPORT.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    issues = []
    fields, tables = load_fields(issues)
    fixes = enrich(fields, tables, issues)
    corpus = build_corpus(fields, tables)

    def dump(name, obj):
        with open(os.path.join(OUT_DIR, name), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)

    dump("tables.json", list(tables.values()))
    dump("fields.json", fields)
    dump("glossary.json", {k: {"en": v[0], "th": v[1]} for k, v in GLOSSARY.items()})
    with open(os.path.join(OUT_DIR, "corpus.jsonl"), "w", encoding="utf-8") as f:
        for d in corpus:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    # outputs of the old FAQ-based pipeline: remove so nothing can load them by mistake
    for old in ("faqs.json", "ground_truth.json"):
        if os.path.exists(os.path.join(OUT_DIR, old)):
            os.remove(os.path.join(OUT_DIR, old))
    write_report(fields, tables, corpus, fixes, issues)

    print(f"tables={len(tables)} fields={len(fields)} corpus={len(corpus)} -> {OUT_DIR}")
    for i in issues:
        print("WARN:", i)


if __name__ == "__main__":
    main()
