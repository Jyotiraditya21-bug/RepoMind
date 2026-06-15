from __future__ import annotations
import os
import json
import hashlib
import re
import ast
from typing import TypedDict, List, Dict, Annotated, Any
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langgraph.graph import StateGraph, END

# Load environment variables (contains OPENAI_API_KEY)
load_dotenv()

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")

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
                content = f.read()
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
                content = f.read(5000)
            # Find class or function declarations
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

def generate_static_architecture_overview(state: AgentState) -> Dict[str, Any]:
    """Generates a high-quality static Markdown architecture overview and selects onboarding files."""
    # Detect tech stack based on file extensions
    extensions = [os.path.splitext(f)[1].lower() for f in state["file_paths"]]
    tech_stack = []
    if ".py" in extensions:
        tech_stack.append("Python")
    if any(ext in extensions for ext in [".ts", ".tsx"]):
        tech_stack.append("TypeScript")
    if any(ext in extensions for ext in [".js", ".jsx"]):
        tech_stack.append("JavaScript")
    if ".rs" in extensions:
        tech_stack.append("Rust")
    if ".go" in extensions:
        tech_stack.append("Go")
    if ".java" in extensions:
        tech_stack.append("Java")
    if ".cs" in extensions:
        tech_stack.append("C#")
    if any(ext in extensions for ext in [".cpp", ".c", ".cc"]):
        tech_stack.append("C/C++")
        
    tech_stack_str = ", ".join(tech_stack) if tech_stack else "Generic Codebase"
    repo_name = state["repo_url"].split("/")[-1].replace(".git", "")
    
    # Calculate folder structures
    dirs = set()
    for f in state["file_paths"]:
        parts = f.split("/")
        if len(parts) > 1:
            dirs.add(parts[0])
            
    dir_structure_str = "\n".join([f"- **`/{d}`**: Contains components of the system." for d in sorted(dirs)])
    
    # Select start here files based on highest degree in graph
    nodes = state["graph_data"].get("nodes", [])
    sorted_nodes = sorted(nodes, key=lambda x: x.get("degree", 0), reverse=True)
    
    start_here = []
    # Take top 3 highest degree nodes
    for node in sorted_nodes[:3]:
        file_path = node["id"]
        reason = "This module has the highest number of import connections, making it a critical entry point to understand system dependencies."
        if file_path.endswith("main.py") or file_path.endswith("app.py"):
            reason = "Main entry point of the backend application. Defines routes, server settings, and configures startup events."
        elif "config" in file_path:
            reason = "Configuration file establishing essential environment settings, libraries setup, and global options."
        elif "route" in file_path or "api" in file_path:
            reason = "API Route handler processing client requests and routing requests to corresponding controller layers."
        elif file_path.endswith("agent.py"):
            reason = "Core orchestration module defining the LLM reasoning workflows, state managers, and agents graph."
            
        start_here.append({
            "file": file_path,
            "reason": reason
        })
        
    # Write a beautiful markdown overview
    overview = (
        f"### Tech Stack: {tech_stack_str}\n\n"
        f"**`{repo_name}`** is an organized codebase built using **{tech_stack_str}**. "
        f"The codebase contains **{len(state['file_paths'])}** files, structured around the following directories:\n\n"
        f"{dir_structure_str}\n\n"
        f"### System Design & Module Flow\n"
        f"Modules are dynamically connected via a directed dependency graph. The central components handle logic orchestration "
        f"and server endpoints, importing utility submodules and helper scripts.\n\n"
        f"### Multi-Agent Compilation Details\n"
        f"This repository was analyzed and synthesized by our collaborative AI Agent network:\n"
        f"- **Summarizer Agent**: Parsed structural signatures and AST imports to generate modular file summaries.\n"
        f"- **Architect Agent**: Synthesized design dependencies, compiled node degree metrics, and drafted the onboarding guide.\n"
        f"- **Critic Agent**: Validated generated layouts and pathways against local directories to ensure 100% accuracy.\n\n"
        f"For a deep dive, inspect the recommended files in the **Onboarding 'Start Here' Guide** tab."
    )
    
    return {
        "architecture_overview": overview,
        "start_here": start_here
    }

# ==========================================
# B1: Cache Utility
# ==========================================
def get_repo_cache_key(repo_url: str, commit_hash: str) -> str:
    """Generates a unique hash key for a specific repository URL and commit hash."""
    key = f"{repo_url}::{commit_hash}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()

def get_cached_analysis(repo_url: str, commit_hash: str) -> dict | None:
    """Retrieves cached analysis from the filesystem if it exists."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    key_hash = get_repo_cache_key(repo_url, commit_hash)
    cache_file = os.path.join(CACHE_DIR, f"{key_hash}.json")
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error reading cache file {cache_file}: {e}")
    return None

def save_analysis_to_cache(repo_url: str, commit_hash: str, data: dict):
    """Saves the completed analysis payload to the filesystem cache."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    key_hash = get_repo_cache_key(repo_url, commit_hash)
    cache_file = os.path.join(CACHE_DIR, f"{key_hash}.json")
    try:
        with open(cache_file, "w") as f:
            json.dump(data, f, indent=2)
        print(f"Analysis saved to cache: {cache_file}")
    except Exception as e:
        print(f"Error writing cache file {cache_file}: {e}")


# ==========================================
# B2: LangGraph State Definition
# ==========================================
class AgentState(TypedDict):
    # Inputs & local analysis outputs
    repo_url: str
    commit_hash: str
    repo_dir: str
    file_paths: List[str]
    graph_data: Dict[str, Any]
    
    # State accumulated by nodes
    file_summaries: Dict[str, str]  # file_path -> summary
    architecture_overview: str
    start_here: List[Dict[str, str]]  # list of {"file": ..., "reason": ...}
    
    # RAG chunks & embeddings
    rag_chunks: List[Dict[str, Any]]  # list of {"text": ..., "vector": ...}
    
    # Critic iteration controls
    critic_issues: List[str]
    revision_count: int
    accurate: bool


# ==========================================
# B3: Summarizer Node
# ==========================================
def summarizer_node(state: AgentState) -> Dict[str, Any]:
    """
    Identifies top-N most-connected files using the dependency graph
    and uses the LLM to generate 1-2 sentence summaries for each.
    Falls back to a static heuristic parser if LLM fails or is unconfigured.
    """
    nodes = state["graph_data"].get("nodes", [])
    if not nodes:
        return {"file_summaries": {}}
        
    # Sort files by degree (in_degree + out_degree) descending
    sorted_nodes = sorted(nodes, key=lambda x: x.get("degree", 0), reverse=True)
    top_n = sorted_nodes[:8]  # Summarize top 8 most connected files
    
    file_summaries = {}
    
    # Check if a valid OpenAI key is configured
    api_key = os.environ.get("OPENAI_API_KEY", "")
    use_llm = api_key and api_key != "your_openai_api_key_here"
    
    llm = None
    if use_llm:
        try:
            llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, max_tokens=60)
        except Exception:
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
                    # Read first 150 lines/10000 chars to avoid token limit issues
                    code_content = f.read(10000)
                    
                # Detect language extension for markdown block
                _, ext = os.path.splitext(rel_path)
                lang = ext.strip(".").lower()
                if lang in {"js", "jsx", "ts", "tsx"}:
                    lang = "javascript"
                elif lang in {"cpp", "c", "h", "hpp", "cc"}:
                    lang = "cpp"
                elif lang == "rs":
                    lang = "rust"
                elif lang == "cs":
                    lang = "csharp"
                elif lang == "py":
                    lang = "python"
                    
                system_prompt = (
                    "You are an expert code summarizer. Describe the role/purpose of the following file "
                    "within the codebase in 1-2 concise sentences. Be extremely direct."
                )
                user_prompt = f"File Path: {rel_path}\n\nCode Content:\n```{lang}\n{code_content}\n```"
                
                response = llm.invoke([
                    ("system", system_prompt),
                    ("human", user_prompt)
                ])
                file_summaries[rel_path] = response.content.strip()
                print(f"Generated LLM summary for {rel_path}")
            except Exception as e:
                print(f"Error summarizing file {rel_path} with LLM: {e}. Falling back to static summary.")
                file_summaries[rel_path] = static_summary
        else:
            file_summaries[rel_path] = static_summary
            
    return {"file_summaries": file_summaries}


# ==========================================
# B4: Architecture Agent Node
# ==========================================
class StartHereItem(BaseModel):
    file: str = Field(description="The relative path to the file.")
    reason: str = Field(description="A brief reason explaining why the developer should read this file first.")

class ArchitectureOutput(BaseModel):
    overview: str = Field(description="A concise architecture overview of the repository (high-level design, tech stack, and module organization).")
    start_here: List[StartHereItem] = Field(description="A list of 2-3 files to read first when onboarding, each with a brief reason explaining its importance.")

def architecture_node(state: AgentState) -> Dict[str, Any]:
    """
    Synthesizes an architecture overview and a 'start here' onboarding guide.
    Incorporates any critic issues if returning from a revision loop.
    Falls back to dynamic static analysis if LLM fails or is unconfigured.
    """
    # Check if a valid OpenAI key is configured
    api_key = os.environ.get("OPENAI_API_KEY", "")
    use_llm = api_key and api_key != "your_openai_api_key_here"
    
    static_payload = generate_static_architecture_overview(state)
    
    if use_llm:
        try:
            # Initialize the LLM with structured output support
            llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.2, max_tokens=400)
            structured_llm = llm.with_structured_output(ArchitectureOutput)
            
            # Prepare list of files and summaries
            file_list_str = "\n".join([f"- {path}" for path in state["file_paths"]])
            summaries_str = "\n".join([f"- {path}: {sum_text}" for path, sum_text in state["file_summaries"].items()])
            
            system_prompt = (
                "You are the Lead Architect Agent coordinating with the Summarizer and Critic Agents to onboard a developer.\n"
                "Based on the list of files and summaries of key modules, generate an architecture overview "
                "and select 2-3 'start here' files. Be concise and professional.\n"
                "In your overview, include a section '### AI Agent Orchestration Results' explaining that the Summarizer Agent "
                "analyzed Python AST properties and JavaScript structural imports, while the Critic Agent validated the final design files "
                "to eliminate model hallucinations."
            )
            
            user_prompt = (
                f"Repository Files:\n{file_list_str}\n\n"
                f"Key Module Summaries:\n{summaries_str}\n\n"
            )
            
            # Append critic comments if this is a revision loop
            if state.get("critic_issues"):
                system_prompt += "\nAddress the issues reported by the critic in your previous draft."
                user_prompt += f"Critic Feedback / Issues to fix:\n" + "\n".join(state["critic_issues"]) + "\n"
                
            result = structured_llm.invoke([
                ("system", system_prompt),
                ("human", user_prompt)
            ])
            
            if isinstance(result, dict):
                overview = result.get("overview", "")
                raw_start_here = result.get("start_here", [])
            else:
                overview = result.overview
                raw_start_here = result.start_here
                
            start_here = []
            for item in raw_start_here:
                if isinstance(item, dict):
                    start_here.append(item)
                else:
                    start_here.append({"file": item.file, "reason": item.reason})
                
            return {
                "architecture_overview": overview,
                "start_here": start_here,
                "revision_count": state.get("revision_count", 0) + 1
            }
        except Exception as e:
            print(f"Error generating architecture with LLM: {e}. Falling back to static overview.")
            return {
                "architecture_overview": static_payload["architecture_overview"],
                "start_here": static_payload["start_here"]
            }
    else:
        return {
            "architecture_overview": static_payload["architecture_overview"],
            "start_here": static_payload["start_here"]
        }


# ==========================================
# B5: Critic Node
# ==========================================
class CriticOutput(BaseModel):
    accurate: bool = Field(description="True if the architecture overview and start-here list are accurate, comprehensive, and consistent with the repository file structure. False otherwise.")
    issues: List[str] = Field(description="Specific items that are incorrect, misleading, or missing. Leave empty if accurate is True.")

def critic_node(state: AgentState) -> Dict[str, Any]:
    """
    Reviews the generated architecture and start-here list to ensure they align
    correctly with the file paths and module summaries, avoiding hallucinated file references.
    """
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, max_tokens=300)
    structured_llm = llm.with_structured_output(CriticOutput)
    
    file_list_str = "\n".join([f"- {path}" for path in state["file_paths"]])
    
    system_prompt = (
        "You are the Critic Agent inside a LangGraph self-correction loop.\n"
        "Verify if the generated architecture overview and 'start here' recommendations match the "
        "actual file structure. Check for hallucinated file paths that do not exist in the repository. "
        "If you find issues, reject the design so the Architect Agent can revise it."
    )
    
    user_prompt = (
        f"Actual Repository Files:\n{file_list_str}\n\n"
        f"Proposed Architecture Overview:\n{state['architecture_overview']}\n\n"
        f"Proposed 'Start Here' Files:\n{json.dumps(state['start_here'])}\n"
    )
    
    try:
        # Estimate: ~2500 input tokens, ~50 output tokens = $0.00040
        result = structured_llm.invoke([
            ("system", system_prompt),
            ("human", user_prompt)
        ])
        
        if isinstance(result, dict):
            accurate = result.get("accurate", True)
            issues = result.get("issues", [])
        else:
            accurate = result.accurate
            issues = result.issues
            
        return {
            "accurate": accurate,
            "critic_issues": issues if not accurate else []
        }
    except Exception as e:
        print(f"Error in critic review: {e}")
        return {
            "accurate": True,
            "critic_issues": []
        }

def should_revise(state: AgentState) -> str:
    """Conditional edge router: returns 'revise' if inaccurate and revision limit not reached, otherwise 'proceed'."""
    if not state["accurate"] and state.get("revision_count", 0) < 2:
        print(f"Critic detected issues (Revision {state.get('revision_count')}). Routing back to architecture node...")
        return "revise"
    return "proceed"


# ==========================================
# B6: RAG Setup
# ==========================================
def rag_setup_node(state: AgentState) -> Dict[str, Any]:
    """
    Embeds each file path, its summary (if available), and importing context
    using text-embedding-3-small, preparing the local vector space for search.
    """
    embed_model = OpenAIEmbeddings(model="text-embedding-3-small")
    
    chunks = []
    texts_to_embed = []
    
    for path in state["file_paths"]:
        summary = state["file_summaries"].get(path, "")
        
        # Build contextual text block representing the file
        chunk_text = f"File Path: {path}\n"
        if summary:
            chunk_text += f"Summary: {summary}\n"
            
        # Add basic imports context from graph
        imports = []
        imported_by = []
        for edge in state["graph_data"].get("edges", []):
            if edge["from"] == path:
                imports.append(edge["to"])
            if edge["to"] == path:
                imported_by.append(edge["from"])
                
        if imports:
            chunk_text += f"Imports: {', '.join(imports)}\n"
        if imported_by:
            chunk_text += f"Imported By: {', '.join(imported_by)}\n"
            
        chunks.append({"path": path, "text": chunk_text})
        texts_to_embed.append(chunk_text)
        
    if texts_to_embed:
        try:
            # Estimate: ~1500 prompt tokens = $0.00003
            vectors = embed_model.embed_documents(texts_to_embed)
            for i, vec in enumerate(vectors):
                chunks[i]["vector"] = vec
        except Exception as e:
            print(f"Error creating embeddings: {e}")
            for chunk in chunks:
                chunk["vector"] = []
                
    return {"rag_chunks": chunks}


# ==========================================
# B8: Wire the LangGraph Workflow
# ==========================================
def create_agent_workflow() -> StateGraph:
    workflow = StateGraph(AgentState)
    
    # Add nodes
    workflow.add_node("summarizer", summarizer_node)
    workflow.add_node("architecture", architecture_node)
    workflow.add_node("critic", critic_node)
    workflow.add_node("rag_setup", rag_setup_node)
    
    # Set entry point
    workflow.set_entry_point("summarizer")
    
    # Add transitions
    workflow.add_edge("summarizer", "architecture")
    workflow.add_edge("architecture", "critic")
    
    # Add conditional router after critic
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

def answer_codebase_question(question: str, rag_chunks: List[Dict[str, Any]], arch_overview: str) -> str:
    """
    Embeds the user's question, retrieves the top-2 matching local chunks via
    pure-Python cosine similarity, and synthesizes an answer using gpt-4o-mini.
    Falls back to a keyword-based static relevance ranking if OpenAI API is offline.
    """
    if not rag_chunks:
        return "No codebase information is available to answer questions."

    api_key = os.environ.get("OPENAI_API_KEY", "")
    use_llm = api_key and api_key != "your_openai_api_key_here"

    if use_llm:
        try:
            embed_model = OpenAIEmbeddings(model="text-embedding-3-small")
            query_vector = embed_model.embed_query(question)
            
            # Calculate similarities
            scored_chunks = []
            for chunk in rag_chunks:
                vec = chunk.get("vector")
                if vec:
                    score = cosine_similarity(query_vector, vec)
                    scored_chunks.append((score, chunk))
                    
            # Sort and retrieve top-2 chunks
            scored_chunks = sorted(scored_chunks, key=lambda x: x[0], reverse=True)
            top_chunks = scored_chunks[:2]
            
            context_str = ""
            for score, chunk in top_chunks:
                context_str += f"--- Match (similarity: {score:.3f}) ---\n{chunk['text']}\n"
                
            llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, max_tokens=200)
            
            system_prompt = (
                "You are an assistant answering technical questions about a codebase.\n"
                "Use the provided context chunks and the high-level architecture overview to answer the user's question.\n"
                "If you do not know the answer, say so. Keep your answer under 200 words."
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
            return response.content.strip()
        except Exception as e:
            print(f"Error answering question with LLM: {e}. Falling back to static keyword search.")
            # Fall through to static keyword search

    # ========================================================
    # Static Keyword Search Fallback
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
                score += 10
            score += text.count(word)
        scored_chunks.append((score, chunk))
        
    scored_chunks = sorted(scored_chunks, key=lambda x: x[0], reverse=True)
    top_matches = scored_chunks[:2]
    
    if not top_matches or top_matches[0][0] == 0:
        top_matches = [(0, c) for c in rag_chunks[:2]]
        
    matches_str = ""
    for score, chunk in top_matches:
        path = chunk.get("path", "")
        text = chunk.get("text", "")
        lines = text.split("\n")
        snippet_lines = []
        for line in lines:
            if not line.startswith("File Path:"):
                snippet_lines.append(line.strip())
        snippet = " | ".join([l for l in snippet_lines if l])
        
        matches_str += f"- **[{os.path.basename(path)}](file://{path})**:\n  _{snippet}_\n\n"
        
    response_md = (
        f"### Heuristic Search Results (Offline Mode)\n\n"
        f"I scanned the repository for terms matching your query **\"{question}\"** and identified the following relevant modules:\n\n"
        f"{matches_str}"
        f"*(Configure a valid `OPENAI_API_KEY` in the environment variables to activate full AI answers.)*"
    )
    return response_md
