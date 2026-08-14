import os
import glob
from pathlib import Path
from fastapi.testclient import TestClient
from main import app

SEED_POLICIES_DIR = Path(__file__).resolve().parent.parent.parent / "seed" / "policies"


def test_batch1_evidence_library_happy_path(temp_app_settings):
    """
    Happy-path acceptance test for Batch 1:
    1. Uploads all 7 seed policy files to POST /api/documents.
    2. Searches GET /api/evidence/search?q=recruitment fees.
    3. Asserts ALS-POL-001 §2.3 and ALS-POL-005 §2.1 are in top three results.
    """
    policy_files = list(SEED_POLICIES_DIR.glob("*.md"))
    assert len(policy_files) == 7, f"Expected 7 policy files in {SEED_POLICIES_DIR}, found {len(policy_files)}"

    files_payload = []
    for ppath in policy_files:
        files_payload.append(
            ("files", (ppath.name, ppath.read_bytes(), "text/markdown"))
        )

    with TestClient(app) as client:
        # Upload 7 policies
        upload_resp = client.post("/api/documents", files=files_payload)
        assert upload_resp.status_code == 200
        upload_data = upload_resp.json()
        assert upload_data["status"] == "success"
        assert upload_data["count"] == 7

        # List documents
        list_resp = client.get("/api/documents")
        assert list_resp.status_code == 200
        docs = list_resp.json()["documents"]
        assert len(docs) == 7

        # Search "recruitment fees"
        search_resp = client.get("/api/evidence/search?q=recruitment%20fees")
        assert search_resp.status_code == 200
        results = search_resp.json()["results"]
        assert len(results) >= 2

        top3 = results[:3]
        top3_matches = [(r["doc_id"], r["clause_ref"]) for r in top3]

        assert ("ALS-POL-001", "2.3") in top3_matches, f"Expected ('ALS-POL-001', '2.3') in top 3, got {top3_matches}"
        assert ("ALS-POL-005", "2.1") in top3_matches, f"Expected ('ALS-POL-005', '2.1') in top 3, got {top3_matches}"
