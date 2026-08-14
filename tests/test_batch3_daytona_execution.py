import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from main import app

TEST_QUESTIONNAIRE_PATH = Path(__file__).resolve().parent.parent.parent / "seed" / "questionnaires" / "ALS-FRM-001_supplier_self_assessment.xlsx"


def test_batch3_daytona_execution_happy_path(temp_app_settings):
    """
    Happy-path acceptance test for Batch 3:
    1. Posts a buyer questionnaire file to POST /api/runs.
    2. Asserts run is created with sandbox_id and parsed questions.
    3. Fetches GET /api/runs/{id} and verifies SandboxEvent logs are persisted.
    """
    with TestClient(app) as client:
        file_bytes = b"mock questionnaire xlsx content"
        if TEST_QUESTIONNAIRE_PATH.exists():
            file_bytes = TEST_QUESTIONNAIRE_PATH.read_bytes()

        files = {"file": ("test_questionnaire.xlsx", file_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        data = {"buyer_name": "Test Acme Corp"}

        create_resp = client.post("/api/runs", data=data, files=files)
        assert create_resp.status_code == 200
        run_data = create_resp.json()
        assert run_data["status"] == "success"
        run_id = run_data["run_id"]
        assert run_data["questions_count"] >= 1

        # Fetch details
        details_resp = client.get(f"/api/runs/{run_id}")
        assert details_resp.status_code == 200
        details = details_resp.json()

        assert details["run"]["id"] == run_id
        assert details["run"]["sandbox_id"] is not None
        assert len(details["questions"]) >= 1
        assert len(details["sandbox_events"]) >= 1
