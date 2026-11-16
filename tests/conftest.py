import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from backend.store import FileOrMemoryStore

@pytest.fixture(autouse=True)
def isolate_store(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        test_store = FileOrMemoryStore(tmpdir)
        monkeypatch.setattr("backend.main.store", test_store)
        monkeypatch.setattr("backend.main.CACHE_DIR", tmpdir)
        yield test_store
