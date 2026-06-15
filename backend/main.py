import os
import json
import datetime
import tempfile
import shutil
from typing import List, Dict, Any
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException, status, Request
from fastapi.middleware.cors import CORSMiddleware

from analyzer import clone_repo, walk_repo, build_dependency_graph
from agent import (
    create_agent_workflow, 
    get_cached_analysis, 
    save_analysis_to_cache, 
    answer_codebase_question,
    get_repo_cache_key
)

app = FastAPI(title="AI Codebase Onboarding Assistant API")

# Configure CORS
# Allow requests from GitHub Pages and local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Using * for HF Spaces compatibility with static frontends
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Constants
CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
COUNTER_FILE = os.path.join(CACHE_DIR, "daily_counter.json")
USER_LIMITS_FILE = os.path.join(CACHE_DIR, "user_limits.json")
DAILY_LIMIT = 10
USER_SEARCH_LIMIT = 5

# Request & Response schemas
class AnalyzeRequest(BaseModel):
    repo_url: str
    device_id: str | None = None

class ChatRequest(BaseModel):
    repo_id: str
    question: str

# Helper functions for rate limiting
def get_client_ip(request: Request) -> str:
    """Helper to retrieve client IP, checking behind reverse proxies first."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown_ip"

def check_user_limit(client_ip: str, device_id: str | None = None) -> bool:
    """Returns True if neither the client IP nor the device ID has exceeded the 5-search limit."""
    if not os.path.exists(USER_LIMITS_FILE):
        return True
    try:
        with open(USER_LIMITS_FILE, "r") as f:
            limits = json.load(f)
            
        # Check IP limit
        if limits.get(client_ip, 0) >= USER_SEARCH_LIMIT:
            return False
            
        # Check device ID limit
        if device_id and limits.get(device_id, 0) >= USER_SEARCH_LIMIT:
            return False
            
        return True
    except Exception as e:
        print(f"Error checking user limit: {e}")
        return True

def increment_user_counter(client_ip: str, device_id: str | None = None):
    """Increments the search counter for the client IP and device ID."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    limits = {}
    if os.path.exists(USER_LIMITS_FILE):
        try:
            with open(USER_LIMITS_FILE, "r") as f:
                limits = json.load(f)
        except Exception:
            pass
            
    limits[client_ip] = limits.get(client_ip, 0) + 1
    if device_id:
        limits[device_id] = limits.get(device_id, 0) + 1
        
    try:
        with open(USER_LIMITS_FILE, "w") as f:
            json.dump(limits, f)
    except Exception as e:
        print(f"Error saving user limits: {e}")

def check_daily_limit() -> bool:
    """Returns True if the limit of new analyses for the day has NOT been reached."""
    today = datetime.date.today().isoformat()
    if not os.path.exists(COUNTER_FILE):
        return True
    try:
        with open(COUNTER_FILE, "r") as f:
            counter = json.load(f)
        day_entry = counter.get(today, {"count": 0, "repos": []})
        return day_entry["count"] < DAILY_LIMIT
    except Exception as e:
        print(f"Error checking daily limit: {e}")
        return True

def increment_daily_counter(repo_url: str):
    """Increments the daily analysis counter for non-cached repos."""
    today = datetime.date.today().isoformat()
    os.makedirs(CACHE_DIR, exist_ok=True)
    counter = {}
    if os.path.exists(COUNTER_FILE):
        try:
            with open(COUNTER_FILE, "r") as f:
                counter = json.load(f)
        except Exception:
            pass
            
    day_entry = counter.setdefault(today, {"count": 0, "repos": []})
    if repo_url not in day_entry["repos"]:
        day_entry["repos"].append(repo_url)
        day_entry["count"] += 1
        try:
            with open(COUNTER_FILE, "w") as f:
                json.dump(counter, f)
        except Exception as e:
            print(f"Error saving daily counter: {e}")

# Helper to load analysis from cache
def load_analysis_payload(repo_id: str) -> dict | None:
    # Check standard cache
    cache_file = os.path.join(CACHE_DIR, f"{repo_id}.json")
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r") as f:
                return json.load(f)
        except Exception:
            pass
            
    return None

# Endpoints
@app.get("/")
def read_root():
    return {
        "status": "active",
        "message": "RepoMind API is online and running successfully. Access the frontend site to analyze repositories."
    }

@app.get("/demos")
def get_demos():
    """Returns the list of pre-baked demo repositories (now empty)."""
    return []

@app.post("/analyze")
def analyze_repo(request: AnalyzeRequest, req: Request):
    """Clones a GitHub repository, computes dependencies, runs LangGraph analysis, and returns results."""
    repo_url = request.repo_url.strip()
    if not repo_url.startswith(("http://", "https://", "git://")) or "github.com" not in repo_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid repository URL. Please provide a valid GitHub repository URL."
        )

    # Pre-baked demos check removed

    # 2. Setup local cloning
    with tempfile.TemporaryDirectory() as temp_dir:
        try:
            commit_hash = clone_repo(repo_url, temp_dir)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to clone repository: {str(e)}"
            )
            
        repo_id = get_repo_cache_key(repo_url, commit_hash)
        
        # 3. Check Cache
        cached_payload = load_analysis_payload(repo_id)
        if cached_payload:
            print(f"Cache hit for {repo_url} (commit: {commit_hash})")
            cached_payload["repo_id"] = repo_id
            return cached_payload
            
        # 4. Enforce Per-User Search Limit (5 per user/IP/Device)
        client_ip = get_client_ip(req)
        if not check_user_limit(client_ip, request.device_id):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="You have reached your limit of 5 new repository analyses. Deploy your own backend to lift this limit!"
            )

        # 5. Enforce Daily Limit (exemption for pre-baked demos) - Bypassed to allow unlimited global runs with a 5-runs-per-user limit
        # if not check_daily_limit():
        #     raise HTTPException(
        #         status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        #         detail="Daily limit of 10 new repository analyses reached. Try a pre-baked demo repo!"
        #     )
            
        # 6. Walk Files & Filter
        file_paths = walk_repo(temp_dir)
        if not file_paths:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No supported code files found in this repository. RepoMind supports Python, JS/TS, Go, Rust, Java, C#, and C/C++."
            )
            
        # 7. Build Dependency Graph
        graph_data = build_dependency_graph(temp_dir, file_paths)
        
        # 8. Run LangGraph Workflow
        try:
            workflow = create_agent_workflow()
            initial_state = {
                "repo_url": repo_url,
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
                "accurate": False
            }
            final_state = workflow.invoke(initial_state)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error running agent analysis: {str(e)}"
            )
            
        # 9. Save to Cache
        cache_payload = {
            "graph_data": final_state["graph_data"],
            "file_summaries": final_state["file_summaries"],
            "architecture_overview": final_state["architecture_overview"],
            "start_here": final_state["start_here"],
            "rag_chunks": final_state["rag_chunks"]
        }
        save_analysis_to_cache(repo_url, commit_hash, cache_payload)
        
        # 10. Increment limit counters
        increment_daily_counter(repo_url)
        increment_user_counter(client_ip, request.device_id)
        
        cache_payload["repo_id"] = repo_id
        return cache_payload

@app.post("/chat")
def chat_with_codebase(request: ChatRequest):
    """Answers a question about a repository using cached embeddings and RAG."""
    payload = load_analysis_payload(request.repo_id)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Codebase analysis not found. Please analyze the repository first."
        )
        
    answer = answer_codebase_question(
        question=request.question,
        rag_chunks=payload.get("rag_chunks", []),
        arch_overview=payload.get("architecture_overview", "")
    )
    
    return {"answer": answer}
