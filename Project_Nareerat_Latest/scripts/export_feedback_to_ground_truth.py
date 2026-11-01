"""
Turn user evaluations (Human-in-the-Loop) into Ground Truth test cases.

A query becomes a candidate when:
- at least one search of it had EVERY displayed item evaluated (so the relevant set is complete), and
- at least one item has net feedback > 0 (เกี่ยวข้อง more often than ไม่เกี่ยวข้อง).
Queries already in the ground truth are skipped (reported if the user feedback disagrees).

Usage:
  python scripts/export_feedback_to_ground_truth.py           # write data/ground_truth_candidates.json for review
  python scripts/export_feedback_to_ground_truth.py --merge   # also append candidates to data/ground_truth.json (backup kept)
"""
import json
import os
import shutil
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from backend.database import get_feedback_by_query, normalize_query  # noqa: E402

GT_FILE = os.path.join(ROOT, "data", "ground_truth.json")
CANDIDATES_FILE = os.path.join(ROOT, "data", "ground_truth_candidates.json")


def main():
    merge = "--merge" in sys.argv[1:]

    with open(GT_FILE, encoding="utf-8") as f:
        ground_truth = json.load(f)
    existing = {normalize_query(c["query"]): c for c in ground_truth}

    candidates, conflicts, incomplete = [], [], []
    for key, entry in sorted(get_feedback_by_query().items()):
        relevant = sorted(i for i, net in entry["items"].items() if net > 0)
        if not relevant:
            continue
        if key in existing:
            if sorted(existing[key]["expected_item_ids"]) != relevant:
                conflicts.append({"query": entry["query"],
                                  "ground_truth": sorted(existing[key]["expected_item_ids"]),
                                  "user_feedback": relevant})
            continue
        if not entry["fully_evaluated"]:
            incomplete.append(entry["query"])
            continue
        candidates.append({
            "query": entry["query"],
            "description": "สร้างจากผลประเมินของผู้ใช้ (Human-in-the-Loop) - รอตรวจสอบ",
            "expected_item_ids": relevant,
            "source": "user_feedback",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })

    with open(CANDIDATES_FILE, "w", encoding="utf-8") as f:
        json.dump({"candidates": candidates, "conflicts_with_ground_truth": conflicts,
                   "not_fully_evaluated": incomplete}, f, ensure_ascii=False, indent=2)

    print(f"candidates: {len(candidates)}  conflicts: {len(conflicts)}  not fully evaluated: {len(incomplete)}")
    print(f"written: {CANDIDATES_FILE}")
    for c in candidates:
        print(f"  + {c['query']} -> {c['expected_item_ids']}")
    for c in conflicts:
        print(f"  ! {c['query']}: ground truth {c['ground_truth']} vs feedback {c['user_feedback']}")

    if merge and candidates:
        backup = GT_FILE + datetime.now().strftime(".%Y%m%d-%H%M%S.bak")
        shutil.copy2(GT_FILE, backup)
        with open(GT_FILE, "w", encoding="utf-8") as f:
            json.dump(ground_truth + candidates, f, ensure_ascii=False, indent=2)
        print(f"merged {len(candidates)} case(s) into {GT_FILE} (backup: {backup})")
        print("re-run scripts/build_rag_dataset.py to refresh data_rag/ground_truth.json")


if __name__ == "__main__":
    main()
