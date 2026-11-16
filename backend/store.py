import os
import json
import datetime
import threading
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger("repomind.store")

class BaseStore:
    def check_user_limit(self, client_id: str, limit: int = 5) -> bool:
        raise NotImplementedError

    def increment_user_counter(self, client_id: str) -> int:
        raise NotImplementedError

    def check_daily_limit(self, limit: int = 20) -> bool:
        raise NotImplementedError

    def increment_daily_counter(self, repo_url: str) -> int:
        raise NotImplementedError

    def get_cache(self, key: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def set_cache(self, key: str, payload: Dict[str, Any]) -> bool:
        raise NotImplementedError


class FileOrMemoryStore(BaseStore):
    """
    Thread-safe, atomic file-backed store with in-memory caching.
    Uses atomic file replacement (write to temp then os.replace) to prevent corruption.
    """
    def __init__(self, cache_dir: str):
        self.cache_dir = cache_dir
        os.makedirs(self.cache_dir, exist_ok=True)
        self.counter_file = os.path.join(self.cache_dir, "daily_counter.json")
        self.user_limits_file = os.path.join(self.cache_dir, "user_limits.json")
        self.lock = threading.Lock()
        self.mode = "file_store"

    def _atomic_write_json(self, file_path: str, data: Any):
        temp_file = f"{file_path}.tmp.{os.getpid()}.{threading.get_ident()}"
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(temp_file, file_path)

    def _read_json(self, file_path: str, default: Any) -> Any:
        if not os.path.exists(file_path):
            return default
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning("Error reading JSON from %s: %s", file_path, e)
            return default

    def check_user_limit(self, client_id: str, limit: int = 5) -> bool:
        with self.lock:
            limits = self._read_json(self.user_limits_file, {})
            current_count = limits.get(client_id, 0)
            return current_count < limit

    def increment_user_counter(self, client_id: str) -> int:
        with self.lock:
            limits = self._read_json(self.user_limits_file, {})
            new_count = limits.get(client_id, 0) + 1
            limits[client_id] = new_count
            try:
                self._atomic_write_json(self.user_limits_file, limits)
            except Exception as e:
                logger.error("Failed to persist user counter: %s", e)
            return new_count

    def check_daily_limit(self, limit: int = 20) -> bool:
        today = datetime.date.today().isoformat()
        with self.lock:
            counter = self._read_json(self.counter_file, {})
            day_entry = counter.get(today, {"count": 0, "repos": []})
            return day_entry.get("count", 0) < limit

    def increment_daily_counter(self, repo_url: str) -> int:
        today = datetime.date.today().isoformat()
        with self.lock:
            counter = self._read_json(self.counter_file, {})
            day_entry = counter.setdefault(today, {"count": 0, "repos": []})
            if repo_url not in day_entry.get("repos", []):
                day_entry.setdefault("repos", []).append(repo_url)
                day_entry["count"] = day_entry.get("count", 0) + 1
                try:
                    self._atomic_write_json(self.counter_file, counter)
                except Exception as e:
                    logger.error("Failed to persist daily counter: %s", e)
            return day_entry["count"]

    def get_cache(self, key: str) -> Optional[Dict[str, Any]]:
        # Key sanitization to prevent path traversal
        clean_key = os.path.basename(key)
        cache_file = os.path.join(self.cache_dir, f"{clean_key}.json")
        if not os.path.exists(cache_file):
            return None
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning("Error reading cache for %s: %s", key, e)
            return None

    def set_cache(self, key: str, payload: Dict[str, Any]) -> bool:
        clean_key = os.path.basename(key)
        cache_file = os.path.join(self.cache_dir, f"{clean_key}.json")
        try:
            with self.lock:
                self._atomic_write_json(cache_file, payload)
            return True
        except Exception as e:
            logger.error("Error writing cache for %s: %s", key, e)
            return False


class RedisStore(BaseStore):
    """
    Redis-backed durable store for rate limiting and cache metadata.
    """
    def __init__(self, redis_url: str, fallback_store: BaseStore):
        self.fallback = fallback_store
        self.mode = "redis"
        try:
            import redis
            self.client = redis.from_url(redis_url, socket_timeout=3, socket_connect_timeout=3)
            self.client.ping()
            logger.info("Connected successfully to Redis at %s", redis_url.split("@")[-1])
        except Exception as e:
            logger.warning("Redis connection failed (%s). Falling back to local store.", e)
            self.client = None
            self.mode = "file_fallback"

    def check_user_limit(self, client_id: str, limit: int = 5) -> bool:
        if not self.client:
            return self.fallback.check_user_limit(client_id, limit)
        try:
            count = int(self.client.get(f"repomind:user:{client_id}") or 0)
            return count < limit
        except Exception as e:
            logger.warning("Redis check_user_limit error (%s), using fallback", e)
            return self.fallback.check_user_limit(client_id, limit)

    def increment_user_counter(self, client_id: str) -> int:
        if not self.client:
            return self.fallback.increment_user_counter(client_id)
        try:
            key = f"repomind:user:{client_id}"
            count = self.client.incr(key)
            # 24-hour expiry on user window
            if count == 1:
                self.client.expire(key, 86400)
            return count
        except Exception as e:
            logger.warning("Redis increment_user_counter error (%s), using fallback", e)
            return self.fallback.increment_user_counter(client_id)

    def check_daily_limit(self, limit: int = 20) -> bool:
        if not self.client:
            return self.fallback.check_daily_limit(limit)
        try:
            today = datetime.date.today().isoformat()
            count = self.client.scard(f"repomind:daily_repos:{today}")
            return count < limit
        except Exception as e:
            logger.warning("Redis check_daily_limit error (%s), using fallback", e)
            return self.fallback.check_daily_limit(limit)

    def increment_daily_counter(self, repo_url: str) -> int:
        if not self.client:
            return self.fallback.increment_daily_counter(repo_url)
        try:
            today = datetime.date.today().isoformat()
            key = f"repomind:daily_repos:{today}"
            self.client.sadd(key, repo_url)
            self.client.expire(key, 172800) # 2 days
            return self.client.scard(key)
        except Exception as e:
            logger.warning("Redis increment_daily_counter error (%s), using fallback", e)
            return self.fallback.increment_daily_counter(repo_url)

    def get_cache(self, key: str) -> Optional[Dict[str, Any]]:
        if not self.client:
            return self.fallback.get_cache(key)
        try:
            raw = self.client.get(f"repomind:cache:{key}")
            if raw:
                return json.loads(raw)
            return self.fallback.get_cache(key)
        except Exception as e:
            logger.warning("Redis get_cache error (%s), checking fallback", e)
            return self.fallback.get_cache(key)

    def set_cache(self, key: str, payload: Dict[str, Any]) -> bool:
        # Save to both Redis and local fallback
        saved_fallback = self.fallback.set_cache(key, payload)
        if not self.client:
            return saved_fallback
        try:
            self.client.setex(
                f"repomind:cache:{key}",
                86400 * 7, # 7 days
                json.dumps(payload)
            )
            return True
        except Exception as e:
            logger.warning("Redis set_cache error (%s), saved to fallback: %s", e, saved_fallback)
            return saved_fallback


def create_store(cache_dir: str) -> BaseStore:
    file_store = FileOrMemoryStore(cache_dir)
    redis_url = os.environ.get("REDIS_URL")
    if redis_url:
        return RedisStore(redis_url, file_store)
    return file_store
