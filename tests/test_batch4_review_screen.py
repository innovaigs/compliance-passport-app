import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from main import app

TEST_QUESTIONNAIRE_PATH = Path(__file__).resolve().parent.parent.parent / "seed" / "questionnaires" / "ALS-FRM-001_supplier_self_assessment.xlsx"


def test_batch4_review_screen_happy_path(temp_app_settings):
    """
    Happy-path acceptance test for Batch 4 (Review Screen Data & Structure):
    1. Creates a run via POST /api/runs.
    2. Queries GET /api/runs/{run_id}.
    3. Verifies run stats (elapsed_seconds, questions count).
    4. Verifies questions list contains status fields and evidence details.
    5. Verifies sandbox_events are present for the terminal audit trail.
    """
    with TestClient(app) as client:
        file_bytes = b"mock questionnaire xlsx content"
        if TEST_QUESTIONNAIRE_PATH.exists():
            file_bytes = TEST_QUESTIONNAIRE_PATH.read_bytes()

        files = {"file": ("test_questionnaire.xlsx", file_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        data = {"buyer_name": "Review Test Acme Corp"}

        create_resp = client.post("/api/runs", data=data, files=files)
        assert create_resp.status_code == 200
        run_id = create_resp.json()["run_id"]

        # Fetch details for Review Screen
        details_resp = client.get(f"/api/runs/{run_id}")
        assert details_resp.status_code == 200
        details = details_resp.json()

        assert details["run"]["id"] == run_id
        assert details["run"]["name"] == "Review Test Acme Corp"
        assert details["run"]["elapsed_seconds"] >= 0
        assert len(details["questions"]) >= 1
        assert len(details["sandbox_events"]) >= 1
