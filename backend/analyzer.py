import os
import shutil
import tempfile
import git
import ast
import re
import networkx as nx

# Extensions of all supported programming languages
ALLOWED_EXTENSIONS = {
    ".py",                        # Python
    ".js", ".jsx", ".ts", ".tsx",  # JavaScript / TypeScript
    ".go",                        # Go
    ".rs",                        # Rust
    ".java",                      # Java
    ".cs",                        # C#
    ".cpp", ".c", ".h", ".hpp", ".cc" # C / C++
}

def clone_repo(repo_url: str, dest_dir: str) -> str:
    """
    Shallow clones a GitHub repository to the target directory.
    Returns the latest commit hash (hexsha) of the cloned repo.
    """
    if os.path.exists(dest_dir):
        shutil.rmtree(dest_dir)
        
    print(f"Shallow cloning {repo_url} into {dest_dir}...")
    repo = git.Repo.clone_from(repo_url, dest_dir, depth=1)
    
    # Retrieve the latest commit hash for caching purposes
    commit_hash = repo.head.commit.hexsha
    return commit_hash


def walk_repo(repo_dir: str, max_files: int = 60) -> list[str]:
    """
    Traverses the repo directory recursively, filtering for allowed file types,
    skipping common ignored directories and lockfiles, and enforcing a file count cap.
    Returns a list of file paths relative to repo_dir.
    """
    ignored_dirs = {
        ".git", "node_modules", "venv", ".venv", "__pycache__",
        "build", "dist", ".pytest_cache", ".github", "docs",
        "target", "bin", "obj", "out"
    }
    
    file_list = []
    
    for root, dirs, files in os.walk(repo_dir):
        # Prune ignored directories in-place to prevent os.walk from entering them
        dirs[:] = [d for d in dirs if d not in ignored_dirs and not d.startswith(".")]
        
        for file in files:
            if file.startswith("."):
                continue
            
            # Filter by extension and skip lockfiles/compiled files
            _, ext = os.path.splitext(file)
            if ext.lower() in ALLOWED_EXTENSIONS:
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, repo_dir)
                file_list.append(rel_path)
                
                if len(file_list) >= max_files:
                    print(f"File count cap reached ({max_files} files). Stopping traversal.")
                    return file_list
                    
    return file_list


def resolve_module(mod_name: str, module_to_file: dict) -> str | None:
    """
    Checks if a module name exists in the module_to_file mapping.
    Used primarily for Python absolute/relative module resolution.
    """
    if not mod_name:
        return None
    # Try direct match
    if mod_name in module_to_file:
        return module_to_file[mod_name]
    
    # Try matching parents (e.g. from utils import helper where helper is a function in utils.py)
    parts = mod_name.split(".")
    for i in range(len(parts) - 1, 0, -1):
        parent = ".".join(parts[:i])
        if parent in module_to_file:
            return module_to_file[parent]
            
    return None


def extract_python_imports(file_path: str, rel_path: str, module_to_file: dict) -> list[str]:
    """
    Uses Python's AST module to extract local imports with 100% accuracy.
    """
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        tree = ast.parse(content)
    except Exception as e:
        print(f"Error parsing AST for {rel_path}: {e}")
        return []

    imports = set()
    parts = os.path.splitext(rel_path)[0].split(os.sep)
    importing_pkg = ".".join(parts[:-1]) if len(parts) > 1 else ""

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for name in node.names:
                mod = name.name
                resolved = resolve_module(mod, module_to_file)
                if resolved:
                    imports.add(resolved)
        elif isinstance(node, ast.ImportFrom):
            level = node.level
            module = node.module
            
            if level > 0:
                pkg_parts = importing_pkg.split(".") if importing_pkg else []
                if level - 1 <= len(pkg_parts):
                    base_parts = pkg_parts[:len(pkg_parts) - (level - 1)]
                    base_pkg = ".".join(base_parts)
                else:
                    base_pkg = ""
                
                target_mod = f"{base_pkg}.{module}" if base_pkg and module else (module or base_pkg)
            else:
                target_mod = module if module else ""
                
            resolved = resolve_module(target_mod, module_to_file)
            if resolved:
                imports.add(resolved)
            
            for name in node.names:
                sub_mod = f"{target_mod}.{name.name}" if target_mod else name.name
                resolved_sub = resolve_module(sub_mod, module_to_file)
                if resolved_sub:
                    imports.add(resolved_sub)
                    
    return list(imports)


def extract_imports(file_path: str, rel_path: str, module_to_file: dict, all_files: set[str]) -> list[str]:
    """
    Multilingual import extractor. Resolves imports for Python (AST), JS/TS, Go, Rust, Java, C#, and C/C++.
    """
    _, ext = os.path.splitext(rel_path)
    ext = ext.lower()
    
    if ext == ".py":
        return extract_python_imports(file_path, rel_path, module_to_file)
        
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except Exception as e:
        print(f"Error reading {rel_path} for imports: {e}")
        return []
        
    imports = set()
    current_dir = os.path.dirname(rel_path)
    
    # 1. Regex for JS/TS & C/C++ relative path imports/includes
    # Matches strings inside quotes following import, require, or include statements
    quote_matches = re.findall(
        r'(?:import|export|require|#include)\s+(?:.*?from\s+)?[\'"]([^\'"]+)[\'"]|'
        r'#include\s+<([^>]+)>', 
        content
    )
    
    for match in quote_matches:
        imp_path = match[0] or match[1]
        if not imp_path:
            continue
            
        # Handle relative imports or folder path references
        if imp_path.startswith(".") or "/" in imp_path or "\\" in imp_path:
            resolved_rel = os.path.normpath(os.path.join(current_dir, imp_path))
            
            # Check candidates with standard extensions appended
            possible_extensions = ["", ".ts", ".tsx", ".js", ".jsx", ".h", ".hpp", ".cpp", ".c", ".go", ".rs", ".java", ".cs"]
            for pe in possible_extensions:
                p_candidate = resolved_rel + pe
                # Handle directory index files (common in JS/TS and Rust)
                for idx_file in ["", "index.ts", "index.tsx", "index.js", "index.jsx", "mod.rs"]:
                    p_trial = os.path.join(p_candidate, idx_file) if idx_file else p_candidate
                    p_trial_normalized = os.path.normpath(p_trial).replace("\\", "/")
                    if p_trial_normalized in all_files:
                        imports.add(p_trial_normalized)
                        break
                        
    # 2. Rust 'use crate::' and 'mod' imports
    if ext == ".rs":
        rust_matches = re.findall(r'use\s+(?:crate|super|self)::([\w:]+)|mod\s+(\w+);', content)
        for r_match in rust_matches:
            target = r_match[0] or r_match[1]
            if not target:
                continue
            parts = target.split("::")
            for part in parts:
                for file_candidate in all_files:
                    if file_candidate.endswith(f"/{part}.rs") or file_candidate == f"{part}.rs" or file_candidate.endswith(f"/{part}/mod.rs"):
                        imports.add(file_candidate)
                        
    # 3. Go, Java, and C# Package Imports/Namespaces
    if ext in {".go", ".java", ".cs"}:
        pkg_matches = re.findall(r'import\s+[\'"]([^\'"]+)[\'"]|import\s+([\w.]+);|using\s+([\w.]+);', content)
        for match in pkg_matches:
            pkg = match[0] or match[1] or match[2]
            if not pkg:
                continue
            pkg_parts = pkg.replace(".", "/").split("/")
            for file_candidate in all_files:
                for part in pkg_parts:
                    if part and len(part) > 2:  # Avoid matching very short namespace terms
                        if f"/{part}/" in f"/{file_candidate}/" or file_candidate.startswith(f"{part}/") or os.path.splitext(os.path.basename(file_candidate))[0] == part:
                            imports.add(file_candidate)
                            
    return list(imports)


def build_dependency_graph(repo_dir: str, file_paths: list[str]) -> dict:
    """
    Builds a directed dependency graph using networkx, computes node positions,
    and returns a serialization-friendly dictionary of nodes and edges.
    """
    G = nx.DiGraph()
    for f in file_paths:
        G.add_node(f)
        
    # Map Python files to module representations for AST resolving
    module_to_file = {}
    for f in file_paths:
        _, ext = os.path.splitext(f)
        if ext.lower() == ".py":
            mod_name = os.path.splitext(f)[0].replace(os.sep, ".")
            if mod_name.endswith(".__init__"):
                mod_name = mod_name[:-9]
            module_to_file[mod_name] = f
        
    # Extract imports and build edges
    all_files_set = set(file_paths)
    for f in file_paths:
        full_path = os.path.join(repo_dir, f)
        imports = extract_imports(full_path, f, module_to_file, all_files_set)
        for imp in imports:
            if imp in all_files_set and imp != f:
                # Directed edge: f depends on imp (f -> imp)
                G.add_edge(f, imp)
                
    # Calculate spring layout positions (scaled for frontend canvas)
    if len(G) > 0:
        pos = nx.spring_layout(G, k=1.5 / (len(G) ** 0.5) if len(G) > 0 else 1.0, seed=42)
    else:
        pos = {}
        
    nodes = []
    for node in G.nodes():
        x, y = pos.get(node, (0.0, 0.0))
        in_degree = G.in_degree(node)
        out_degree = G.out_degree(node)
        nodes.append({
            "id": node,
            "label": os.path.basename(node),
            "path": node,
            "x": float(x) * 600,
            "y": float(y) * 600,
            "in_degree": in_degree,
            "out_degree": out_degree,
            "degree": in_degree + out_degree
        })
        
    edges = []
    for u, v in G.edges():
        edges.append({
            "from": u,
            "to": v
        })
        
    return {"nodes": nodes, "edges": edges}
