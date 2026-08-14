import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from main import app

SEED_POLICIES_DIR = Path(__file__).resolve().parent.parent.parent / "seed" / "policies"


def test_batch2_answer_engine_happy_path(temp_app_settings):
    """
    Happy-path acceptance test for Batch 2:
    1. Uploads policy documents.
    2. Tests POST /api/answer with a supported question: 'Does Altura Language Services permit worker-paid recruitment fees?'
       Asserts status is 'answered', confidence > 0, citation doc is valid.
    3. Tests relevance floor guardrail with an unsupported question: 'What is Altura Language Services policy on rocket propulsion?'
       Asserts status is 'gap' immediately with empty answer string and zero citations.
    """
    policy_files = list(SEED_POLICIES_DIR.glob("*.md"))
    files_payload = [("files", (p.name, p.read_bytes(), "text/markdown")) for p in policy_files]

    with TestClient(app) as client:
        # Ingest policies
        upload_resp = client.post("/api/documents", files=files_payload)
        assert upload_resp.status_code == 200

        # Supported question
        supported_req = {
            "question": "Does Altura Language Services permit worker-paid recruitment fees?",
            "answer_type": "yes_no",
            "buyer_name": "Altura Language Services Buyer Assessment",
        }
        ans_resp = client.post("/api/answer", json=supported_req)
        assert ans_resp.status_code == 200
        ans_data = ans_resp.json()

        assert ans_data["status"] in ("answered", "partial")
        assert ans_data["confidence"] > 0
        assert len(ans_data["citations"]) >= 1
        assert ans_data["citations"][0]["doc_id"] in ("ALS-POL-001", "ALS-POL-005")

        # Unsupported question (Relevance Floor Guardrail)
        gap_req = {
            "question": "What is Altura Language Services policy on interplanetary rocket propulsion fuel?",
            "answer_type": "free_text",
            "buyer_name": "Space Buyer",
        }
        gap_resp = client.post("/api/answer", json=gap_req)
        assert gap_resp.status_code == 200
        gap_data = gap_resp.json()

        assert gap_data["status"] == "gap"
        assert gap_data["answer"] == ""
        assert gap_data["citations"] == []
