# RepoMind

RepoMind is an AI-powered codebase onboarding assistant that analyzes public GitHub repositories to generate interactive dependency graphs, architectural designs, and custom developer onboarding checklists.

Live Project Link: https://jyotiraditya21-bug.github.io/RepoMind/

## AI Multi-Agent Orchestration

The core analysis engine utilizes a collaborative Multi-Agent architecture orchestrated via LangGraph:

- **Summarizer Agent**: Performs AST structure parsing and module export signature analysis to compile contextual summaries of key source files.
- **Architect Agent**: Models codebase design relationships, computes module connection metrics, and maps developer onboarding pathways.
- **Critic Agent**: Executes inside a self-correcting review loop, auditing generated guides and layouts against the physical codebase structure to eliminate LLM hallucinations.
- **RAG Indexer Agent**: Prepares local codebase fragments and caches them into a semantic vector space.
- **Q&A Router Agent**: Handles developer queries with RAG context using semantic retrieval and cosine-similarity searches.

## Project Structure

- **frontend/**: Static HTML, CSS, and JavaScript interface hosted via GitHub Pages.
- **backend/**: FastAPI service running AST analysis and the LangGraph multi-agent orchestration workflow.
