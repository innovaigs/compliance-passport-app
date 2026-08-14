import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from main import app

TEST_QUESTIONNAIRE_PATH = Path(__file__).resolve().parent.parent.parent / "seed" / "questionnaires" / "ALS-FRM-001_supplier_self_assessment.xlsx"


def test_batch5_export_happy_path(temp_app_settings):
    """
    Happy-path acceptance test for Batch 5 (Export Layer):
    1. Ingests a questionnaire run.
    2. Answers questions.
    3. Calls POST /api/runs/{run_id}/export.
    4. Verifies response returns filled file bytes with _COMPLETED filename header.
    """
    with TestClient(app) as client:
        file_bytes = b"mock questionnaire xlsx content"
        if TEST_QUESTIONNAIRE_PATH.exists():
            file_bytes = TEST_QUESTIONNAIRE_PATH.read_bytes()

        files = {"file": ("test_questionnaire.xlsx", file_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        data = {"buyer_name": "Export Test Acme Corp"}

        create_resp = client.post("/api/runs", data=data, files=files)
        assert create_resp.status_code == 200
        run_id = create_resp.json()["run_id"]

        # Answer questions
        ans_resp = client.post(f"/api/runs/{run_id}/answer-all")
        assert ans_resp.status_code == 200

        # Export filled file
        export_resp = client.post(f"/api/runs/{run_id}/export")
        assert export_resp.status_code == 200
        assert len(export_resp.content) > 0
        disp = export_resp.headers.get("content-disposition", "")
        assert "COMPLETED" in disp
