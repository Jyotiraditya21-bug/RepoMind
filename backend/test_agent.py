import os
import shutil
import tempfile
from analyzer import clone_repo, walk_repo, build_dependency_graph
from agent import create_agent_workflow, get_cached_analysis, save_analysis_to_cache, answer_codebase_question

def run_test():
    repo_url = "/Users/jimmycodes/RepoMind" # Using local repo path for instant testing
    
    with tempfile.TemporaryDirectory() as temp_dir:
        # Step A2: Shallow clone
        try:
            commit_hash = clone_repo(repo_url, temp_dir)
        except Exception as e:
            print(f"Cloning failed (likely not initialized as a git repo yet): {e}")
            # Fallback to local files directly for test purposes
            commit_hash = "local_test_hash"
            shutil.copytree(repo_url, temp_dir, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git", "venv", ".venv", "__pycache__", "cache"))
            
        print(f"Target Commit Hash: {commit_hash}")
        
        # Check cache first
        cached = get_cached_analysis(repo_url, commit_hash)
        if cached:
            print("--- Cache Hit! Loading analysis from cache ---")
            print(f"Overview:\n{cached['architecture_overview']}\n")
            print("Testing RAG Chat from cache:")
            answer = answer_codebase_question("What does analyzer.py do?", cached["rag_chunks"], cached["architecture_overview"])
            print(f"RAG Answer: {answer}")
            return
            
        # Step A3: Walk
        file_paths = walk_repo(temp_dir)
        print(f"Found {len(file_paths)} files to analyze: {file_paths}")
        
        # Step A5: Graph
        graph_data = build_dependency_graph(temp_dir, file_paths)
        print(f"Graph Nodes: {[node['id'] for node in graph_data['nodes']]}")
        print(f"Graph Edges: {graph_data['edges']}")
        
        # Wire and run workflow
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
        
        print("Running LangGraph workflow...")
        final_state = workflow.invoke(initial_state)
        
        print("\n--- LangGraph Workflow Finished ---")
        print(f"Overview:\n{final_state['architecture_overview']}\n")
        print(f"Start Here files: {final_state['start_here']}\n")
        
        # Save to Cache
        cache_payload = {
            "graph_data": final_state["graph_data"],
            "file_summaries": final_state["file_summaries"],
            "architecture_overview": final_state["architecture_overview"],
            "start_here": final_state["start_here"],
            "rag_chunks": final_state["rag_chunks"]
        }
        save_analysis_to_cache(repo_url, commit_hash, cache_payload)
        
        # Test RAG
        print("Testing RAG Chat:")
        answer = answer_codebase_question("What is the role of agent.py?", final_state["rag_chunks"], final_state["architecture_overview"])
        print(f"RAG Answer: {answer}")

if __name__ == "__main__":
    run_test()
