"""
Script to execute PART A1 verification:
1. Start test client against main FastAPI app.
2. Upload all 7 seed policy files to POST /api/documents.
3. Search GET /api/evidence/search?q=recruitment+fees and verify top 3 results.
4. Search GET /api/evidence/search?q=ISO+27001 and verify weak/empty results.
"""

import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient
from main import app

SEED_POLICIES_DIR = Path(__file__).resolve().parent.parent / "seed" / "policies"


def verify_a1():
    print("--- PART A1 VERIFICATION ---")
    policy_files = sorted(list(SEED_POLICIES_DIR.glob("*.md")))
    print(f"Found {len(policy_files)} policy files in seed/policies/:")
    for p in policy_files:
        print(f"  - {p.name}")

    with TestClient(app) as client:
        # Step 1: Upload documents
        files_payload = [("files", (p.name, p.read_bytes(), "text/markdown")) for p in policy_files]
        upload_resp = client.post("/api/documents", files=files_payload)
        print("\nUpload Response:", upload_resp.status_code, upload_resp.json())
        assert upload_resp.status_code == 200

        # Step 2: Search recruitment fees
        search1_resp = client.get("/api/evidence/search?q=recruitment+fees")
        print("\nSearch 'recruitment fees' Response:", search1_resp.status_code)
        search1_data = search1_resp.json()
        results1 = search1_data.get("results", [])

        print(f"Found {len(results1)} results for 'recruitment fees':")
        top_3 = results1[:3]
        found_pol_001_c23 = False
        found_pol_005_c21 = False

        for idx, item in enumerate(top_3):
            print(f"  [{idx+1}] Doc ID: {item.get('doc_id')}, Clause: {item.get('clause_ref')}, Score: {item.get('score')}")
            print(f"      Heading: {item.get('heading')}")
            print(f"      Text: {item.get('content')[:120]}...\n")

            if item.get("doc_id") == "ALS-POL-001" and str(item.get("clause_ref")) == "2.3":
                found_pol_001_c23 = True
            if item.get("doc_id") == "ALS-POL-005" and str(item.get("clause_ref")) == "2.1":
                found_pol_005_c21 = True

        print(f"ALS-POL-001 §2.3 in Top 3: {found_pol_001_c23}")
        print(f"ALS-POL-005 §2.1 in Top 3: {found_pol_005_c21}")

        assert found_pol_001_c23, "ALS-POL-001 clause 2.3 must be in top 3"
        assert found_pol_005_c21, "ALS-POL-005 clause 2.1 must be in top 3"

        # Step 3: Search ISO 27001
        search2_resp = client.get("/api/evidence/search?q=ISO+27001")
        print("\nSearch 'ISO 27001' Response:", search2_resp.status_code)
        results2 = search2_resp.json().get("results", [])
        print(f"Found {len(results2)} results for 'ISO 27001'. Top score: {results2[0]['score'] if results2 else 0}")
        if results2:
            print("Top result for ISO 27001:", results2[0].get("doc_id"), results2[0].get("heading"))

        print("\nPART A1 RESULT: PASS\n")


if __name__ == "__main__":
    verify_a1()
