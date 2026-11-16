import os
import tempfile
from backend.store import FileOrMemoryStore

def test_store_user_rate_limiting():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = FileOrMemoryStore(tmpdir)
        user_id = "test_user_ip_123"

        # Limit is 3
        assert store.check_user_limit(user_id, limit=3) is True
        assert store.increment_user_counter(user_id) == 1
        assert store.check_user_limit(user_id, limit=3) is True
        assert store.increment_user_counter(user_id) == 2
        assert store.increment_user_counter(user_id) == 3
        # Exceeded
        assert store.check_user_limit(user_id, limit=3) is False


def test_store_daily_limits():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = FileOrMemoryStore(tmpdir)
        repo1 = "https://github.com/owner/repo1"
        repo2 = "https://github.com/owner/repo2"

        assert store.check_daily_limit(limit=2) is True
        assert store.increment_daily_counter(repo1) == 1
        # Re-adding same repo doesn't increment count
        assert store.increment_daily_counter(repo1) == 1
        assert store.check_daily_limit(limit=2) is True
        assert store.increment_daily_counter(repo2) == 2
        # Limit reached
        assert store.check_daily_limit(limit=2) is False


def test_store_cache_operations():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = FileOrMemoryStore(tmpdir)
        key = "sample_cache_key_abc"
        payload = {"data": "test_analysis", "score": 42}

        assert store.get_cache(key) is None
        assert store.set_cache(key, payload) is True
        cached = store.get_cache(key)
        assert cached is not None
        assert cached["data"] == "test_analysis"
        assert cached["score"] == 42


def test_store_persistence_failure_resilience(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        store = FileOrMemoryStore(tmpdir)
        
        # Simulate os.replace failure during counter write
        def broken_replace(src, dst):
            raise OSError("Disk quota exceeded / permission denied")
        monkeypatch.setattr(os, "replace", broken_replace)

        # Counter should handle the error gracefully without raising unhandled exception
        count = store.increment_user_counter("client_123")
        assert count == 1
