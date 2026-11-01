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

from backend.rag_engine import rag_engine, RAGEngine
from backend.database import (
    init_db,
    save_query_search,
    save_evaluation,
    get_user_precision_metrics,
    get_db_connection
)

class TestPolicyRAGSystem(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        init_db()

    def test_01_policy_only_data_loaded(self):
        """Test that ONLY Policy data and its 14 sub-tables are loaded (209 fields incl. Loan Payment), plus 36 in-scope Policy FAQs. Payment/Claim are excluded."""
        docs = rag_engine.documents
        fields = rag_engine.fields
        faqs = rag_engine.faqs
        self.assertGreater(len(docs), 0, "Documents should not be empty")

        for d in docs:
            table = d.get("table", "")
            field_name = d.get("field_name", "")
            # Ensure no Payment or Claim tables or fields
            self.assertFalse("การชำระเบี้ย" in table or ("Payment" in table and "Loan Payment" not in table),
                             f"Payment table should be excluded: {table}")
            self.assertFalse("การเคลม" in table or ("Claim" in table and "claim_document" not in field_name),
                             f"Claim table should be excluded: {table}")
            self.assertFalse("SIMB2" in table, f"SIMB2 should be excluded: {table}")

        # Check key Policy fields exist
        field_ids = [d["id"] for d in fields]
        self.assertEqual(len(fields), 209, "Must contain exactly 209 Policy fields (incl. Loan Payment)")
        self.assertEqual(len(faqs), 36, "Must contain exactly 36 in-scope Policy FAQs")
        self.assertIn("FIELD-loan_payment_amount", field_ids, "Loan Payment is Policy data and must not be excluded")
        self.assertNotIn("FAQ-POL-13", [d["id"] for d in faqs], "Claim-status FAQ is out of scope")
        self.assertIn("FIELD-pol_start_date", field_ids)
        self.assertIn("FIELD-pol_status", field_ids)
        self.assertIn("FIELD-pol_license", field_ids)
        self.assertIn("FIELD-insrd_birthday", field_ids)

    def test_02_exact_field_matching_and_thai_search(self):
        """Test exact field name matching and Thai question queries (Fields returned first)."""
        # 1. Exact field name query
        res1 = rag_engine.search("pol_start_date")
        self.assertGreater(len(res1), 0)
        self.assertEqual(res1[0]["item_id"], "FIELD-pol_start_date")

        # 2. Thai phrase query
        res2 = rag_engine.search("วันที่เริ่มต้นความคุ้มครอง")
        self.assertGreater(len(res2), 0)
        self.assertEqual(res2[0]["item_id"], "FIELD-pol_start_date")

        # 3. Thai status query
        res3 = rag_engine.search("สถานะของกรมธรรม์")
        self.assertGreater(len(res3), 0)
        self.assertEqual(res3[0]["item_id"], "FIELD-pol_status")

    def test_03_fields_first_then_corresponding_faqs_and_unlimited(self):
        """Ensure search returns fields first, followed by corresponding FAQs, and does not limit to 3 when top_k is None."""
        structured = rag_engine.search_structured("pol_status")
        fields = structured["fields"]
        faqs = structured["faqs"]
        items = structured["items"]

        self.assertGreater(len(fields), 0, "Must find matching fields")
        self.assertEqual(fields[0]["item_id"], "FIELD-pol_status")
        self.assertGreater(len(faqs), 0, "Must find corresponding FAQs for pol_status")
        
        # Verify fields are first, then FAQs
        field_ids = [f["item_id"] for f in fields]
        faq_ids = [f["item_id"] for f in faqs]
        self.assertIn("FAQ-POL-01", faq_ids, "FAQ-POL-01 must be linked to pol_status")

        # Items order check
        self.assertEqual(items[:len(fields)], fields)
        self.assertEqual(items[len(fields):], faqs)

        # Deduplication
        all_ids = [it["item_id"] for it in items]
        self.assertEqual(len(all_ids), len(set(all_ids)), "All item IDs must be distinct")

    def test_04_save_and_update_evaluation_no_duplicates(self):
        """Test saving and updating human-in-the-loop evaluation in SQLite without duplicate rows."""
        items = [
            {"item_id": "FIELD-pol_start_date", "name": "pol_start_date", "content": "text", "source": "src", "similarity_score": 95.0},
            {"item_id": "FIELD-pol_end_date", "name": "pol_end_date", "content": "text", "source": "src", "similarity_score": 85.0},
            {"item_id": "FIELD-pol_status", "name": "pol_status", "content": "text", "source": "src", "similarity_score": 75.0}
        ]
        q_id = save_query_search("test_query_eval", items)
        self.assertIsInstance(q_id, int)

        # First evaluation
        eval1 = [
            {"item_id": "FIELD-pol_start_date", "relevance": 1},
            {"item_id": "FIELD-pol_end_date", "relevance": 1},
            {"item_id": "FIELD-pol_status", "relevance": 0}
        ]
        save_evaluation(q_id, eval1, comment="First evaluation comment")

        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM query_items WHERE query_id = ?", (q_id,))
        count1 = c.fetchone()[0]
        self.assertEqual(count1, 3)

        # Update evaluation for same query (modify relevance)
        eval2 = [
            {"item_id": "FIELD-pol_status", "relevance": 1}
        ]
        save_evaluation(q_id, eval2, comment="Updated comment")

        c.execute("SELECT COUNT(*) FROM query_items WHERE query_id = ?", (q_id,))
        count2 = c.fetchone()[0]
        self.assertEqual(count2, 3, "Updating evaluation must not duplicate query_items")

        # Check updated values
        c.execute("SELECT user_relevance FROM query_items WHERE query_id = ? AND item_id = ?", (q_id, "FIELD-pol_status"))
        rel = c.fetchone()[0]
        self.assertEqual(rel, 1)

        c.execute("SELECT feedback_comment FROM queries WHERE id = ?", (q_id,))
        comm = c.fetchone()[0]
        self.assertEqual(comm, "Updated comment")
        conn.close()

    def test_05_ground_truth_metrics_calculation(self):
        """Test Ground Truth calculation produces correct Precision, Recall, and F1 mathematically."""
        gt_res = rag_engine.evaluate_ground_truth()
        self.assertTrue(gt_res.get("available"))
        self.assertGreater(gt_res.get("test_count", 0), 0)

        # Verify each case's math
        for c in gt_res.get("test_cases", []):
            tp, fp, fn = c["tp"], c["fp"], c["fn"]
            expected_p = tp / float(tp + fp) if (tp + fp) > 0 else 0.0
            expected_r = tp / float(tp + fn) if (tp + fn) > 0 else 0.0
            expected_f1 = (2 * expected_p * expected_r) / (expected_p + expected_r) if (expected_p + expected_r) > 0 else 0.0

            self.assertAlmostEqual(c["precision"], round(expected_p, 4), places=3)
            self.assertAlmostEqual(c["recall"], round(expected_r, 4), places=3)
            self.assertAlmostEqual(c["f1"], round(expected_f1, 4), places=3)

    def test_06_user_precision_calculation(self):
        """Test User Precision@3 calculation rules."""
        items = [
            {"item_id": f"FIELD-p6_{i}", "name": f"p6_{i}", "content": "text", "source": "src", "similarity_score": 90.0 - i}
            for i in range(5)
        ]

        # Fully evaluated top 3: 2 relevant -> 2/3 (ranks 4-5 are ignored)
        q_full = save_query_search("test_06_full", items)
        save_evaluation(q_full, [
            {"item_id": "FIELD-p6_0", "relevance": 1},
            {"item_id": "FIELD-p6_1", "relevance": 0},
            {"item_id": "FIELD-p6_2", "relevance": 1},
            {"item_id": "FIELD-p6_3", "relevance": 1},
        ])

        # Partially evaluated top 3: must be excluded entirely
        q_partial = save_query_search("test_06_partial", items)
        save_evaluation(q_partial, [{"item_id": "FIELD-p6_0", "relevance": 1}])

        metrics = get_user_precision_metrics()
        by_id = {q["query_id"]: q for q in metrics["evaluated_queries_list"]}

        self.assertIn(q_full, by_id)
        self.assertNotIn(q_partial, by_id, "Partially evaluated queries must not be counted")
        row = by_id[q_full]
        # Real displayed count is reported (5), not the top-3 slice
        self.assertEqual(row["displayed_count"], 5)
        self.assertEqual(row["evaluated_count"], 4)
        self.assertEqual(row["relevant_count"], 3)
        # P@3 uses only ranks 1-3: 2 relevant of 3
        self.assertEqual(row["top_count"], 3)
        self.assertEqual(row["top_relevant_count"], 2)
        self.assertAlmostEqual(row["precision_at_3"], round(2 / 3, 4), places=4)
        # Rank 5 was never evaluated, so precision over all displayed is not computed
        self.assertIsNone(row["precision_all"])
        self.assertNotIn("macro_recall_at_3", metrics, "Recall must not be computed from user clicks")

    def test_07_llm_and_web_fallback_when_unconfigured(self):
        """Test honest fallback when LLM / Web Search API keys are missing."""
        from unittest.mock import patch

        with patch("backend.rag_engine.get_api_keys", return_value=(None, None, None)):
            retrieved = rag_engine.search("pol_start_date", top_k=3)
            ans, status = rag_engine.synthesize_answer("pol_start_date", retrieved)
            self.assertEqual(status, "fallback_structured")
            self.assertIn("pol_start_date", ans)
            self.assertIn("วันที่เริ่มต้นความคุ้มครอง", ans)
            self.assertIn("แหล่งอ้างอิง:", ans)
            # Ensure no Markdown decorations, backticks, or emojis
            self.assertNotIn("**", ans)
            self.assertNotIn("###", ans)
            self.assertNotIn("`", ans)
            self.assertNotIn("📌", ans)
            # Ensure no dangling empty 'ความยาว: '
            self.assertNotIn("ความยาว: \n", ans)
            self.assertNotIn("ความยาว:\n", ans)

            ext = rag_engine.external_ai_search("pol_start_date")
            self.assertEqual(ext["status"], "unconfigured")
            self.assertIn("ยังไม่ได้ตั้งค่า Web Search API Key", ext["message"])

    def test_08_result_count_cutoff_and_name_or_detail_matching(self):
        """Test weak matches are cut off (relative score + max cap), not locked to 3, and both ชื่อข้อมูล and รายละเอียดข้อมูล match."""
        from backend.rag_engine import FIELD_MAX_RESULTS, FAQ_MAX_RESULTS
        max_total = FIELD_MAX_RESULTS + FAQ_MAX_RESULTS

        # 1. Search by ชื่อข้อมูล (pol_id): exact field first, weak partial matches cut off
        res_name = rag_engine.search("pol_id", top_k=None)
        self.assertGreater(len(res_name), 0)
        self.assertLessEqual(len(res_name), max_total, "Weak tail matches must be cut off")
        self.assertEqual(res_name[0]["item_id"], "FIELD-pol_id")

        # 2. Search by รายละเอียดข้อมูล (เลขที่กรมธรรม์)
        res_detail = rag_engine.search("เลขที่กรมธรรม์", top_k=None)
        self.assertGreater(len(res_detail), 0)
        self.assertLessEqual(len(res_detail), max_total, "Weak tail matches must be cut off")
        self.assertEqual(res_detail[0]["item_id"], "FIELD-pol_id")

        # A broad query may still return more than 3 (not artificially locked to 3)
        res_broad = rag_engine.search("เบี้ยประกันภัย", top_k=None)
        self.assertGreater(len(res_broad), 3)

        # A precise field name returns just the field, not 80+ partial matches
        res_exact = rag_engine.search_fields("pol_start_date")
        self.assertEqual([f["item_id"] for f in res_exact], ["FIELD-pol_start_date"])

        # 3. Search by รายละเอียดข้อมูล (การต่ออายุ)
        res_renewal = rag_engine.search("การต่ออายุ", top_k=None)
        self.assertGreater(len(res_renewal), 0)
        self.assertIn("FIELD-renewal", [it["item_id"] for it in res_renewal])

        # 4. Search with unrelated query should not return false matches
        res_unrelated = rag_engine.search("xyzabc12345", top_k=None)
        self.assertEqual(len(res_unrelated), 0, "Unrelated query should return 0 items")

    def test_09_ranked_top_k_is_really_k_and_keeps_exact_field(self):
        """search_ranked returns at most k items across Fields+FAQs, and an exact field match is never pushed out by its FAQs."""
        for q in ["pol_license", "pol_status", "pol_start_date"]:
            ranked = rag_engine.search_ranked(q, k=3)
            self.assertLessEqual(len(ranked), 3)
            self.assertIn(f"FIELD-{q}", [it["item_id"] for it in ranked])

    def test_10_aliases_from_rag_dataset(self):
        """Wording users type (aliases in data_rag/) maps to the right field."""
        self.assertEqual(rag_engine.search_fields("ทุนประกัน")[0]["item_id"], "FIELD-cov_amt")
        self.assertEqual(rag_engine.search_fields("เบี้ยประกัน")[0]["item_id"], "FIELD-pol_payment_amount")
        self.assertEqual(rag_engine.search_fields("ผู้เอาประกัน")[0]["item_id"], "FIELD-insureds")

    def test_11_user_feedback_reranks_next_search(self):
        """Evaluations take effect on the next search of the same query: not-relevant hidden, relevant promoted/re-added."""
        from backend.database import get_query_feedback

        query = "  Test_11 Feedback  "
        baseline = rag_engine.search_structured("pol_status")["fields"]
        items = [{"item_id": f["item_id"], "name": f["field_name"], "content": "", "source": "",
                  "similarity_score": 1.0} for f in baseline]
        q_id = save_query_search(query, items + [{"item_id": "FIELD-pol_id", "name": "pol_id", "content": "",
                                                    "source": "", "similarity_score": 1.0}])
        save_evaluation(q_id, [
            {"item_id": "FIELD-pol_status", "relevance": 0},
            {"item_id": "FIELD-pol_id", "relevance": 1},
        ])

        # Case/whitespace-insensitive lookup
        feedback = get_query_feedback("test_11 feedback")
        self.assertEqual(feedback, {"FIELD-pol_status": -1, "FIELD-pol_id": 1})

        fields = rag_engine.search_structured("pol_status", feedback=feedback)["fields"]
        ids = [f["item_id"] for f in fields]
        self.assertNotIn("FIELD-pol_status", ids, "Item rated not relevant must be hidden")
        self.assertEqual(ids[0], "FIELD-pol_id", "Item rated relevant must be first (re-added if cut off)")
        self.assertEqual(fields[0]["user_feedback"], 1)
        self.assertIn("faq_answers", fields[0], "Re-added items still get their FAQ answers")

        # Ground Truth benchmark ignores feedback
        self.assertIn("FIELD-pol_status", [it["item_id"] for it in rag_engine.search_ranked("pol_status", k=3)])

if __name__ == "__main__":
    unittest.main()
