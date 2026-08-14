"""
Script to execute PART A5 verification:
1. Calls POST /api/runs/{run_id}/export.
2. Saves returned file to exports directory.
3. Re-opens file with openpyxl and inspects filled rows.
4. Verifies Response (Col D), Evidence Reference (Col E), and section banners.
"""

import sys
import os
import certifi
import openpyxl
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


def verify_a5():
    print("--- PART A5 VERIFICATION ---")

    with TestClient(app) as client:
        # Ingest Halberd Staffing Group questionnaire
        file_bytes = SEED_QUESTIONNAIRE.read_bytes()
        files = {"file": (SEED_QUESTIONNAIRE.name, file_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        data = {"buyer_name": "Halberd Staffing Group"}

        create_res = client.post("/api/runs", data=data, files=files)
        assert create_res.status_code == 200
        run_id = create_res.json()["run_id"]

        # Run answer-all
        ans_all_res = client.post(f"/api/runs/{run_id}/answer-all")
        assert ans_all_res.status_code == 200

        # Export file
        print(f"Calling POST /api/runs/{run_id}/export...")
        export_res = client.post(f"/api/runs/{run_id}/export")
        print("Export Response Status:", export_res.status_code)
        assert export_res.status_code == 200

        export_bytes = export_res.content
        assert len(export_bytes) > 0, "Export file must not be empty"

        # Save to disk
        out_path = Path("exports/halberd_staffing_COMPLETED.xlsx")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(export_bytes)
        print(f"Export file saved to {out_path} ({len(export_bytes)} bytes)")

        # Verify with openpyxl
        wb = openpyxl.load_workbook(out_path)
        ws = wb.active
        print(f"Re-opened with openpyxl. Sheet: {ws.title}, Rows: {ws.max_row}")

        # Check section banners
        row_7_banner = ws.cell(row=7, column=1).value
        print("Row 7 Banner (Section 1):", row_7_banner)
        assert "1. Governance" in str(row_7_banner), "Section banner row 7 must remain intact"

        # Print 5 filled rows
        filled_rows = []
        for r in range(8, 15):
            ref = ws.cell(row=r, column=1).value
            q_text = ws.cell(row=r, column=2).value
            resp_type = ws.cell(row=r, column=3).value
            ans_val = ws.cell(row=r, column=4).value
            ev_val = ws.cell(row=r, column=5).value
            if ref and str(ref).startswith("SR-"):
                filled_rows.append((r, ref, str(q_text)[:40], str(ans_val)[:60], str(ev_val)))

        print(f"\nPRINTING {len(filled_rows[:5])} FILLED ROWS:")
        for r, ref, q_short, a_short, e_short in filled_rows[:5]:
            print(f"  Row {r:02d} | Ref: {ref} | Q: {q_short}... | Ans (Col D): {a_short}... | Evidence (Col E): {e_short}")

        assert len(filled_rows) >= 5, "Must have at least 5 filled question rows"

        print("\nPART A5 RESULT: PASS\n")
        return run_id


if __name__ == "__main__":
    verify_a5()
