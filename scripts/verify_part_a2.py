"""
Script to execute PART A2 verification:
1. POST seed/questionnaires/halberd_staffing_supplier_risk_assessment_FY26.xlsx to /api/runs with buyer_name="Halberd Staffing Group".
2. Uses REAL Daytona Sandbox API.
3. Prints real Sandbox ID, question count, first 3 questions, last 2 questions, and all SandboxEvent logs.
"""

import sys
import os
import json
import certifi
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Ensure SSL and Daytona API key are set
# Credentials come from the process environment (see .env.example).
# Hardcoded key literals are prohibited in this repo.
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

from fastapi.testclient import TestClient
from main import app

SEED_QUESTIONNAIRE = Path(__file__).resolve().parent.parent / "seed" / "questionnaires" / "halberd_staffing_supplier_risk_assessment_FY26.xlsx"


def verify_a2():
    print("--- PART A2 VERIFICATION ---")
    assert SEED_QUESTIONNAIRE.exists(), f"Seed file missing at {SEED_QUESTIONNAIRE}"

    with TestClient(app) as client:
        file_bytes = SEED_QUESTIONNAIRE.read_bytes()
        files = {"file": (SEED_QUESTIONNAIRE.name, file_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        data = {"buyer_name": "Halberd Staffing Group"}

        print(f"Ingesting {SEED_QUESTIONNAIRE.name} into real Daytona sandbox...")
        res = client.post("/api/runs", data=data, files=files)
        print("Response status:", res.status_code)
        assert res.status_code == 200, f"Error: {res.text}"
        res_json = res.json()
        run_id = res_json["run_id"]

        # Fetch details
        details_res = client.get(f"/api/runs/{run_id}")
        assert details_res.status_code == 200
        details = details_res.json()

        sandbox_id = details["run"]["sandbox_id"]
        questions = details["questions"]
        events = details["sandbox_events"]

        print(f"\nREAL DAYTONA SANDBOX ID: {sandbox_id}")
        print(f"TOTAL QUESTIONS RETURNED: {len(questions)}")

        print("\nFIRST 3 QUESTIONS:")
        for q in questions[:3]:
            print(f"  Ref: {q.get('question_ref')}, Section: {q.get('section')}, Text: {q.get('question_text')}")

        print("\nLAST 2 QUESTIONS:")
        for q in questions[-2:]:
            print(f"  Ref: {q.get('question_ref')}, Section: {q.get('section')}, Text: {q.get('question_text')}")

        print("\nPERSISTED SANDBOX EVENTS:")
        for idx, evt in enumerate(events):
            print(f"  [{idx+1}] Event: {evt.get('event_type')}, Details: {evt.get('details_json')}")

        print("\nPART A2 RESULT: PASS\n")
        return run_id


if __name__ == "__main__":
    verify_a2()
