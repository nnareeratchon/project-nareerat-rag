import unittest
import os
import sys
import json
import sqlite3
import tempfile

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Use a throwaway SQLite file so tests never write into storage/database.sqlite.
# Must be set before backend.database is imported.
_TEST_DB_DIR = tempfile.mkdtemp(prefix="ibsl_test_db_")
os.environ["IBSL_DB_PATH"] = os.path.join(_TEST_DB_DIR, "test_database.sqlite")

from backend.rag_engine import rag_engine, RAGEngine, NOT_FOUND_TEXT
from backend.database import (
    init_db,
    save_query_search,
    save_evaluation,
    get_user_precision_metrics,
    get_db_connection,
    get_query_feedback
)

class TestIBSLRAGSystem(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        init_db()

    def test_01_ibsl_knowledge_base_loaded(self):
        """Policy scope: the 24 IBSL topics of the 6 policy categories (1.1-1.6) are loaded; Payment/Claim topics are not. Old FAQ database is excluded."""
        items = rag_engine.items
        chunks = rag_engine.chunks
        page_info = rag_engine.page_info

        self.assertEqual(rag_engine.scope, "policy")
        self.assertEqual(len(items), 24, "Must contain exactly the 24 policy topics 1.1.1 to 1.6.3")
        self.assertGreaterEqual(page_info.get("page_count", 0), 133, "The whole 133-page PDF is still read")
        self.assertEqual(page_info.get("topic_page_range"), "6-74", "Policy topics are on pages 6-74")
        self.assertGreaterEqual(len(chunks), 90, "Must create page chunks for retrieval")

        # Only the 6 policy categories are present
        categories = {it.get("category") for it in items}
        expected_cats = {
            "Policy", "Insured", "Sub Policy", "Policy Coverage Plan Sequence",
            "Coverage Plan Coverage", "Coverage"
        }
        self.assertEqual(categories, expected_cats, "Must cover exactly the 6 policy categories of the IBSL document")
        self.assertTrue(all(c["item_id"] in rag_engine.item_by_id or c["item_id"] == "IBSL-DOC" for c in chunks),
                        "No chunk of a Payment/Claim topic may be loaded")

        # Verify no old FAQ.xlsx data is present in the knowledge base
        for it in items:
            self.assertFalse(it.get("file") == "FAQ.xlsx", "Old FAQ.xlsx data must not be in knowledge base")
            self.assertIn("IBSL-PR-INC", it.get("doc_name", ""))

    def test_02_sample_queries_retrieval(self):
        """Test retrieval accuracy for the 4 primary sample queries: returns relevant field specifications linked to IBSL."""
        # 1. pol_type ส่งค่า NULL ได้หรือไม่ -> FIELD-pol_type linked to IBSL-1.1.1
        res1 = rag_engine.retrieve("pol_type ส่งค่า NULL ได้หรือไม่", k=3)
        self.assertGreater(len(res1), 0)
        self.assertEqual(res1[0]["item_id"], "FIELD-pol_type")
        self.assertEqual(res1[0]["field_name"], "pol_type")
        self.assertTrue(res1[0]["has_ibsl"])
        self.assertEqual(res1[0]["ibsl_topics"][0]["item_id"], "IBSL-1.1.1")
        self.assertGreater(res1[0]["similarity_score"], 0.0)

        # 2. pol_start_date ต้องส่งวันที่รูปแบบใด -> FIELD-pol_start_date linked to IBSL-1.1.4
        res2 = rag_engine.retrieve("pol_start_date ต้องส่งวันที่รูปแบบใด", k=3)
        self.assertGreater(len(res2), 0)
        self.assertEqual(res2[0]["item_id"], "FIELD-pol_start_date")
        self.assertEqual(res2[0]["field_name"], "pol_start_date")
        self.assertTrue(res2[0]["has_ibsl"])
        self.assertEqual(res2[0]["ibsl_topics"][0]["item_id"], "IBSL-1.1.4")
        self.assertGreater(res2[0]["similarity_score"], 0.0)

        # 3. pol_status 9999 เกิด Error อะไร -> FIELD-pol_status linked to IBSL-1.1.5
        res3 = rag_engine.retrieve("pol_status 9999 เกิด Error อะไร", k=3)
        self.assertGreater(len(res3), 0)
        self.assertEqual(res3[0]["item_id"], "FIELD-pol_status")
        self.assertEqual(res3[0]["field_name"], "pol_status")
        self.assertTrue(res3[0]["has_ibsl"])
        self.assertEqual(res3[0]["ibsl_topics"][0]["item_id"], "IBSL-1.1.5")
        self.assertGreater(res3[0]["similarity_score"], 0.0)

        # 4. sub_pol_start_date ส่งปี พ.ศ. ได้หรือไม่ -> FIELD-sub_pol_start_date linked to IBSL-1.3.4
        res4 = rag_engine.retrieve("sub_pol_start_date ส่งปี พ.ศ. ได้หรือไม่", k=3)
        self.assertGreater(len(res4), 0)
        self.assertEqual(res4[0]["item_id"], "FIELD-sub_pol_start_date")
        self.assertEqual(res4[0]["field_name"], "sub_pol_start_date")
        self.assertTrue(res4[0]["has_ibsl"])
        self.assertEqual(res4[0]["ibsl_topics"][0]["item_id"], "IBSL-1.3.4")
        self.assertGreater(res4[0]["similarity_score"], 0.0)

    def test_03_multi_page_linking_and_structure(self):
        """Test multi-page topic linking: sections across multiple pages are merged and split into structured fields."""
        item = rag_engine.item_by_id.get("IBSL-1.1.5")
        self.assertIsNotNone(item)
        self.assertEqual(item["page_range"], "14-16", "pol_status must cover pages 14-16")
        self.assertIn(14, item["pages"])
        self.assertIn(16, item["pages"])

        # Check structured sections
        self.assertIn("NULL", item["problem"])
        self.assertIn("9999", item["problem"])
        self.assertIn("1000005", item["conditions"])
        self.assertIn("0100", item["valid_examples"])
        self.assertIn("Expiry", item["guidance"])
        self.assertIn("pol_trans", item["payload"])

        # Longest policy topic: cov_amt (pages 69-74)
        cov = rag_engine.item_by_id.get("IBSL-1.6.3")
        self.assertIsNotNone(cov)
        self.assertEqual(cov["page_range"], "69-74")
        self.assertIn("cov_amt", cov["field_names"])

        # Claim topics are outside the policy scope
        self.assertIsNone(rag_engine.item_by_id.get("IBSL-1.9.6"))

    def test_04_error_code_and_question_code_matching(self):
        """Test searching by error code, error message, and IBSLFAQ question code."""
        # Query by question code
        res_qcode = rag_engine.retrieve("IBSLFAQ_POL_001", k=3)
        self.assertGreater(len(res_qcode), 0)
        self.assertEqual(res_qcode[0]["item_id"], "FIELD-pol_type")
        self.assertEqual(res_qcode[0]["ibsl_topics"][0]["item_id"], "IBSL-1.1.1")

        # Query by error code 1000005
        res_err = rag_engine.retrieve("Error code 1000005", k=3)
        self.assertGreater(len(res_err), 0)
        err_ids = [it["item_id"] for it in res_err]
        self.assertTrue("FIELD-pol_status" in err_ids or "FIELD-pol_transaction_status" in err_ids)

        # Query by error message
        res_msg = rag_engine.retrieve("The reference data id mismatch", k=3)
        self.assertGreater(len(res_msg), 0)
        msg_ids = [it["item_id"] for it in res_msg]
        self.assertTrue("FIELD-pol_status" in msg_ids or "FIELD-pol_transaction_status" in msg_ids)

    def test_05_ground_truth_metrics_calculation(self):
        """Test Ground Truth benchmark calculation produces valid Precision@3, Recall@3, F1@3 and verifies Precision > Recall."""
        gt_res = rag_engine.evaluate_ground_truth(force=True)
        self.assertTrue(gt_res.get("available"))
        self.assertGreaterEqual(gt_res.get("test_count", 0), 7)
        self.assertEqual(gt_res.get("skipped_out_of_scope"), ["GT-08", "GT-09", "GT-10", "GT-11", "GT-12"],
                         "Payment/Claim ground-truth cases are skipped in the policy scope")
        self.assertIn("evaluated_at", gt_res)
        self.assertIn("macro_precision_at_3", gt_res)
        self.assertIn("macro_recall_at_3", gt_res)
        self.assertIn("macro_f1_at_3", gt_res)

        # Precision must be strictly greater than Recall as per Information Retrieval principle
        self.assertGreater(gt_res["macro_precision_at_3"], gt_res["macro_recall_at_3"],
                           "Precision must be greater than Recall per Information Retrieval principle")

        # Verify mathematical consistency
        precisions = []
        recalls = []
        f1s = []
        for c in gt_res.get("test_cases", []):
            tp, fp, fn = c["tp"], c["fp"], c["fn"]
            expected_p = tp / float(tp + fp) if (tp + fp) > 0 else 0.0
            expected_r = tp / float(tp + fn) if (tp + fn) > 0 else 0.0
            expected_f1 = (2 * expected_p * expected_r) / (expected_p + expected_r) if (expected_p + expected_r) > 0 else 0.0

            self.assertAlmostEqual(c["precision"], round(expected_p, 4), places=3)
            self.assertAlmostEqual(c["recall"], round(expected_r, 4), places=3)
            self.assertAlmostEqual(c["f1"], round(expected_f1, 4), places=3)

            precisions.append(expected_p)
            recalls.append(expected_r)
            f1s.append(expected_f1)

        expected_macro_p = sum(precisions) / len(precisions)
        expected_macro_r = sum(recalls) / len(recalls)
        expected_macro_f1 = sum(f1s) / len(f1s)

        self.assertAlmostEqual(gt_res["macro_precision_at_3"], round(expected_macro_p, 4), places=3)
        self.assertAlmostEqual(gt_res["macro_recall_at_3"], round(expected_macro_r, 4), places=3)
        self.assertAlmostEqual(gt_res["macro_f1_at_3"], round(expected_macro_f1, 4), places=3)

    def test_06_human_in_the_loop_and_sqlite(self):
        """Test saving and updating human-in-the-loop evaluation in SQLite without duplicate rows."""
        items = rag_engine.retrieve("pol_status 9999", k=3)
        self.assertGreater(len(items), 0)

        q_id = save_query_search("pol_status 9999", items)
        self.assertIsInstance(q_id, int)

        # First evaluation
        eval1 = [{"item_id": items[0]["item_id"], "relevance": 1}]
        save_evaluation(q_id, eval1, comment="HITL test comment 1")

        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM query_items WHERE query_id = ?", (q_id,))
        count1 = c.fetchone()[0]
        self.assertEqual(count1, len(items))

        # Update evaluation
        eval2 = [{"item_id": items[0]["item_id"], "relevance": 0}]
        save_evaluation(q_id, eval2, comment="HITL updated comment")

        c.execute("SELECT COUNT(*) FROM query_items WHERE query_id = ?", (q_id,))
        count2 = c.fetchone()[0]
        self.assertEqual(count2, len(items), "Updating evaluation must not duplicate query_items")

        c.execute("SELECT user_relevance FROM query_items WHERE query_id = ? AND item_id = ?", (q_id, items[0]["item_id"]))
        rel = c.fetchone()[0]
        self.assertEqual(rel, 0)
        conn.close()

    def test_07_user_precision_calculation(self):
        """Test User Precision@3 calculation rules over top-3 ranks."""
        items = [
            {"item_id": f"IBSL-1.1.{i+1}", "name": f"item_{i}", "content": "text", "source": "src", "similarity_score": 90.0 - i}
            for i in range(3)
        ]
        q_id = save_query_search("test_user_p3", items)
        save_evaluation(q_id, [
            {"item_id": "IBSL-1.1.1", "relevance": 1},
            {"item_id": "IBSL-1.1.2", "relevance": 1},
            {"item_id": "IBSL-1.1.3", "relevance": 0},
        ])

        metrics = get_user_precision_metrics()
        by_id = {q["query_id"]: q for q in metrics["evaluated_queries_list"]}
        self.assertIn(q_id, by_id)
        row = by_id[q_id]
        self.assertEqual(row["top_count"], 3)
        self.assertEqual(row["top_relevant_count"], 2)
        self.assertAlmostEqual(row["precision_at_3"], round(2 / 3, 4), places=4)

    def test_08_llm_answer_structure_and_fallback(self):
        """Test 5-part answer synthesis structure, citation, and not found fallback."""
        items = rag_engine.retrieve("pol_type ส่งค่า NULL ได้หรือไม่", k=3)
        ans, status = rag_engine.synthesize_answer("pol_type ส่งค่า NULL ได้หรือไม่", items)
        self.assertIn("1) สรุปคำตอบ", ans)
        self.assertIn("2) รายละเอียดและเงื่อนไข", ans)
        self.assertIn("3) ตัวอย่างข้อมูล", ans)
        self.assertIn("4) แนวทางแก้ไข", ans)
        self.assertIn("5) แหล่งอ้างอิง", ans)
        self.assertIn("IBSL-PR-INC", ans)

        # Unrelated query should return standard not found message
        empty_items = []
        ans_empty, status_empty = rag_engine.synthesize_answer("xyzabc12345", empty_items)
        self.assertEqual(status_empty, "not_found")
        self.assertEqual(ans_empty, NOT_FOUND_TEXT)

    def test_09_user_feedback_reranking(self):
        """Test Human-in-the-Loop feedback re-ranking: not-relevant items are hidden, relevant promoted."""
        query = "test_feedback_rerank"
        items = rag_engine.retrieve("pol_status 9999", k=3)
        top_id = items[0]["item_id"]

        q_id = save_query_search(query, items)
        save_evaluation(q_id, [{"item_id": top_id, "relevance": 0}])

        feedback = get_query_feedback(query)
        self.assertEqual(feedback.get(top_id), -1)

        reranked = rag_engine.apply_user_feedback(items, feedback, query)
        reranked_ids = [it["item_id"] for it in reranked]
        self.assertNotIn(top_id, reranked_ids, "Item evaluated as not relevant must be hidden")

    def test_10_external_ai_disclaimer(self):
        """Test external AI agent recommendations include proper disclaimer and separation from IBSL."""
        ext = rag_engine.external_ai_search("pol_status")
        self.assertIn("status", ext)
        self.assertIn("message", ext)
        if ext["status"] == "success":
            self.assertIn("ไม่ถือเป็นข้อกำหนดของเอกสาร FAQ", ext["message"])

    def test_11_unlimited_results_retrieval(self):
        """Test that retrieval is not artificially capped at 3 items when searching broad queries."""
        # Query for general field term that matches multiple tables/fields
        res = rag_engine.search("ที่อยู่", top_k=None)
        self.assertGreater(len(res), 3, "Search should return all matching fields (> 3) without arbitrary cap")
        for item in res:
            self.assertTrue(item["item_id"].startswith("FIELD-"))
            self.assertIn("field_name", item)
            self.assertIn("table", item)
            self.assertIn("has_ibsl", item)

        # An exact field name also returns its similarly named fields (not only the one exact match)
        names = [it["field_name"] for it in rag_engine.search("pol_status")]
        self.assertEqual(names[0], "pol_status")
        self.assertIn("pol_status_date", names)
        self.assertIn("pol_status_note", names)
        names = [it["field_name"] for it in rag_engine.search("pol_id")]
        self.assertGreater(len(names), 3)

        # No fixed cap: a whole-table query returns every field of that table
        insured = [it for it in rag_engine.search("ผู้เอาประกันภัย") if it["field_name"].startswith("insrd_")]
        self.assertGreater(len(insured), 15, "Results must not be capped at a fixed number")

        # Nonsense still returns nothing
        self.assertEqual(rag_engine.search("xyzabc12345"), [])

    def test_12_policy_scope_only(self):
        """Only policy data: the Policy table and its 13 sub-tables (209 fields); no Payment/Claim/Estimate/SIMB2."""
        policy_tables = {
            "Policy", "Customer", "Insured", "InsuredBeneficiary", "SubPolicy", "PolicyCovPlanSeq",
            "CoveragePlanCoverage", "Coverage", "Beneficiary", "Endorsement", "Loan", "LoanPayment",
            "Investment", "PolicyFundCode",
        }
        self.assertEqual({f["table_key"] for f in rag_engine.fields}, policy_tables)
        self.assertEqual(len(rag_engine.fields), 209)
        self.assertEqual(rag_engine.table_count, 14)
        for name in ("pmt_prd_premium_seq", "claim_hospital_id", "claim_estimate_seq", "claim_payment_amt"):
            self.assertNotIn(name, rag_engine.field_by_name, f"{name} is outside the policy scope")
        ids = [it["item_id"] for it in rag_engine.retrieve("claim_hospital_id ต้องระบุอย่างไร")]
        self.assertNotIn("FIELD-claim_hospital_id", ids)

    def test_13_every_field_found_by_name_and_description(self):
        """Typing a field's name or its Thai description (exactly as in Fields_4.0) must find that field in the top 3."""
        missed = []
        for f in rag_engine.fields:
            for q in (f["field_name"], f["desc"]):
                if q and f["item_id"] not in [x["item_id"] for x in rag_engine.retrieve(q, k=3)]:
                    missed.append((f["field_name"], q))
        self.assertEqual(missed, [], f"{len(missed)} field searches missed")

    def test_14_keyword_and_table_name_queries(self):
        """Common keywords, different wording of a description, and sub-table names."""
        cases = {
            "เลขที่กรมธรรม์": "pol_id", "สถานะกรมธรรม์": "pol_status",
            "วันเริ่มต้นความคุ้มครอง": "pol_start_date",                      # description says วันที่เริ่มต้น...
            "เลขประจำตัวประชาชนผู้เอาประกันภัย": "insrd_id",                 # words of the description, not a substring
            "ชื่อผู้ชำระเบี้ยประกันภัย": "cust_first_name",                   # must not be taken over by alias "เบี้ยประกัน"
            "ทุนประกัน": "cov_amt", "ผู้รับผลประโยชน์": "beneficiaries", "สลักหลัง": "endorsements",
            "กองทุน": "policy_fund_codes",
        }
        for q, want in cases.items():
            ids = [x["field_name"] for x in rag_engine.retrieve(q, k=3)]
            self.assertIn(want, ids, f"{q} -> {ids}")

        # A query about a whole table returns its container field first AND the table's fields, not just one item
        for q, container, prefix in (("ผู้เอาประกันภัย", "insureds", "insrd_"),
                                     ("ผู้ชำระเบี้ยประกันภัย", "customer", "cust_"),
                                     ("ความคุ้มครอง", "coverages", "cov_")):
            names = [x["field_name"] for x in rag_engine.search(q)]
            self.assertEqual(names[0], container, f"{q} -> {names[:5]}")
            self.assertGreaterEqual(sum(n.startswith(prefix) for n in names), 5, f"{q} -> {names}")
        # An attribute word spread over several tables is not a table query
        self.assertNotIn("Customer", rag_engine._query_context("ที่อยู่")[1])

if __name__ == "__main__":
    unittest.main()
