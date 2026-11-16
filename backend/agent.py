from __future__ import annotations
import os
import json
import hashlib
import re
import ast
import logging
from typing import TypedDict, List, Dict, Any
from dotenv import load_dotenv

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langgraph.graph import StateGraph, END

logger = logging.getLogger("repomind.agent")

# Load environment variables (contains OPENAI_API_KEY)
load_dotenv()

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")


def get_runtime_mode() -> str:
    """
    Returns the active runtime mode of the backend:
    - 'AI mode': Valid OpenAI API key configured for LangGraph multi-agent LLM analysis & RAG
    - 'Heuristic mode': Deterministic AST parsing, keyword ranking, and static graph heuristics
    """
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key or api_key == "your_openai_api_key_here" or api_key.startswith("sk-dummy"):
        return "Heuristic mode"
    return "AI mode"


def generate_source_link(path: str, repo_url: str = "", commit_hash: str = "") -> str:
    """
    Constructs a valid GitHub source link for a file.
    Never returns a local file:// link.
    """
    clean_path = path.strip("/").replace("\\", "/")
    if repo_url and "github.com" in repo_url:
        base_url = repo_url.rstrip("/")
        if base_url.endswith(".git"):
            base_url = base_url[:-4]
        ref = commit_hash if commit_hash and len(commit_hash) >= 7 else "main"
        return f"{base_url}/blob/{ref}/{clean_path}"
    return f"https://github.com/search?q={clean_path}"


# ==========================================
# A1: Static Fallback Heuristic Generators
# ==========================================
def generate_static_file_summary(rel_path: str, full_path: str) -> str:
    """Generates a smart, structure-based static summary for a file if LLM is unavailable."""
    _, ext = os.path.splitext(rel_path)
    ext = ext.lower()
    
    # 1. Try Python docstring or class/function structure
    if ext == ".py":
        try:
            with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read(50000)
            tree = ast.parse(content)
            doc = ast.get_docstring(tree)
            if doc:
                first_lines = [line.strip() for line in doc.split("\n") if line.strip()]
                if first_lines:
                    return first_lines[0]
                    
            classes = [node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
            functions = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")]
            
            desc = "Python module"
            if classes or functions:
                desc += " defining "
                parts = []
                if classes:
                    parts.append(f"class(es): {', '.join(classes[:2])}")
                if functions:
                    parts.append(f"function(s): {', '.join(functions[:3])}")
                desc += " and ".join(parts)
            else:
                desc += " containing utility scripts"
            return desc + "."
        except Exception:
            return "Python implementation file."
            
    # 2. Try JS/TS structures
    if ext in {".js", ".jsx", ".ts", ".tsx"}:
        try:
            with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read(10000)
            classes = re.findall(r'class\s+(\w+)', content)
            functions = re.findall(r'function\s+(\w+)|const\s+(\w+)\s*=\s*(?:\(.*?\)|[^=\n]+)\s*=>', content)
            function_names = [f[0] or f[1] for f in functions if f[0] or f[1]]
            
            desc = "JavaScript/TypeScript file"
            if "api/" in rel_path or "route" in rel_path:
                desc = "API Router endpoint handling requests"
            elif "page.tsx" in rel_path or "page.ts" in rel_path:
                desc = "UI view component defining layout structures"
            elif classes or function_names:
                desc += " exporting "
                parts = []
                if classes:
                    parts.append(f"class(es): {', '.join(classes[:2])}")
                if function_names:
                    parts.append(f"function(s): {', '.join(function_names[:3])}")
                desc += " and ".join(parts)
            return desc + "."
        except Exception:
            return "JavaScript/TypeScript source file."
            
    # 3. Handle configurations
    if ext in {".json", ".yaml", ".yml", ".toml", ".config"}:
        filename = os.path.basename(rel_path)
        return f"Configuration file establishing options and build settings for {filename}."
        
    # 4. Handle other compiled languages
    lang_map = {
        ".go": "Go",
        ".rs": "Rust",
        ".java": "Java",
        ".cs": "C#",
        ".cpp": "C++",
        ".c": "C",
        ".h": "C/C++ Header"
    }
    lang_name = lang_map.get(ext, "source")
    return f"{lang_name} implementation file containing codebase components."


def generate_static_architecture_overview(file_paths: List[str], graph_data: Dict[str, Any], file_summaries: Dict[str, str]) -> str:
    """Creates a deterministic, grounded architecture overview using graph topology and discovered summaries."""
    total_files = len(file_paths)
    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])
    
    # Categorize languages
    ext_counts = {}
    for fp in file_paths:
        _, ext = os.path.splitext(fp)
        ext = ext.lower() or "other"
        ext_counts[ext] = ext_counts.get(ext, 0) + 1
        
    lang_summary = ", ".join([f"{ext}: {count}" for ext, count in sorted(ext_counts.items(), key=lambda x: x[1], reverse=True)[:4]])
    
    # Identify entry points (high out-degree, low in-degree)
    entry_nodes = sorted(nodes, key=lambda n: (n.get("out_degree", 0) - n.get("in_degree", 0)), reverse=True)
    top_entries = [n["id"] for n in entry_nodes[:3] if n.get("out_degree", 0) > 0]
    
    # Identify core utilities (high in-degree)
    core_nodes = sorted(nodes, key=lambda n: n.get("in_degree", 0), reverse=True)
    top_cores = [n["id"] for n in core_nodes[:3] if n.get("in_degree", 0) > 0]
    
    overview = "### Codebase Architecture Overview (Deterministic Analysis)\n\n"
    overview += f"The repository comprises **{total_files} analyzed code files** and **{len(edges)} detected inter-module dependencies**.\n\n"
    overview += f"**Language Distribution:** {lang_summary}\n\n"
    
    if top_entries:
        overview += "**Key Orchestrators / Entry Points:**\n"
        for entry in top_entries:
            summary = file_summaries.get(entry, "Main orchestration script.")
            overview += f"- `{entry}`: {summary}\n"
        overview += "\n"
        
    if top_cores:
        overview += "**Shared Foundation & Utility Modules:**\n"
        for core in top_cores:
            summary = file_summaries.get(core, "Foundational shared module.")
            overview += f"- `{core}`: {summary}\n"
        overview += "\n"
        
    overview += "Analysis performed using deterministic AST module extraction and NetworkX dependency graph synthesis."
    return overview


def generate_static_start_here(file_paths: List[str], graph_data: Dict[str, Any]) -> List[Dict[str, str]]:
    """Generates an ordered checklist of starting files based on centrality and entry point metrics."""
    nodes = graph_data.get("nodes", [])
    if not nodes:
        candidates = file_paths[:3]
        return [{"file": c, "reason": "Repository source file to inspect."} for c in candidates]
        
    # Score files: favor entry points, main files, and high connectivity
    scored = []
    for node in nodes:
        nid = node["id"].lower()
        score = 0
        if "main" in nid or "app" in nid or "index" in nid:
            score += 15
        if "api" in nid or "server" in nid:
            score += 8
        score += node.get("out_degree", 0) * 2
        score += node.get("in_degree", 0)
        scored.append((score, node["id"]))
        
    scored = sorted(scored, key=lambda x: x[0], reverse=True)
    top_files = [item[1] for item in scored[:4]]
    
    result = []
    for fp in top_files:
        reason = "Central application hub with downstream dependencies."
        fp_lower = fp.lower()
        if "main" in fp_lower or "app" in fp_lower:
            reason = "Primary application entry point and execution setup."
        elif "api" in fp_lower or "route" in fp_lower:
            reason = "Key routing endpoint defining client-server contracts."
        elif "agent" in fp_lower or "model" in fp_lower:
            reason = "Core computational logic and model execution pipeline."
        result.append({"file": fp, "reason": reason})
    return result


def get_repo_cache_key(repo_url: str, commit_hash: str) -> str:
    """Generates a secure MD5 hash string combining repo URL and commit hash."""
    raw = f"{repo_url.strip().lower()}:{commit_hash.strip()}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def get_cached_analysis(repo_url: str, commit_hash: str) -> dict | None:
    """Retrieves cached analysis from disk if available."""
    key_hash = get_repo_cache_key(repo_url, commit_hash)
    cache_file = os.path.join(CACHE_DIR, f"{key_hash}.json")
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def save_analysis_to_cache(repo_url: str, commit_hash: str, data: dict):
    """Saves the completed analysis payload to the filesystem cache."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    key_hash = get_repo_cache_key(repo_url, commit_hash)
    cache_file = os.path.join(CACHE_DIR, f"{key_hash}.json")
    try:
        temp_file = f"{cache_file}.tmp.{os.getpid()}"
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(temp_file, cache_file)
        logger.info("Analysis saved to cache: %s", cache_file)
    except Exception as e:
        logger.error("Error writing cache file %s: %s", cache_file, e)


# ==========================================
# B2: LangGraph State Definition
# ==========================================
class AgentState(TypedDict):
    repo_url: str
    commit_hash: str
    repo_dir: str
    file_paths: List[str]
    graph_data: Dict[str, Any]
    
    file_summaries: Dict[str, str]
    architecture_overview: str
    start_here: List[Dict[str, str]]
    
    rag_chunks: List[Dict[str, Any]]
    
    critic_issues: List[str]
    revision_count: int
    accurate: bool
    runtime_mode: str


# ==========================================
# B3: Summarizer Node
# ==========================================
def summarizer_node(state: AgentState) -> Dict[str, Any]:
    nodes = state["graph_data"].get("nodes", [])
    if not nodes:
        return {"file_summaries": {}}
        
    sorted_nodes = sorted(nodes, key=lambda x: x.get("degree", 0), reverse=True)
    top_n = sorted_nodes[:8]
    
    file_summaries = {}
    mode = get_runtime_mode()
    use_llm = mode == "AI mode"
    
    llm = None
    if use_llm:
        try:
            llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, max_tokens=60)
        except Exception as e:
            logger.warning("ChatOpenAI initialization failed: %s", e)
            use_llm = False
            
    for item in top_n:
        rel_path = item["id"]
        full_path = os.path.join(state["repo_dir"], rel_path)
        
        if not os.path.exists(full_path):
            continue
            
        static_summary = generate_static_file_summary(rel_path, full_path)
        
        if use_llm and llm:
            try:
                with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                    code_content = f.read(10000)
                    
                _, ext = os.path.splitext(rel_path)
                lang = ext.strip(".").lower() or "text"
                    
                system_prompt = (
                    "You are an expert code summarizer. Describe the role/purpose of the following file "
                    "in the codebase in exactly 1-2 concise, highly informative sentences. Be specific."
                )
                user_prompt = f"File: {rel_path}\nLanguage: {lang}\n\n```{lang}\n{code_content}\n```"
                
                resp = llm.invoke([("system", system_prompt), ("human", user_prompt)])
                summary = resp.content.strip() if resp and resp.content else static_summary
                file_summaries[rel_path] = summary
            except Exception as e:
                logger.debug("Summarizer LLM call failed for %s (%s). Using static summary.", rel_path, e)
                file_summaries[rel_path] = static_summary
        else:
            file_summaries[rel_path] = static_summary
            
    return {"file_summaries": file_summaries, "runtime_mode": mode}


# ==========================================
# B4: Architecture Synthesizer Node
# ==========================================
def architecture_node(state: AgentState) -> Dict[str, Any]:
    file_paths = state.get("file_paths", [])
    graph_data = state.get("graph_data", {})
    file_summaries = state.get("file_summaries", {})
    
    mode = state.get("runtime_mode", get_runtime_mode())
    use_llm = mode == "AI mode"
    
    static_overview = generate_static_architecture_overview(file_paths, graph_data, file_summaries)
    static_start_here = generate_static_start_here(file_paths, graph_data)
    
    if not use_llm:
        return {
            "architecture_overview": static_overview,
            "start_here": static_start_here
        }
        
    try:
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, max_tokens=600)
        system_prompt = (
            "You are a Principal Software Architect. Synthesize a structured, Markdown-formatted "
            "architecture overview and an onboarding guide for a new engineer exploring this codebase."
        )
        summaries_str = "\n".join([f"- **{f}**: {s}" for f, s in file_summaries.items()])
        edges_str = "\n".join([f"- `{e['from']}` -> `{e['to']}`" for e in graph_data.get("edges", [])[:20]])
        
        user_prompt = (
            f"Codebase files ({len(file_paths)} files):\n{summaries_str}\n\n"
            f"Dependency Connections:\n{edges_str}\n\n"
            "Provide:\n"
            "1. Architecture Overview (High-level design & flow)\n"
            "2. Key Modules & Roles\n"
        )
        
        resp = llm.invoke([("system", system_prompt), ("human", user_prompt)])
        overview = resp.content.strip() if resp and resp.content else static_overview
        return {
            "architecture_overview": overview,
            "start_here": static_start_here
        }
    except Exception as e:
        logger.warning("Architecture LLM call failed: %s. Using static overview.", e)
        return {
            "architecture_overview": static_overview,
            "start_here": static_start_here
        }


# ==========================================
# B5: Critic Node
# ==========================================
def critic_node(state: AgentState) -> Dict[str, Any]:
    arch = state.get("architecture_overview", "")
    revision_count = state.get("revision_count", 0)
    
    # Simple, reliable validation
    if len(arch) > 50 and revision_count >= 0:
        return {"accurate": True, "critic_issues": [], "revision_count": revision_count + 1}
        
    return {"accurate": False, "critic_issues": ["Overview was too brief."], "revision_count": revision_count + 1}


def should_revise(state: AgentState) -> str:
    if state.get("accurate", True) or state.get("revision_count", 0) >= 1:
        return "proceed"
    return "revise"


# ==========================================
# B6: RAG Setup Node
# ==========================================
def rag_setup_node(state: AgentState) -> Dict[str, Any]:
    file_paths = state.get("file_paths", [])
    file_summaries = state.get("file_summaries", {})
    graph_data = state.get("graph_data", {})
    edges = graph_data.get("edges", [])
    
    chunks = []
    texts_to_embed = []
    
    for path in file_paths:
        summary = file_summaries.get(path, "")
        imports = [e["to"] for e in edges if e["from"] == path]
        imported_by = [e["from"] for e in edges if e["to"] == path]
        
        chunk_text = f"File Path: {path}\nSummary: {summary}\n"
        if imports:
            chunk_text += f"Imports: {', '.join(imports)}\n"
        if imported_by:
            chunk_text += f"Imported By: {', '.join(imported_by)}\n"
            
        chunks.append({"path": path, "text": chunk_text})
        texts_to_embed.append(chunk_text)
        
    mode = state.get("runtime_mode", get_runtime_mode())
    if mode == "AI mode" and texts_to_embed:
        try:
            embed_model = OpenAIEmbeddings(model="text-embedding-3-small")
            vectors = embed_model.embed_documents(texts_to_embed)
            for i, vec in enumerate(vectors):
                chunks[i]["vector"] = vec
        except Exception as e:
            logger.warning("Error creating embeddings: %s. Falling back to vectorless chunks.", e)
            for chunk in chunks:
                chunk["vector"] = []
    else:
        for chunk in chunks:
            chunk["vector"] = []
            
    return {"rag_chunks": chunks}


# ==========================================
# B8: Wire the LangGraph Workflow
# ==========================================
def create_agent_workflow() -> StateGraph:
    workflow = StateGraph(AgentState)
    
    workflow.add_node("summarizer", summarizer_node)
    workflow.add_node("architecture", architecture_node)
    workflow.add_node("critic", critic_node)
    workflow.add_node("rag_setup", rag_setup_node)
    
    workflow.set_entry_point("summarizer")
    
    workflow.add_edge("summarizer", "architecture")
    workflow.add_edge("architecture", "critic")
    
    workflow.add_conditional_edges(
        "critic",
        should_revise,
        {
            "revise": "architecture",
            "proceed": "rag_setup"
        }
    )
    
    workflow.add_edge("rag_setup", END)
    
    return workflow.compile()


# ==========================================
# B7: RAG Q&A Node / Search Function
# ==========================================
def dot_product(v1: List[float], v2: List[float]) -> float:
    return sum(x * y for x, y in zip(v1, v2))

def magnitude(v: List[float]) -> float:
    return sum(x * x for x in v) ** 0.5

def cosine_similarity(v1: List[float], v2: List[float]) -> float:
    mag1 = magnitude(v1)
    mag2 = magnitude(v2)
    if mag1 == 0 or mag2 == 0:
        return 0.0
    return dot_product(v1, v2) / (mag1 * mag2)


def answer_codebase_question(
    question: str,
    rag_chunks: List[Dict[str, Any]],
    arch_overview: str,
    repo_url: str = "",
    commit_hash: str = ""
) -> Dict[str, Any]:
    """
    Answers a technical question about the codebase.
    Returns:
      {
        "answer": str,
        "runtime_mode": "AI mode" | "Heuristic mode",
        "sources": List[str]
      }
    Never generates invalid file:// URLs; produces valid GitHub source links.
    """
    if not rag_chunks:
        return {
            "answer": "No codebase context is available to answer questions. Please analyze a repository first.",
            "runtime_mode": "Unavailable",
            "sources": []
        }

    mode = get_runtime_mode()
    sources = []

    # Attempt AI RAG if AI mode is active
    if mode == "AI mode":
        try:
            embed_model = OpenAIEmbeddings(model="text-embedding-3-small")
            query_vector = embed_model.embed_query(question)
            
            scored_chunks = []
            for chunk in rag_chunks:
                vec = chunk.get("vector")
                if vec:
                    score = cosine_similarity(query_vector, vec)
                    scored_chunks.append((score, chunk))
                    
            scored_chunks = sorted(scored_chunks, key=lambda x: x[0], reverse=True)
            top_chunks = scored_chunks[:3]
            
            context_str = ""
            for score, chunk in top_chunks:
                p = chunk.get("path", "")
                link = generate_source_link(p, repo_url, commit_hash)
                sources.append(link)
                context_str += f"--- Match: [{p}]({link}) (score: {score:.3f}) ---\n{chunk['text']}\n"
                
            llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, max_tokens=350)
            
            system_prompt = (
                "You are an expert senior code assistant answering technical questions about a codebase.\n"
                "Use the provided context chunks and architecture overview.\n"
                "When referencing files, use their exact paths. Be precise and keep your response under 250 words."
            )
            user_prompt = (
                f"Architecture Overview:\n{arch_overview}\n\n"
                f"Codebase Context:\n{context_str}\n\n"
                f"Question: {question}"
            )
            
            response = llm.invoke([
                ("system", system_prompt),
                ("human", user_prompt)
            ])
            return {
                "answer": response.content.strip(),
                "runtime_mode": "AI mode",
                "sources": sources
            }
        except Exception as e:
            logger.warning("LLM question answering failed: %s. Falling back to heuristic search.", e)

    # ========================================================
    # Heuristic Keyword Search Fallback
    # ========================================================
    query_words = re.findall(r'\w+', question.lower())
    if not query_words:
        query_words = [question.lower()]
        
    scored_chunks = []
    for chunk in rag_chunks:
        text = chunk.get("text", "").lower()
        path = chunk.get("path", "").lower()
        score = 0
        for word in query_words:
            if word in path:
                score += 15
            score += text.count(word)
        scored_chunks.append((score, chunk))
        
    scored_chunks = sorted(scored_chunks, key=lambda x: x[0], reverse=True)
    top_matches = scored_chunks[:3]
    
    if not top_matches or top_matches[0][0] == 0:
        top_matches = [(0, c) for c in rag_chunks[:3]]
        
    matches_md = ""
    for score, chunk in top_matches:
        path = chunk.get("path", "")
        text = chunk.get("text", "")
        source_link = generate_source_link(path, repo_url, commit_hash)
        sources.append(source_link)
        
        lines = [line.strip() for line in text.split("\n") if line.strip() and not line.startswith("File Path:")]
        snippet = " | ".join(lines)
        matches_md += f"- [{path}]({source_link})\n  _{snippet}_\n\n"
        
    response_md = (
        f"### Heuristic Search Results (Deterministic Mode)\n\n"
        f"Scanning modules for terms matching **\"{question}\"** identified the following relevant files in this repository:\n\n"
        f"{matches_md}"
        f"*(Note: To activate full generative reasoning, configure an `OPENAI_API_KEY` on the backend deployment.)*"
    )
    return {
        "answer": response_md,
        "runtime_mode": "Heuristic mode",
        "sources": sources
    }
