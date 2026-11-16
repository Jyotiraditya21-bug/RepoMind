import os
import sys
import datetime
import tempfile
import logging
from typing import Optional
from pydantic import BaseModel, Field
from fastapi import FastAPI, HTTPException, status, Request, Response
from fastapi.middleware.cors import CORSMiddleware

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
)
logger = logging.getLogger("repomind.api")

# Ensure local backend modules can be imported reliably
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

from analyzer import (
    validate_github_url,
    clone_repo,
    walk_repo,
    build_dependency_graph
)
from agent import (
    create_agent_workflow,
    get_runtime_mode,
    answer_codebase_question,
    get_repo_cache_key
)
from store import create_store

VERSION = "1.1.0"
CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
store = create_store(CACHE_DIR)

DAILY_LIMIT = int(os.environ.get("DAILY_ANALYSIS_LIMIT", "20"))
USER_SEARCH_LIMIT = int(os.environ.get("USER_ANALYSIS_LIMIT", "10"))

app = FastAPI(
    title="RepoMind API",
    description="Multi-Agent & Heuristic Codebase Onboarding Assistant",
    version=VERSION
)

# CORS Configuration
DEFAULT_ORIGINS = [
    "https://jyotiraditya21-bug.github.io",
    "https://jyotiraditya.is-a.dev",
    "http://localhost:3000",
    "http://localhost:5173",
    "http://localhost:8000",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:8000",
    "http://127.0.0.1:5500",
]
extra_origins = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
allowed_origins = list(set(DEFAULT_ORIGINS + extra_origins))

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# Request & Response schemas
class AnalyzeRequest(BaseModel):
    repo_url: str = Field(..., description="Canonical HTTPS URL of a public GitHub repository")
    device_id: Optional[str] = Field(None, description="Optional anonymous device fingerprint")

class ChatRequest(BaseModel):
    repo_id: str = Field(..., description="Unique analysis cache identifier")
    question: str = Field(..., min_length=2, max_length=500, description="Question about codebase")


def get_client_ip(request: Request) -> str:
    """Helper to retrieve client IP from trusted reverse proxy headers or direct client host."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown_ip"


# Endpoints
@app.get("/")
def read_root():
    return {
        "service": "RepoMind API",
        "status": "online",
        "version": VERSION,
        "runtime_mode": get_runtime_mode(),
        "storage": getattr(store, "mode", "file_store"),
        "documentation": "https://jyotiraditya21-bug.github.io/RepoMind/"
    }


@app.get("/health")
def health_check():
    """Liveness probe returning operational state and active runtime mode."""
    return {
        "status": "ok",
        "service": "RepoMind Backend",
        "version": VERSION,
        "runtime_mode": get_runtime_mode(),
        "storage_mode": getattr(store, "mode", "file_store"),
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }


@app.get("/ready")
def readiness_check():
    """Readiness probe verifying cache directory accessibility."""
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        test_file = os.path.join(CACHE_DIR, ".ready_check")
        with open(test_file, "w") as f:
            f.write("ok")
        os.remove(test_file)
        return {"status": "ready"}
    except Exception as e:
        logger.error("Readiness check failed: %s", e)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Storage not ready")


@app.get("/version")
def version_endpoint():
    """Returns application semantic version information."""
    return {
        "name": "RepoMind",
        "version": VERSION,
        "runtime_mode": get_runtime_mode()
    }


@app.get("/demos")
def get_demos():
    return []


@app.post("/analyze")
def analyze_repo(request: AnalyzeRequest, req: Request, response: Response):
    """
    Analyzes a GitHub repository:
      1. Validates repository URL with strict URL parser.
      2. Checks rate limits (per-user and daily).
      3. Checks cache for instant replay (idempotent, no double counting).
      4. Bounded shallow clone and AST dependency graph extraction.
      5. LangGraph multi-agent analysis (or deterministic heuristic fallback).
      6. Persists cache atomically and updates counters.
    """
    client_ip = get_client_ip(req)
    device_id = request.device_id or client_ip
    client_identifier = f"{client_ip}:{device_id}"

    # 1. URL validation
    try:
        canonical_url, owner, repo_name = validate_github_url(request.repo_url)
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))

    logger.info("Analyze request received for %s from IP %s", canonical_url, client_ip)

    # 2. Rate limit pre-check
    if not store.check_user_limit(client_identifier, USER_SEARCH_LIMIT):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"You have reached your limit of {USER_SEARCH_LIMIT} repository analyses. Please try again tomorrow."
        )

    if not store.check_daily_limit(DAILY_LIMIT):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Daily limit of {DAILY_LIMIT} new repository analyses reached. Please try again later."
        )

    # 3. Setup temporary workspace for bounded shallow clone
    with tempfile.TemporaryDirectory() as temp_dir:
        try:
            commit_hash = clone_repo(canonical_url, temp_dir)
        except ValueError as ve:
            # e.g. oversized repo
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to clone repository '{owner}/{repo_name}'. Please verify it is a public repository."
            )

        repo_id = get_repo_cache_key(canonical_url, commit_hash)

        # 4. Check cache hit (idempotent, doesn't consume user counter on replay)
        cached_payload = store.get_cache(repo_id)
        if cached_payload:
            logger.info("Cache hit for %s (commit %s)", canonical_url, commit_hash[:7])
            cached_payload["repo_id"] = repo_id
            cached_payload["cache_hit"] = True
            cached_payload["runtime_mode"] = cached_payload.get("runtime_mode", get_runtime_mode())
            response.headers["X-Cache"] = "HIT"
            return cached_payload

        response.headers["X-Cache"] = "MISS"

        # 5. Walk Files & Filter
        file_paths = walk_repo(temp_dir)
        if not file_paths:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No supported code files found in this repository. Supported languages include Python, JS/TS, Go, Rust, Java, C#, and C/C++."
            )

        # 6. Build Dependency Graph
        graph_data = build_dependency_graph(temp_dir, file_paths)

        # 7. Run LangGraph Workflow (or deterministic heuristic)
        active_mode = get_runtime_mode()
        try:
            workflow = create_agent_workflow()
            initial_state = {
                "repo_url": canonical_url,
                "commit_hash": commit_hash,
                "repo_dir": temp_dir,
                "file_paths": file_paths,
                "graph_data": graph_data,
                "file_summaries": {},
                "architecture_overview": "",
                "start_here": [],
                "rag_chunks": [],
                "critic_issues": [],
                "revision_count": 0,
                "accurate": False,
                "runtime_mode": active_mode
            }
            final_state = workflow.invoke(initial_state)
        except Exception as e:
            logger.error("Error executing agent workflow for %s: %s", canonical_url, e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error executing codebase analysis: {str(e)}"
            )

        # 8. Construct response payload
        cache_payload = {
            "repo_id": repo_id,
            "repo_url": canonical_url,
            "commit_hash": commit_hash,
            "owner": owner,
            "repo": repo_name,
            "runtime_mode": active_mode,
            "graph_data": final_state["graph_data"],
            "file_summaries": final_state["file_summaries"],
            "architecture_overview": final_state["architecture_overview"],
            "start_here": final_state["start_here"],
            "rag_chunks": final_state["rag_chunks"],
            "cache_hit": False
        }

        # 9. Atomic save to cache
        store.set_cache(repo_id, cache_payload)

        # 10. Increment rate counters only after successful analysis
        store.increment_daily_counter(canonical_url)
        store.increment_user_counter(client_identifier)

        return cache_payload


@app.post("/chat")
def chat_with_codebase(request: ChatRequest):
    """Answers a technical question about the analyzed codebase."""
    payload = store.get_cache(request.repo_id)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Codebase analysis not found. Please analyze the repository first."
        )

    res = answer_codebase_question(
        question=request.question,
        rag_chunks=payload.get("rag_chunks", []),
        arch_overview=payload.get("architecture_overview", ""),
        repo_url=payload.get("repo_url", ""),
        commit_hash=payload.get("commit_hash", "")
    )

    return {
        "answer": res["answer"],
        "runtime_mode": res["runtime_mode"],
        "sources": res.get("sources", [])
    }
