from fastapi.testclient import TestClient
from main import app, get_health

client = TestClient(app)


def test_unit_get_health():
    """Unit test for health check response logic."""
    data = get_health()
    assert data["status"] == "healthy"
    assert data["service_name"] == "Compliance Passport"
    assert "timestamp" in data
    assert len(data["timestamp"]) > 0


def test_integration_health_endpoint():
    """Integration test verifying GET /api/health boundary and HTTP contract."""
    response = client.get("/api/health")
    assert response.status_code == 200
    json_data = response.json()
    assert json_data["status"] == "healthy"
    assert json_data["service_name"] == "Compliance Passport"
    assert "timestamp" in json_data
