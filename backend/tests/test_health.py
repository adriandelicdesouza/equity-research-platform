from fastapi.testclient import TestClient


def test_health_reports_database_and_versions(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["environment"] == "test"
    assert body["engine_version"]
    assert "x-request-id" in r.headers
