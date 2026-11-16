import os
import sys
from fastapi.testclient import TestClient

# Ensure backend directory is in path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from backend.main import app

client = TestClient(app)

def test_health_endpoints():
    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert "runtime_mode" in data
    assert "version" in data

    res = client.get("/ready")
    assert res.status_code == 200
    assert res.json()["status"] == "ready"

    res = client.get("/version")
    assert res.status_code == 200
    assert "version" in res.json()


def test_invalid_repo_url():
    res = client.post("/analyze", json={"repo_url": "https://notgithub.com/bad/repo"})
    assert res.status_code == 400
    assert "github.com" in res.json()["detail"]


def test_clone_failure_handling(monkeypatch):
    def mock_clone(url, dest, timeout=30):
        raise RuntimeError("Mock clone connection failure")
    monkeypatch.setattr("backend.main.clone_repo", mock_clone)

    res = client.post("/analyze", json={"repo_url": "https://github.com/valid/repo"})
    assert res.status_code == 400
    assert "Failed to clone repository" in res.json()["detail"]


def test_analysis_and_cache_hit(monkeypatch):
    test_hash = "abc123commit456"

    # Mock clone_repo to create synthetic files in dest_dir
    def mock_clone(url, dest, timeout=30):
        os.makedirs(os.path.join(dest, "pkg"), exist_ok=True)
        with open(os.path.join(dest, "main.py"), "w") as f:
            f.write("from pkg import helper\n\ndef main():\n    helper.run()\n")
        with open(os.path.join(dest, "pkg", "helper.py"), "w") as f:
            f.write("def run():\n    return 'running'\n")
        return test_hash

    monkeypatch.setattr("backend.main.clone_repo", mock_clone)
    monkeypatch.setenv("OPENAI_API_KEY", "") # Test Heuristic mode

    # 1. First run: Cache Miss
    res1 = client.post("/analyze", json={"repo_url": "https://github.com/testowner/testrepo"})
    assert res1.status_code == 200, res1.text
    data1 = res1.json()
    assert data1["cache_hit"] is False
    assert data1["runtime_mode"] == "Heuristic mode"
    assert "graph_data" in data1
    assert "architecture_overview" in data1
    assert len(data1["start_here"]) > 0
    repo_id = data1["repo_id"]

    # 2. Second run: Cache Hit
    res2 = client.post("/analyze", json={"repo_url": "https://github.com/testowner/testrepo"})
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["cache_hit"] is True
    assert data2["repo_id"] == repo_id
    assert res2.headers.get("x-cache") == "HIT"

    # 3. Chat with codebase
    chat_res = client.post("/chat", json={"repo_id": repo_id, "question": "What does helper.py do?"})
    assert chat_res.status_code == 200
    chat_data = chat_res.json()
    assert chat_data["runtime_mode"] == "Heuristic mode"
    assert "file://" not in chat_data["answer"]
    assert "github.com/testowner/testrepo/blob" in chat_data["answer"]


def test_rate_limit_exceeded(monkeypatch):
    # Set user limit to 1
    monkeypatch.setattr("backend.main.USER_SEARCH_LIMIT", 1)

    def mock_clone(url, dest, timeout=30):
        with open(os.path.join(dest, "app.py"), "w") as f:
            f.write("print('hello')\n")
        return "commit12345"

    monkeypatch.setattr("backend.main.clone_repo", mock_clone)

    # First request succeeds
    res1 = client.post("/analyze", json={
        "repo_url": "https://github.com/testlimit/repo1",
        "device_id": "test_unique_dev_999"
    })
    assert res1.status_code == 200

    # Second request hits 429
    res2 = client.post("/analyze", json={
        "repo_url": "https://github.com/testlimit/repo2",
        "device_id": "test_unique_dev_999"
    })
    assert res2.status_code == 429
    assert "reached your limit" in res2.json()["detail"]


def test_analysis_timeout(monkeypatch):
    def mock_timeout_clone(url, dest, timeout=30):
        raise RuntimeError("Cloning process timed out after 30 seconds.")
    monkeypatch.setattr("backend.main.clone_repo", mock_timeout_clone)

    res = client.post("/analyze", json={"repo_url": "https://github.com/timeout/testrepo"})
    assert res.status_code == 400
    assert "Failed to clone repository" in res.json()["detail"]


def test_unavailable_model_provider_chat_fallback(monkeypatch):
    # Ensure OPENAI_API_KEY is not set
    monkeypatch.setenv("OPENAI_API_KEY", "")

    # Pre-populate store with dummy analysis
    dummy_repo_id = "dummy_offline_test_id"
    payload = {
        "repo_id": dummy_repo_id,
        "repo_url": "https://github.com/org/sample",
        "commit_hash": "deadbeef1234",
        "runtime_mode": "Heuristic mode",
        "rag_chunks": [
            {"path": "core/engine.py", "text": "File Path: core/engine.py\nSummary: Main compute engine."}
        ],
        "architecture_overview": "Sample architecture"
    }
    import backend.main as main
    main.store.set_cache(dummy_repo_id, payload)

    chat_res = client.post("/chat", json={"repo_id": dummy_repo_id, "question": "Where is the compute engine?"})
    assert chat_res.status_code == 200
    data = chat_res.json()
    assert data["runtime_mode"] == "Heuristic mode"
    assert "Heuristic Search Results" in data["answer"]
    assert "https://github.com/org/sample/blob/deadbeef1234/core/engine.py" in data["answer"]
    assert "file://" not in data["answer"]
