import os
import shutil
import tempfile
import git
import ast
import networkx as nx

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
    allowed_extensions = {".py"}
    ignored_dirs = {
        ".git", "node_modules", "venv", ".venv", "__pycache__",
        "build", "dist", ".pytest_cache", ".github", "docs"
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
            if ext in allowed_extensions:
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
    Also handles parent packages (e.g. if mod_name is 'a.b.c' but we only have 'a.b' which is a file/package).
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


def extract_imports(file_path: str, rel_path: str, module_to_file: dict) -> list[str]:
    """
    Parses the AST of a python file to extract local imports that exist within the repository.
    """
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        tree = ast.parse(content)
    except Exception as e:
        print(f"Error parsing AST for {rel_path}: {e}")
        return []

    imports = set()
    # Get importing file's package name
    parts = os.path.splitext(rel_path)[0].split(os.sep)
    if len(parts) > 1:
        importing_pkg = ".".join(parts[:-1])
    else:
        importing_pkg = ""

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
            
            # Resolve relative package
            if level > 0:
                pkg_parts = importing_pkg.split(".") if importing_pkg else []
                # Go up level-1 times
                if level - 1 <= len(pkg_parts):
                    base_parts = pkg_parts[:len(pkg_parts) - (level - 1)]
                    base_pkg = ".".join(base_parts)
                else:
                    base_pkg = ""
                
                if module:
                    target_mod = f"{base_pkg}.{module}" if base_pkg else module
                else:
                    target_mod = base_pkg
            else:
                target_mod = module if module else ""
                
            # Direct check if target_mod is a file
            resolved = resolve_module(target_mod, module_to_file)
            if resolved:
                imports.add(resolved)
            
            # Also check if we are importing specific sub-modules/files via "names"
            for name in node.names:
                sub_mod = f"{target_mod}.{name.name}" if target_mod else name.name
                resolved_sub = resolve_module(sub_mod, module_to_file)
                if resolved_sub:
                    imports.add(resolved_sub)
                    
    return list(imports)


def build_dependency_graph(repo_dir: str, file_paths: list[str]) -> dict:
    """
    Builds a directed dependency graph using networkx, computes node positions,
    and returns a serialization-friendly dictionary of nodes and edges.
    """
    G = nx.DiGraph()
    for f in file_paths:
        G.add_node(f)
        
    # Map files to module representations
    module_to_file = {}
    for f in file_paths:
        mod_name = os.path.splitext(f)[0].replace(os.sep, ".")
        if mod_name.endswith(".__init__"):
            mod_name = mod_name[:-9]
        module_to_file[mod_name] = f
        
    # Extract imports and build edges
    all_files_set = set(file_paths)
    for f in file_paths:
        full_path = os.path.join(repo_dir, f)
        imports = extract_imports(full_path, f, module_to_file)
        for imp in imports:
            if imp in all_files_set and imp != f:
                # Directed edge: f imports/depends on imp (f -> imp)
                G.add_edge(f, imp)
                
    # Calculate spring layout positions (scaled for frontend canvas)
    if len(G) > 0:
        pos = nx.spring_layout(G, k=1.5 / (len(G) ** 0.5) if len(G) > 0 else 1.0, seed=42)
    else:
        pos = {}
        
    nodes = []
    # Calculate degree or incoming/outgoing counts to help frontend sizing
    for node in G.nodes():
        x, y = pos.get(node, (0.0, 0.0))
        # Degree count for relative sizing in visualization
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
