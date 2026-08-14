"""
Script to execute PART A6 verification:
1. POST seed/questionnaires/meridian_health_vendor_questionnaire_v3.csv to /api/runs with buyer_name="Meridian Health Systems".
2. Verifies 25 questions parsed from unseen CSV format.
3. Runs answer-all and verifies answers drafted.
4. Prints question count and 2 drafted answers.
"""

import sys
import os
import certifi
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Credentials come from the process environment (see .env.example).
# Hardcoded key literals are prohibited in this repo.
os.environ["SSL_CERT_FILE"] = certifi.where()

from fastapi.testclient import TestClient
from main import app

SEED_CSV = Path(__file__).resolve().parent.parent / "seed" / "questionnaires" / "meridian_health_vendor_questionnaire_v3.csv"


def verify_a6():
    print("--- PART A6 VERIFICATION ---")

    with TestClient(app) as client:
        file_bytes = SEED_CSV.read_bytes()
        files = {"file": (SEED_CSV.name, file_bytes, "text/csv")}
        data = {"buyer_name": "Meridian Health Systems"}

        print(f"Ingesting {SEED_CSV.name} into real Daytona sandbox...")
        res = client.post("/api/runs", data=data, files=files)
        print("Ingest Response Status:", res.status_code)
        assert res.status_code == 200
        run_id = res.json()["run_id"]

        # Run answer-all
        ans_all_res = client.post(f"/api/runs/{run_id}/answer-all")
        assert ans_all_res.status_code == 200

        # Fetch details
        details_res = client.get(f"/api/runs/{run_id}")
        assert details_res.status_code == 200
        questions = details_res.json()["questions"]

        print(f"\nPARSED QUESTIONS COUNT: {len(questions)}")
        assert len(questions) == 25, f"Expected 25 questions, got {len(questions)}"

        print("\nPRINTING 2 DRAFTED ANSWERS:")
        for idx, q in enumerate(questions[:2]):
            ans = q.get("answer", {})
            print(f"  [{idx+1}] Ref: {q.get('question_ref')}, Section: {q.get('section')}")
            print(f"      Question: {q.get('question_text')}")
            print(f"      Draft Answer: {ans.get('draft_answer')}")
            print(f"      Evidence Ref: {ans.get('evidence_ref')}\n")

        print("PART A6 RESULT: PASS\n")
        return run_id


if __name__ == "__main__":
    verify_a6()
