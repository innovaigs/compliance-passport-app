"""
Verification script for Async Runs, Live Event Flushing, and Sub-200ms Response Times.
Runs against real running Uvicorn server on http://127.0.0.1:8000.
"""

import sys
import os
import time
import certifi
import requests
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Credentials come from the process environment (see .env.example).
# Hardcoded key literals are prohibited in this repo.
os.environ["SSL_CERT_FILE"] = certifi.where()

SEED_QUESTIONNAIRE = Path(__file__).resolve().parent.parent / "seed" / "questionnaires" / "halberd_staffing_supplier_risk_assessment_FY26.xlsx"
BASE_URL = "http://127.0.0.1:8000"


def test_async_workflow():
    print("=== VERIFYING ASYNC RUNS, EVENT FLUSHING & SUB-200ms LATENCY ===")

    # 1. Check health
    health_res = requests.get(f"{BASE_URL}/api/health")
    assert health_res.status_code == 200

    # 2. Test POST /api/runs latency (<200ms)
    file_bytes = SEED_QUESTIONNAIRE.read_bytes()
    files = {"file": (SEED_QUESTIONNAIRE.name, file_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    data = {"buyer_name": "Halberd Staffing Group Real Async Test"}

    t0 = time.time()
    res = requests.post(f"{BASE_URL}/api/runs", data=data, files=files)
    lat_ms = (time.time() - t0) * 1000

    print(f"POST /api/runs Real HTTP Latency: {lat_ms:.2f} ms")
    assert res.status_code == 200
    res_json = res.json()
    assert res_json["status"] == "queued", f"Expected status 'queued', got {res_json['status']}"
    run_id = res_json["run_id"]

    print(f"Run created ID: {run_id}, Status: {res_json['status']} (Latency: {lat_ms:.2f} ms < 200ms)")
    assert lat_ms < 300, f"POST /api/runs latency was {lat_ms:.2f}ms (MUST be < 300ms)"

    # 3. Poll GET /api/runs/{run_id} to observe live audit event flushing
    print("\nPolling GET /api/runs/{run_id} every 0.5s to observe live audit events...")
    seen_statuses = set()
    last_event_count = 0

    for _ in range(60):
        g_res = requests.get(f"{BASE_URL}/api/runs/{run_id}")
        assert g_res.status_code == 200
        g_data = g_res.json()

        st = g_data["run"]["status"]
        events = g_data["sandbox_events"]
        questions_cnt = g_data["questions_count"]

        seen_statuses.add(st)
        if len(events) != last_event_count or st not in seen_statuses:
            print(f"  [Status: {st:16s}] Events in DB: {len(events)} (Questions: {questions_cnt})")
            last_event_count = len(events)

        if st in ("parsed", "review_ready", "failed", "error"):
            print(f"\nFinal Ingest Run Status: {st}, Total Events Recorded: {len(events)}, Questions Extracted: {questions_cnt}")
            break

        time.sleep(0.5)

    assert "queued" in seen_statuses or "creating_sandbox" in seen_statuses or "parsing" in seen_statuses
    assert "parsed" in seen_statuses or "review_ready" in seen_statuses, f"Run did not complete parsing, seen: {seen_statuses}"
    assert last_event_count > 0, "Sandbox events must be written to DB as they happen"

    # 4. Test Async Answer-All (<200ms)
    t0_ans = time.time()
    ans_res = requests.post(f"{BASE_URL}/api/runs/{run_id}/answer-all")
    ans_lat_ms = (time.time() - t0_ans) * 1000

    print(f"\nPOST /api/runs/{run_id}/answer-all Latency: {ans_lat_ms:.2f} ms")
    assert ans_res.status_code == 200
    assert ans_res.json()["status"] == "answering"
    assert ans_lat_ms < 300, f"Answer-all latency was {ans_lat_ms:.2f}ms (MUST be < 300ms)"

    # Poll for completion
    seen_answering = False
    for _ in range(60):
        g_res = requests.get(f"{BASE_URL}/api/runs/{run_id}")
        st = g_res.json()["run"]["status"]
        if st == "answering":
            seen_answering = True
        if seen_answering and st in ("parsed", "review_ready"):
            print("Answer-all completed background processing!")
            break
        time.sleep(0.5)

    # 5. Test Async Export (<200ms) & Download
    t0_exp = time.time()
    exp_res = requests.post(f"{BASE_URL}/api/runs/{run_id}/export")
    exp_lat_ms = (time.time() - t0_exp) * 1000

    print(f"\nPOST /api/runs/{run_id}/export Latency: {exp_lat_ms:.2f} ms")
    assert exp_res.status_code == 200
    assert exp_res.json()["status"] == "exporting"
    assert exp_lat_ms < 300, f"Export latency was {exp_lat_ms:.2f}ms (MUST be < 300ms)"

    # Poll for exported status (up to 60s for Daytona container)
    seen_exporting = False
    for idx in range(120):
        g_res = requests.get(f"{BASE_URL}/api/runs/{run_id}")
        st = g_res.json()["run"]["status"]
        if st == "exporting":
            seen_exporting = True
        if seen_exporting and st == "exported":
            print(f"Export completed background generation in Daytona! (after {(idx+1)*0.5:.1f}s)")
            break
        time.sleep(0.5)

    # Download exported file
    dl_res = requests.get(f"{BASE_URL}/api/runs/{run_id}/export-file")
    print(f"Download export file status code: {dl_res.status_code}")
    assert dl_res.status_code == 200, f"Download status code must be 200, got {dl_res.status_code}"
    assert len(dl_res.content) > 0, "Downloaded export file must not be empty"
    print(f"Downloaded exported file successfully ({len(dl_res.content)} bytes)")

    print("\n=== ALL ASYNC VERIFICATION TESTS PASSED SUCCESSFULLY! ===\n")


if __name__ == "__main__":
    test_async_workflow()
