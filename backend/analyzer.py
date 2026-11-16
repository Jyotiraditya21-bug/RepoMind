import os
import shutil
import git
import ast
import re
import urllib.parse
import networkx as nx
import logging

logger = logging.getLogger("repomind.analyzer")

# Extensions of all supported programming languages
ALLOWED_EXTENSIONS = {
    ".py",                              # Python
    ".js", ".jsx", ".ts", ".tsx",      # JavaScript / TypeScript
    ".go",                              # Go
    ".rs",                              # Rust
    ".java",                            # Java
    ".cs",                              # C#
    ".cpp", ".c", ".h", ".hpp", ".cc"  # C / C++
}

# Resource and security limits
MAX_FILES = 60
MAX_SINGLE_FILE_BYTES = 500 * 1024       # 500 KB
MAX_TOTAL_FILE_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_REPO_DISK_BYTES = 25 * 1024 * 1024   # 25 MB max clone size
CLONE_TIMEOUT_SECONDS = 30


def validate_github_url(repo_url: str) -> tuple[str, str, str]:
    """
    Strict URL validation for public GitHub repositories.
    Enforces:
      - HTTPS only
      - Exact hostname 'github.com'
      - No embedded credentials (username/password)
      - No query parameters or fragments
      - Canonical owner/repo path only
    Returns:
      (canonical_url, owner, repo_name)
    Raises:
      ValueError with safe client message on failure.
    """
    if not repo_url or not isinstance(repo_url, str):
        raise ValueError("Repository URL must be a non-empty string.")

    cleaned_url = repo_url.strip()
    try:
        parsed = urllib.parse.urlparse(cleaned_url)
    except Exception as e:
        raise ValueError("Invalid URL format.") from e

    # Protocol must be https
    if parsed.scheme.lower() != "https":
        raise ValueError("Only secure HTTPS GitHub URLs are supported (https://github.com/owner/repo).")

    # Hostname must strictly be github.com (or www.github.com normalized)
    host = (parsed.hostname or "").lower()
    if host not in {"github.com", "www.github.com"}:
        raise ValueError("Only repositories hosted directly on github.com are supported.")

    # Port must be default or 443
    if parsed.port not in {None, 443}:
        raise ValueError("Non-standard ports are not allowed.")

    # Disallow embedded credentials
    if parsed.username or parsed.password:
        raise ValueError("Embedded credentials in repository URLs are strictly forbidden.")

    # Disallow query parameters and fragments
    if parsed.query or parsed.fragment:
        raise ValueError("Repository URLs must not contain query parameters or fragments.")

    # Validate path: must be /<owner>/<repo>[.git][/]
    path = parsed.path.strip("/")
    parts = [p for p in path.split("/") if p]
    if len(parts) != 2:
        raise ValueError("URL must point directly to a GitHub repository: https://github.com/owner/repo")

    owner, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]

    # Validate owner and repo names against safe characters
    name_regex = re.compile(r"^[a-zA-Z0-9_.-]+$")
    if not name_regex.match(owner) or not name_regex.match(repo):
        raise ValueError("Owner or repository name contains invalid characters.")

    # Guard against git CLI option injection (e.g. repo starting with '-')
    if owner.startswith("-") or repo.startswith("-"):
        raise ValueError("Owner or repository names starting with '-' are not allowed.")

    canonical_url = f"https://github.com/{owner}/{repo}"
    return canonical_url, owner, repo


def get_dir_size_bytes(dir_path: str) -> int:
    """Calculates total size in bytes of files in directory."""
    total = 0
    for root, _, files in os.walk(dir_path):
        for f in files:
            fp = os.path.join(root, f)
            if not os.path.islink(fp):
                try:
                    total += os.path.getsize(fp)
                except OSError:
                    pass
    return total


def clone_repo(repo_url: str, dest_dir: str, timeout: int = CLONE_TIMEOUT_SECONDS) -> str:
    """
    Shallow clones a GitHub repository to the target directory with strict bounds.
    Returns the latest commit hash (hexsha) of the cloned repo.
    """
    # Strict validation first
    canonical_url, owner, repo_name = validate_github_url(repo_url)

    if os.path.exists(dest_dir):
        shutil.rmtree(dest_dir)

    logger.info("Shallow cloning %s into %s (depth=1, timeout=%ds)...", canonical_url, dest_dir, timeout)
    try:
        repo = git.Repo.clone_from(
            canonical_url,
            dest_dir,
            depth=1,
            single_branch=True,
            kill_after_timeout=timeout,
            env={"GIT_TERMINAL_PROMPT": "0"}  # Never prompt for passwords
        )
    except git.exc.GitCommandError as ge:
        logger.warning("Git clone failed for %s: %s", canonical_url, ge.stderr or ge)
        # Redact any paths/commands
        raise RuntimeError(f"Failed to clone repository '{owner}/{repo_name}'. Please verify the repository is public and accessible.")
    except Exception as e:
        logger.error("Clone error: %s", e)
        raise RuntimeError(f"Cloning failed: {str(e)}")

    # Enforce directory size cap
    dir_size = get_dir_size_bytes(dest_dir)
    if dir_size > MAX_REPO_DISK_BYTES:
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise ValueError(
            f"Repository size ({dir_size // (1024 * 1024)}MB) exceeds maximum allowed limit ({MAX_REPO_DISK_BYTES // (1024 * 1024)}MB)."
        )

    # Retrieve the latest commit hash for caching purposes
    commit_hash = repo.head.commit.hexsha
    return commit_hash


def walk_repo(repo_dir: str, max_files: int = MAX_FILES) -> list[str]:
    """
    Traverses the repo directory recursively, filtering for allowed file types,
    skipping common ignored directories and lockfiles, and enforcing a file count cap
    and individual file size limits.
    Returns a list of file paths relative to repo_dir.
    """
    ignored_dirs = {
        ".git", "node_modules", "venv", ".venv", "__pycache__",
        "build", "dist", ".pytest_cache", ".github", "docs",
        "target", "bin", "obj", "out", ".next", ".turbo"
    }

    file_list = []
    total_bytes_read = 0

    for root, dirs, files in os.walk(repo_dir):
        dirs[:] = [d for d in dirs if d not in ignored_dirs and not d.startswith(".")]

        for file in files:
            if file.startswith("."):
                continue

            _, ext = os.path.splitext(file)
            if ext.lower() in ALLOWED_EXTENSIONS:
                full_path = os.path.join(root, file)
                try:
                    fsize = os.path.getsize(full_path)
                except OSError:
                    continue

                # Skip oversized individual source files (generated / bundled code)
                if fsize > MAX_SINGLE_FILE_BYTES:
                    continue

                if total_bytes_read + fsize > MAX_TOTAL_FILE_BYTES:
                    logger.warning("Total file byte limit (%d MB) reached.", MAX_TOTAL_FILE_BYTES // (1024 * 1024))
                    return file_list

                rel_path = os.path.relpath(full_path, repo_dir).replace("\\", "/")
                file_list.append(rel_path)
                total_bytes_read += fsize

                if len(file_list) >= max_files:
                    logger.info("File count cap reached (%d files). Stopping traversal.", max_files)
                    return file_list

    return file_list


def resolve_module(mod_name: str, module_to_file: dict) -> str | None:
    """
    Checks if a module name exists in the module_to_file mapping.
    Used primarily for Python absolute/relative module resolution.
    """
    if not mod_name:
        return None
    if mod_name in module_to_file:
        return module_to_file[mod_name]

    parts = mod_name.split(".")
    for i in range(len(parts) - 1, 0, -1):
        parent = ".".join(parts[:i])
        if parent in module_to_file:
            return module_to_file[parent]

    return None


def extract_python_imports(file_path: str, rel_path: str, module_to_file: dict) -> list[str]:
    """
    Uses Python's AST module to extract local imports.
    """
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read(MAX_SINGLE_FILE_BYTES)
        tree = ast.parse(content)
    except Exception as e:
        logger.debug("Error parsing AST for %s: %s", rel_path, e)
        return []

    imports = set()
    parts = os.path.splitext(rel_path)[0].split("/")
    importing_pkg = ".".join(parts[:-1]) if len(parts) > 1 else ""

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for name in node.names:
                resolved = resolve_module(name.name, module_to_file)
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
            content = f.read(MAX_SINGLE_FILE_BYTES)
    except Exception as e:
        logger.debug("Error reading %s for imports: %s", rel_path, e)
        return []

    imports = set()
    current_dir = os.path.dirname(rel_path)

    # 1. Regex for JS/TS & C/C++ relative path imports/includes
    quote_matches = re.findall(
        r'(?:import|export|require|#include)\s+(?:.*?from\s+)?[\'"]([^\'"]+)[\'"]|'
        r'#include\s+<([^>]+)>',
        content
    )

    for match in quote_matches:
        imp_path = match[0] or match[1]
        if not imp_path:
            continue

        if imp_path.startswith(".") or "/" in imp_path or "\\" in imp_path:
            resolved_rel = os.path.normpath(os.path.join(current_dir, imp_path)).replace("\\", "/")

            possible_extensions = ["", ".ts", ".tsx", ".js", ".jsx", ".h", ".hpp", ".cpp", ".c", ".go", ".rs", ".java", ".cs"]
            for pe in possible_extensions:
                p_candidate = resolved_rel + pe
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
                    if part and len(part) > 2:
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

    module_to_file = {}
    for f in file_paths:
        _, ext = os.path.splitext(f)
        if ext.lower() == ".py":
            mod_name = os.path.splitext(f)[0].replace("/", ".")
            if mod_name.endswith(".__init__"):
                mod_name = mod_name[:-9]
            module_to_file[mod_name] = f

    all_files_set = set(file_paths)
    for f in file_paths:
        full_path = os.path.join(repo_dir, f)
        imports = extract_imports(full_path, f, module_to_file, all_files_set)
        for imp in imports:
            if imp in all_files_set and imp != f:
                G.add_edge(f, imp)

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
