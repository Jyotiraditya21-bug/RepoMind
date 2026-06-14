import os
import json
import hashlib
from typing import TypedDict, List, Dict, Annotated, Any
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langgraph.graph import StateGraph, END

# Load environment variables (contains OPENAI_API_KEY)
load_dotenv()

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")

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
    """
    nodes = state["graph_data"].get("nodes", [])
    if not nodes:
        return {"file_summaries": {}}
        
    # Sort files by degree (in_degree + out_degree) descending
    sorted_nodes = sorted(nodes, key=lambda x: x.get("degree", 0), reverse=True)
    top_n = sorted_nodes[:8]  # Summarize top 8 most connected files
    
    file_summaries = {}
    
    # Initialize the LLM (gpt-4o-mini, max_tokens=60 for small summaries)
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, max_tokens=60)
    
    for item in top_n:
        rel_path = item["id"]
        full_path = os.path.join(state["repo_dir"], rel_path)
        
        if not os.path.exists(full_path):
            continue
            
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
            
            # Estimate: ~2500 input tokens, ~40 output tokens = $0.00040
            response = llm.invoke([
                ("system", system_prompt),
                ("human", user_prompt)
            ])
            
            file_summaries[rel_path] = response.content.strip()
            print(f"Generated summary for {rel_path}")
        except Exception as e:
            print(f"Error summarizing file {rel_path}: {e}")
            file_summaries[rel_path] = "Failed to generate summary."
            
    return {"file_summaries": file_summaries}


# ==========================================
# B4: Architecture Agent Node
# ==========================================
class ArchitectureOutput(BaseModel):
    overview: str = Field(description="A concise architecture overview of the repository (high-level design, tech stack, and module organization).")
    start_here: List[Dict[str, str]] = Field(description="A list of 2-3 files to read first when onboarding, each with a brief reason explaining its importance.")

def architecture_node(state: AgentState) -> Dict[str, Any]:
    """
    Synthesizes an architecture overview and a 'start here' onboarding guide.
    Incorporates any critic issues if returning from a revision loop.
    """
    # Initialize the LLM with structured output support
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.2, max_tokens=400)
    structured_llm = llm.with_structured_output(ArchitectureOutput)
    
    # Prepare list of files and summaries
    file_list_str = "\n".join([f"- {path}" for path in state["file_paths"]])
    summaries_str = "\n".join([f"- {path}: {sum_text}" for path, sum_text in state["file_summaries"].items()])
    
    system_prompt = (
        "You are an expert software architect onboarding a new developer to a repository.\n"
        "Based on the list of files and summaries of key modules, generate an architecture overview "
        "and select 2-3 'start here' files. Be concise and professional."
    )
    
    user_prompt = (
        f"Repository Files:\n{file_list_str}\n\n"
        f"Key Module Summaries:\n{summaries_str}\n\n"
    )
    
    # Append critic comments if this is a revision loop
    if state.get("critic_issues"):
        system_prompt += "\nAddress the issues reported by the critic in your previous draft."
        user_prompt += f"Critic Feedback / Issues to fix:\n" + "\n".join(state["critic_issues"]) + "\n"
        
    try:
        # Estimate: ~3000 input tokens, ~300 output tokens = $0.00063
        result = structured_llm.invoke([
            ("system", system_prompt),
            ("human", user_prompt)
        ])
        
        if isinstance(result, dict):
            overview = result.get("overview", "")
            start_here = result.get("start_here", [])
        else:
            overview = result.overview
            start_here = result.start_here
            
        return {
            "architecture_overview": overview,
            "start_here": start_here,
            "revision_count": state.get("revision_count", 0) + 1
        }
    except Exception as e:
        print(f"Error generating architecture: {e}")
        return {
            "architecture_overview": "Error generating architecture overview.",
            "start_here": []
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
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, max_tokens=100)
    structured_llm = llm.with_structured_output(CriticOutput)
    
    file_list_str = "\n".join([f"- {path}" for path in state["file_paths"]])
    
    system_prompt = (
        "You are an expert code reviewer / critic.\n"
        "Verify if the generated architecture overview and 'start here' recommendations match the "
        "actual file structure. Check for hallucinated file paths that do not exist in the repository."
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
    """
    if not rag_chunks:
        return "No codebase information is available to answer questions."
        
    embed_model = OpenAIEmbeddings(model="text-embedding-3-small")
    try:
        # Estimate: ~20 prompt tokens = $0.0000004
        query_vector = embed_model.embed_query(question)
    except Exception as e:
        print(f"Error embedding query: {e}")
        return "Failed to process the question vector."
        
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
    
    try:
        # Estimate: ~800 prompt tokens, ~100 completion tokens = $0.00018
        response = llm.invoke([
            ("system", system_prompt),
            ("human", user_prompt)
        ])
        return response.content.strip()
    except Exception as e:
        print(f"Error answering question: {e}")
        return "Error occurred while generating an answer."
